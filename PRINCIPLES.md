# ORCAdesk Development Principles

Normative engineering principles for ORCAdesk. They were extracted from the
codebase itself — every principle below is one the code already practices (with
its known exceptions recorded honestly in Appendix A) — and then ratified as
binding. New code is expected to follow them; a deliberate deviation must be
justified in the commit body and, if it sticks, recorded here.

Companion document: [`DESIGN.md`](DESIGN.md) (visual/UX principles).
[`CLAUDE.md`](CLAUDE.md) remains the operational guide (architecture map,
commands, workflow mechanics); this file is the *why* behind it. Where the two
overlap, this file is the norm and CLAUDE.md is the summary.

**How to amend.** The same way this project has always grown its rules
(P53): when a new convention emerges or a violation incident occurs, codify
the rule here *in the same commit* as the change or fix. Principles have
stable IDs (`P1`, `P10`, …) so reviews and commit messages can cite them;
IDs are allocated in blocks of ten per section, gaps are reserved for future
principles (not deletions), and a new principle takes the next free number in
its section's block. References name files and symbols, not line numbers, so
they survive edits.

---

## 0. Prime directives

Everything else in this document is a consequence of these seven.

### P1 — The user's computation outlives the app

An ORCA job can run for days. Nothing ORCAdesk does — closing, crashing,
updating, Ctrl-C in a dev console — may kill or corrupt a running
calculation. This is why ORCA is launched **detached** with its stdout going
straight to the `.out` file (never through a pipe: a pipe dies with its
reader), why `(pid, create_time)` is persisted immediately after launch, why
shutdown *pauses* monitoring instead of cancelling (`MainWindow.shutdown`,
`QueueStore.pause_run`), and why cleanup is idempotent and registered on every
exit path (closeEvent, aboutToQuit, atexit, SIGINT/SIGTERM in `main.py`).
Evidence: `orcamgr/core/runner.py` module docstring, `orcamgr/gui/window.py`.

One deliberate exception: **MLIP jobs are short** and carry no
detach/reattach machinery — on shutdown a running MLIP relaxation is
terminated (`mlip/runner.py` docstring; see A17). The absolute rule is about
ORCA jobs.

### P2 — Honesty over reassurance

Never display certainty the system does not have.

- Readiness indicators go green only on verified capability: the MLIP pill is
  an **import probe** in the user's interpreter, not a file-exists check
  (`orcamgr/mlip/env.py` — "the common failure mode of 'I ran pip myself' is
  a venv missing a package, which a file-exists check would miss").
- Progress never reads 100% before the completion condition is actually met
  (the optimizer 99% cap in `web/scf_graph.js`).
- Inherently uncertain estimates are shown as calibrated ranges or
  order-of-magnitude buckets, never a precise countdown ("a precise countdown
  would be false precision"); when confidence is too low, show
  "estimating…" instead of a wrong number. Where no honest model exists
  (analytical-frequency / NEB step prediction), show no ETA at all rather
  than a fake one; known-total work (numerical-frequency displacements) may
  show a real time remaining (DESIGN D43).
- Failure reasons are surfaced, not smoothed over (P28).

### P3 — Empirical validation, with counts

This app's correctness rests on what the installed ORCA actually prints and
accepts, not on the manual or on plausible-looking logic. Every parser,
input-generator, or stage-detection change is validated against the real
`.out` corpus, and the *scale and result* of the validation is recorded
("85 real opt runs, ~8% median error", "286 real outputs: zero false
activations") — in the CHANGELOG entry, and in a comment next to the
heuristic it justifies (P7), so a later "improvement" can't silently discard
the evidence. Subtle choices (case-sensitive markers, first-vs-last
occurrence) are justified by the actual counterexample strings that were
observed. A no-behavior-change refactor gets the same treatment (wire format
"byte-identical, verified against 40 captured payloads").

### P4 — Single source of truth

Any fact the system needs twice is defined once and derived everywhere else:

- runtime state → the one shared `QueueStore` instance (P12);
- wire payload shapes → `orcamgr/state/schemas.py`, mirrored by
  `web/types.js` (P15);
- the version → `APP_VERSION` in `orcamgr/paths.py` (P50);
- path resolution → `orcamgr/paths.py` only (P18);
- success judgment and parser dispatch → module-level `validate_result` /
  `result_from_output` shared by the live engine and session reconciliation
  (P19);
- ORCA defaults and keyword normalization → `input_generator.py` constants.

When the same judgment must be made from two code paths, extract it to one
function *before* the second caller exists, not after they diverge.

### P5 — Orchestrate; never bundle, never install

ORCAdesk does not do the chemistry. External scientific toolchains — the ORCA
executable, and equally the MLIP stack (PyTorch/mace/ASE) — are the user's
own installations, invoked by shelling out. ORCAdesk never installs them,
never imports them into its own process, and never bundles them. Core
`requirements.txt` stays minimal (PyQt6 + PyQt6-WebEngine + psutil); optional
features carry
their own requirements file and degrade gracefully when absent (P17). Even
ORCA itself is required only when a non-MLIP calc will actually run.

### P6 — Failure is data, and stays inside its blast radius

Errors are values, not exceptions, once they matter to someone else:

- Parsers and probes never fail on *content*: `parse_mlip_result` and
  `probe_env` never raise at all; `parse_file` tolerates any file content
  (P27) but does propagate file-I/O errors (`OSError`) — every calling
  boundary catches and converts them (`_parse_path`, `_parse_if_exists`,
  `_resolve_geometry`).
- A calc's failure blocks only its (transitive) dependents — never the whole
  queue (`_dependents_of` in `queue.py`).
- Persistence failures never take down the app: reads degrade to defaults or
  partial recovery (skip the corrupt entry, keep the queue), writes are
  best-effort, and the session file is written atomically (P32).
- Optional-dependency absence is a disabled feature, not an error (P17).
- Partial success that would poison downstream work is demoted to failure
  (an MLIP result with no geometry reports `terminated_normally=False` so a
  reference never silently receives an empty geometry).

### P7 — Record the *why* where the decision lives

Every non-obvious decision carries a comment naming the concrete failure mode
it prevents — the no-op signal timer in `main.py`, the case-sensitive parser
markers, the reason DONE calcs aren't re-parsed at startup, why `localhost`
isn't in the trusted-peer list. Decisions *not* to do something are recorded
too (CHANGELOG "Notes"/"Known limitations": freq/NEB ETA evaluated and
rejected, with the reason), so dead ends aren't re-explored. Comment density
here is discipline, not decoration: the comment is what stops the next editor
from breaking the invariant.

---

## 1. Architecture

### P10 — Layering: `gui → state ← server`, and `core` speaks only through callbacks

`orcamgr/state` is the hub; `gui/` and `server/` both depend on it.
`server/` never imports `gui/`; `gui/` imports `server/` only to control the
server's lifecycle (`ServerController` in `window.py`) — queue state always
flows through `state`, never through the other side. `core/` imports no GUI,
HTTP, or store code — it talks to the
world only through `QueueCallbacks` (`log`, `calc_update`, both defaulting to
no-ops) and its exception types. The engine is constructed via a factory the
caller injects (`make_engine_factory`), so the store doesn't know how engines
are built either.

### P11 — State and core layers are framework-free

No PyQt or FastAPI imports in `orcamgr/state` or `orcamgr/core` — declared
in the `state/` docstrings and expressed in `core/` as the GUI-agnostic
callback contract (P10) — so they stay unit-testable in isolation and
reusable from both the Qt desktop and the HTTP server.

### P12 — One shared store; clients hold mirrors, not state

`QueueStore` is created once (`window.py`) and injected into both the Bridge
and the `ServerController` — desktop and phone structurally cannot see
different queues. The JS front-end owns no queue state: it polls and renders
a mirror (`let queue = []  // UI mirror`), and even its local cache re-fetches
on miss.

### P13 — Poll with monotonic counters; never push

State flows to clients by polling plus cheap change detection — the queue
`version` bumped on every mutation, the log `seq` cursor — not by Qt signals
or server push. This is deliberate: it decouples the run worker thread from
the UI thread and gives the desktop and the phone the identical contract.
Slow work follows the same shape: daemon background thread computes, UI polls
(`get_mlip_status`), and a late-arriving async result re-checks that its
target still exists before publishing (P37). The one push in the app is an
OS *event*, not state: a dropped file is dispatched to JS via a one-shot
`runJavaScript` call (`window.py` `_dispatch_drop`).

### P14 — Bridge slots and endpoints exchange JSON strings

Structured data crosses the QWebChannel as a JSON string, always built through
a schema type; only slots returning a single primitive string (a path, file
text) may return a bare string. The moment a slot needs a second value, it
becomes JSON. Slots that look like getters must not mutate (known exception:
`autodetect_orca` — Appendix A).

### P15 — Typed wire schema: construct through it, never validate through it

Every payload shape is declared once as a TypedDict in `state/schemas.py` and
mirrored field-by-field in `web/types.js` (both updated in the same commit;
`tsc --noEmit` stays at zero errors). Responses are *constructed* through the
types — plain dicts at runtime, wire format byte-identical — and no runtime
validation layer is inserted: FastAPI endpoints keep `-> dict` on purpose so
pydantic never sits between the dict and the wire. Wire-shape helpers live
with the domain that owns the vocabulary (`env_payload_*` in `mlip/env.py`),
not in the bridge.

### P16 — Domain raises; the boundary converts

The store and core raise (`ValueError`, `OrcaRunError`); each transport
boundary converts to its own idiom — `{"ok": false, "error": …}` JSON on the
QWebChannel, status codes (400/404/409/401) over HTTP. Exceptions never cross
the JS boundary. The judgment of what is invalid lives in one place (the
domain); only its representation differs per transport.

### P17 — Optional features degrade gracefully

Optional dependencies (fastapi/uvicorn, qrcode, the MLIP toolchain) are
imported lazily at the point of use and gated by an availability check
(`ServerController.is_available()`); their absence disables the feature and
never breaks the core app. `core/` imports `mlip/` lazily so it stays
importable without the package. Partial capability is still offered (the QR
slot returns the connect URL even when `qrcode` is missing).

### P18 — All paths through `paths.py`; never write to the resource root

Read-only bundled assets (dev: project dir; frozen: `sys._MEIPASS`) are
strictly separated from the writable user-data root (`%APPDATA%\ORCAdesk`).
Writing to the resource root is data loss in a frozen build. `build.spec`
must land `web/` and `data/` at the same relative paths `paths.py` reads
(the bundle-path contract). App metadata (`APP_NAME/VERSION/AUTHOR/…`) lives
here too.

### P19 — Separate packages for separate toolchains; shared judgment single-sourced

A subsystem that drives a different toolchain is its own package (`mlip/` is
deliberately outside `core/`; `server/` is a thin adapter over the store with
no domain logic of its own). What *is* shared converges deliberately: all
pipelines produce the same `ParseResult` (units preserved — MACE eV converted
to Hartree), parser choice dispatches through `result_from_output`, success
through `validate_result`, and each share-vs-copy decision carries its
justification in a comment.

### P20 — A moved module leaves a deprecation shim

Relocations keep the old import path working through a re-export shim that
emits a `DeprecationWarning` (`orcamgr/server/store.py`), so no import breaks
silently. New code must use the new path.

---

## 2. Queue and execution semantics

### P21 — Process identity is `(pid, create_time)`; never kill an unverified process

A bare "is pid N alive?" is unsafe across sessions (PID reuse). Reattach and
kill decisions go through `procutil.process_matches` (with a tolerance for
float round-tripping); `kill_tree` verifies identity before killing, targets
the whole tree (terminate → bounded wait → kill), and never raises —
cleanup paths must not fail.

### P22 — Three stop verbs; the user's intent is never recorded as failure

`cancel()` (kill the in-flight job → CANCELLED; the rest of the queue is left
as-is, so PENDING rows stay PENDING and runnable — a stop must not discard the
queued plan), `request_stop_after_current()` (drain; the in-flight job finishes,
rest stays PENDING and runs next time), `detach()` (stop monitoring, leave ORCA
running — the shutdown path). So `cancel()` and the drain differ only in whether
the in-flight job is killed; neither throws away the pending calcs. The
distinction is carried in the type system (`OrcaCancelled` / `OrcaDetached`
subclasses of `OrcaRunError`) so a cancelled calc is CANCELLED, not FAILED, and
does not block its dependents. Control from the UI thread only sets an event; the
worker loop performs the actual kill, so the UI never blocks.

### P23 — Failure propagation is dependency-scoped, not whole-queue

One calc's failure marks only its transitive referents BLOCKED (a distinct
state, with a "Skipped: a dependency failed." message); unrelated calcs keep
running. An overnight batch must not lose the other molecules to one failure.
Even an unexpected `Exception` in the engine is handled the same way — the
queue itself never dies.

### P24 — DONE is frozen; FAILED is locked; only CANCELLED re-runs

A DONE calc is never recomputed (recomputing wastes hours and overwrites a
good result on disk). A calc that fails is **locked at the moment of
failure** — terminally inert, in the spirit of the MLIP-card lock (D41,
§13.3): it cannot be edited, re-run, or reordered, never re-enters the run
loop, and never appears in the overwrite/keep-existing decision. Its only
interactions are read-only diagnosis (state badge, failure message, its
on-disk output — P28) and **removal**. A failed result must never be
promoted to DONE (P2, P25); retrying means building a new calculation, not
resurrecting the failed one. CANCELLED calcs (a deliberate user stop — P22,
not a failure) remain editable and do re-run. Only `EDITABLE_STATES`
(pending/cancelled/blocked) may be edited or reordered, and the UI derives
its affordances from the same rule (D64). Frozen results are reconstructed
lazily (parse-on-miss), never eagerly at startup.

**Removal never touches disk.** The × control stays available in every
state except RUNNING, but it is strictly a queue-list operation — ORCAdesk
never deletes a calculation's workspace folder (already true of every code
path: the app contains no file-deletion code; this makes it binding). For
PENDING that is a plain removal (nothing on disk yet); removing a
DONE/FAILED row deletes only the queue entry — the on-disk results survive,
the unique name is freed for reuse, and calculations that referenced the
removed one stay in the queue to be re-pointed at another calculation
(their BLOCKED state does not lock them out of editing — BLOCKED is in
`EDITABLE_STATES` for exactly this reason).

*(Amended 2026-07-03; implemented in 0.4.3-beta.)*

### P25 — Success is judged per-kind, chemically

Normal termination is not success. `validate_result` enforces the chemistry:
opt/ts_opt (and `*_freq` composites) must converge; freq must have zero
imaginary frequencies; ts_freq exactly one (zero = not a TS; more = a
higher-order saddle point); neb_ts exactly one when FREQ was computed;
mlip_opt must converge. A validation failure is FAILED, with the chemical
meaning in the message.

### P26 — Fail fast before the expensive launch; detect, don't auto-fix

Every user mistake that can be caught before launching a multi-hour process
is caught: empty coordinates, self-reference, missing reference, missing
`{{GEOMETRY}}` placeholder, NEB reactant/product atom-order mismatch, missing
MLIP interpreter. But chemical decisions are never auto-corrected — atom
mapping is detected and reported (first mismatching index), not "fixed".

### P27 — The parser is tolerant, never hard-fails, and the last value wins

Marker-based section detection, `\r\n`-normalized, `errors="replace"` reads,
every field Optional with a default, absent sections silently skipped, and
bounded forward scans. When a value recurs across opt steps, the **last**
occurrence is the converged one and wins. Partial output (a truncated or
crashed run) yields partial results, not an exception — even error extraction
has a three-stage fallback.

### P28 — Errors are diagnosed, actionable user sentences

The audience is a chemist, not a developer. On failure, read the real cause
out of the `.out` via the prioritized signature dictionary
(`_ERROR_SIGNATURES`: most specific first) and present *what* went wrong,
the raw ORCA line, and *what to try next* ("Try more SCF iterations…", "Set
the correct path in Settings."). Diagnosis code must not itself fail
silently — leave a breadcrumb warn log. MLIP failures keep the worker's
precise reason; never run them through the ORCA parser to invent a message.

### P29 — The running queue is live, guarded by state — never diverging

`start_run` hands the engine the store's **own list**, and the engine's
walk picks the next unhandled row under the store's **own lock** — one
queue, one lock, so the visible queue and the executing queue cannot
diverge (the historical failure mode of the frozen-snapshot design was a
mid-run add that would never execute; the live walk makes it execute in
the same run). What mutations must respect while the run flag is set:
only `EDITABLE_STATES` rows may be added/removed/edited/reordered; the
engine's in-flight calcs (state RUNNING, plus `active_names` for the
picked-but-not-yet-stamped window) are untouchable; `clear` still raises;
and reference integrity is enforced at the mutation (no dangling reference
in a mid-run add/edit, no removal/rename of a referenced parent) because
the pre-run screen (`find_dangling_reference`) never sees a mid-run
mutation — an unresolvable reference would FAIL (and lock, P24) the
dependent instead of being refused up front. When more than one calc runs
at a time, the same walk admits by budget instead of one-at-a-time (P57).

---

## 3. Chemical correctness

### P30 — Exact verified mappings; verbatim otherwise

Keyword *name rewriting* toward ORCA happens only through exact, per-entry
maps verified against the installed ORCA build (6.1.1) — never a heuristic
(a hyphen-stripping rule would destroy valid `CAM-B3LYP`). Classification
checks (does this method need a `/C` aux; is this a def2 basis) may use
verified substring tests, but they only gate *whether* something is added
(P31) and never rewrite a name. Anything not in
the map passes through **verbatim**: option lists in `data/*.json` are
navigation aids with provenance (`_source`/`_note` metadata keys, skipped by
the loader), not closed enums, and the UI says so ("No match — X will be
used as-is"). When adding entries, verify against the installed ORCA, not
just the manual.

### P31 — Dispersion is explicit; auxiliary bases only when certain

Always emit `D3BJ` (or `D4`) as a separate keyword — never bare or combined
`-D3` (ambiguous between damping schemes, and ORCA rejects combined tokens).
`_auto_aux` adds an aux basis only in the verified cases (RI-J + def2 family
→ `def2/J`; double hybrids/MP2 → `AutoAux`; RIJK → `def2/JK` for def2 bases,
`AutoAux` otherwise); if the user set one, or RI is off, or an RI-J basis is
unknown: add nothing — don't guess, let the user decide.

---

## 4. Robustness and security

### P32 — Persistence never crashes the app

Reads degrade: corrupt JSON → defaults; a corrupt session entry is skipped
item-by-item rather than losing the queue; a broken `data/*.json` empties
only that list. Writes are best-effort (a save failure must never break the
running app) and the session file is replaced atomically (tmp +
`os.replace`). Every queue mutation autosaves (`_bump_and_save`). The session
payload carries a `schema` version for future migrations. (Known gap:
`Settings.save` is not yet atomic — Appendix A.)

### P33 — Validate at the shared choke point; allowlist settings

Client input is validated where all clients converge — `calc_from_dict` —
because a guard in the desktop JS is bypassed by the phone/HTTP path. Calc
names become on-disk folder names, so they are checked for path-dangerous
characters, `..`, and Windows reserved names (Unicode, e.g. Korean, is
allowed). Settings accept only allowlisted keys (on save and on load) and
allowlisted enum values on the bridge save path; values already on disk are
trusted at load time. The front-end validates too, but for message quality —
the shared layer is the security boundary.

### P34 — Clamp untrusted numerics at the boundary; keep chemical keywords free

Numbers from across a trust boundary are coerced and clamped to `[lo, hi]`
(NaN included) at deserialization (`StepConfig.from_dict`), because they are
f-string-interpolated into `.inp` text — a string would be line injection, an
absurd value resource exhaustion. Strings like functional/basis stay verbatim
(P30): defense for numbers, freedom for chemistry. Unknown fields are
filtered, not rejected (forward compatibility). `charge`/`multiplicity` are
clamped in `calc_from_dict` too — ±100 and [1, 200] respectively (A16,
resolved in 0.4.3-beta).

### P35 — Never trust a spoofable signal

The loopback auth-bypass applies only when the server is actually bound to
loopback — on a LAN bind, a same-host proxy makes every request look like
127.0.0.1, so every `/api` call needs the PIN. Token comparison is
`hmac.compare_digest`; `/api/ping` reports validity without revealing the
token; the PIN is generated once per app launch (at `QueueStore`
construction) and lives for the whole app session, across server
stop/starts.

### P36 — Worker scripts are parameter-free JSON-I/O

Code that runs in the *user's* interpreter (`MACE_WORKER_SCRIPT`, the probe
script) is a module-level constant with no *user-controlled* value
string-formatted into it (no code injection from model names/paths — the
probe interpolates only the module's own backend registry at import time);
all inputs arrive as a JSON config
file via argv, results leave as a JSON file (or a single JSON line), and
stdout is for human logs only. Worker exceptions are recorded in the result
JSON, not inferred from exit codes. Run folders keep the inputs
(`mlip_config.json`, the worker script) so a run is inspectable and
re-runnable by hand.

### P37 — Lock discipline: blocking work outside the lock

Shared mutable state is guarded by a dedicated lock (the store's RLock,
`Bridge._mlip_lock`); anything that can block (process kill, thread join)
takes references inside the lock and acts outside it, so the 1-second UI
polls never stall. Async results arriving late re-validate their target
under the lock before publishing (a probe result for a removed env is
dropped). (Known tensions: session-save I/O under the store lock — accepted;
see Appendix A.)

### P38 — Background threads are named daemons

Long-running helpers run as `daemon=True` threads with identifying names
(`orcadesk-run`, `orcadesk-server`) so they never block process exit and are
identifiable in diagnostics.

---

## 5. Front-end

### P40 — No build step: plain JS + JSDoc, `@ts-check`, strict off on purpose

The front-end is plain global scripts loaded in order — no framework, no
modules, no Node toolchain at runtime. Type safety comes from `// @ts-check`
+ JSDoc against `jsconfig.json` (`npx -p typescript tsc --noEmit` stays at
zero errors). `strict` is off deliberately: the bug class that matters is
payload-field typos across the bridge, not null-safety; `strictNullChecks`
would demand hundreds of `getElementById` guards without catching it (the
reasoning is recorded in `jsconfig.json` itself).

### P41 — The mirror contract

Every payload crossing the bridge is typedef'd in `web/types.js`, mirroring
the Python serialization layer **field by field** (with key counts pinned in
the comments to make drift detectable); every `JSON.parse` of a bridge
response is cast to its typedef; every slot signature is declared in
`web/globals.d.ts`. Adding or changing a slot or payload updates
`schemas.py`, `types.js`, and `globals.d.ts` in the same commit.

### P42 — Escape dynamic HTML

Any dynamic string interpolated into `innerHTML` — names, file contents,
parse results, error messages — goes through `escapeHtml()`. Backend-produced
values are not exempt (parser labels are escaped too). Do not rely on the
calc-name validation regex to make unescaped interpolation safe (Appendix A).

### P43 — Full re-render, gated; incremental only where growth is unbounded

Rendering rebuilds a section's `innerHTML` from template literals; the cost is
controlled by change gates (queue `version`, log `seq`, dirty flags, at most
one graph redraw per tick) rather than by partial-update logic. The one
exception is the log: incremental append with a DOM node cap (2000 lines —
"this was the lag"). The log pipeline is bounded end-to-end — the store trims
its buffer at 5000 → the newest 4000 lines plus up to 500 older `[web] `
console-capture lines retained preferentially (still a hard ~4500 bound;
without the exemption a single ORCA run's stdout would evict every front-end
error before it was read) — and a derived view that must outlive the
buffer (graph history on reattach) is rebuilt from the `.out` on disk
(`get_graph_lines`), never from the capped stream. When the document is
hidden, DOM/SVG work is skipped
entirely and catches up from the counters later. Handlers: index-parameter
interactions use inline `onclick` globals; interactions that must capture a
closure (env ids, drag state, modals) use `createElement`/listeners.

### P44 — Guard every await against races

The 1-second poll runs during every `await`, and buttons can be double-
clicked. Set the guard flag or reservation *before* the await, roll it back
on failure (try/finally), and after returning re-validate anything that could
have shifted (indices into the queue, the edited entry still existing).
The backend independently rejects the same races — the UI guard is for
front-end-only artifacts (double modals), not the security boundary.

### P45 — Results gating lives on the front-end

The backend always sends the whole `ParseResult` plus two gating flags; what
to show per calc kind is decided in `renderResultSections`/`renderSummary`,
and the payload is cached (`_resultExtras`) so the "Show all" toggle
re-renders with **no re-fetch**. Specialty sections are present-only — they
exist only for their kind, so presence is the gate.

### P46 — Stream-parsing and progress logic live in the `SCFGraph` modules

Log-line parsing, progress tracking, and ETA estimation — the logic-dense,
regression-prone part — live outside `app.js`, in modules shared by desktop and
mobile: DOM-free trackers (unit-testable in node, push-based, idempotent by
keying steps on the real cycle number so re-fed lines overwrite instead of
inflating) plus pure functions returning HTML/SVG strings. `app.js` only
pushes lines and inserts the returned strings. One-shot static SVG over a
finished payload may stay in `app.js`. Every regex/heuristic in these modules
carries its real-output validation evidence in a comment (P3).

They are two files behind **one namespace**: `scf_graph.js` (SCF + geometry
convergence graph, the half the mobile PWA loads on its own) and
`progress_panels.js` (freq / TD-DFT / CREST step panels), which extends the
namespace in place and therefore loads right after it. Callers only ever see
`SCFGraph.*`. Split further only along a seam this real — these two halves
share a single helper.

### P47 — Mirror backend state rules; the failed response never overwrites the mirror

UI affordances (editable/removable/draggable, run buttons) derive from the
same state rules the store enforces (`isEditableState` mirrors
`EDITABLE_STATES`), are re-checked on indirect paths (drag targets, stale
indices after awaits), and the server re-validates regardless. An `{error}`
response must never clobber the local mirror (settings, queue) — show the
toast, keep the previous state, re-sync from the store.

### P48 — Keep huge files off the JS heap

Files are handled by OS path handed to Python, not by reading content into
JS (`FileReader`), so multi-hundred-MB `.out` files never enter the renderer
heap. Drag-and-drop routes by extension to the right tab.

---

## 6. Process

### P50 — The version is single-sourced from `APP_VERSION`

Every displayed version derives from `APP_VERSION` (`orcamgr/paths.py`):
window title, About payload, top-bar badge, server API metadata. Never
hardcode a version in `web/` (the 0.4.1 badge drift is the incident that
made this a rule). A release bumps `APP_VERSION` plus the prose in
`CHANGELOG.md`/`README.md` — those two are the only accepted manual copies.

### P51 — Branch and commit discipline

`main` holds release commits only — merges from `dev`, subject
`x.x.x <one-line English summary>`, tagged `v{version}` — so `main`'s
first-parent log reads as release notes. Day-to-day work happens on `dev`
with `type: summary` subjects (`feat:`/`fix:`/`hotfix:`/`docs:`/`chore:`), a
blank line, then detailed bullets (which double as the CHANGELOG draft). No
`Co-Authored-By` or tool-attribution trailers, ever. Suggest a commit at
roughly 200–300 accumulated lines or a natural boundary.

### P52 — Docs sync in the same commit

If a change touches anything a document describes, that document is updated
in the **same commit** — `CHANGELOG.md` (any user-visible change),
`CLAUDE.md` (architecture/invariants/conventions), `README.md`
(commands/features), the `*_KR.md` procedure docs, and now `PRINCIPLES.md` /
`DESIGN.md` (when a principle is added, amended, or deliberately deviated
from). The pre-commit documentation check is mandatory every time, even when
the answer is "nothing affected".

### P53 — The constitution is living

Rules are codified at the moment they are learned, in the same commit as the
event that taught them: a violation incident produces the fix *and* the rule
(version drift → single-sourcing rule); accumulated domain knowledge is
recorded when acquired (the D3BJ/normalization conventions); missing
vocabulary is added when first needed (the `docs:`/`chore:` commit types).
This document inherits that practice.

### P54 — English canonical, Korean procedural

Canonical repo documents (README, CHANGELOG, CLAUDE.md, this file, code
comments, commit messages) are English. Step-by-step procedure documents
meant to be followed by hand (install guide, manual test checklists) are
Korean, marked with the `_KR` filename suffix. User data treats Korean as
first-class (calc names allow Unicode).

### P55 — Validation assets are credited

Externally contributed validation data is acknowledged in `CONTRIBUTORS.md`
("Data contributors"). The real-output corpus is a project asset; changes
that grow or reclassify it should say so.

### P56 — Tests pin the principle contracts

The automated suite (`tests/`, pytest + a plain-Node script) exists to keep
the invariants in this document true, not to chase coverage numbers. Its
rules:

- **Scope is the framework-free layers** (P11): `state/`, `core/`, `mlip/`,
  `config`, `procutil`, and the HTTP API via `fastapi.testclient`. The Qt
  bridge/window are thin adapters and are exercised manually — the logic
  under them is what the suite pins.
- **Tests cite the contract they pin** (a principle ID in the docstring) and
  are named for the invariant (`test_failed_calc_never_reruns`), so a red
  test tells you *which rule* broke.
- **A red test is a bug** — in the test or in the product. Never weaken a
  test to make wrong behavior pass; a product bug found by a test is fixed
  (or tracked in Appendix A with the test marked `xfail(reason=…)`).
- **Green on any machine**: no ORCA executable, no MACE env (the stdlib-only
  stub worker stands in), no network; real-corpus tests auto-skip when the
  corpus directory is absent (they are evidence *supplements* — P3's manual
  corpus validation remains the primary evidence for parser heuristics).
- **Isolated**: never write to the real `%APPDATA%` — path functions are
  monkeypatched to tmp dirs.
- Run the suite before any commit touching Python or the `SCFGraph` modules
  (`scf_graph.js` / `progress_panels.js`) (~3 s).

*(Adopted 2026-07-03 with the initial 234-test suite — which found and led to
the fix of three real boundary bugs on its first run.)*

---

### P57 — Parallelism is admission, never rewriting

Several calculations may run at once, and the limit is a **budget**, not a
lane count: each calculation declares what it will occupy (ORCA `%pal
nprocs`, CREST `-T`, the MLIP worker's thread cap) and the dispatcher
starts it only while those cores — and its estimated memory — still fit
inside the user's budget (`core/resources.py`). So "two 8-core jobs" and
"four 4-core jobs" are the same setting, expressed by the calculations
themselves. ORCAdesk never edits a calculation to make it fit: a raw
`.inp` owns its `%pal`, and rewriting it to hit a lane count would break
the verbatim rule (P26) precisely where the user was most explicit. The
cost is therefore *read* from what will actually execute — a raw input's
own text, not the hidden form field.

Three consequences the sequential engine got for free and the dispatcher
must now state outright:

* **Dependencies are deferred, not failed.** A REFERENCE calc whose parent
  may still become DONE is passed over and re-examined later; queue order
  no longer guarantees the parent ran first. "May still become DONE" is
  about what *this run* will do, not the parent's current state alone: a
  **CANCELLED** parent re-runs (P24), and a parent picked but not yet
  stamped RUNNING is mid-launch — reading either as final admits the
  dependent alongside its own parent, where geometry resolution FAILS it,
  and FAILED is locked. A parent that genuinely cannot succeed
  (FAILED/BLOCKED, missing, or itself) is *not* deferred — it must reach
  its existing, precise error (P28) rather than hang.
* **Nothing starves and nothing deadlocks.** A calculation larger than the
  whole budget runs alone once nothing else is in flight (logged, P2);
  when every remaining row waits on a reference that can never complete,
  the rows are stamped BLOCKED instead of spinning.
* **Memory is a first-class limit.** ORCA's `%maxcore` is *per core*, so
  a 6-core job at 2400 MB reserves 14.4 GB: a core-only budget would let
  two of them push a 32 GB machine into swap and make parallel slower than
  sequential. Cores are declared and exact; memory is an estimate, and is
  labelled as one.

Because their output interleaves in one log buffer, every line a job
produces carries its calculation's name (`LogLine.calc`) — the raw ORCA
tail has no name of its own, and without the tag neither the log nor the
convergence graphs could be separated per job.

---

## Appendix A — Known deviations

Tracked honestly (P2). Each entry names the principle it strains and its
disposition: **fix** (should be corrected), **accepted** (deliberate
tradeoff, kept), or **stale-doc** (the description, not the code, is wrong).

| # | Deviation | Strains | Disposition |
|---|-----------|---------|-------------|
| A1 | `Settings.save` wrote non-atomically (`write_text`, no tmp+replace, no try/except) while `session.json` got the full treatment | P32 | resolved (0.4.3-beta — same tmp + `os.replace`, best-effort) |
| A2 | File-loader slots signalled cancel/failure three different ways (`""`, `"{}"`, `{text,name,error}`); `load_xyz_*` couldn't distinguish user-cancel from OSError | P14, P16 | resolved (0.4.3-beta — unified `LoadResult` envelope with an explicit `cancelled` flag) |
| A3 | `autodetect_orca` looked like a getter but mutated `settings.orca_path` and saved | P14 | resolved (0.4.3-beta — returns an explicit `AutodetectResult` envelope; the mutation is documented at the slot and the UI re-pulls settings after it) |
| A4 | `get_free_energy_profile` mutated `Calculation.result` without the store lock (parse-on-miss); safe only via the DONE-frozen invariant, which was not documented at the call site | P37 | resolved (0.4.3-beta — the invariant is now stated in a comment at the write) |
| A5 | Queue mutations hold the store lock through the session-save file I/O (`_bump_and_save`) | P37 | accepted (small file; RLock reentrancy documented) — revisit if poll latency ever shows it |
| A6 | `_parse_tddft_states` used the *first* occurrence — the only exception to last-wins — and its docstring admitted it wasn't validated on a real TD-DFT `.out` | P27, P3 | resolved (0.4.3-beta — validated against 9 real TD-DFT outputs; now last-wins with a case-sensitive marker, the counterexample recorded in the docstring) |
| A7 | The keep-existing (skip) path called `parse_file` directly instead of `result_from_output` (a skipped MLIP calc would have been parsed by the ORCA parser) **and stamped DONE without `validate_result`/`terminated_normally`** — a bad `.out` could be resurrected as DONE (reproduced on 0.4.2: a FAILED raw calc became DONE via "Keep existing") | P19, P4, P25, P2 | resolved (0.4.3-beta — kind-dispatched parse, DONE gated behind `validate_result`, honest fallback is to run the calc) |
| A8 | Stale docstrings: `queue.py` said name-uniqueness was enforced "at the UI/bridge layer" (it's the store); kind lists in `queue.py`/`input_generator.py` omitted half the real kinds; `mlip/__init__.py` claimed the package held only `env.py`; the slot list atop `bridge.py` omitted six slots (`parse_out_path`, `reorder_calc`, `update_calc`, `get_free_energy_profile`, `check_overwrite_conflicts`, `get_connect_qr`) | P7 | resolved (0.4.3-beta) |
| A9 | `get_server_status` reported `available: true` from the mere existence of the controller object, without `is_available()`; an install without fastapi showed available | P2, P17 | resolved (0.4.3-beta) |
| A10 | CLAUDE.md self-contradicted on tests: "no automated test suite" vs "the automated tests use a stdlib-only stub" — no test files or CI exist in the repo | P52 | resolved — CLAUDE.md wording fixed alongside this document's adoption |
| A11 | `core/` error strings name desktop UI navigation ("Settings → MLIP environments") | P10 | accepted (they are user-facing sentences; revisit if core ever gets a second front-end) |
| A12 | `_as_xyz_file` duplicated in `mlip/runner.py` and `core/queue.py` (justified by a comment, but the copies can drift) | P19, P4 | accepted for now; lift to a shared util if it grows again |
| A13 | Escape gaps: the overwrite-conflict name list, the remove-confirm name, and `renderQueue`'s `ref → ${c.ref_name}` were interpolated unescaped (safe only via the name-validation regex) | P42 | resolved (0.4.3-beta) |
| A14 | Bridge uses in-memory `self.settings`; server endpoints re-load from disk per call — consistent only while every mutation saves immediately | P4 | accepted (documented here as a structural fragility; keep the save-immediately rule absolute) |
| A15 | History: one `docs:`-style commit landed directly on `main` (90cb1ca); one unprefixed dev commit (7e4c6f7); first release 0.1.0-beta untagged | P51 | accepted (historical; rules post-date them) |
| A16 | `charge`/`multiplicity` were coerced (`int()`) but not range-clamped in `calc_from_dict`, then f-string-interpolated into the `.inp` (`* xyz {charge} {multiplicity}`) — injection was blocked, absurd values were not | P34 | resolved (0.4.3-beta — clamped to ±100 / [1, 200]) |
| A17 | MLIP detach *terminates* the process by design (short jobs, no reattach machinery) — but `run_all`'s shared `OrcaDetached` handler then logged "[name] left running in the background", which was false for MLIP | P1, P2 | accepted (the kill); the misleading log resolved (0.4.3-beta — MLIP logs "stopped on shutdown") |
| A18 | The phone `/api/run` endpoint didn't pass `mlip_envs` to `make_engine_factory` and unconditionally required a valid ORCA path — an all-MLIP queue ran from the desktop but not from the phone (the bridge and server run paths had diverged) | P4, P12, P19 | resolved (0.4.3-beta — both entry points share `queue_needs_orca` and pass `mlip_envs`) |
| A19 | `QueueStore.regenerate_token` was dead code (no callers), and the store comment "generated fresh per server start" was wrong — the PIN lives for the whole app session | P7, P35 | resolved (0.4.3-beta — dead method deleted, comment states the app-session lifetime) |
| A20 | A Korean phrase survived in the `state/store.py` module docstring ("길 1" design) — canonical code comments are English | P54 | resolved (0.4.3-beta) |
| A21 | Code predated the amended P24 (FAILED-locked): `EDITABLE_STATES` still included `failed`, `run_all` re-ran FAILED calcs, `check_overwrite_conflicts` offered keep-existing for a failed `.out` (which the skip path then stamped DONE — A7), and the front-end `isEditableState` mirrored the old rule; BLOCKED sat outside `EDITABLE_STATES`, which would have stranded dependents after their failed parent's removal | P24 | resolved (0.4.3-beta — failure lock implemented end-to-end: store, engine, conflict check, UI affordances, BLOCKED editability; × removal stays available in every non-RUNNING state and remains queue-list-only) |
| A22 | If a keep-existing fallback run (the skip path discarding an invalid kept result and running fresh) fails *before* ORCA launches (e.g. geometry resolution), `_failure_reason` may read the stale on-disk `.out` and mask the real pre-launch cause | P28, P2 | resolved (0.4.3-beta — `_failure_reason` consults the `.out` only after this attempt actually launched or adopted ORCA; a pre-launch failure keeps the engine's own error, with regression tests) |
