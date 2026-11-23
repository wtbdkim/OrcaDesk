# ORCAdesk Design Principles

Normative visual and UX principles for ORCAdesk. Like
[`PRINCIPLES.md`](PRINCIPLES.md), every rule below was extracted from what the
UI already practices — several were forged by a recorded incident or a
reverted experiment — and is now binding. Deviations are justified in the
commit body and, if kept, recorded here (Appendix B).

Principles have stable IDs (`D1`, `D10`, …), allocated in blocks of ten per
section — gaps are reserved for future principles, not deletions.

The document has two parts. **Part I (§0–§7)** states the principles — the
*why*. **§8 and Part II (§9–§15)** are the prescriptive spec — the exact
scales, component recipes, state matrix, and copy templates. Consistency
rule: when building UI, copy the recipe; when the recipe is silent, derive
from the principles; never invent a value that is not on a scale here.
Where the spec legislates a canonical value that current code deviates
from, the deviation is tracked in Appendix B.

§8 and Part II were extracted from the classic front-end's `web/style.css`.
That front-end is removed (unreleased); the desktop UI is the notebook (§17),
whose exact values are its approved design's (`web/next/app.css`, §17.0), and
re-mirroring §8/Part II against it is open (B33). The principles in Part I
bind both the notebook and the phone PWA.

---

## 0. Design philosophy

### D1 — The chrome is achromatic; color belongs to the data

The UI is built on the shadcn/ui **zinc** palette with an achromatic primary
(`--primary` is just the bright foreground) — no brand color competes for
attention. Hue is reserved for meaning: calculation state, log levels,
convergence criteria, spectra. A scientific tool's chrome should recede.

### D2 — Honesty is a design property

What the screen claims must be true (PRINCIPLES P2, rendered visually):

- An indicator is green only when the capability was actually verified.
- Progress caps at 99% until the completion condition is really met (the
  optimizer tracker; the numerical-freq percent is floored so the final
  displacement never reads a premature 100%).
- Uncertain numbers look uncertain: the ETA line is opacity-reduced and
  italic, placed *below* the accurate signals ("ETA is the uncertain part:
  keep it visually subordinate to the accurate signals"), and is a coarse
  bucket, not a countdown. When no estimate survives the ensemble agreement
  gate, "estimating…" replaces the number; an estimate that survives with
  low confidence is still shown, but only ever as a coarse bucket.
- Unavailable features are locked and explained, not hidden and not
  pretend-enabled (D41).

### D3 — Scannable density

One 16px rhythm, one-line noun-form descriptions, no information that repeats
what another element already says. Density is raised by removing noise, not
by shrinking signal.

---

## 1. Color

### D10 — Tokens only; a theme is a token swap

Every color is consumed through the semantic custom properties defined on
`:root`; the light theme is `html[data-theme="light"]` redefining **tokens
only** — "every element reads these variables, so the whole UI flips with no
per-element overrides". JS applies a theme with one
`setAttribute("data-theme", …)`. JS-generated SVG uses `var(--…)` too (via
inline `style`, since presentation attributes don't resolve `var()`).
No hardcoded hex/rgba in components (known gaps: Appendix B).

### D11 — Dark zinc by default; warm light; no pure-black/white surfaces

Dark (shadcn zinc) is the default. The light theme is deliberately warm —
ivory page `#FFFFF0`, beige cards `#F5F5DC`, "not stark white" — with cards a
touch darker than the page so they read as raised panels. Pure `#000`/`#FFF`
surfaces are not used: the 0.3.4 pure-black/white graph surface was rolled
back in 0.4.1 to the page tint. Colors that clash with the warm palette get
swapped, not forced (the raw badge's purple → teal in light), and thin chart
lines get darkened light-theme variants for legibility.

### D12 — State-color semantics are fixed everywhere

The same five meanings carry the same hue on every surface — badges, log
lines, status-pill dots, buttons, charts:

| Meaning | Token | Note |
|---|---|---|
| success / ready / converged / goal | `--ok` (green) | the chart *goal line* is green |
| running / in progress / caution | `--warn` (amber) | **running is amber, not blue**; the live chart trace is amber |
| failure / error / imaginary mode / destructive | `--err` (red) | |
| neutral / pending / cancelled / blocked / filtered / raw ORCA text | grey (`--info`, `--orca`, `--muted-foreground`) | `blocked` styles like the other neutral states; `filtered` (left out by an edge's energy condition) is neutral too, never a failure |
| raw-input / MLIP special modes | `--raw` (purple; teal in light) | |

Value coloring is decided by meaning at render time (e.g. summary values:
`NOT converged` → err, `converged` → ok, imaginary count ≠ 0 → warn;
HOMO → ok, LUMO → warn).

### D13 — The tint formula

A colored *surface* is the semantic color at low alpha with the full-strength
color as text: status badges = 15% alpha background + colored text; error
surfaces (`.qerror`, `.freq-warn`) = 8% alpha + a 2px left border or 30%
alpha outline; toggle-on buttons reuse the done-badge accent (ok at 15%).
Tints are consumed through the `--*-tint` tokens (the §11.19 alpha ladder),
defined next to their semantic base colors and re-derived per theme — a
hardcoded tint is what forced the light-theme raw-badge retint hack, whose
tint base didn't even match its text token (B3, resolved in 0.4.3-beta).

### D14 — Chart series get their own tokens

The five optimization-criteria series are dedicated tokens (`--crit-*`),
tuned for the dark card and re-tuned (darkened) for light. Chart colors are
part of the theme system, not per-chart constants.

---

## 2. Typography

### D20 — Mono marks machine text

Two stacks only. `--font-mono` (JetBrains Mono → Cascadia Code → Consolas)
marks text that a machine produced or will consume verbatim: logs, raw
`.inp`/`.out`, numeric result values, data tables, file paths, ORCA keywords,
chart axes, queue meta lines. `--font-sans` is for human copy. In a key-value
grid the *label* is sans/muted and only the *value* is mono — the typeface
itself says "this is data". The `.mono` utility marks any element as machine
text.

### D21 — The type scale

Base 14px/1.5 (desktop). Field labels 12px/500 muted; card titles
13px/600/−0.01em; hints and descriptions 12px muted; badges 10–11px/600–700
(the top-bar version badge is a quieter meta label at 11px/500);
buttons 13px/500. Large numerals are exceptional and purposeful: the HUD
stage number (26px/700, `tabular-nums`). Don't invent sizes between these.

### D22 — Micro-labels: uppercase + wide tracking

Tiny structural labels (combo group headers, HUD captions, state badges) are
uppercase with letter-spacing (+0.03–0.22em; the HUD title sits at the top
of the range) — small type earns legibility
through tracking, and the treatment visually separates *chrome labels* from
*content*.

---

## 3. Space and shape

### D30 — One gutter: 16px everywhere

Box-to-window-edge gutter == box-to-box gap == 16px, and the top bar, tabs,
and cards all align to the same left/right gutter. Even computed layouts
honor it (the graph's viewBox height is calculated so the SVG lands flush on
the bottom 16px gutter). In-card section dividers use the same 16px rhythm.
(Known exception: the SCF graph panel runs a compact 14px rhythm — padding
and inner divider — B13. Mobile runs a denser 12px card gap — a documented
fork, D54.)

### D31 — Pill for status, rounded rectangle for action

Fully-round shapes (999px / 50%) are reserved for *status, chip, and
floating* things: status pills, state badges, dots, toasts, the floating
"↓ Latest" jump button. Primary actions and inputs are rounded rectangles
on the radius scale — `--radius-sm` 6px (buttons, inputs), `--radius` 8px
(cards), `--radius-lg` 12px. The same badge class renders identically
wherever it appears; only placement rules (e.g. `margin-left: auto`) may be
container-scoped (D50).

### D32 — Flat borders structure; shadows float

In-flow structure (cards, panels, inputs, tables) is delimited by 1px
`var(--border)` flat borders — no shadows. `box-shadow` means exactly one
thing: this element floats above the page (modal, toast, combo dropdown,
floating button) — plus the focus ring (3px `--ring-glow`). Two
drawing-technique uses are sanctioned and mean neither: the drag
drop-target's inset insertion line and the pulse-glow keyframes. The
`design_preview.html` hover-lift buttons and glow tab underline were
evaluated and **rejected**; keep it that way unless deliberately revisited.

---

## 4. State representation

### D40 — De-emphasis is opacity, not color surgery

Reduced states dim the original colors instead of replacing them, so the
treatment works in both themes: disabled controls opacity .5 +
`cursor: not-allowed`; locked card sections opacity .45 +
`pointer-events: none`; dragging item .5; the uncertain ETA line .72 +
italic. Three levels — .45 (locked), .5 (disabled), .72 (de-emphasized
info) — don't invent new ones.

### D41 — Locked until ready, with the reason in place

A feature whose precondition isn't met is **locked, not hidden**: the card
greys out, its inputs/buttons disable, and a one-line note says why
("Ready MACE environment required."). It unlocks automatically when the
polled status turns ready — and the action handler keeps its own guard
regardless, because a disabled UI is not a trust boundary (PRINCIPLES P44).
Discoverability is preserved; failure is prevented; the "why" is answered on
the spot.

**Scope: this governs a feature the user has *adopted*, not one they have
never set up** (amended 0.8.0-beta). "Locked with the reason" answers *why
can't I use this yet* — a real question for someone who registered an MLIP
environment that fails to import, or picked a WSL distro without CREST. It
answers nothing for someone who runs ORCA and has never wanted either
toolchain: for them a permanently greyed card, a dead mode button and a
"MLIP not set" pill are not guidance, they are chrome for a feature that
will never be used, and they are most of what makes the app feel larger than
the job. So an **unadopted optional backend is hidden** (D62) — its mode
button and its top-bar pill both — behind one signposted way in: a
"+ More backends…" chip on the build toggle that goes to Settings, whose
MLIP and CREST sections are always present. Adoption is judged from what the
user did (an environment is registered; a distro has CREST or was chosen),
never from the probe's verdict, so a *broken* setup still shows and still
locks with its reason. A backend also stays visible while the queue holds
one of its calculations — a row must never become uneditable because its
backend went quiet.

### D42 — Indicators reflect actual workability

Status pills have four states (ready / checking… / error / not set) mapped
consistently to text, dot color, and a hover tooltip that names what was
detected or *what is missing* — never a bare red dot. Green means "this will
actually work" *to the extent verifiable*: the MLIP pill import-probes
(PRINCIPLES P2); the ORCA pill can only check that the executable path
exists (a binary can't be import-probed) and is currently two-state with no
tooltip — new indicators should follow the MLIP pill's four-state standard.

### D43 — Progress tells the truth

Accurate, measured signals first (criteria met N/5, real per-step time);
the uncertain estimate below, coarse and subordinate (D2). 100% only on the
real completion condition. Known-total work (numerical-freq displacements)
may show a real time remaining; unknown-total work gets buckets; no honest
model → no ETA at all. Infinite animations (pulse, shimmer) mean exactly
one thing: *in progress right now*.

**A wait is reported where it was started, and never takes the screen away
to do it.** A control that begins work becomes the progress indicator — the
Visual row's *Plot* button becomes `Plotting… 1:23` in place — so the rest of
the surface stays reachable for however long it runs. Covering the window with
a modal that has nothing on it yet is the failure case this rules out: the ESP
map is minutes, and the modal used to open onto an empty stage for all of them.
A view opens when it has something to show. The exception is a wait started
*from inside* an already-open view (stepping to another orbital): there the
view is the context, and the progress belongs on it.

---

## 5. Components

### D50 — Component classes are ancestor-free

A reusable component class (`.qstate`, `.qerror`, `.rm`) is defined without
ancestor scoping so it renders identically wherever it's used — the scoping
trap (a badge styled only under `.queue-item`, rendering unstyled elsewhere)
was hit twice and fixed in 0.3.0. Placement-dependent properties (margins,
alignment) are the only thing allowed in container-scoped rules.

### D51 — The button vocabulary

`.btn` (secondary surface + border) is the default; `.btn-primary`
(achromatic inversion) for the main action; `.btn-ghost` for low-emphasis;
`.btn-danger` (red text, red tint on hover) for destructive; `.btn.on`
(ok-tinted) for active toggles; `.btn-sm` for size. Pick from
this vocabulary — don't style one-off buttons.

### D52 — Empty states answer "what's missing and where it comes from"

An empty region shows icon (border-colored) + title + a next-action hint
("No calculations queued / … from the Build tab"), with generous padding —
never a blank box, never just "empty".

### D53 — Motion is quiet

Interaction color transitions 0.12s; panel fade-in 0.15s (+3px translateY);
toast 0.2s; progress-bar width 0.3s. Nothing else animates on its own. The
one motion that is content rather than chrome — a normal mode moving on a
structure (§17.4q) — starts only when the reader asks for it, stops when they
say so, and opens held still when the system asks for reduced motion. No
entrance choreography, no hover lifts (D32).

### D54 — Mobile: same identity, touch density

The mobile PWA keeps the identical color tokens and semantic classes
(`.qstate`, `log-*`, `.kv`) so both form factors read as one product;
differences are ergonomic only — 15px base type, 12–13px input/button
padding, full-width buttons, bottom tab bar + FAB, safe-area insets, hidden
scrollbars. It is currently a copy-fork of the stylesheet, and it has
drifted (Appendix B); when touching mobile styles, restore parity with the
desktop tokens rather than extending the fork.

---

## 6. Interaction

### D60 — Never yank the user

Automatic updates must not steal the user's context: the log auto-follows
only when already near the bottom (otherwise a floating "↓ Latest" appears);
auto-fill never clobbers a deliberately typed value; re-clicking the active
mode keeps form state; a failed save never overwrites the on-screen settings;
combo focus doesn't pre-filter by the current value. The one sanctioned
navigation: jumping to the *result of the user's own action* (add-to-queue →
Queue tab; dropped `.out` → Results; an opened `.xyz` → Results › Visual, which
is the only mode that has anything to say about it).

### D61 — Destructive actions confirm, and offer the alternative

Irreversible actions (cancel a running job, clear/remove, form→raw
conversion, overwrite results) use the themed modal — never the system
dialog (desktop; the mobile PWA still uses a native `confirm()` for remove —
B6). The body states (1) what will actually happen, (2) what is lost, and
(3) the less-destructive alternative when one exists ("To finish the current
job first, use Stop after current instead."); the destructive button is
danger-styled. Real choices get real options: the overwrite conflict is
three-way (Cancel / Keep existing / Overwrite), not yes/no. Escape and
backdrop-click dismiss — and dismissal is never interpreted as the
destructive choice. Confirm only when something would be lost.

### D62 — Progressive disclosure

Complexity is revealed in layers: three mutually-exclusive build backends
(DFT / MLIP / CREST) — of which MLIP and CREST appear at all only once the
user has set them up (D41 scope clause), so a plain ORCA install shows one —
with a Beginner/Expert sub-toggle revealed only inside
DFT (the sub-choice is one-way for content: the form converts to a raw `.inp`,
never back), form fields driven per calc-kind by
`KIND_DEFS` flags, conditional fields appearing only after their premise
(solvent model → solvent; InitHess=read → filename), Results gated per kind
with **Show all** as the override. Hiding is filtering, never data removal —
gating stays on the front-end so revealing everything is a re-render, not a
re-fetch (PRINCIPLES P45). The chosen disclosure level is remembered. The
Results tab splits the same way, but by *question* rather than by depth —
**Output** (what the parser read) and **Visual** (what can be drawn from it) —
over one shared result: the picker and the file button sit above the toggle, so
changing mode never changes what is being looked at.

### D63 — Escape hatches, labeled

Option lists are aids, not walls: combos accept free text and *say so*
("searchable — or your own value"; "No match — X will be used as-is"), and
anything the form can't express can drop to raw `.inp` editing — with
insertable snippet blocks so the escape hatch has a floor. When the user
leaves the paved path, tell them the contract (their input is used verbatim).

### D64 — Affordances derive from state

Whether an item can be edited, removed, or dragged comes from the backend's
state rules mirrored in the UI (`isEditableState` ⇄ `EDITABLE_STATES`):
frozen items lose their controls at render time (a placeholder keeps
alignment), drag targets re-check editability, and the server re-validates
anyway. Buttons reflect run state (`Run`/`Cancel`/`Stop after current`
enablement). The UI never shows a control that the backend would reject.

### D65 — Everything lands in the Log

Every event the user might care about — action results, warnings, errors —
becomes a leveled, ordered log line (info/ok/warn/err). Urgent failures go
out on *both* channels: toast (immediate, evaporates in 2.2s) + log
(persistent record). Front-end console output and uncaught JS errors are
captured into the same log as `[web]` lines (rate-limited per identical
message) so a deployed build is diagnosable without devtools. Silent catches
are sanctioned only for (a) transient failures of a self-healing loop (the
1s poll) and (b) explicit fallback paths whose behavior is stated in a
comment ("if the check fails, fall through and run normally").

---

## 7. Copy

### D70 — Noun-form, no filler

UI copy — card descriptions, hints, radio explanations, status/toast/log
messages, graph labels — is concise noun-form phrasing: "One ORCA run per
calculation; unique name (used as its folder)." Not full imperative
sentences. Redundant restating is deleted outright (a "Completed." note next
to a `done` badge; "Settings for this calculation."). If a description is
self-evident, the correct copy is none. Validation errors that demand an
action may remain short imperatives ("Load an .xyz file first.") — resolved
as a deliberate exception.

### D71 — Domain-native language

Write for a computational chemist. Units always accompany numbers (Å, cm⁻¹,
eV, kcal/mol vs kJ/mol, K/atm, "MB / core"); ORCA-native names appear
verbatim (`TightSCF`, `%eprnmr`, `wB97X-D4`, `.hess`); and where a value
could be misused, one line carries the scientific implication ("this is a
saddle point, not a minimum. Re-optimize … before trusting thermochemistry";
"electronic, not free energies"; "Absolute shieldings — reference
subtraction (e.g. TMS) for chemical shifts"). The copy shares responsibility
for correct interpretation.

### D72 — Case and glyph conventions

Sentence case for buttons ("Run queue", "Stop after current"); lowercase for
micro-actions inside rows ("edit", ".inp"). State-badge *source strings* are
lowercase (pending/running/done/…) — on screen they render uppercase via the
badge's CSS `text-transform` (D22): write the copy lowercase and let the
chrome do the shouting. Ellipsis `…` for ongoing states ("checking…"), middot
`·` for meta separators ("47% · step 12"), em-dash `—` for asides, arrows
for direction ("Add to queue →", "↓ Latest"), `⚠`/`✓` for warn/success
inline marks.

### D73 — The UI speaks English

All user-facing strings in `web/`/`web_mobile/` are English (repo procedure
docs may be Korean — PRINCIPLES P54 — and user data like calc names may be
Korean). One language per surface; no mixed-language copy.

---

## 8. Token reference

Source of truth: was `web/style.css` `:root` and `html[data-theme="light"]`,
removed with the classic front-end; the desktop's tokens now live in
`web/next/app.css` (the approved design's stylesheet, §17.0) and this table is
not yet re-mirrored against it (B33). When they disagree, the CSS wins and
this file must be updated in the same commit (PRINCIPLES P52). Values
are the desktop's; mobile drift is tracked in B6.

### Core palette

| Token | Dark (default) | Light (warm) |
|---|---|---|
| `--background` | `#09090b` | `#fffff0` (ivory) |
| `--foreground` | `#fafafa` | `#09090b` |
| `--card` / `--popover` | `#18181b` | `#f5f5dc` (beige) / `#fffff0` |
| `--border` / `--input` | `#27272a` | `#dad6b8` |
| `--input-bg` | `#0e0e10` | `#fffff0` |
| `--code-bg` (terminal surface: log, raw editor) | `#050506` | `#f2efd8` |
| `--muted-foreground` | `#a1a1aa` | `#6f6d5c` |
| `--accent` / `--secondary` | `#27272a` | `#ece8ce` |
| `--secondary-hover` | `#3f3f46` | `#e2dec0` |
| `--primary` / `--primary-foreground` | `#fafafa` / `#18181b` | `#18181b` / `#fafafa` |
| `--primary-hover` | `#e4e4e7` | `#27272a` |
| `--ring` / `--ring-glow` | `#d4d4d8` / `rgba(212,212,216,.18)` | `#18181b` / `rgba(24,24,27,.14)` |

### Semantic colors

| Token | Dark | Light | Meaning |
|---|---|---|---|
| `--ok` | `#4ade80` | `#15803d` | success / ready / converged |
| `--warn` | `#fbbf24` | `#b45309` | running / caution |
| `--err` | `#f87171` | `#dc2626` | failure / imaginary / destructive |
| `--info` | `#a1a1aa` | `#52525b` | neutral log info |
| `--orca` | `#71717a` | `#52525b` | raw ORCA output lines |
| `--raw` | `#c084fc` | `#0f766e` (teal) | raw/MLIP special modes |
| `--lost` | `#fb923c` | `#ea580c` | a job on a remote device that stopped answering ("Disconnected") — neither running (amber) nor failed (red); defined in the named *remote devices* block at the bottom of `app.css` |

Each semantic color also carries **tint tokens** on the §11.19 alpha ladder
(`--ok-tint`/`--ok-tint-strong`, `--warn-tint`/`--warn-tint-bg`,
`--err-tint`/`--err-tint-bg`/`--err-tint-hover`/`--err-outline`,
`--raw-tint`, `--lost-tint`), redefined per theme next to their base colors — components
consume the tint token, never a hand-mixed rgba (D13).

### Chart criterion series (`--crit-*`)

| Series | Dark | Light |
|---|---|---|
| ΔE | `#4cc9f0` | `#0e7490` |
| RMS gradient | `#52b788` | `#15803d` |
| MAX gradient | `#ffd166` | `#b45309` |
| RMS step | `#c08eff` | `#7c3aed` |
| MAX step | `#ff8fa3` | `#be123c` |

### Shape, type, motion, timing

- Radius: `--radius-sm` 6px (buttons/inputs) · `--radius` 8px (cards) ·
  `--radius-lg` 12px · 999px (pills/toasts) · 50% (dots).
  Mobile: 10px / 7px (fork drift — Appendix B).
- Fonts: `--font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI",
  sans-serif`; `--font-mono: "JetBrains Mono", "Cascadia Code", Consolas,
  ui-monospace, monospace`. The notebook front-end names the reference's own
  two faces first (`Inter`, `JetBrains Mono`) and **ships them** rather than
  naming and hoping — see §17.0c.
- Type: base 14px/1.5 (mobile 15px); labels 12px/500; card titles
  13px/600/−0.01em; badges 10–11px uppercase; HUD numeral 26px/700
  tabular-nums.
- Spacing: 16px gutter/gap everywhere (desktop); card padding 18px;
  field gap 12px.
- Motion: hover/active 0.12s; panel fade 0.15s; toast 0.2s; progress bar
  0.3s; pulse/shimmer 1.2–1.4s (in-progress only).
- Timing constants: poll 1000ms; MLIP status re-poll 800ms; toast 2200ms;
  "Saved." 2000ms; log DOM cap 2000 lines; backend log buffer trim
  5000 → 4000 lines + up to 500 retained older `[web]` lines (so ORCA's
  stdout flood can't evict the console captures); `[web]` console
  rate-limit 5s/signature; log auto-follow threshold 40px.
- ETA buckets: under a minute (<45s) / a few minutes (<8m) / tens of minutes
  (<50m) / a few hours (<5h) / many hours (<24h) / a day or more.
- **Isosurface colors** (`--orb-pos` `#4f86f7` · `--orb-neg` `#f2555a` ·
  `--orb-dens` `#5eead4`) are the one token group defined **once, outside every
  theme block**, and the one read from JS rather than applied as CSS —
  `web/next/mvwin.js` hands them to 3Dmol as WebGL material colors. The blue/red
  phase pair is a convention of the chemistry literature, not a UI accent: an
  orbital whose lobes changed color with the app theme would be a figure that
  means something different in each. Adding a surface color means adding it
  here, not a literal in the viewer.
- **ESP map ramp** (`--esp-neg` `#f2555a` · `--esp-mid` `#f5f5f5` · `--esp-pos`
  `#4f86f7`) joins that group under the same rule and for the same reason: red =
  negative potential, white = neutral, blue = positive is how every ESP figure
  is read. The viewer hands the three to 3Dmol as a `Gradient.CustomLinear` and
  the `.mvramp` swatch is a CSS gradient over the same tokens, so the
  key and the surface cannot drift apart.

---

# Part II — Prescriptive spec

Part I says *why*; this part says *exactly what*. When building or changing
UI: **copy the recipe**. When the recipe is silent, derive from Part I. Never
invent a value (a spacing step, a font size, a tint alpha, a shadow) that is
not on a scale in this part — if a new value seems needed, amend the scale
here in the same commit. Where the current code deviates from a recipe, the
deviation is listed in Appendix B; recipes state the **canonical** value.

## 9. Layout: spacing, surfaces, sizes

### 9.1 The spacing scale

All margins/paddings/gaps come from this closed scale. Pick by role, not by
eye:

| Step | Role |
|---|---|
| 2px | hairline nudges (first-line alignment of radio input, detail `margin-top`) |
| 4px | micro gaps: tab gap, card-title → desc, error chip inside a row |
| 6px | label → input; icon/text gap inside a control (`.btn`, `.qname`, pill); kv row gap; repeated compact rows (`.basis-row`, `.mlip-env-list`) |
| 8px | control vertical padding (`.btn`, inputs); gaps between sibling controls (`.btn-group`, `.modal-actions`, grid gaps); repeated list rows (`.queue-item margin-bottom`); `.hint margin-top` |
| 10px | tight group gaps (`.brand`, `.radio-row`); input horizontal padding; inset-surface padding |
| 12px | form rhythm: `.field margin-bottom`, `.field-row gap`; list-row vertical padding; code-surface padding; topbar vertical padding |
| 14px | compact-panel rhythm: `.scf-panel` padding, `.graph-divider` margin, desc → content (`.card-desc margin-bottom`); list-row horizontal padding; button horizontal padding |
| 16px | **the layout unit**: page gutter, card gap, `.divider` margin, kv column gap |
| 18px | card padding |
| 20px | modal padding |
| 24px | toast bottom offset |
| 40px / 56px | full empty-state padding (56px vertical, 40px horizontal) |

### 9.2 The surface scale

Padding of a bordered surface is decided by its nesting/elevation, not
per-component:

| Surface | Padding | Radius | Background |
|---|---|---|---|
| Modal (floats) | 20px | `--radius-lg` | `--card` |
| Card (page-level) | 18px | `--radius` | `--card` |
| Compact panel (nested in a tab, dense) | 14px | `--radius` | `--card` |
| Code surface (log, raw editor, inp view) | 12px | `--radius-sm` | `--code-bg` |
| Inset media surface (`.graph-plot`) | 10px | `--radius-sm` | `--background` (page tint — D11) |

### 9.3 Size constraints (canonical maxima/minima)

Log box `height: calc(100vh − 220px)`, `min-height 160px` · combo list
`max-height 280px` · Results table wrapper `max-height 280px` (nested
sub-table 240px) · raw editor `min-height 320px` · `.inp-view`
`max-height 60vh` · spectrum SVG height 220px · SCF plot inner height
min 220 · inline 3D stage (`.struct-gl`) height 220px · viewer-panel list
column 220px. Wide content always scrolls inside its own wrapper
(`overflow: auto`), never the page sideways.

## 10. Typography roles

Every piece of text is one of these roles. No sizes between the steps
(9 / 10 / 11 / 12 / 13 / 14 / 15 / 26 px):

| Role | Spec |
|---|---|
| Body / base | 14px/400 `--font-sans`, lh 1.5, `--foreground` |
| Brand title | 15px/600, ls −0.01em |
| Modal title | 15px/600 |
| Empty-state title | 14px/600, `--foreground` |
| Card title | 13px/600, ls −0.01em |
| Button | 13px/500 |
| Input / select / body-small (checkbox, radio, combo item, kv, `.qname` at 600) | 13px |
| Field label | 12px/500, `--muted-foreground` |
| Descriptions (card-desc, hint, brand sub), log line (mono), table text (mono), small button, segmented toggle (600), status pill, env detail | 12px |
| State badge (600, uppercase, +.03em), version badge (500, meta label), truncated paths (mono), progress meta/ETA (mono) | 11px |
| Type tag (600), combo group header (700, uppercase, +.04em), chart axis titles, static-chart tick labels | 10px |
| Live-graph tick labels (mono, `.scf-axis`) | 9px |
| Pipeline-stepper title (700, uppercase, +.12em), stage label (13px), stage detail (11px muted) | 13px |

Micro-label tracking scale: +.02em (version badge) / +.03em (badges) /
+.04em (group headers) / +.12em (pipeline-stepper title). Machine text is always
`--font-mono` (D20); `.mono` marks it on any element. Icon glyphs rendered
as text (`≡` 16px, `×` 14px, `☽/☀` 15px) size to their control and are
exempt from the text scale.

## 11. Component recipes

Anatomy values are exact. "Canonical" marks a legislated value where current
code varies (deviation tracked in Appendix B).

### 11.1 Card

`background --card; border 1px solid --border; radius --radius; padding
18px; margin-bottom 16px`. Title: card-title role, `margin-bottom 4px`.
Description: `.card-desc`, 12px muted, `margin-bottom 14px`, one line,
noun-form (§14). Header rows with a right-aligned action use
`display:flex; justify-content:space-between; margin-bottom 8px`.

### 11.2 Buttons

Base `.btn`: `13px/500; padding 8px 14px; radius --radius-sm; border 1px
solid --border; background --secondary; color --secondary-foreground;
inline-flex, gap 6px; transition background/opacity/border-color .12s`.
Hover `--secondary-hover`. Disabled `opacity .5; cursor not-allowed`.

| Variant | Delta | Use for |
|---|---|---|
| `.btn-primary` | bg/border `--primary`, text `--primary-foreground`; hover `--primary-hover` | **the** main action — at most one per card/view (Add to queue →, Run queue, Save settings, modal confirm) |
| `.btn-ghost` | transparent bg; hover `--accent` | row/inline actions (edit, ×, Check, snippets), usually with `.btn-sm` |
| `.btn-danger` | text `--err`; hover bg err-tint 10%, border `--err` | destructive triggers and destructive modal confirms |
| `.btn.on` | ok-tint 15% bg, `--ok` border+text; hover 22% | active state of a toggle button (Show all) |
| `.btn-sm` | `padding 5px 10px; 12px` | any button inside a card body |
| `.rm` | transparent, 1px border, `--err` text, radius-sm; hover err-tint 10% | the × remove button in compact grid rows |

Buttons never carry shadows, uppercase, or letter-spacing. Do not invent new
variants.

### 11.3 Fields and inputs

`.field` stacks label + control, `margin-bottom 12px`; `.field-row` lays
fields side by side (`flex; gap 12px`; children `flex:1`, fixed-width
exceptions use `flex:0 0 <px>`). Label: 12px/500 muted, `margin-bottom 6px`.
Input/select: `13px; padding 8px 10px; background --input-bg; border 1px
solid --input; radius --radius-sm`. Focus (all editable fields incl. raw
editor): `border-color --ring; box-shadow 0 0 0 3px --ring-glow;
outline none`. Machine-value inputs (paths, elements, keywords) add `.mono`.
Compact grid rows (`.basis-row`: `90px 1fr 1fr 34px; gap 8px`) shrink input
padding to `6px 8px`.

### 11.4 Combo (searchable select)

Input is a normal `.mono` input with placeholder
`searchable — or your own value`. List: absolute below, `margin-top 4px;
max-height 280px; background --popover; border --border; radius --radius-sm;
shadow 0 8px 24px rgba(0,0,0,.4); z-index 50`. Group header: sticky, 10px/700
uppercase +.04em muted. Item: `padding 7px 10px; 13px mono`; hover/active
`--accent`. No match: italic 12px muted `No match — "X" will be used as-is`
(the escape-hatch contract, D63). Keyboard: arrows/Enter/Escape/Tab;
selection on `mousedown`; close 120ms after blur.

### 11.5 Tabs and segmented toggles

Main tabs: `13px/500; padding 9px 16px; radius top-only --radius-sm;
border-bottom 2px transparent`; hover text `--foreground` + bg `--accent`;
active: text `--foreground` + 2px `--foreground` underline. Panels fade in
.15s (+3px rise).

Segmented toggle: `12px/600; padding 6px 12px; radius
--radius-sm; border 1px --border; color --muted-foreground`; active:
`background --accent; color --foreground`. Background is `--card` at page
level, `--background` when nested inside a panel.

Placed **on a card** rather than at page level (the Results tab's
Output/Visual), the same toggle takes `.on-card`, which swaps the inactive
chip's background to `--background`: `--card` on `--card` is a chip with only a
border. A modifier, never an ancestor rule (D50).

Sub-level segmented toggle (a secondary choice nested in the same row as its
parent toggle — the Build tab's DFT Beginner/Expert): one step smaller,
`11.5px; padding 5px 10px` (`.bsub button`), separated from the parent group
by a `1px × 20px --border` vertical rule (`.bmode-sep`), and shown only while
its parent segment is active. Same size logic as the type tag vs the state
badge (§11.7): the reduction marks it as subordinate, not a sibling.

### 11.6 Status pill and dots

Pill: `12px; padding 5px 10px; radius 999px; border 1px --border; background
--card; color --muted-foreground; gap 6px`. Dot diameters by context:
**7px** topbar pill · **8px** list row (`.mlip-dot`) · **10px** HUD phase
dot (1.5px outline style). Dot colors: unset/neutral `--muted-foreground`,
ready `--ok`, failed probe `--err`. The `.status-pill .dot` base rule
defaults **neutral**; ok/err come only from state classes — so a new pill
can never show a bare red dot before its state lands (D42).

### 11.7 State badge and type tag

Badge `.qstate`: `11px/600; padding 3px 9px; radius 999px; uppercase;
+.03em`. Colors per §12. In a queue row, the state badge (direct child)
takes `margin-left:auto`. Type tag (raw/MLIP, inside `.qname`): same class,
`10px; padding 1px 7px` — reads as a tag, not a second status.

**Verdict badges** (`.qstate.match` / `.qstate.mismatch`, Build-tab NEB
endpoints) share the badge anatomy and the done/failed colour pair, but report
a *comparison result*, not a run state — §12 keeps them on their own row so
the two vocabularies never merge (a "done" endpoint pair would mean nothing).

### 11.8 Error chip and warning panel

Three chips; the first two are `--err` text on an 8% err-tint:

- **Inline error chip** `.qerror` (row-level messages, NEB mismatch):
  `12px; padding 5px 8px; border-left 2px solid --err; radius 4px;
  lh 1.45`. Prefix `⚠ `.
- **Warning panel** `.freq-warn` (section-level, Results):
  `12px; padding 8px 10px; border 1px solid --err-outline; radius
  --radius-sm; lh 1.5; margin-top 8px`.
- **Inline warning chip** `.qwarn` — the `.qerror` anatomy exactly, in warn
  tokens (`--warn` on `--warn-tint-bg`, `border-left 2px solid --warn`). For a
  finding that is *worth a look but not wrong*: a two-fragment complex, a
  units suspicion. The two chips exist as a pair so a findings list (Build-tab
  structure screening) says which is which without a legend — an error chip
  alone would either overstate the warnings or hide them.

A findings list stacks chips in a `.struct-findings` column at the 4px step.

### 11.9 List row (canonical)

The queue row is the canonical list-row recipe: `flex; gap 12px; padding
12px 14px; border 1px --border; radius --radius-sm; background --card;
margin-bottom 8px`. Name block 13px/600 (+ tag), meta line 12px mono muted
(` · ` separators), right side = state badge then `.btn-sm .btn-ghost`
actions. Frozen rows keep an invisible drag-handle placeholder so columns
align (D64). Drag: `.dragging opacity .5`; `.drop-target` ring border +
inset bottom insertion line (`box-shadow 0 -2px 0 --ring inset`).
Drag arms only from the ≡ handle (`draggable` is set on handle mousedown and
cleared on release/dragend): a statically-draggable row swallows native text
selection, and row text (names, meta) must stay selectable/copyable.

### 11.10 Modal

Overlay `rgba(0,0,0,.6)`, **z-index 120 — above every other surface**,
including the viewer panels at 110 (§11.21): a confirm can be raised from
inside one (discarding structure edits), and a confirmation the user cannot see
is worse than no confirmation at all. Box: `--card; border --border; radius
--radius-lg; padding 20px; max-width 460px; width
calc(100% − 40px); shadow 0 16px 48px rgba(0,0,0,.5)`. Title 15px/600,
`margin-bottom 10px`. Body 13px muted, lh 1.6, `margin-bottom 18px`; bold
the destructive fact; calc names in a `--foreground` mono span. Actions
right-aligned, `gap 8px`, **Cancel left, confirm right**; confirm is
`.btn-primary`, or `.btn-danger` when destructive; the cancel label may be a
state-preserving phrase (`Keep running`). Name *lists* use the `.names`
span; a single inline name may be `<b>`. Escape/backdrop dismiss = null
(never the destructive choice). No entrance animation.

### 11.11 Toast

Singleton pill fixed at `bottom 24px`, centered: `13px; padding 10px 16px;
radius 999px; --card + --border; shadow 0 4px 16px rgba(0,0,0,.4)`; fades
.2s, auto-hides after 2200ms; `pointer-events none`. One sentence (§14) —
never the only record of an error (D65).

### 11.12 Log box

`--code-bg; 12px mono; padding 12px; radius --radius-sm; pre-wrap`. Line
classes `log-info/ok/warn/err/orca` per §12. Auto-follow only within 40px of
the bottom; otherwise the floating `↓ Latest` pill (`padding 5px 11px; 12px;
--accent bg; radius 999px; shadow 0 2px 10px rgba(0,0,0,.35)`) appears
bottom-right (offset 14px). DOM capped at 2000 lines.

**Restored history** (after a reattach): a job already running
when the app opened has its earlier output prepended to the log from the
`.out` — muted (`.log-restored`, `opacity .62`, so ORCA's own level colours
still read) and fenced by two `.log-mark` rules
(`--muted-foreground; 600; padding 6px 0 2px`):
`── restored history · NAME.out · last 500 lines ──` above,
`── live output continues below ──` under it. Fencing is the whole point —
unlabelled history reads as live output, and a stalled run and a restored one
would look identical. The block carries `data-calc`, so the job filter covers
it like any other line, and it sits at the top of the box, which is where the
line cap trims first: history is the first thing dropped as the live stream
fills the box again.

**Job filter** (`.log-jobs`, in the mode-toggle row): one button per job plus
*All*, at `11.5px mono; padding 5px 10px` — a step below the Raw/Graph buttons,
so the mode choice stays the primary level (the same relationship as the Build
tab's `.bsub`), behind a 1px `--border` rule. Wraps rather than scrolls. Hidden
until a **second** calculation has produced output — a single-calculation run
must look exactly as it always did, and a strip with one job to choose between
offers nothing. (It is keyed on how many calculations have EMITTED output, not
on whether they overlap, so a sequential run of several also grows the strip
once the second one starts. That is intended: by then the log genuinely holds
two jobs' lines and separating them is worth a row of buttons.) Hidden outside
**Raw** mode: it filters log
lines, the graph panel has its own job picker, and two identical strips where
only one does anything in the current mode is a trap. Filtering hides every line not tagged with the chosen
calculation, engine-level lines included (they are not that job's output).

> Buttons, not a `<select>`: switching between running jobs is the frequent
> action, and a menu costs two clicks and hides the job list until opened. The
> job name lives in `data-job` with one delegated handler — a calculation name
> may legally contain a quote, which would break out of an inline `onclick`.

**Run occupancy** (`.queue-resources`, above the queue list): `flex; gap 8px;
margin-bottom 8px; 11.5px mono; --muted-foreground`, the job count in
`--foreground` 600 and `/` separators at 0.45 opacity. Shown only while a
parallel run actually holds cores (`jobs > 0 && max_jobs > 1`); a one-at-a-time
run needs no budget readout, so it stays hidden.
The notebook's rail draws the same figures as meters labelled **Cores
reserved** and **Memory reserved**, each with a tooltip saying what it is: what
the run's budget sets aside for the jobs it admitted, never what they use
(cores are declared and exact; memory is the estimate of `core/resources.py`,
far above what a CREST search takes, whose real use under WSL sits inside the
WSL VM).

> Any component that sets its own `display` **must** pair it with a
> `[hidden] { display: none; }` rule (`.queue-resources[hidden]`,
> `.log-to-bottom[hidden]`). An author declaration beats the UA `[hidden]`
> rule at equal specificity, so `el.hidden = true` alone leaves it painted —
> and a JS check of `el.hidden` still reports true, so the bug survives
> testing unless the assertion reads `getComputedStyle(...).display`.

### 11.13 KV grid and data table

KV: `grid 180px 1fr; gap 6px 16px; 13px`; key sans muted, value mono, value
may carry `.ok/.warn/.err`. Table `table.data`: `12px mono; cells padding
6px 10px; border-bottom 1px --border`; numeric columns right-aligned, first
column left; header muted/500; no row hover. Tables live in a
`max-height 280px; overflow:auto` wrapper. Emphasis inside tables: inline
`color: var(--ok|--warn)` + weight 600 (HOMO/LUMO pattern).

### 11.14 Empty states (three tiers)

1. **Primary panel** (queue): centered block, `padding 56px 40px` — 40×40
   outline icon in `--border` color, title 14px/600 `--foreground`, sub
   12px muted. Copy: title `No {things} {verbed}` (no period), sub says
   where they come from (period).
2. **Sub-region** (list, dropdown, side panel): one `.hint` line —
   `No {thing} … .` or a parenthesized lowercase option
   `(no calculations in queue yet)`.
3. **Live canvas** (charts): lowercase + ellipsis, muted, 11px —
   `waiting for SCF data…`.

### 11.15 Progress and HUD

Progress bar: track `--accent`, `height 8px, radius 4px`; fill `--ok`,
width transition .3s. Label 13px/600 (`Optimization 62% · step 12`); meta
line 11px mono muted (accurate signals, omitted when there are none);
ETA line below at `opacity .72` italic (D2). Staged
pipeline (`.vstep`): a **vertical stepper** that fills the Graph panel. Header
= mono title (17px/700) + a state pill (`▷ RUNNING`/`✓ DONE` in `--ok` tint,
`■ STOPPED` in `--err` tint); a meta line below it, 12.5px mono muted, joins
`method · cores · elapsed` (the last a live clock while running). Then one row
per stage — everything mono, one typeface throughout: a 34px dot rail on the
left (2px `--border` connector, turning `--ok` once a node is reached), the
stage label (15px/600) with its **key result** beneath (12.5px mono muted —
e.g. `1,204 structures collected`; frozen at the value the stage held when it
ended, live for the current one), and the stage's **wall time** right-aligned
(mono, tabular). Done row = 16px `--ok` dot; current = `--ok` dot + `--ok-tint`
ring + 1.4s pulse with an `--ok` label; pending = hollow `opacity .75`; stopped
current = `--err` dot + label (no pulse). A **skipped** stage — a conditional
phase the pipeline jumped over (no entry stamp of its own but a later stage has
one, e.g. CREST's Genetic crossing when there's nothing to cross) — keeps the
rail `--ok` through it but shows a hollow dashed muted dot and an italic
`skipped` result, so it never reads as a misleading instant "done". Rows are
`flex:1` between `46–96px`, so **the rail follows the window height**. An
optional **footer strip** (`.vstep-foot`, 12.5px mono muted, tabular numerals,
`--border` top rule) may sit under the rows for a pipeline-wide status that
belongs to no single stage — CREST uses it once finished for the ensemble
statistics + runtime split. Live mid-run state stays on the current row's
key-result line — a value belongs next to the progress it describes, not in
a detached strip. When the panel is too short to fit the rows
(`< head + foot + n·46px`) the renderer falls back to `.stepc` — the retired HUD
frame minus its hazard stripes: centered title, `STEP k/n` (or `DONE`/
`STOPPED`) left, the current stage's label + key result center, a dot chain
right, meta line below. Reuse this exact stepper for any staged pipeline
display (CREST / analytical freq / TD-DFT); per-stage times come from the
tracker's stage-entry stamps and are suppressed on a disk-rebuilt tracker
(`noTimes`, replayed in one burst).

**Graph views** (`.graph-subtoggle`, above the graph body): one button per
view the job actually has, in pipeline order: `Optimization`, then the phase
chain it ran (`Frequencies` / `TD-DFT` / `Conformers`), then `Current SCF`
last — that one is not a stage but a drill-down into whichever single SCF is
solving right now, so it sits after the pipeline it belongs to. A stepper still fills the panel
when it is the selected view — a phase chain has no meaningful convergence
curve *below* it — but it is a **view, not a takeover**: a two-stage run
(opt_freq, ts_opt_freq) has a real optimization curve and a real chain, and
hiding the curve the moment the Hessian starts loses half the run. Default is
the live edge of the pipeline (the chain if there is one, else geometry for an
opt, else SCF); an explicit pick holds until the job changes, falling back on
its own if the view disappears (a pre-step Hessian's chain is cleared by the
next optimization cycle). One view = no strip.

**Graph job picker** (`.graph-jobpick`, above the panel): `flex; gap 8px;
margin-bottom 8px`, a muted `Showing` label plus a `.graph-subtoggle` strip of
mono buttons — one per job, matching the view toggle directly below it.
Rendered only when two or more calculations have **graph
data**: a calc that merely logged a status line has a tracker bundle but nothing
to draw, and offering it would open an empty graph. With one job the panel is
byte-for-byte what it was. The panel shows one job at a time — the explicit
pick, else the newest running job with a curve.

### 11.16 Charts (SVG)

Colors only via CSS classes or inline `style="…: var(--token)"` (SVG
presentation attributes don't resolve `var()`). Text scale: live-graph
ticks 9px mono (`.scf-axis`) · static Results-chart ticks 10px mono ·
axis titles/captions 10px (`.scf-axis-title`, weight 600) · emphasized
data labels 11px/600. Grid `--border` at 0.5 width; goal lines
`--ok` dashed `4 3`; live trace `--warn` 1.8; series use `--crit-*`;
baselines/zero-lines `--border` dashed `3 3`. Never hardcode a hex in chart
code. Reference-line labels sit left of the curves.

### 11.17 Locking and de-emphasis

Locked region: `opacity .45; pointer-events none` on the card's inner
blocks + a 12px muted note naming the requirement, `gap 6px`, in place of
the description. Disabled control: `opacity .5; cursor not-allowed`
(built into `.btn`/inputs). De-emphasized live info: `opacity .72` +
italic. These three values are the whole scale (D40).

### 11.18 Elevation (shadow) scale

| Level | Shadow | Used by |
|---|---|---|
| E1 floating chip | `0 2px 10px rgba(0,0,0,.35)` | `↓ Latest` |
| E2 toast | `0 4px 16px rgba(0,0,0,.4)` | toast |
| E3 dropdown | `0 8px 24px rgba(0,0,0,.4)` | combo list |
| E4 modal | `0 16px 48px rgba(0,0,0,.5)` | modal |

Nothing else casts a shadow (D32).

### 11.19 Tint alphas

Tints are the semantic color at a fixed alpha ladder, implemented as the
`--*-tint` tokens (§8): **8%** message surfaces (`--err-tint-bg`: `.qerror`,
`.freq-warn`) · **10%** hover of danger actions (`--err-tint-hover`) ·
**15%** badge/toggle fills (`--ok-tint`, `--warn-tint`, `--err-tint`,
`--raw-tint`) · **22%** toggle hover (`--ok-tint-strong`) · **30%** warning
outline (`--err-outline`) · **55%** (a stripe tint, not currently defined —
retired with the old HUD's hazard stripes). Consume
the token; adding a new tint means adding a token on this ladder first.

### 11.20 Slider (continuous control)

For a value the user *hunts for* rather than knows — the viewer's isovalue,
where the right level is whatever shows the feature. A labelled `.mv-ctl`
wrapper (`flex; gap 8px; 12px muted`) holds the label, `input[type=range]`
(`width 120px; accent-color --primary`), and a **fixed-width mono readout**
(`.mv-ctl-val`, `min-width 42px`, `--foreground`) — fixed so the caption beside
it does not jog while dragging.

Two rules make a slider honest rather than decorative:
- **It must be cheap to move.** A slider invites dragging, so its `oninput`
  may only touch data already in memory. Anything that re-fetches or
  recomputes belongs on a `<select>` or a button, where one deliberate choice
  costs one wait — which is exactly why the viewer pairs the instant isovalue
  *slider* with a grid *selector*.
- **Log-scale when the range spans orders of magnitude.** The isovalue slider
  is 0..100 mapped onto 0.001–0.5, because a spin density is read near 0.005
  and an orbital near 0.05; linear would bury the bottom decade in the first
  few pixels.
- **Open it where the data is, not where the convention is.** A conventional
  default that renders an empty stage is worse than no default: the user reads
  it as a broken feature, not as a slider to move. Fit the opening value to the
  data and let the convention cap it — never raise past the convention, which
  would be a claim the convention does not make.

### 11.21 Viewer panel (3D stages)

Two shapes, one vocabulary — every WebGL stage in the app is one of these.

- **Full panel** (`.mv-overlay` / `.mv-panel` / `.mv-head` / `.mv-body` /
  `.mv-list` / `.mv-stage` / `.mv-gl` / `.mv-bar`): fixed overlay, z-index 110
  over `rgba(0,0,0,.62)`; panel `min(1080px, 100vw − 48px)` ×
  `min(760px, 100vh − 48px)`, `--card` on `--radius-lg`, shadow
  `0 16px 48px rgba(0,0,0,.5)`. Head `12px 16px` + bottom border, title
  15px/600, hint 12px muted pushed right, then the close button. Body is a flex
  row: an optional 220px list column (`--input-bg`, 6px padding, right border)
  beside the stage — put away entirely when there is only one thing to show, as
  the ESP map does. The stage's bar is `10px 16px` + top border with a centred
  13px mono caption. Used by the Results-tab structure/orbital viewer and the
  ESP map.
- **Inline panel** (`.struct-panel` / `.struct-gl` / `.struct-bar` /
  `.struct-caption`): the same bar (`8px 10px`, 12px mono caption) over a 220px
  media stage (§9.3), inset in a card at `--radius-sm` on `--background`
  (§9.2's inset media surface). Used by the Build-tab geometry preview and the
  NEB endpoint comparison.

One rule governs both: **a stage's camera is reset (`zoomTo`) only when the
scene is genuinely a different one.** These panels re-render on events that are
not about the molecule at all — a charge keystroke, a tab entry, a re-render —
and re-zooming on each would throw away the orientation the user just set with
the mouse (D60). Track a signature of what is drawn and zoom on the
change, never on the redraw.

## 12. State → color matrix

| Surface | pending | running | done | failed | cancelled | blocked | filtered | raw/MLIP tag |
|---|---|---|---|---|---|---|---|---|
| Badge bg | `--accent` | warn-15% | ok-15% | err-15% | `--accent` | `--accent` | `--accent` | raw-15% |
| Badge text | muted | `--warn` | `--ok` | `--err` | muted | muted | muted | `--raw` |

**Filtered** is a terminal state with no output: the energy condition on the
edge into a calculation (§17.6) left it out, or what it depends on was. It is
neutral like cancelled and blocked and told apart from both where a state is
drawn small: its dot is a solid faint disc (`--qe-faint`; theirs are rings),
a queue block's border is dotted faint and the card at 60% (blocked: 45%),
a stack member at 55%, and its `message` (the reason, "Filtered: ΔE 4.21
kcal/mol > 3.00") is the block's tooltip and the inspector's note. In the
main window it sits in the rail's Finished group.

A **running calc ORCA has already failed** (`CalcSummary.faulted`: a fatal
marker printed, the process still alive) stays where running rows go, because
it still holds its cores and Stop still reaches it, but it wears the *failed*
column: the badge reads "failed" in err colours, the dot is `--err`, the message
is the error chip, and the progress bar is dropped. The amber of a live row is
a promise of an answer, and this row will not give one.

Verdict badges (a comparison result, not a run state — §11.7):

| Surface | match | mismatch |
|---|---|---|
| Badge bg | ok-15% | err-15% |
| Badge text | `--ok` | `--err` |

Structure findings: error-level chip `.qerror` (`--err` on err-8%),
warn-level chip `.qwarn` (`--warn` on warn-8%); the atoms a finding names are
marked `--err` in the 3D preview.

Log levels: `info → --info`, `ok → --ok`, `warn → --warn`, `err → --err`,
ORCA raw output → `--orca`. Pills: unset/checking dot muted, ready `--ok`,
error `--err`. Values: converged/Normal → `.ok`; imaginary ≠ 0 → `.warn`;
NOT converged/ABNORMAL → `.err`; HOMO `--ok`, LUMO `--warn`. Progress fill
`--ok`; live trace `--warn`; goal `--ok` dashed. Missing value: `—`.

## 13. Interaction recipes

### 13.1 Confirmation decision

Confirm iff the action destroys something (process, queue entries, results
on disk, form state via irreversible conversion) **and** something would
actually be lost. Recipe: title = `{Verb} {object}?` (question form —
"Overwrite existing results?"); body = what happens (destructive
fact in `<b>`) + what is lost + `(To …, use <b>{alternative}</b> instead.)`
when one exists; buttons `[Cancel] [{Verb}]` with danger styling on the
confirm; three-way choices get three labeled buttons (Keep existing
(skip these)). Dismiss ≠ confirm.

### 13.2 Channel selection

| Event | Channels |
|---|---|
| Validation/constraint block (user must act) | toast + `err` log |
| Operation failed (backend `{error}`) | toast + `err` log (`Could not {verb}: {reason}`) |
| Success mutation | `ok` log (+ navigate to the result, D60) |
| Passive state change / progress | log only, or progress UI |
| Choice with loss potential | modal (13.1) |
| Front-end exception | `[web]` log capture (automatic) |
| Transient poll failure | silent (self-healing) |

A toast alone is never the only record (D65). Failure call sites route
through the shared `failNotify` helper (toast + `err` log in one call) —
don't hand-roll a single-channel failure.

### 13.3 Hidden vs locked vs disabled

- **Hide** what is *irrelevant* (fields for another calc kind, solvent when
  gas-phase) — progressive disclosure (D62) — and what the user has *never
  adopted* (an optional backend with no environment registered / no CREST
  installed: its mode button and status pill both go, reachable through the
  build toggle's "+ More backends…" chip and Settings, which always lists
  them). See D41's scope clause.
- **Lock** (11.17, with the reason) what is *adopted but not ready*
  (a registered MLIP env whose probe fails) — never hide it (D41). Locking also
  covers *terminal* states: a FAILED calculation locks at the moment of
  failure (PRINCIPLES P24) — its row keeps only read-only diagnosis (state
  badge + failure message + output access) and the × remove control
  (queue-entry removal only; workspace folders are never deleted). Do
  **not** dim the failure message itself: the diagnosis must stay fully
  legible (D2, PRINCIPLES P28); dim/disable only the edit/drag affordances.
- **Disable** individual controls that are *momentarily unavailable*
  (Run while running).
Every UI gate has a backend twin (D64); the UI gate is UX, not security.

### 13.4 Auto-update etiquette

Log follows only within 40px of the bottom. Re-rendered `<select>`s restore
the previous selection (`const prev = sel.value` pattern). Auto-fill only
empty fields. Failed saves never touch the mirror. Re-clicking the active
mode is a no-op. The only sanctioned auto-navigation: to the result of the
user's own action.

## 14. Copy spec

Language: English (D73). Current strings were normalized onto these forms
in the 0.4.3-beta sweep (B20).

### 14.1 Form per surface

| Surface | Form | Case | End punctuation |
|---|---|---|---|
| Card title | noun phrase (+ `(count, unit)` in Results) | Sentence | none |
| Card desc / hint | noun-form fragment | Sentence | period |
| Field label | noun phrase; units in `( )`; ORCA keywords verbatim (`maxcore (MB / core)`) | Sentence (keywords as-is) | none |
| Placeholder | `e.g. {example}` or a literal example value | lower | none |
| Button | imperative verb phrase; `→` for the forward action; no trailing `…` | Sentence | none |
| Tab / mode / option | noun (ORCA abbreviations verbatim; `Opt + Freq`; `NEB-TS`) | Sentence | none |
| Validation error | `{Diagnosis}. {Imperative fix}.` or `{Requirement} (where to fix).` | Sentence | period |
| Toast | one sentence | Sentence | period |
| Log ok/info | `"{name}" ({kind}) {verbed}.`; next step as `Next: {step}, then {action}.` | Sentence | period |
| Log err | `Could not {verb}: {backend reason}` | Sentence | period |
| Log warn | `{Diagnosis} — {needed action, noun-form}.` | Sentence | period |
| Modal title | confirm: `{Verb} {object}?`; viewer/info: `{Noun} · {name}` | Sentence | `?` / none |
| Empty state | tier forms per 11.14 | per tier | per tier |
| Status pill | `{Subsystem} {state}` — no colon | state lowercase | none |
| Inline live status (next to a control) | lowercase fragment: `loaded (12 atoms)`, `no product loaded`, `checking…` | lower | none |
| Tooltip (`title=`) | noun phrase (HUD phase-dot tooltips echo the lowercase status) | Sentence | none |
| Progress headline | `{Stage} {NN}% · {unit} {n}`; pre-stage form `{Stage} {unit} {n} · {substage} {NN}%`; completion `{Stage} complete · 100% · {unit} {n}` | Sentence | none |
| Progress meta / ETA | lowercase fragments: `4/5 criteria met · ~2m/step`, `roughly a few minutes left` | lower | none |
| HUD | title/status UPPERCASE; captions lowercase nouns | — | none |
| Chart axis | lowercase unless starting with an abbreviation/symbol (`SCF cycle`, `optimization step`, `ΔG (kcal/mol)`) | — | none |
| Result verdict banner | `⚠/✓ {Diagnosis} — {interpretation}. {Recommendation}.` | Sentence | period |

### 14.2 Vocabulary (canonical terms)

| Concept | Use | Never |
|---|---|---|
| a queue entry | **calculation** (space-constrained labels: *calc*) | *job* for queue entries |
| the running process | **job** | — |
| execute the queue | **run** / *Run queue* | *start* (except `Could not start:`) |
| remove one / empty all | **Remove** / **Clear all** | *delete* |
| stop current, keep rest | **Stop after current** | — |
| kill run + cancel rest | **Cancel** (button) / *Stop run* (confirm) | — |
| coordinates | **geometry** (`.xyz` for files) | *structure* |
| use another calc's result | **reference** (chip: `ref → {name}`) | — |
| interpreter entry | **MLIP environment**; name the backend only when the requirement is backend-specific (*Ready MACE environment required.*) | *env* in prose |
| optimization iteration / SCF iteration | **step** / **cycle** (respectively — never mixed) | — |
| excited-state method | **TD-DFT** (hyphenated) | *TDDFT* |

Articles before extensions: **an** `.xyz` / **an** `.inp` / **an** `.out`
(letter-name vowel sounds). Calc names are double-quoted in log/toast copy;
in modal bodies they use the `.names` mono span (a single inline name may be
`<b>`, §11.10). Plurals are programmatic (`mode${n === 1 ? "" : "s"}`), not
`(s)`.

### 14.3 Glyphs and numbers

`…` ongoing · ` · ` meta separator · `—` aside/interpretation · `→`
direction/result · `↓` jump · `⚠`/`✓` verdict · `~` estimate (time/quantity)
· `≈` approximate equality in scientific copy (`ΔE ≈ 12.3 kcal/mol`) · `—`
missing value. Units always shown, attached in compact meta (`~2m/step`,
`~12s each`, `1e-8`), spaced in labels (`298.15 K`, `450 nm`, `ΔG
(kcal/mol)`); scientific glyphs verbatim (`cm⁻¹`, `Å`, `÷`, `Δ`). Percent
attached: `62%`.

## 15. New-UI checklist

Before committing any UI change, confirm:

1. Colors: tokens only — no hex/rgba literals; tints on the §11.19 alpha
   ladder; checked in **both themes**.
2. Spacing/size/radius/shadow: every value is on a §9–§11 scale.
3. Typography: every string maps to a §10 role; machine text is `.mono`.
4. Component: reused a §11 recipe (or amended the recipe here first);
   class is ancestor-free (D50).
5. State: colors follow §12; new state added to the matrix if novel.
6. Readiness: an *adopted* feature that isn't ready is locked with a
   reason (13.3), never hidden; an optional backend the user never set up
   is hidden with a signposted way in (D41 scope clause).
7. Destructive paths: 13.1 confirm recipe; dismiss is safe.
8. Feedback: every outcome lands in the log; failures use both channels
   (13.2).
9. Copy: surface form + vocabulary from §14; escaped via `escapeHtml`
   (PRINCIPLES P42).
10. Honesty: progress/ETA/indicator claims match reality (D2, D42, D43).
11. Etiquette: no context yanking (13.4); `<select>` re-renders preserve
    selection.
12. `web/types.js` / `globals.d.ts` mirrors updated; `tsc --noEmit` clean
    (PRINCIPLES P41).

---

## 16. Liquid Glass theme variant (retired)

Retired with the classic front-end (unreleased): the Liquid-Glass variant, its
intensity levels and wallpapers were drawn only by `web/style.css`, and the
notebook (§17) draws flat. The settings (`theme_variant`, `glass_level`,
`wallpaper`) are gone; an old `settings.json` carrying them still loads. The
section number stays reserved; the text is in git history.

---

## 17. Notebook front-end

The desktop window's only front-end: `MainWindow._index_file()` loads
`web/next/index.html`. It began as a preview beside the classic tabbed UI in
`web/` (selected by a `Settings.ui_variant` switch); both the classic UI and
the switch are removed (unreleased).

**17.0 The design is a file, and the difference is a number.**
`design/notebook-reference.html` is the approved design, kept in the repo, and
`web/next/app.css` IS its stylesheet — copied, not re-interpreted, so the markup
here uses the design's own class vocabulary. "Looks about right" is not a check
anyone can repeat, so the two are rendered and measured through one landmark
list:

```bash
python tools/uispec.py mock spec_mock.json     # the reference, as a plain page
python tools/uispec.py real spec_real.json     # web/next/, in the real window
python tools/uidiff.py spec_mock.json spec_real.json    # exit code = differences
```

116 landmarks × 23 computed properties, currently **0 differences** — in three
passes, because the first list was chrome only (top bar, rail, cell, pane) and a
0 over it said nothing about the inside of a result section, which was markup
`app.css` had never heard of. The second pass opens a real result, turns *Show
all* on so the tables exist, and measures what a section is built from:
`.secblock` at every span, `.sech h2`, `.secdesc`, `.plate`, `.tw` with its
`th`/`td`, `.factline`, `.rawin`/`.rawout`, and the two picture sections —
`.viewstage`, `.vizpanel`, `.vizlist`, `.vizstage`, `.viztypes`, `.viztype`.

The real side is seeded so both pages hold the same SHAPE of data — finished
rows and queued ones, a raw calculation, a cell with both a chart and an output
pane — because a diff against a different sample measures the sample, not the
layout.
Element boxes and grid ROW sizes are excluded for that reason (they are
content), and column splits are compared as ratios, so a scrollbar on one side
does not read as a different layout.

The third pass opens what nothing at rest can see — the pop-out viewer, the
settings sheet, and the builder's conditional blocks (NEB slots, a per-element
basis row, the snippet strip) — with both sides opened to the same state first.

Deliberate departures from the reference live in one named block at the bottom
of `app.css`, not scattered through it, so the diff has one place to account
for — and the landmark each one moves is listed in `uidiff.py`'s
`PER_LANDMARK_SKIP` with the reason, because a departure the check still
reports is a difference the next reader will helpfully undo. The standing
example: `.secchips` is sticky at `top:0`, and a sticky top sticks to the
SCROLLPORT'S PADDING EDGE — so the reference's 20px of `.rescontent` padding is
a band the outline can never cover, and in a report that actually scrolls a
table header sat over the chips. The 20px moved onto the bar. It is the same
20px on the other side of one edge, which is why both paddings are skipped
rather than the rule being re-argued. `web/next/README.md` is the hand-editing guide: save, press F5 in the app
(the reload costs nothing — the queue is Python-side), and
`ORCADESK_REMOTE_DEBUG=9222` opens Chromium's inspector on the live page.

**17.0a Borrowing a renderer means borrowing its stylesheet.**
../scf_graph.js and ../progress_panels.js are used as PARSERS only. Their own
`render*` helpers emit a vocabulary of their own (`.scf-line`,
`.vstep-*`, `.stepc-*`), for which `app.css` has **no rule at all** — a chart
drawn through them had no stroke colour and a stage chain no dots. The trackers
hand over their numbers and `web/next/charts.js` draws them in the design's
vocabulary instead: `.chart` + `.serieskey` for a converging trace,
`.stages`/`.stagerow` for a run that is a sequence of named stages,
`.progrow`/`.prog`/`.eta` for how far along it is. Every SVG carries its colours
inline, so it needs no stylesheet at all.

**17.0b The stylesheet is a contract.** A class `app.css` has no rule for
renders as *nothing* — no grid, no card, no type scale. Section contents are
therefore built from the design's own components (`.plate`, `.secdesc`,
`.sech`, `.tw`/`table`, `.kvline`, `.factline`, `.field-row`, `.checkbox`,
`.grouplabel`, `.divider`), never from invented names;
`tests/test_next_ui.py` fails on any class the sheet does not style, apart from
four behaviour hooks the design uses the same way.

**17.1 It owns its markup.** `web/next/` has its own `index.html`, its own
`app.css` and its own logic against the `Bridge`.
The only files it borrows are `../scf_graph.js` and `../progress_panels.js` —
convergence trackers and chart/step renderers that own no ids and no elements,
so sharing them is reuse rather than coupling — plus its own `mol.js`, a 2D
ball-and-stick renderer for the cell thumbnails (a still per cell, several on
screen at once: a GL context each would cost more than the picture is worth).

One tsc project checks every script under `web/`, and they share one global
scope, so the notebook keeps everything on a single `NB` namespace (plus `MOL`
and `CHARTS`) — any other top-level name is refused by `tests/test_next_ui.py`.

**17.2 The window is the queue plus one thing.** Two places (Jobs and Results)
and a rail that stays on screen in both — 600px in the reference, a 256px
status strip here since the queue got its own window. Building and editing the
queue is the queue window's (§17.6); this window reads it:

- **Rail** — the queue, grouped by what a row is doing (running / queued /
  done), because that is the question being asked of the list. A row carries
  its own progress bar, and it is drawn **only when a real one can be computed**
  (an optimization's gradient progress, an SCF's convergence); a run with
  nothing to report gets no bar rather than a decorative one. The bar is the
  **whole job**, not the stage running now: an optimization, IRC or
  frequency kind is the list of phases it runs (a TS search's or an IRC's
  initial Hessian, the optimization or the walk, an `*_opt_freq`'s
  frequencies), each an equal share read off its own tracker (`phasesOf` in
  `rail.js`). Read stage by stage, the bar filled when a TS search's Hessian
  finished and emptied when the optimization began. It is full only when ORCA
  says the job is through. The cell's own bar under its chart stays the stage's.
  The rail is the queue's **status**, not its editor — a **compact strip**,
  256px wide (stacked on top below 760px as before). A row is the state dot
  and the calculation's name, ellipsized; a running row adds its thin progress
  bar. Everything else the reference's row carried — backend tag, meta line,
  message line, state badge, *edit*, *.inp*, the grip and the expand caret —
  is gone from the row and said in its tooltip and accessible name instead
  (name · kind · state · last message). A click brings that calculation's cell
  into view in Jobs, or opens its report in Results. Running / Queued /
  Finished keep their small labels and counts, and Finished still folds; its
  *Clear* is gone. The header is *Queue*, the count and **Open queue**; the
  footer one row of three small buttons (Run queue, Stop after current,
  Cancel) — *Clear all* is the queue window's. That row is the whole-queue
  command and stays the primary one; above it the resource box carries a
  quieter line per **pool** — *This PC* and each enabled remote device — with
  its own *Run / Stop / Cancel* (`.poolbtn`: 11px text buttons, no fill,
  bordered on hover, Cancel turning `--err` on hover and asking first) and its
  state word after the name (`.poolstate`: *draining* in `--warn`, *stopped*
  muted; nothing while it runs), so a drain never reads as a stall. While idle
  the box shows only these lines, and only when a device is enabled: a
  device's *Run* starts the queue on that device alone. The footer's *Run
  queue* also resumes a run that is draining or has pools stopped. *Opened files* stays, in the
  same row style, with an × that takes a file off the list. Each departure from
  the reference is listed in `tools/uidiff.py` (`REMOVED`,
  `PER_LANDMARK_SKIP`, `STYLE_SKIP`) and the dropped row classes in
  `OUTSTANDING` (`tests/test_next_ui.py`), with this section as the reason.
- **Builder** — the cell IS the form, and it lives in the queue window now
  (§17.6): its inspector column hosts the same `.cell.editing`. The stream has
  no *New calculation* stub any more (an empty Jobs view says where
  calculations come from); a cell's *New from this* opens the builder in the
  queue window, handing the seed over (§17.6). It is not a place you go to and
  come back from.
- **Stream** — one **cell** per calculation: its chart, its output tail and its
  facts, together. A tabbed layout asks you to pick a job and then pick a view
  of it (Raw or Graph, one at a time, one job at a time); here you read down
  the run.
- **Stream, first column** — the structure the calculation is working on:
  220px for a convergence curve, 236px when the run is a stage chain instead.
- **Report, curated** — ORCA's parse carries ~40 scalars and printing them in
  source order is a data dump, not a result. The questions asked first (did it
  finish, did it converge, how long) are a `.factline`; what the calculation
  produced is a plate of `.kvline` pairs; provenance and the SCF energy
  decomposition sit behind *Show all*. A value this front-end has not heard of
  lands in the VISIBLE plate — a new parser row must not be able to hide itself.
- **Structure** — the geometry the run ended on, and where the per-atom and
  per-bond numbers live: click an atom for its charges and Mayer valence, click
  a bond for its length and bond order, right-drag between two atoms to measure,
  and the axis button walks x, y, z. The measurement is drawn WHILE the button
  is down — a dashed line from the anchored atom to the cursor, a ring on the
  anchor, a second ring once the cursor is over an atom that would end it, and
  the distance only when there is one to state. A drag whose only feedback
  arrives on release is a drag you cannot tell you are making. A charge belongs to an atom you can point
  at, not to row 27 of a list — which is why the report has no charge tables of
  its own (they stay under *Show all*, for reading without a mouse).
- **Visual** — everything in the result's FOLDER that can be drawn: a
  trajectory, a CREST ensemble, an exported set. Discovered, never asked for.
- **Report** — the parsed result as a document, on the design's 12-column grid:
  the summary wide, what the parser found beside it, the **input this run read
  next to the output it produced** (`third` : `twothirds`), and the reaction
  profile last. A sticky outline lists only the sections the result has.
- **Settings** — a right-hand slide-over, not a third view: somewhere you go and
  come straight back from, and taking the window for it would throw away the
  queue being watched.

**17.0a A landmark list has to reach past what was easy to measure.** The
check runs in three passes — the window at rest, a result section with *Show
all* on, and the overlays (the pop-out viewer, the settings sheet, the builder's
conditional blocks), both sides opened to the same state first. Each pass exists
because the one before it read `DIFFERENCES: 0` while something real was
unstyled: the first list was chrome only and missed a report of markup `app.css`
had never heard of; the second stopped at the window at rest and could not see a
single overlay. A green number that stops the looking is worse than no number.

Twice more it read zero while something real was wrong, and both were blind
spots of the same kind — a property the check compared could not *express* the
difference. A computed `font-family` is the stack that was **declared**,
identical on both sides by construction, so the whole page being drawn in a
substituted face was not a value it could disagree on (17.0c). And the landmark
list measures the properties of elements it *names*, so a control that exists on
one side only changes nothing it looks at: the Finished group's *Clear* and the
entire *Opened files* group were missing from the rail without a single
difference. The check now measures the face that actually renders — the width of
one sample line in each stack, against a deliberately unresolvable family as the
control — and prints a census of every class present on each page as an advisory
that is reported but not counted, because class presence follows the sample
data.

And a third kind, for the differences that are *inside* no landmark: a
**metric audit** over every `(parent, element)` class combination both pages
draw, comparing the box-model and type properties the stylesheet fixes —
padding, margin, border, radius, font size/weight/leading/tracking, min-height,
display. The landmark list is 121 selectors somebody chose; a padding a pixel
off on a control nobody named is exactly as visible. Three rules keep it
readable rather than noisy, and each was forced by a false report: the key
carries the **parent**, because these rules are contextual and a flex or grid
parent also blockifies its children's `display`; an element carrying its own
`style=` attribute is **skipped**, because the audit measures the stylesheet and
not a one-off; and **colour and opacity are absent**, because they carry state
(a badge is green or red by what it reports) while metrics do not. It is what
found an 11px separator in a control the design sizes exactly (17.0d).

The rule this leaves: **when a difference is invisible to the check, the fix is
a new kind of measurement, not a new landmark of the same kind.**

**17.0d A divider divides groups, not buttons.** `.seg .sep` is the rule that
separates two groups inside one segmented control — the reference uses it in
exactly one place, the builder's `DFT | MLIP | CREST . Beginner | Expert`. A
two-button control has no groups, and the divider is not free: a 1px rule, 8px
of margin and one more 2px gap is **11px** of extra width on a control whose
size the design fixes. Two of them had appeared in the preview's top bar and
viewer head. The general form: a class the design uses once, for one job, is not
a decoration to reach for — read what it divides before using it.

**17.0c The design's typefaces ship with the app.** `design/notebook-reference.html`
loads Inter and JetBrains Mono from Google Fonts, as the artifact it was
published as could. A desktop app that runs offline against a local ORCA cannot,
and shipping the stack without the faces is not a smaller version of the design —
it is a different one: the substitutes (Segoe UI Variable and Consolas on
Windows) have a smaller x-height than the faces the spacing was set for, so every
10–11px muted label reads fainter and leaves more air inside a box whose padding
never changed. That is the whole of why the preview looked *less dense* than the
design it is a copy of. Both families are carried in `web/fonts/` as variable
woff2, latin + latin-ext, under the SIL OFL, and linked from
`web/next/index.html` — the local stand-in for the reference's own `<link>`, and
the reason `app.css` itself stays a verbatim copy of the reference stylesheet.
`font-display` is `block`, not `swap`: the files are on disk, so the only thing a
swap could do is flash the substitution this exists to remove.

**17.0e The rail is the picker; the toolbar is not a second one.** A result is
chosen by clicking its row — in either view — and a result that is not in the
rail is opened with *Open file…* / *Open folder…*, which is also what puts it
there, under **Opened files**, with the folder it came from; the group is kept
across launches until a row is closed. The preview had
grown a `<select>` in the Results toolbar listing the queue *and* a scan of the
workspace: a second picker for the same job, the one that could not say where an
entry came from, and a disk walk on every entry to the tab. *Output / Visual*
takes that place, and it does not switch a mode — Visual is one of the report's
sections, so the pair jumps to it exactly as the outline chips do, and *Show
all*, which is Output's, goes away while Visual is being read.

**17.4f A canvas that sizes itself from its own box must be out of flow.** Every
stage here draws into a `.molcv` whose bitmap is set from its own measured box
each frame (`canvas.width = box.width * dpr`). With the reference's
`width:100%; height:100%` *in flow*, that is a feedback loop wherever the
percentage cannot resolve — `.viewstage` is `flex:1` in a column whose height
comes from a grid row measured from its content, so the canvas falls back to its
intrinsic size, which is the bitmap the last frame set. At
`devicePixelRatio: 1` the two coincide and nothing moves; at 1.25 each frame
multiplies the height by 1.25 and the Structure stage walks away from the Visual
panel beside it. Chromium says so out loud — *"ResizeObserver loop completed
with undelivered notifications"*. So: the canvas is `position:absolute; inset:0`
(every stage is already `position:relative`), and the ResizeObserver watches the
**stage**, not the canvas it is about to resize. Both halves, because either
alone leaves the loop one step from closing.

**17.3 A rule that sets `display` names the whole state.** Regions are hidden by
default and revealed by the view — never the other way round — so no two rules
that both set `display` can match the same element. This is written down
because the first attempt at this layout got it wrong twice: a rule that won on
source order drew the build form and the log on top of each other, and a rule
that named only half the state left a pane drawn under two other views.

**17.4 Charts are drawn at the box's real pixel width.** Every SVG here takes
`{width, height}` in device pixels and emits a matching `viewBox`, so a stroke
is the width it says it is. A fixed viewBox scaled to fit is the bug that made
an earlier revision's charts 2.6× too thick. The outermost x-axis ticks anchor
to their own edge rather than their centre, or an exponential label hangs past
the plot.

**17.4a One stage, two renderers — and the second is loaded only if asked
for.** The pop-out viewer paints structures with `mol.js`, the same painter as
every other stage here, so drag, click and right-drag-to-measure behave the same
in the big window as in the small one. An isosurface is a volume, not a set of
atoms, so that half uses the vendored 3Dmol (`web/vendor/`) — and
it is fetched on the first surface, never at boot. A machine with no WebGL
context can still walk a trajectory, and the one thing it cannot do says so
instead of failing silently.

And one renderer, two stages: **a pick is drawn where it was made.** The Visual
panel used to answer a click on an orbital by opening the pop-out window over
the report you were reading — a renderer's limitation showing through as
behaviour, because that stage paints atoms and an isosurface is a volume. The
surface now appears in the panel, and the window is what you open when you want
it big with the isovalue beside it. There is still exactly one WebGL context,
because a browser keeps only a handful alive and drops the oldest without
asking, and because the panel's stage is destroyed and rebuilt on every report
render — so the *box* moves between the two stages instead (`adopt`), and
anything that is about to re-render a stage sends it home first (`park`). A
context lost with an `innerHTML` would take every later surface with it. The
box goes in *under* the stage's own overlays — right after its atom canvas,
where it sits at home — so the caption (isovalue, which colour is which sign)
and *Open in viewer* stay on top of a surface; appended last, it covered both.

Drawing it there brings its controls with it. The reference's panel has none,
because its surface is a picture of one and a picture needs no isovalue; a real
one does, and the grid it was sampled at is the other thing worth changing. They
go under the list, in the column that already answers *which one* — a deliberate
addition to the reference, named in `app.css` ("isovalue, range, grid"). The two
are not the same kind of control and the panel does not pretend they are: an
isovalue re-meshes what is already in memory, instant and with no backend, while
a grid is a fresh orca_plot run, so it asks before it spends and re-answers
every *ready* mark on screen — the same orbital at 40³ and 80³ are two different
files. The grid is remembered per KIND, because an orbital at 80³ is a second
and an ESP map at 80³ is minutes.

**17.4g A list of one is not a list.** A kind with a single instance — an ESP
map is one map — used to draw a one-row list beside an otherwise empty column.
The column says there is nothing to step through (the reference's own words
here) and carries the controls instead, which is the useful half of it. What is
already on disk is simply drawn; what is not offers a button that names the
cost, because selecting a tile is not permission to spend an orca_plot run
(P26).

**17.4h A cell's two columns end level, so a chart has a height and not a
ratio.** The chart shares its row with the output pane, whose `.logbox` is
`flex:1` up to 425px — so a chart sized as a fraction of its own width ran
further past that cap the wider the window got, and the two columns drifted
apart by 200px at full screen. Chart heights here are fixed numbers with the
budget written beside them (`charts.js`: 270, measured at 383 with its pane head
against the 463 the output column can give). The general rule: **when two
columns are meant to end together, the one that can grow states its height in
the same units as the one that cannot.** It is also why a cell shows one chart
and not two: stacking the optimization and the SCF halves both and spends the
budget twice.

**17.4i A convergence is five things at once.** A geometry optimization
converges ΔE, RMS/MAX gradient and RMS/MAX step, and *which one is holding it
up* is the question being asked of the chart — a single worst-of line answers
"how far" and never "what". Each is drawn as a multiple of **its own**
tolerance, so all five share one goal line at 1 and the `n/5 met` count is
readable straight off the picture; the `--crit-*` tokens exist for exactly this.
Gridlines land on whole decades: five evenly spaced lines labelled with a
rounded exponent printed `1e3` twice over a three-decade range, which is an axis
that lies.

An optimization is an SCF per cycle, so a running cell has two convergences to
look at, and they are **a toggle above the chart** — *Optimization* / *Current
SCF*, the reference's own control and its own words. The chart used to drop the
SCF the moment the first cycle landed; it is the same question at two scales,
and the answer is which one you are asking, not both at half size. The toggle
appears only when both exist: before the first cycle there is only an SCF, and
after the run there is no cycle solving now.

**17.4b A band is 1.6px wide, so the plot is the hit target.** The reference's
spectrum is a picture of one; a real one is 150 bands whose exact wavenumber is
the reason to look at it. The table that used to carry those numbers is gone —
it was the chart transcribed, and a hundred-odd rows of one number each pushed
every section after it off the screen — so the number lives on the band, in a
`.chtip` plate over the plot. No hit-target rule can save a 1.6px mark
(§11.16), and widening the mark would be a lie about the data, so the target is
the whole plot area and the **nearest band wins**. It is not hover-only: the
svg is focusable and ←/→/Home/End step the same cursor, Escape puts it away,
and the plate is a `role="status"` so what the cursor lands on is announced.
The cursor line is a CSS rule rather than a presentation attribute, because
`var()` does not resolve in one (§11.16). A path chart (an IRC or NEB-TS
*Reaction path*) reads the same way, point by point: the nearest point grows
and the plate names its step and energy.

**17.4c What can be drawn is one strip, and the expensive half asks.** The
`.xyz` sets are found by listing the folder and the surfaces by asking the
wavefunction, but the reader is choosing *what to look at*, not a way of
finding it, so both land in one `.viztypes` row. The difference that matters is
not where they came from: it is that a structure set is already on disk and a
surface may have to be computed. So every surface tile carries **ready** or
**not plotted**, a ready one opens with no question, and one that is not asks
— naming the grid and the real wait (D2: "this may take a while" is not a
cost). The numbers come from `core/plot.py`'s own measurements, carried on the
pick, so the question cannot quote a figure the backend has stopped believing.
**A tile is a KIND; the list beside it is the instances.** The strip is one
line of the panel, so what goes in it has to be a closed set of categories —
Structure, Trajectory, Orbitals, ESP map, Conformers — and everything countable
goes in the `.vizlist`: every orbital of a manifold, forty-two conformers, a hundred
trajectory frames. Putting instances in the strip is a mistake that looks fine
at four orbitals and cannot survive ten, which is the tell that they were never
tiles. Selecting a surface kind fills the list rather than drawing: this stage
paints atoms, an isosurface needs the pop-out viewer's renderer, so a **row**
opens that window and the structure on the stage is left where it was.
**The structure is the strip's first tile, not a section of its own.** The
reference sets a Structure section beside Visual, each with a stage, and in a
real report the two drew the same molecule side by side at 5 + 7 columns. The
parsed geometry is the *Structure* tile instead, and the report opens on it:
it is the picture the result is OF, before anything found beside it. Its list
column holds no rows (one structure is nothing to step through) and says what
the stage answers — click an atom or a bond for its numbers, right-drag to
measure — and those picks are read only on this tile, because a trajectory
frame's atom 3 is not the parse's once anything has moved. The panel is alone
on its row and spans it (`.vizhalf.solo`); `.secblock.viewer` and `.viewstage`
are gone, listed as deliberate in `tools/uidiff.py` `REMOVED` and in
`test_next_ui.py`'s `OUTSTANDING`.

**17.4f A marker that states the orientation is the control for it.** The axis
gizmo in the corner is drawn from the same rotation as the molecule, so it
already answers "which way am I facing". Putting the axis views behind a button
that cycles *x -> y -> z* asks the reader to answer that question themselves and
then press up to three times to act on it. Pressing the arm is one gesture, it
needs no label, and it cannot be off by a step. The rule generalizes: **when a
thing on screen already depicts the state, make the depiction the control**
rather than adding a widget beside it that names the state again.

Two obligations come with it, because a shape painted on a canvas is not a
button. The target is the arm's LABEL, not its line — two arms nearly edge-on
cross near the origin and a click there cannot mean one of them, while the
labels stay at least an arm apart — and it is sized for a pointer (a 28px
target, the desktop minimum), with hover changing the arm so it reads as
pressable. And it needs a keyboard route, since nothing drawn can be tabbed to:
the stage already takes focus for its arrow keys, so x, y and z ride there and
are named in the canvas's own label. A marker in a corner the stage also
writes in (a cell thumbnail's *final · 9 atoms*, the Visual panel's and the
pop-out's caption) keeps **all** of itself above the caption — an arm can point
straight down, so the room it needs is the arm and its label, not the origin's
height. The stage says where its caption starts; how far the marker reaches is
the painter's to know, and asking the caller for it is how the thumbnail's
labels came to sit on its caption.

**17.4g A list of instances is not the place to guess which one matters.** The
orbital list offered HOMO−4 … LUMO+4 — a window chosen on the theory that the
interesting orbital is near the frontier. It often is not: a ligand-field
argument wants the metal d set wherever it fell, a charge-transfer one the
donor lone pair, a core-hole one nothing near the frontier at all. A row costs
a line; the expensive thing is behind the click, and that is what asks before
it spends. So enumerate everything and let the reader choose. The consequences
are the list's, not the chooser's: the rows are one delegated handler rather
than one listener each, and a unit is stated ONCE beside the list instead of on
every row — a core orbital reads −520.12, and repeating " eV" three hundred
times is what pushed the name that identifies it out of the column.

**17.4e A ball states its element, so its size has to be the element's.**
Ball-and-stick draws a sphere per atom at **0.3 × the van der Waals radius**,
the convention Avogadro, Jmol and 3Dmol share — H at 1.20 Å against C at
1.70 Å, so hydrogen is 71% of carbon: small enough to read as hydrogen, large
enough to be a ball rather than a knob on a stick, and the picture says which
atom is which before any label does. (It was the covalent radius at 0.32×,
which made H 41% of C and every atom a dot on a hairline stick; the rule this
section states — the size is the element's — is unchanged, the radius it is
read from is the one the reader's other tools use.) Main-group radii are
Bondi's, with Mantina 2009 for the elements Bondi left out; the d and f blocks
have no van der Waals radius anyone agrees on, so for those the drawing size
is a stated convention — covalent radius + 0.4 Å — rather than a number
dressed up as data. Two ways the element's size gets quietly lost, both of
which had happened:

*A radius table that stops.* The 24 elements an organic run contains were
listed and everything else took one fallback. That fallback exists for bond
perception, where guessing large is safe (a missed bond draws a molecule as
dust), and it was being reused as a drawing size, where guessing large is a
claim — an unlisted element rendered bigger than iodine. Bonding reads the
covalent table (Cordero 2008, H to Pu) and drawing reads the van der Waals
one, and the two have separate fallbacks: generous for bonding, ordinary
(carbon) for drawing.

*A floor with a number in it.* `max(2.5px, r)` clamps the small atoms first,
so it does not shrink a picture evenly — it deletes the distinction the picture
is for, and only when the molecule is large against its box, which is exactly
the cell thumbnail. A floor still has to exist (an atom at a third of a pixel
is a gap in a line), but it is stated relative to what is beside it: **a ball is
never thinner than the stick it sits on.** Below that it reads as a kink, not an
atom, and above it every ratio is the real one.

**Turning a thing is not a reason for it to change size.** Fitting the view to
the PROJECTED extent re-zooms on every frame of a drag: an elongated molecule
seen end-on is narrow and gets magnified, seen broadside it is wide and shrinks,
so the structure breathes under the cursor and nothing on screen has a stable
size to judge by. Fit to a rotation-INVARIANT quantity instead — the bounding
sphere — and the scale is a property of the molecule rather
than of where you happen to be standing. It costs a little fill in the flattest
orientation, which is the right trade: the picture stops arguing with the hand.

**Invariant is not the same as generous.** The sphere is the *smallest* one
holding the atoms (it is also the point the structure turns about), not the
one about the centroid, which leans toward whichever end has more atoms —
ethanol's hydrogens cost a tenth of the frame. The room each atom needs is its
own ball at its own depth under the perspective, not the largest ball in the
molecule charged to the hydrogen on the rim. The edge left around the sphere
is a share of the stage (4% of its shorter side, within 6–18 px), not a fixed
18 px that was 5% of the pop-out and a quarter of the IRC picker's 148 px
preview. And the floor is for structures with nothing to fit: a lone atom or
H₂ is not blown up into one disc, but a triatomic is drawn at its own size —
the old 1 Å floor on the radius had fitted HCN's 0.73 Å sphere as 1 Å. HCN's
transition state in the IRC picker went from 48% of the preview's height to
69%, a cell thumbnail's ethanol from 55% of its width to 70%, and across water
to a C16 chain, four stage sizes and 37 orientations no atom touches the edge.
The surface view
(3Dmol) answers to the same rule: its `zoomTo` would not fit a sphere under
5 Å, which drew ethanol at two fifths of the panel, so before each fit it is
told the radius the atoms need with their spheres on (1 Å at least), and the
surface's box is counted on top.

**A structure is the one the run ended on.** Geometry cached under a
calculation's name, read once, is the geometry it was *handed* — and a run's
whole purpose is that those are not the same coordinates. Anything holding a
structure records which of the two it has, and re-reads when the run acquires
an output of its own; the caption says which, so it can never claim a final
structure over input coordinates.

**17.4k Depth has to be drawn, because a canvas has none.** The structure
stages paint on a 2D canvas — several thumbnails on screen at once cannot each
hold a WebGL context — and painter's order alone says which atom covers which
but not how far apart they are: a ring seen face-on read as flat, and the
front and back of a conformer were the same size and the same colour. Three
cues carry it together, and each is cheap: **perspective** (the camera sits
four bounding radii out, so the nearest atom is drawn 1.33× and the farthest
0.8×, and the fit allows for that exactly so §17.4e's "turning is not a reason
to change size" still holds for the molecule as a whole), **depth fog** (a far
atom keeps 40% of its own colour and fades toward `--stage`), and a **dark
rim** on every sphere and stick, which cuts a near one out against what it
covers. Sticks are shaded across their width so they read as cylinders, and a
bond is painted as two halves at their own depths, since a bond running front
to back is in front of one atom and behind the other.

The display follows Avogadro2, the editor most ORCA users read structures in:
its four display types (ball and stick, sticks, space-filling, wireframe),
hydrogens on/off, atom labels (the element, or the element and number the
pick panel uses — `C12`), and multiple bonds drawn as parallel sticks in the
plane of a neighbour so they close up edge-on. **Multiple bonds come only from
the run's own Mayer bond orders**, on the geometry they were computed for
(Structure, and Visual when it draws the parsed geometry); a trajectory frame
is a different geometry and draws every bond single, and the Display menu says
which of the two the stage on screen is. The bands are wide — under 1.25
single, to 1.75 a partial bond (one solid stick and one dashed: an aromatic
ring's 1.3–1.5), to 2.4 double, above that triple — because a Mayer order is
not an integer and the picture should not claim one more precisely than it is.
The display belongs to the app, not to a stage: switching to sticks in the
pop-out switches the report beside it, as Avogadro's display types belong to
the scene. It lives in a *Display* popover under *Open in viewer* (an addition
to the reference, in the named `.dispmenu` block of `app.css`), and every
setting has a key on a focused stage — 1–4, H, L, 0 to reset the view.

Navigation is Avogadro's too: a drag turns about the **screen's** axes (the
near side follows the cursor — two Euler angles with a clamped pitch could
never turn a molecule over), Shift+drag or a middle-drag moves it, and the
wheel zooms about the cursor — but **only where the stage owns its window**.
In the report the wheel belongs to the page, since a stage that took it would
trap the reader halfway down a long result, so there it is Ctrl+wheel, which is
also what a touchpad pinch sends. **One wheel rule for every 3D view, whoever
paints it**: the surface view is 3Dmol's picture but not 3Dmol's wheel. Left
to its own handler, a notch moved the camera a third of the way in (×1.44,
measured), toward-you zoomed in where every atom stage zooms out, and it took
the wheel in
the report too — scrolling down the page over an orbital dived into it. It now
zooms by the atom stages' step (9% a notch), in their direction, under the same
window rule.

**17.4d Say a failure once, and number an atom once.** Two content rules the
report and the stream kept breaking in opposite directions.

*A failure is stated where the run is being read.* The rail is an index — every
row gets one line of state, so a failed one gets a one-line `⚠` summary,
ellipsised, exactly like a running row's ETA. The reason itself belongs at the
top of the cell body, which is the thing that *is* the calculation. Putting the
full box in the rail instead cost twice: four failures filled the rail and
pushed the queue off the screen, and the cell — the one place a reader is
actually looking at that run — had to squeeze the same sentence into its fact
line. The fact line states facts (kind, charge, cycles, criteria met); it is
not where a sentence goes.

*Every atom-numbered table on a screen counts from the same end.* ORCA prints
its Mayer and NMR tables indexed from zero and its geometry from one, and the
parse keeps each verbatim — correctly, since the parse's job is to report what
the file says. The report's job is the opposite: one molecule may not wear two
numberings at once, least of all beside copy that says "also on any bond you
click in **Structure**" while the viewer labels that bond `O1 – H2` and the
table calls it `0-O – 1-H`. Everything user-facing counts from one, through a
single helper (`results.js atomLabel` / `mayerAtom`) that is the one place
knowing ORCA does not.

**17.4j The output is the whole file, and its scrollbar is the file's.** The
reference's Output pane holds a short file in full, with *Find*, *↑ Top* and
*↓ Latest* above it. A real `.out` is hundreds of MB, and the pane used to hold
the last 2000 lines scrolled to the bottom — the opposite of "everything ORCA
wrote", and the input echo, the basis summary and the first cycles were simply
not there. It now opens at line one and draws only what is on screen inside a
spacer as tall as the whole file (`results.js` "output", `orcamgr/outview.py`),
so the scrollbar is the navigation and is drawn on purpose rather than left to
the platform's auto-hiding default (the named block "the whole output" at the
bottom of `app.css`). Above ~750k lines the spacer stops growing and the
position maps onto the file proportionally — Chromium does not lay out an
element much past 33M px. *Find* is the reference's control and its
placeholder; the search runs on the backend, which holds the file, and the
matches on screen are marked with the Custom Highlight API — every match amber
(`--warn-tint`), the current one solid (`--warn`), the way a browser's find
does. Enter / Shift+Enter and *Prev* / *Next* step through them, Ctrl+F
reaches the box (the embedded browser has no find bar of its own), and the
count is a live region. The button keeps the app's *↓ End* over the
reference's *↓ Latest*: a finished file has an end, not a latest.

**17.4l The builder draws what it is given, and constraints are picked on
the drawing.** The reference's builder puts a still thumbnail of the loaded
file beside the structure check. The preview gives the structure **a row of its
own** under the coordinates box instead (`.geomtext` / `.geomstage`, a named
block of `app.css`), the form's full width and directly above Constraints,
redrawn as the box is typed in, because that box is what it draws. A drawing is
fitted to the stage's shorter side, so the row's height (60% of the window, within
416–676px) is what sets the molecule's size. For a kind that optimizes (opt, opt_freq, ts_opt,
ts_opt_freq, the kinds the generator writes a `%geom` block for) the stage is
also where `%geom Constraints` are picked. A left drag that **starts on an
atom** collects the atoms it passes over (2 = bond, 3 = angle with the middle
atom as the vertex, 4 = dihedral), with the live value drawn as it goes, and
dragging back onto the previous atom takes the last one off. While the drag
is moving, an atom joins only when the pointer reaches the middle of its ball:
the balls overlap on screen, and a drag that took any ball it brushed picked up
the one in front of the atom aimed at. **Ctrl+click** is the second way, for
atoms one sweep cannot reach (behind another, across the molecule), and it is
a SELECTION, the way Ctrl+click is everywhere else: each click adds an atom,
numbered on the stage in order, a second click on one takes it out, and the
selection outlives the Ctrl key. The stage's one button names what it would
add — *Fix H4* for one atom, *Add angle H3–O1–O2* for three — and pressing it
(or Enter on the stage) adds it. The first version handed the selection over
when Ctrl was let go, and a hand that lets go of Ctrl between clicks lost
every atom but the first. A plain click starts again from that atom; Esc
clears. A drag from empty
space still turns the structure, and a click still selects, so an atom can be
fixed with *Fix C3*. This is the opt-in `onChain` of `MOL.mount`, so every
other stage is unchanged. The list under it names atoms the way the report
does (`C3–O7`, numbered from 1, while the `.inp` counts from 0) beside what the
coordinates measure now and an optional target. A row shows its atoms on the
stage, joined in order (`setHighlight`), because a ring alone would not say
which atom is the vertex. The drag is an addition, never the only way in: a
typed atom list adds the same constraint, and it is the **only** way when the
geometry is referenced from another calculation, which has no coordinates here
to pick on. Those rows are bare numbers, checked against the resolved geometry
when the run starts. A constraint naming an atom the coordinates do not have
is marked on its row and refused at Add, because ORCA's own answer
(`Could not make G matrix`) arrives as a FAILED, locked calculation.

A target is a geometry the user cannot see until the run reaches it, and
editing the coordinates by hand to show it moves one atom and strands the ones
bonded to it — turn a hydroxyl's O and its H is left where the O was. So a row
with a target carries **Apply**: the coordinates move to the target by one
rigid motion of the **smaller** side of the hinge bond, the way every
structure editor sets an internal coordinate. The group moves as a group, and
the end the drag started from does not matter. It stays narrow on purpose
(§17.4e's sibling rule — ORCAdesk reads structures, a molecular editor writes
them): no free moves, no force field, nothing relaxed afterwards. A ring bond
is refused with a sentence saying so and that the optimization will still get
there; an atom the motion lands on top of is reported, not refused. The
result is an edit OF the coordinates box — only the moved atoms' numbers
change, the user's spacing and comments stay — so Ctrl+Z there undoes it.

Targets that share atoms undo each other one Apply at a time: three angles
about one carbon set to 120° (making it planar) never read 120/120/120
together, because each turn opens one angle and closes its neighbour. Once
two rows have targets, **Apply all** sits beside *Add*. It takes the same
rigid turns in rounds until every target holds to 0.01° / 0.0001 Å — still
no free moves and no force field — and refuses, naming the coordinate
furthest off, a set that cannot hold together (three angles about one atom
past 360°). It is one edit of the box, so one Ctrl+Z undoes all of it.

**17.4n A running output follows the reader, not the run.** A cell's Output
pane is the last 260 lines of a running `.out`, re-read on every poll. It
follows the end only while the reader is **at** the end (within half a line).
Scrolling away from it holds the pane still, whether by wheel, scrollbar or
keys. Following resumes when the reader scrolls back down or presses
*↓ Latest*. The pane used to scroll every running job to the end on every
poll, and a running job is exactly when there is earlier output worth reading.
Holding still means holding the **line**, not the pixel. The tail is a sliding
window, so each read is laid over the last one (`stream.js` `slid` /
`paintLog`): lines that left the window come off the top, the last line is
rewritten if it grew, and new ones are appended. The first line on screen is
then put back at the offset it had. If that line has slid out of the window,
the pane shows the oldest line still held. If a read does not continue the last
one (a re-run rewrote the file), `scrollTop` is left as it was. The box's own
scroll anchoring is off (`overflow-anchor:none`), because two mechanisms that
pick an anchor by different rules fight over one `scrollTop`. Laying reads over
each other also keeps a selection through a poll, and lets `role="log"`
announce only the lines that were added. *↓ Latest* is the reference's own
label. There it is an always-present ghost button in the pane head; here it is
the stage pill (`.act`, an addition in the last named block of `app.css`). It
sits on the box's bottom-right corner, appears only while there is somewhere to
go, and counts what arrived meanwhile (*↓ Latest · 12 new*). Its corner is
measured inside the box's scrollbars, because one long ORCA line adds a
horizontal one. The box is focusable, so the keyboard can scroll it, and the
pill hands focus to it as it hides. A run that has just stopped is read once
more, because its last read can be up to a poll older than ORCA's closing
lines.

**17.4o An IRC is a path, so its cell draws the path.** A running IRC cell
used to fall through to the SCF chart and show the convergence of whichever
single SCF ran last — the one thing about an IRC nobody is asking. ORCA prints
a row per step with ΔE against the TS, so the cell draws exactly the picture
the finished run will have in the report: energy (linear, kcal/mol — an
energy, not a residual) against step, **backward to the left of the TS and
forward to the right**, the TS marked at the origin, a converged end ringed.
A walking direction's half of the axis has room ahead of it (twice its steps,
capped at MaxIter) so the path grows into the box rather than the box
re-scaling under every step. The key says, per direction, where it is (ΔE),
how far it has walked, and how far it still has to go — max|G| AND RMS(G),
each as a multiple of its own threshold, until it reads *converged*. Both,
because ORCA ends a direction only when both are under: max|G| alone read
"0.96× limit" on a direction that still had steps to walk.

**17.4p A push is a distance the reader can picture.** Stepping off a
saddle point - pushing the coordinates along an imaginary mode, then
re-optimizing - is done by hand because ORCA has no option for it. The
builder does it where the coordinates are made: **Load .hess…** beside *Load
.xyz…* opens a panel under the stage with the mode (every vibration, the first
imaginary one preselected, imaginary ones marked), a direction (+ / −) and a
distance. The distance is what the **most-moved atom** travels, in Å, as a
slider and a number side by side (the slider for feel, the number for an exact
value) - a normal mode's vector is normalized over every atom, so its own
scale means nothing to the hand moving it, while "H12 moves 0.20 Å" does. The
stage previews the pushed structure as the controls move, and nothing touches
the coordinates until *Apply*, which is an edit of the box (Ctrl+Z undoes it,
as with a constraint's Apply) and resets the distance to 0 so a second push is
from the new coordinates, not a double one. A small imaginary mode says, under
the controls, that it is often noise a tighter optimization removes - the
push is offered, not urged. It lives in the builder rather than the report
because a push is the first step of a NEW calculation, and the builder is
where one is made (a deliberate move from the report, at the user's call).

**17.4q A vibration is judged by watching it.** A wavenumber says how stiff
a mode is, not what it does, and the question a frequency result is opened
for — is this imaginary mode the reaction coordinate, or a methyl rotor that
a tighter optimization would remove? — is answered by seeing the atoms move.
The spectrum is the IR spectrum: a band stands at its IR intensity (ORCA's
*IR SPECTRUM* table) against the strongest one, and its label gives the
km/mol. An imaginary mode, which ORCA lists without an intensity, is drawn
full height in red all the same, since it is the band a TS result is opened
for; an output without the table draws every band one height, as before.
So a band on the vibrational spectrum is also a control: **clicking it, or
Enter on the keyboard cursor, shows that mode moving on the Visual panel's
Structure tile**, and the imaginary modes — the ones people come for, on the
crowded left edge of the axis — get a button each under the spectrum. That
tile because it draws the geometry the frequencies were computed at; a mode
picked while a trajectory or a surface is on the stage switches back to it,
and leaving the tile stops the mode. A bar above the panel (not on it — its corners already hold the atom readout, the
Display pills, the axis marker and the legend) is the whole control: the
mode, ‹ › to the neighbouring ones, Play / Pause, the **swing** (how far the
most-moved atom travels either way, in Å — the push panel's rule, §17.4p, for
the same reason), and *Show structure*, which is also Esc. The band that is
moving stays marked. The motion is one period for every mode (a real one is
picoseconds, so any rate is a convention, and one rate compares shapes); the
view is not refitted per frame (§17.4e), and a turn or a zoom made while it
plays is kept. **Paused, the arrows replace the motion**: each atom's
displacement, the longest a fixed 1 Å so they read as direction rather than
distance. This is the one thing in the window that animates as content, so
it starts only when asked, stops when the stage leaves the document or
scrolls out of view, and opens **held still, with arrows**, when the system
asks for reduced motion — Play stays, because pressing it is asking (D53).
The vectors are matched to the report's numbering **by wavenumber**: the
`.hess` beside the result first, the `.out`'s own NORMAL MODES table when it
is absent or is another Hessian's (an OptTS's first one), and the bar says
which file it read. A small imaginary mode carries the push panel's note
that it is often noise.

**17.4r A scan is picked where a constraint is, and read as a curve.** A
relaxed surface scan names its coordinate with the same atoms a constraint
does, so it is picked the same way, on the same stage, with no second picker.
The constraint panel's heading carries a `.seg`, **Constrain | Scan**
(`.conshead`, an addition in the last named block of `app.css`), which says
what the next pick defines. A drag, a Ctrl+click selection with the stage's
button (now *Scan bond O1–H2*) or a typed atom list all go to the scan in
*Scan* mode. One atom has nothing to walk along, so the button reads *Pick 2–4
atoms* and stays disabled. Both lists stay on screen in either mode, because a
scan runs **with** the constraints (measured: they share the `%geom` block and
both act). The switch picks what a gesture does, not which half exists. The
builder offers ONE coordinate: a new pick replaces it, and a toast says so.
ORCA takes three, but a grid is a surface, and this report does not draw
surfaces.

The scan's row is a constraint row with **from / to / in N steps** where the
target and *Apply* were. *From* opens at what the coordinates measure now, so
the first point is the structure on the stage. A referenced geometry has
nothing to measure, so it opens empty. Under the three numbers a second line
says what they imply (*0.049 Å per step · 7 constrained optimizations*),
because "0.958 to 1.25 in 7" is a spacing nobody sees at a glance. There is no
*Apply*: ORCA walks the coordinate itself. An unfinished row is refused at Add
with the field it lacks. The backend would drop it at the boundary and queue
a plain optimization in its place, so *Preview* warns that the input has no
scan yet.

Only an optimization scans (measured on 6.1.1: a single point ignores the
block, and Opt + Freq computes frequencies after the first point only). On the
other optimizing kinds *Scan* is greyed out and its title says why. It is not
hidden, because a missing control reads as a missing feature, not as a rule.

The report reads the scan as **ΔE (kcal/mol, against the lowest point) along
the coordinate**, in the coordinate's own unit (Å or °). The highest point is
marked and named in the section's sentence, since a scan is most often run to
find it. The table under the chart lists every point. A scan that stopped
part-way says *3 of 5 points* and that the rest are missing, never presenting
the finished part as the whole. A 2-D scan is drawn as its grid, never as a
line, because one line through a grid would invent neighbours. The first
coordinate runs across and the second up. Each point is a mark joined to
nothing, and its **area** is its ΔE, so energies are compared by size. The
highest point is red (`--surf-hi`) and the lowest blue (`--surf-lo`, ringed,
since it is also the smallest mark). The key under the plot names both with
their ΔE and coordinates, so colour is never the only cue. A planned point the
run never reached is a small ring, so a stopped scan shows its hole instead of
passing for a smaller grid. The point is read on hover, and the arrow keys step
along a row or column. A 3-D or larger scan is listed, not drawn: the page has
two axes. Each point's optimized structure
is the *Scan points* set in Visual, `Step 1 … Step n` in scan order, each
frame carrying its energy.

**17.4s One builder, three backends; a backend that cannot run says why.**
The builder's first control is the reference's own backend picker: `DFT |
MLIP | CREST`, then the `.seg .sep` divider and `Guided | .inp text`, which
exist only for DFT (the reference hides them the same way). Switching backend
keeps what the three share — name, charge, multiplicity, the coordinates box
with its stage and structure check, or the reference — and swaps only the block
under them; each backend's own settings survive a switch away and back. An
MLIP or CREST calculation is not an ORCA one, so none of the ORCA-only blocks
are drawn for it (method, push along a mode, Preview .inp), rather than drawn
and ignored. The stage's **Constraints** group is the exception for CREST,
which runs them (its `--cinp` file): the same picks (drag or Ctrl+click 2–4
atoms, one atom to fix, the typed *Add*), the same rows with an optional
target and their *Apply*, saved as the same `geom_constraints` — but no
*Constrain | Scan* toggle (CREST does not scan), and the hint adds what is
CREST's own: fixed atoms keep their distances to each other, not their
places, so at least two are needed (Add refuses one, as does the backend),
and *Constrained atoms out of the RMSD* (`--subrmsd`) usually goes with them.
The CREST form's Advanced section carries *Constraint force constant
(Eh/Bohr²)* (0.5, as CREST's own `--constrain` writes): a `.field` that is
**disabled, its reason on hover and in the hint below it**, when the CREST
that will run cannot take the line (the 3.0.2 release crashes on it;
`CrestDistroPayload.cinp_extended`). A capability the backend lacks is shown
locked, never hidden, so the form reads the same on every install; the same
rule draws *GFN2-xTB // GFN-FF — not in CREST 3* as a disabled option of the
Method select.
An MLIP calculation has no constraints. D41 as amended decides the picker, from the polled
backend status: a backend never set up has **no button**, only *+ More
backends…*, which opens Settings; one set up but not ready (an environment that
fails to import, no WSL distribution with CREST) keeps its button and opens
**locked** — its block at the locked opacity, every control and *Add*
disabled, the reason above it in a `.finding` with *Open Settings* — and
unlocks by itself when the probe turns ready. Add refuses on its own as well,
since a disabled button is not the guard. A backend stays visible while the
form is on it or the queue holds one of its calculations, so *edit* on such a
row always opens on its own form. Two content rules ride along: the device
choice states what *CPU threads* means on that device (the worker's cap always,
the queue's charge only off the GPU, D2), and CREST's MD/MTD numbers and
toggles sit in a closed *Advanced* section that opens by itself when one of
them is set, so a stored knob is never behind a closed door. In the report an
MLIP or CREST result has no Input section and no Natural orbitals — it was
handed no `.inp` and left no wavefunction; the Input pane used to show an ORCA
input generated for a job ORCA never ran.

**17.4t In `.inp text` mode the box shows the geometry that runs.** A
hand-written input runs verbatim, so the coordinates box beside it is one of
three things, and the line under the box (`data-x="rawgeom"`) says which:
the text's own `* xyz` block (then the box IS that block: an edit, a paste, a
*Load .xyz* or a pushed mode in the box rewrites the block's lines, an edit in
the text redraws the box, and the box opens on the block rather than on the
calculation's stored coordinates), what fills `{{GEOMETRY}}`, or — with
neither, an `* xyzfile` input — unused, said as a `.finding warn`. It used to
be the third silently whenever the text had a block: a copied OptTS with IRC
endpoints pasted into the box ran its own TS guess, twice, and both
"endpoint" optimizations reached the same product. The backend says the same
thing where the builder is not the author (`raw_geometry_mismatch`): on add or
edit as the result's `warning`, and in the log before launch.

**17.4u Another program's numbers are labelled as its own.** A `! NBO` run
leaves the NBO program's analysis in the `.out`; the report reads it into three
sections whose titles begin `NBO ·` and whose meta names the version and says
*from the output* — natural populations (the Lewis/non-Lewis accounting as
`.kvline`s over the per-atom table), donor → acceptor, bond orbitals — kept
apart from ORCAdesk's own *Natural orbitals*, whose numbers are a different
implementation and must never be read as these (P5, A25). That section and the
*Reaction profile* are hidden for now (`SHOW_OWN_NBO` / `SHOW_PROFILE` in
`results.js`, 2026-11-04): their code and backend slots stay, and turning one
back on is flipping its switch. When the own analysis returns it goes below
these. Long lists open on what a
reader came for — the 20 largest E(2) rows, the Lewis orbitals and antibonds —
and the rest wait under *Show all*, with a `.hint` saying how many. An NBO is
named once, `BD*(1) C1–C2`, in both tables, with NBO's own number in front so
a donor in one table is found in the other — and in the Visual panel's **NBOs**
tile, whose list is the same orbitals under the same names, the occupancy in
the column where an MO shows its energy (the number that tells a Lewis orbital
from an antibond). ORCA's own `%loc` printout follows
the same rule as *Localized orbitals* (meta: method, MO range, *from the
output*): the counts as `.kvline`s, then one row per localized orbital with
each atom's share muted beside it, so the numbers that move along a reaction
path sit where the eye compares them.

**17.4v Closing does not throw work away.** A list a window builds up (the
rail's *Opened files*) is kept across launches until the user closes a row.
A new calculation's form is a draft: closing it keeps it, the editbar says so
(*draft · kept when you close it*), and *Start over* (§13.1 confirm) is the
one way to throw it away.
The Settings sheet, closed with unsaved edits, asks once (§13.1, three-way):
*Save changes to settings?*, with [Cancel] [Discard] (danger) [Save] (primary,
focused). Escape or a click outside the prompt is Cancel and never Discard
(D61). Changing a field and changing it back is not "unsaved".

**17.4w Third-party terms are said where the choice is made.** What a click
downloads, and the license it comes under, is stated beside that click, not
only in a README. The MACE model picker suffixes every option with the license
of its weights (`MACE-OFF medium · ASL`) and keeps one `.hint` under the row
(`data-x="licnote"`) that names the selected model's license, what it allows
(*ASL — academic, non-commercial use only*) and where its full text is, as plain
text: the page has no way to open a link outside the app, and a link inside it
would navigate the window away. A model no license group lists says so rather
than showing nothing. The queue window's MLIP set editor adds the same sentence
to its hint for the model its text names (the default's, when it names none).
Settings → MLIP environments and Settings → CREST each carry one `.hint` under
their buttons naming what *Create* / *Install CREST* fetches, from where and
under which licenses — for CREST, that the download is a pinned release checked
against its SHA-256. Existing classes only; no new rule in `app.css`.

**17.4x The 2D drawing is the 3D view, flattened — and an add-on says what it
needs.** Results › Visual's strip ends with a **2D structure** tile whenever it
holds a structure (`.viztype`, `d` = *beside the 3D view*). It is a way of
looking at the structure on the stage, not a kind of its own: pressed, the
stage splits (`.vizstage[data-split]`) — the 3D view keeps the left half and
`.viz2d` takes the right; below the panel's own 1500px breakpoint the halves
stack, 3D on top. The structure is whichever the stage holds (the Structure
tile's geometry, or the frame picked in a set, whose list still steps through
frames); from a surface tile it takes the first structure tile's atoms back.
The stage's overlays (*Open in viewer* / *Display*, the element key, the
caption) move with the 3D half. Any other tile closes the split.
The drawing follows every drag (one redraw per animation frame) in exactly the
3D view's orientation — x right, y up, never mirrored — on a **white paper**
(`.d2paper`) in both themes: it is the figure as it will be saved and pasted,
black ink on white like ChemDraw's, so it is data, not chrome (D1, as the ESP
ramp's literal colours are). The preview's frame is the structure's bounding
circle, so the drawing does not jump in scale while it turns; what is saved is
cropped to the ink. Under the paper, `.d2bar`: *Wedge above* — a slider
(5–60°, default 25°, §11.20: cheap to move, it only re-draws) with a fixed-width
mono readout; *All H*; *Charge* (the calculation's own; a change re-perceives);
then *Copy image* (the one non-ghost button) · *Save SVG* · *Save PNG* ·
*Save MOL*. One `.d2note` line says where the bond orders came from (*Bond
orders by RDKit 2026.09.2, for the calculation's charge.*) and turns `--warn`
when it has to admit something: *Connectivity only — every bond single.* with
RDKit's reason, a multiplicity RDKit's Lewis structure disagrees with, or *A
bond at a stereocentre points almost straight at you — turn the structure a
little so the wedges read unambiguously.*
Not set up, the tile is still there (D41): the paper gives way to the card and
one sentence says what is needed — *2D drawing is an add-on: it needs RDKit, in
a Python environment of its own (ORCAdesk can create one — about 50 MB from
PyPI).* — with **Set up in Settings**; an environment that fails its probe says
why instead (*The 2D-drawing environment does not work: …*). The controls are
disabled meanwhile, and the pane notices a set-up finishing without a reload.
Settings carries a **2D drawing** card after CREST: a sentence on what it is,
one `.envrow` (*RDKit environment*, `RDKit x · Python y`, the interpreter in
mono, D42's badge: ready / checking / unusable, *Remove*), then *Create*
(primary; disabled with the reason in its tooltip when an environment already
works, while base Pythons are being found, or when none in RDKit's 3.9–3.15
window exists) · *Register an existing one…* · *Re-check* · *Cancel* (while
building), and the §17.4w `.hint`: what *Create* downloads (RDKit BSD-3-Clause,
NumPy BSD-3-Clause, Pillow MIT-CMU, from PyPI, ~50 MB). *Remove* confirms and
deletes nothing from disk. Not a reference landmark: the mockup has no 2D tile
and no such card, so no uispec role moves; the new rules are one named block at
the bottom of `app.css`.

**The orbital overlay.** Under `.d2bar` a second `.d2bar` row carries
**Orbital** — a `.d2sel` select: *None* (default), then, per manifold (α / β
optgroups for an open-shell run), *near the frontier* (HOMO−4 … LUMO+4) and
*other orbitals*, each named exactly as the Orbitals tile names it (its own
picks: `HOMO (81, −8.37 eV)`) — and **Lobes from** *n* % (a `.d2num`,
default 2: an atom with less of the orbital gets no lobe). Picked, the
orbital is drawn on the paper **under the bonds**, following every turn: per
atom a pair of lobes along its p direction, the + side in `--orb-pos`, the
other in `--orb-neg` (fills mixed toward white and opaque, stroked in the
token: the front lobe hides the back one, so a π orbital seen face-on reads as
one disc of the front lobe's colour); an s-dominated atom a circle in its
sign's colour; a d-block atom that is mostly d a dashed grey ring. Sizes go
as √share, the orbital's largest share drawn about a bond length. The
`.d2note` adds one sentence: *HOMO (−8.37 eV): lobes on atoms holding at least
2 % of the orbital, sized by √share (the largest share's lobes 1.3 Å long);
blue and red are its two phases (which is which is arbitrary for the orbital as
a whole). Copy / Save SVG / PNG include the lobes; Save MOL leaves them out.*
— and *Reading the wavefunction for HOMO…* while it loads. Disabled states
say why, in the select's tooltip and (with an orbital picked) the note in
`--warn`: no `.gbw` (*No .gbw beside this result — there is no wavefunction to
plot from.*); a frame or conformer on the stage (*Orbitals belong to the run's
final geometry — open 2D structure from the Structure tile to draw one on
it.*); a wavefunction whose geometry is not the one drawn. With the add-on not
set up the pane is as before (the paper says what is needed) and the select is
disabled with the rest. One new rule (`.d2sel`) in a named block at the bottom
of `app.css`; not a reference landmark.

**17.4y A compute device says what it prefers, and a spot VM gives work back.**
Each row of Settings › *Compute devices* (a `.envrow.devrow`) has, under
*Use / at once / cores / memory (MB)*, a **prefers** line — the word
(`.devpref`, muted 12px) followed by one checkbox per ORCA kind in the
Guided builder's own words and order (`NB.build.KINDS`: Optimization …
Single point, General); none ticked = no preference — and below it the
checkbox **Spot / preemptible VM**, which, when ticked, adds **re-queue
after (min)** (a number, 5). Each control applies at once (no Save), as the
row's others do, and says on hover what it does: a preference, not a limit —
with nothing of those kinds ready the device takes any other ORCA
calculation; single points (SP, TD-DFT, NMR) never move the structure, so a
spot VM loses only their own run; a job on
a spot VM that stays silent that many minutes *in a row*, or dies with no word
from ORCA, goes back to the queue. A row given back reads
*Re-queued: the spot device stopped answering.* (its message until it starts
again) with a `warn` log line — never FAILED, which would lock it (P24). No
new tokens; one new rule (`.devrow .ctl .devpref`) in the remote-devices
block of `app.css`, the rest laid out by the existing `.devrow .ctl` rules;
not a reference landmark. A Linux device with CREST adds *CREST* to its
status line (*CREST (no constraint force constant)* when its build cannot
take that line) and one more **prefers** box, *CREST conformer search*,
after the ORCA kinds; a device without CREST does not show the box (unless a
stored preference already names it). No new rule.

**17.5 It is the only front-end.** The classic window and the switch between
the two are removed (unreleased), and with them the Settings sheet's Interface
card (the switch) and Appearance card (the Liquid-Glass style and its intensity
slider, which only ever configured the classic window — §16). The reference
still draws the Appearance card; its `.slider` is a listed, deliberate absence
(`OUTSTANDING` in `tests/test_next_ui.py`, `REMOVED` in `tools/uidiff.py`).
Light/dark stays the top bar's toggle. A missing `web/next/index.html` is
reported by `_check_ui_loaded` rather than left as a blank window.

**17.6 The queue is a window of its own, drawn as a graph.** The user asked for
the queue and Jobs/Results to be two windows. Both load `web/next/index.html`
over one Bridge and one store; the URL says which page it is — `?win=queue` is
the queue editor (`qed.js`, `#qed`), anything else the main window
(`html[data-win]`; a missing query is the main window, never an empty page).
The queue window draws no stream, report or rail and reads no log or output
files; the sheet, modal, toasts and the builder are shared.

The layout and interactions are the approved design canvas's (the "ORCAdesk
Queue" prototype); its sample data and template texts are not the spec. Three
columns:

- **Library** (left) — the inp sets in three groups, **ORCA levels**,
  **MLIP** and **CREST**, each with a *+ New* beside its name (a CREST set is
  `key value` lines like an MLIP one -- method, solvent, ewin, threads,
  preset, nci, solvent_model, env -- and a block made from it is a
  conformer search, `crest_conf`, labelled *Conformers*; a text is read as
  CREST settings only with no line an ORCA input starts with `!`, `%` or `*`
  and none naming an MLIP backend or model). An ORCA set is a **level of
  theory, not a job**: a whole input whose `!` line holds `{{JOB}}` where the
  job keyword goes, so one level serves every step of a row and the library
  no longer repeats a level once per job type (the user asked for it: the job
  keyword follows the block's Type, chosen like the Guided builder's). A set
  is dragged onto a block of its group to replace that block's **level** (its
  Type stays), or onto the ghost slot to add the next step; a click applies it
  to the selected block when the group matches and the block can still
  change, and otherwise opens the set in the **set editor** (marked in the
  library by a left bar). The first replacement is confirmed (§13.1) and said
  to be asked once; later ones apply at once. A set dropped on, or clicked
  onto, a **finished or failed** block is an overwrite and is asked every
  time: the warning names it (and which members failed, in a mixed stack),
  says its output is overwritten, and says what it blocked goes back to
  pending. *Overwrite* (danger) puts the set on it and returns it to pending
  (P24's one way back). Undo restores it until it starts. Delete on a focused set removes
  it from the library (confirmed). Under them, **Workflows**: copied
  workflows kept by name (*+ Save copied*, locked with its reason until
  something is copied; *Save as workflow…* in a block's inspector). A click
  puts one into the selected rows (their empty ones) or after the selected
  block, and with nothing to put it on makes it the copied workflow; a drag
  puts it on a + slot; Delete removes it (confirmed; the rows it was put on
  keep their steps). A dropped `.xyz` has no recipe to choose (see *Files
  dropped* below).
- **Lanes** (middle) — one per system, top to bottom in run order; a standalone
  calculation is a one-node lane. A block (140 x 64, 176px a column, 104px a
  row) carries its state dot (§12 colours; *waiting* is an amber ring) — its
  **border takes the dot's colour** (pending the ring's grey, done green,
  running amber with a soft glow, failed red, blocked / cancelled faint) —
  per calculation: a stack's front card is its first member's, each back
  card the next one's, while the dot is the block's overall state — so a
  running queue reads from across the canvas, and a selected block is an
  outer ring that never hides it — its name (too long for the card, the
  system part gives way to `…` and the job part stays: `glyc…_Freq`; the
  whole name and `kind / set` on hover), its run
  order, and `kind / set` (amber with `*` once its template differs from the
  set; a block made from a set before sets were levels is compared by what
  its text converts to, so the conversion alone never marks it). A **stack** (several calculations sharing a node, e.g. an SP after an
  IRC is `_F` and `_B`) is drawn as one card per calculation (the front one
  and a back card 5px behind it for each other member, at most three — past
  that the count tells; each card's border is its calculation's state, and
  past three the last back card stands for all the rest, in their most
  pressing state, so a few finished members never turn the whole stack
  green), shows its count bottom right, at the end of the
  `kind / set` line — never top right, where a block shows its run order —
  and unfolds a member list; once some of its members are **filtered** the
  count reads `passed/total` (`3/10`), and a filtered member is faint in the
  list with its reason on hover. A single block that stands for every
  conformer a CREST search will find (`conf:all`, below) carries the same
  badge reading `all`. A step after a CREST search is a
  **conformer stack**: one member per conformer, `_c1` … `_cK` (`_c1` the
  lowest, as the search's `{name}_c1.xyz`), each taking its structure by
  reference to that conformer (`ref_point` `conf:<k>`). **Edges**
  are solid when the next step runs by itself and dashed amber when it waits
  for approval, and meet a block at its vertical middle. The edge into a
  calculation may carry an **energy condition** (`filter_de`: pass within
  ΔE kcal/mol of the lowest; `filter_top`: pass the N lowest) -- the group
  is the stack it leads into; ΔE is from the CREST search's lowest
  conformer for conformer members, else from the stack's lowest parent
  energy once every member's parent has finished; what fails it ends
  filtered (§12), and so does what hangs off that. It is drawn as a small
  pill on the block the edge leads into, top-left above the card (`ΔE ≤ 3`,
  `top 5`, `ΔE ≤ 3 · top 5`, `*` when the members disagree; the 36px gap
  between columns has no room for words), neutral grey on the canvas colour
  -- never the gate's dash or amber, since the two are independent and can
  both be on one edge -- and a click on it selects the edge. A block hangs off its
  `after`, else the calculation its geometry is taken from: standalone
  calculations tied that way (a TS with the Freq and IRC built from it) share
  one lane, laid out by the slot rule below parent first (a child queued
  before what it hangs off is still drawn to its right), and adding a step to
  any of them turns the whole chain into one system. When the chain's
  calculations run at different charges or multiplicities that is asked
  first: the system takes the selected block's, and each calculation keeps
  its own. Setting a gate on an edge drawn from
  a geometry reference makes that reference the block's `after` first. The
  **ghost +** after the selected block — any block, finished
  or failed included, as the design has it: a next step after a done OptTS or
  IRC is how a follow-up is queued — takes a dropped set (a level runs the
  natural next step: Optimization at a row's start, Frequencies after an
  Opt, TS frequencies after an OptTS, IRC after a TS frequency, Single point
  after an IRC and after anything else; the Type changes it) or a dropped
  saved workflow. **After an IRC, every add asks which structure it starts
  from** (the IRC picker): once the IRC has run, one compact energy profile
  of its whole path — backward end → TS → forward end, ORCA's
  `_IRC_Full_trj.xyz` (else the backward path reversed and the forward one),
  an 88px graph with B, F and TS marked — on which a point is clicked,
  dragged or moved with the arrow keys, or **named by its number** (its frame
  in `_IRC_Full_trj.xyz`, 1 to N; a number past the ends is clamped on Enter
  or leaving the field), the structure there drawn small
  beside the energy (ΔE from the TS). **Choosing and confirming are
  separate**: a segmented *Start from — One point | Both ends* (Both ends
  preselected when adding; picking a point switches to One point; both ends
  are then marked on the graph), and ONE confirm button that says what it
  will do — *Add at both ends*, *Add at point 50*, *Add at the forward end*,
  *Add at the TS (point 35)* (*Use …* when a block's geometry is changed).
  One point at an end is taken by reference (`_F` / `_B`, read from
  `_IRC_F/_B.xyz` when the step runs), any other point with its coordinates
  copied in (`_P<k>`); both ends make the `_F`/`_B` stack. Left / Right step
  the point wherever the keyboard is in the window (Shift: 10); the number
  field has no spin arrows. Before the IRC
  has run there is no path: *Start from — Both ends | Forward end | Backward
  end*, by reference. **Pressed, it opens the add
  menu** beside it (the stack list's popover recipe, drawn over the canvas
  so a redraw of the lanes leaves it open): the **level** (the one the block
  before runs at — a Freq has to match its Opt — else the one last used from
  the menu, else the first), the natural next step as the primary button
  (Enter), every other Type the level can run (O / T / F / I / S answer
  Optimization, TS optimization, Frequencies — TS frequencies after an
  OptTS —, IRC, Single point), an MLIP pre-optimization per MLIP set, a
  conformer search per CREST set (not before a block: what follows a search
  is the conformer stack its picker makes), the
  copied and the saved workflows (locked after a stack, with the reason;
  after an IRC or a CREST search the picker asks first, as for a step), and **Manual build…** (M).
  **After a CREST search, every add asks how many conformers it runs on**
  (the conformer picker, the IRC picker's recipe: choosing and confirming
  separate): *conformers* (**empty by default** -- nothing is assumed for
  the user: empty is every conformer the search found, placeholder `all 38`;
  a number is 1 to the ensemble's size. Before the search has run there is no
  upper bound and empty is **every conformer it finds**: the field reads
  *all it finds*, the button *Add a single point for every conformer it
  finds* (*Paste on every conformer it finds* for a workflow), and the note
  says that one step stands for them until the search has finished and then
  becomes one per conformer with everything after it; a typed number is a
  stack of that many, a member past the ensemble it finds ending filtered.
  After a search that has run but listed no conformers a number has to be
  typed: *Type how many conformers*, disabled, until one is) and an optional
  *pass on ΔE ≤ … kcal/mol* (empty: all pass), and ONE confirm button that
  says what it will do -- *Add 38 single points (all 38 conformers)*,
  *Add 5 single points (conformers 1–5)*, *Paste on 5 conformers (1–5)* for
  a workflow, *Use conformer 3* for a block's geometry. Once the search has
  run, its ensemble is listed small (`c3  1.24`, ΔE from the lowest, from
  `parse_calc_output`'s `conformers`), the members to be added marked and
  those the ΔE window would filter struck through. The result is one
  undoable batch: K calculations on one node, after the search, each by
  reference to its conformer, the window on their incoming edge. A workflow
  after a search is pasted once per conformer, each step's K copies
  stacked, the window on the edge into its first steps. A step after a
  conformer stack is one per member (`_c3` kept), as after any stack.
  **"Every conformer it finds"** adds ONE block (`ref_point` `conf:all`, named
  as the stack's base without the `_c<k>` tail) — a workflow is pasted once,
  its first steps such blocks and the rest hanging off them. The block shows
  its count as **`all`** where a stack shows its members (bottom right, the
  same badge), and its inspector says: *Stands for every conformer X finds:
  when the search has finished it becomes one single point per conformer
  (`…_c1`, `…_c2` …, the lowest first), and so does everything after it. It
  never runs itself, and undo does not bring it back.* (once the search has
  finished but the queue has not run since: *…it becomes … when the queue
  next runs*). When the search is done the queue swaps it, and every block
  after it, for a stack of one per conformer — new rows the next poll draws;
  an undo entry made before that cannot take the block back. Escape or a click elsewhere closes it
  and leaves the selection as it was; what was chosen is one undoable batch,
  like a dropped set. **A step before a block**: the selected block that
  can still change shows a small round dashed **+** on its incoming side (in
  the gap on its arrow, or on its left edge first in a row). Pressed, the
  same menu opens as *Before X* — a level and its step only (Optimization
  suggested), no workflow or manual build — and a level can be dropped on
  it. The new step takes the block's place in the chain: what it ran after,
  the approval gate on that connection, the structure it started from (an
  IRC end included), its charge and multiplicity; the block then runs on its
  result. One per member of a stack (`_F` / `_B` kept). The block and what
  hangs off it move one column right (the whole row from its column when
  that would land on another block); one undoable batch. **Manual
  build** is the Guided builder, seeded for that place, whose
  calculation lands in that lane, column and row. The builder is the main
  window's *New calculation* as it was -- the same cell, title and footer,
  at the reference's cell width (1040px) in a stream-like column drawn over
  the lanes and the inspector (never squeezed into the inspector), the
  library beside it; where it goes is said beside the title. A step it
  makes is named as the editor names one (`row_Opt`, `row_Opt2` ...), and
  the name follows the Type until it is typed over. A standalone block first
  becomes a one-node system named after it (`place_calc`: its place in the
  layout changes, nothing it ran — P24). A pending block below a failed or
  blocked one is drawn blocked at once (the design's derived state), not only
  after a run says so; one below a filtered one is drawn filtered the same
  way. An empty system shows its first slot; an empty queue
  says how a row starts. **Ctrl/Shift+click on row names selects several
  rows**: the inspector then sets one charge and multiplicity on all of them
  (a row that has started keeps its own) and puts a workflow into every
  empty one, as Ctrl+V or a saved workflow's click does; Escape clears. A
  system's name over its row (or *Structures…* on a
  block) selects its **structures panel** -- every row has one: name,
  charge, multiplicity, its structures (add from a file or paste coordinates,
  *View* on a stage with the structure check, remove; a calculation keeps the
  coordinates it was given); *+ system* asks for coordinates to paste or
  `.xyz` files (several at once, each a row by the drop's rules below). *Clear finished* (top bar) takes the done and failed calculations off
  the list -- not cancelled ones, which run again, nor one a calculation yet
  to run starts from -- with any row it empties; not undoable. **While a run
  is going, every add outside the builder** (a set on the + slot, a paste, a
  row copy, *Duplicate*) asks before writing over a folder
  that holds output (`existing_outputs`), as the builder's own add does; the
  names those adds make step around such folders in the first place. A
  copied workflow carries a level block's options and is refused on a
  structure with fewer atoms than its constraints name. A *Manual build*
  can start from a level (*Level* beside *Guided* / *.inp text*), and offers
  a draft kept from an earlier new calculation (*Use the draft*: the draft's
  kind and settings at this place). Removing the last block of a system removes the
  system with it (one undoable batch). The **grip** moves a lane (drag), copies it
  (Ctrl-drop, or a click: copy below) and answers Alt+↑/↓, keeping the
  keyboard on the moved row's grip; *+ system* starts a
  lane from an `.xyz`. Top bar: Clear all, Cancel, Stop after current, Run
  queue (the main window's strip has the last three), and one line of the worst warning (duplicate
  or empty names, a Freq at another level than its Opt, a lane with no
  structure, a template without `{{GEOMETRY}}`; the rest in its tooltip;
  levels are compared with `{{JOB}}` and the job keywords left out).
  **Several blocks of one row** are selected by a rubber band dragged from
  the empty canvas (never from a grip or a set, whose drags are the
  browser's) or by Ctrl/Shift+click; blocks of other rows are left out and
  the hint says so; Escape clears. A band over a single block selects it
  **whole**. **Ctrl+C** (or *Copy workflow* in the
  inspector; *Copy the row*, or Ctrl+C on a focused grip, for the whole row)
  copies a **workflow** — per block its type, its level (set name and text,
  or the Guided config), its gate, and its upstream and geometry source when
  they lie inside the copy (a block whose parent or geometry lies outside
  takes the target's structure), at its place relative to the copy. It is the
  editor's own clipboard, mirrored in the UI state (`queue.workflow`) so it
  outlives a reload, never queue state. **Ctrl+V** (or *Paste workflow*, top
  bar and inspector) pastes it after the selected block, as the first steps
  of the selected empty row(s), or — nothing selected — onto new rows from picked `.xyz` files
  (one row per file, named after it; steps `<file>-<step>`; a name in use in
  an existing row takes `-2`, `-3`; a file whose name ORCA refuses or whose
  row exists is listed with the reason, never renamed). One batch, Ctrl+Z
  undoes it. A stack takes no paste after it (it has several ends); after an
  IRC the IRC picker asks which structure(s), and the workflow is pasted once
  per structure picked (`_F` / `_B`, each step's two copies stacked); after a
  CREST search the conformer picker asks how many (`_c1` … `_cK`, or, before
  the search has run, empty: once, on blocks that stand for every conformer
  it finds). A copied
  workflow carries the energy condition of an edge inside the copy.
- **Names** — a step the editor makes is `<system>_<Job>`: the job as ORCA's
  keyword (`{{JOB}}`) reads it — `_Opt`, `_OptTS`, `_FreqTS`, `_IRC`, `_SP`,
  `_Preopt` (MLIP), `_CREST` (a conformer search) — a taken one numbered
  (`_SP2`), a stack's members tailed `_F` / `_B` / `_P<k>` / `_c<k>` (the
  k-th conformer), a copy `<name>_copy`. Older hyphen names still read
  as stacks. A redraw (a poll while the queue runs) waits while a mouse button
  is down, so a click is never lost to the block being redrawn under it.
- **Inspector** (right) — for a block: the system name (renames the lane:
  every calculation that has not run follows, its folder too; one that has
  run keeps its name and the folder it ran in, and the tooltip says so once
  the lane has started), charge
  and multiplicity (the system's; a calculation set apart shows its own, says
  so, and those fields then change it alone — a system's recharge moves only
  the calculations that had its old value), the geometry (the system's structures, or any ancestor's
  output along the `after` chain — every member of an ancestor stack, an IRC's
  two ends and *a point on its path…*, which opens the IRC picker; never an
  IRC's bare final geometry, which measured as the TS it started from; a
  CREST search's lowest conformer and *a conformer…*, which asks its number), the template, and *Save as* a new set of the kind its text is. A level's
  block also has its **Type** (Optimization, TS optimization, Frequencies, TS
  frequencies, Opt + Freq, TS Opt + Freq, IRC, Single point, NMR — the kinds
  the backend fills `{{JOB}}` for, in the Guided builder's words), editable
  while the block is (a stack whose members are not all editable has its
  Type and template read-only), which changes only that block (a block still named
  after its old type takes the new one's name when nothing refers to it),
  and a read-only **rendered input** for that Type (`build_inp_preview`, the
  renderer the engine runs). A block whose text names its own job (no
  `{{JOB}}`, made before levels) runs as written and says so, and keeps *Edit
  in the builder* (as `.inp` text) -- a text block can always go back to the
  builder. A block the Guided builder made, or a pasted text that is no level,
  shows its generated input read-only
  and *Edit in the builder* (an MLIP or CREST block: the settings it runs, in
  `key value` lines, never an ORCA input it does not run -- for CREST every
  option that is not StepConfig's default, the builder's search speed, NCI,
  solvent model, MD / MTD numbers and toggles included). A block made from
  an **MLIP or CREST set** shows the set's lines (editable while the block
  is) and its own *Edit in the builder*: the Guided builder in that
  backend's form, seeded from the block's whole config (the form holds more
  than a set's few keys can say). Saved there, the block keeps its place in
  the graph and is **no longer the set's**: its `set_name` and text go and it
  reads `Conformers / guided`, never the set's name with a `*` -- a `*` says
  "this set's text, edited", and a builder config is not a text the set
  could be compared with. That save, and every builder save of an MLIP or
  CREST block, is one `update_calc` patch through the undoable batch (a
  rename in the form is left to `update_calc`, which carries the
  references along). A level's block
  has **its own options** (calc.config under StepConfig's names, merged into
  the level by the backend): *Edit options in the builder* opens the builder
  in **Level mode** -- the Type (the template kinds), geometry, the stage
  with constraints / scan / *Apply all* / push along a mode / the structure
  check, and the per-kind blocks (MaxIter, Thermochemistry, IRC with
  *Parent's Hessian (auto)* as its default, NMR J, TD-DFT, NEB), extra
  keywords, SCF tier, solvation; in place of Method / Resources /
  Per-element basis, the level's name (a picker of the library's levels) and
  its text read-only. Its Save is one `update_calc` patch through the
  undoable batch (raw_text / set_name only when another level was picked);
  coordinates moved there are the block's own, never the row's structure.
  The block's options are named under it (`1 constraint · MaxIter 300`) and
  in the inspector. A refusal (`'name': …`) is said in the inspector, and in
  the builder above its Save, not only in a toast. An MLIP set's block has a
  *Task* select (`mlip_jobs`); a CREST set's block runs `crest_conf`
  (`crest_jobs`) and has no Task. Below that: *↑ Earlier* /
  *↓ Later* (Alt+←/→: the block's run order within its row, an `order` op,
  never past what it depends on or what depends on it), *Duplicate* (the
  whole setup as `<name>_copy`, next free row of its column), *Show folder*,
  *Structures…* (the row's panel). Its own name after the row's is editable
  while it is (what runs after it, or takes its geometry, follows). The
  geometry list ends with **every other calculation** that does not hang off
  it. Once it has run, the input shown is the `.inp` its run folder holds
  (`get_inp`), not a regenerated one. Remove… offers *Remove only this*
  when something hangs off it and no run is going: those stay, drawn blocked
  and listed in the warning line, until each is given another geometry. A block that waits
  at its gate has its own *Approve*, and one that runs after a calculation in
  another row says so (no edge on the canvas shows it). For an edge: *Auto* /
  *After approval*, the auto-pass box after a Freq (`ts_freq`: one imaginary
  mode expected, otherwise none), and *Approve* while the next step waits.
  **The energy condition** is one compact row, *Pass on ΔE ≤ [ ] kcal/mol ·
  top [ ]* (empty: off), with a line saying what ΔE is measured from, in
  the inspector of an edge out of or into a stack or out of a CREST search,
  and of a block that is a stack or follows one (or a search); anywhere a
  condition is already set too. A change goes to every member that can
  still take it -- one still to run, or a **filtered** one, which then goes
  back to pending with what it filtered (the store's rule: on a filtered row
  the condition is the one thing that changes, every other edit is refused
  as on a done one) -- in one undoable batch; a member that has run keeps
  the condition it ran under, and the line says so.
  With **nothing selected** the inspector is the **set editor** (not a help
  text): the set's name as the title, whether it is an ORCA level or MLIP
  settings (a text that reads as the other says it will move group), the
  text in the template field, one line naming the placeholders (`{{JOB}}`,
  `{{GEOMETRY}}`, `{{CHARGE}}` `{{MULT}}`, `{{HESS}}` optional; for MLIP the
  setting keys), then *Save*, *Save as new*, *Delete…* (a new one:
  *Discard*), *Duplicate* and *Import .inp…*. *+ New* starts a level from the
  default level's text. Refusals (no name, no `{{GEOMETRY}}`, no `{{JOB}}` on
  the `!` line, a name its group already has) are said in the editor, in the
  error colour, not only in a toast. An imported input's job keyword becomes
  `{{JOB}}` and its coordinate block the placeholders only when the user says
  so (one question, a box for each). Blocks copied their text: a saved set changes none by
  itself; the pending blocks made from it are offered the new text once, as
  one undoable batch — started blocks never. Unsaved edits survive selecting
  blocks; switching to another set asks *Keep editing* / *Discard changes*.
  Escape or a click on the empty canvas comes back to the editor, on the set
  last opened (else the first). The inspector never repaints under the cursor
  while it still shows what is selected; a new selection is drawn at once,
  and a save or refusal in the editor is drawn with the cursor kept in place.
  Its controls act on the block as the queue holds it when they fire, so a
  rename followed by an edit in the same inspector edits the renamed
  calculation; a paint that waited on the backend is dropped when another
  member or block was chosen meanwhile.

It holds **no queue state of its own**. Every change is one `apply_queue_ops`
batch (all or nothing) and the editor redraws from the next poll; what it keeps
is the selection, a popover, a drag, and an **undo stack of inverse batches**
(Ctrl+Z). What a batch adds is selected once it is in the snapshot the batch
returns, never before (a render in between would drop a selection of a block
that does not exist yet). A removal that takes a finished calculation with it is not offered as
undoable — the store would take it back as PENDING, which is queueing it to run
again, not undoing. For the same reason an undo never removes a calculation
that has started since: an undo whose batch would remove one is refused with
the reason and dropped (Remove… on the block takes a finished row off the
list). An undone move or removal re-orders the queue as it is at that moment
(rows added since keep their places), not as it was when the change was made. Delete (or Remove…) takes **the selected calculation**
and everything after it (confirmed; a running one is refused: cancel it
first): a stack loses one member at a time — the one picked in its member
list, the first when it was clicked — and stays selected. A stack selected
whole by a band (*Remove all N…*) or several blocks (*Remove all…* in their
inspector) go together.

Files dropped on the queue window: every `.xyz` at once becomes a row
holding its structure, named after the file, with **nothing queued** — its
steps are built on the row (the + slot's add menu, a pasted or saved
workflow); one `apply_queue_ops` batch, Ctrl+Z undoes it; the new rows arrive
**selected**, so one Ctrl+V, one click on a saved workflow or one charge
fills them all; a file's comment line may state its charge and multiplicity
(`charge=1 mult=2`, `Charge: -1 Multiplicity: 2`, xtb's `uhf=N` as N+1),
which the row takes (none stated: 0 and 1; a value no molecule has is not
read); a refused file is listed with its reason, never renamed to fit; an
`.inp` opens in the builder as pasted input. On the main window an `.out` opens in Results, an `.xyz`
becomes such a row and the queue window opens, an `.inp` opens there in the
builder. (A drop no longer runs anything by itself: the single point it used
to queue at a chosen level was removed at the user's request.) What the main window hands the queue window (the builder with a seed,
an `.inp`, a calculation to show) goes through a one-slot mailbox in the UI
state (`queue.handoff`, stamped, read while the window is on screen) — the two
pages share the Bridge, not a line of JS.

Visual language: the notebook's own tokens, light and dark. The prototype is
dark-only with literal zinc hexes; each is mapped onto a token (`app.css`,
*the queue window*, has the table), with three derived greys (`--qe-dim`,
`--qe-faint`, `--qe-ghost`) mixed from `--muted-foreground` into the
background so they follow the theme. None of it is a uispec landmark: the
reference has no queue window, and the main page's landmarks are unchanged.
The conformer stack, the condition chip and control and the filtered state
are one named block at the bottom of `app.css` ("the conformer stack and the
edge condition"), drawn only from those tokens; no token was added.

---

## Appendix B — Known deviations

Same convention as PRINCIPLES.md Appendix A: **fix** / **accepted** /
**stale-doc**.

| # | Deviation | Strains | Disposition |
|---|-----------|---------|-------------|
| B1 | Desktop `style.css` had no `.qstate.blocked` rule — a BLOCKED badge rendered unstyled (mobile styled it) | D12 | resolved (0.4.3-beta — neutral badge per the §12 matrix) |
| B2 | `.modal-body .names` referenced undefined `var(--mono)` (should be `--font-mono`) — modal name lists silently lost their monospace | D20, D10 | resolved (0.4.3-beta) |
| B3 | Hardcoded `rgba(...)` tints were pervasive, not token-derived: status badges, `.btn.on`, danger hovers, the `.qerror`/`.freq-warn` error surfaces, the HUD hazard stripes — and the raw badge's tint even used a different purple base (`#a855f7`) than its text token (`#c084fc`), at 13% alpha in light, which forced the light-theme raw-badge retint hack | D13, D10 | resolved (0.4.3-beta — `--*-tint` tokens on the §11.19 ladder, redefined per theme; the retint hack removed) |
| B4 | `scf_graph.js` hardcoded `#52b788` for the converged-zone shading (the dark `--crit-rmsg` value) — it didn't re-color in light theme, despite the same file declaring the CSS-var convention | D10, D14 | resolved (0.4.3-beta — inline `style="fill:var(--crit-rmsg)"`) |
| B5 | Combo dropdown fallback `var(--popover, #1a1d24)` carried a pre-zinc color that differed from the real token `#18181b` | D10 | resolved (0.4.3-beta — fallback dropped) |
| B6 | Mobile stylesheet fork drift: undefined `var(--fg)` used twice; `--muted` (a background tone) used as text color (near-invisible); log box hardcoded `#050506` instead of a token; radius values differ; no light theme; copy still sentence-form; native `confirm()` for the destructive remove (vs D61); its own timing constants (poll 1500ms, log cap 1500 lines, heartbeat 5000ms) and motion (4px panel fade, animated accordion chevron) undocumented | D54, D10, D70, D61, D53 | partially resolved (0.4.3-beta: the outright bugs — undefined `--fg`, `--muted`-as-text, the hardcoded log background — fixed via the desktop token names incl. `--code-bg`) — light theme, copy normalization, and the themed confirm remain; restore parity when touching mobile |
| B7 | ~75 inline `style=""` attributes (37 in `index.html`, 38 in the web JS templates) do layout micro-tuning outside classes | D50 | accepted (pragmatic for one-off layout; promote to a class the second time a style repeats) |
| B8 | Two versions of the empty-queue copy existed (static HTML vs `renderQueue`) with different wording | D70 | resolved (0.4.3-beta — one string; the static HTML is marked as a pre-bridge placeholder kept in sync) |
| B9 | Inline file-load status hints mixed conventions ("N atoms loaded." vs "no product loaded") and the MACE-env-missing message existed in two non-identical phrasings | D70, D72 | resolved (0.4.3-beta — `loaded (N atoms)` inline-status form; one MACE-env-missing phrasing) |
| B10 | `design_preview.html` sits in the repo root; its "after" proposals (hover-lift, glow tab underline) were rejected but the file's status is undocumented | D32 | stale-doc (this entry is now the record: preview = proposal sandbox, not spec; rejected proposals stay rejected) |
| B11 | MLIP model picker is a closed `<select>` (no free-text escape hatch, unlike the ORCA combos) | D63 | accepted (model ids map to concrete downloadable weights; revisit if custom/local models are supported) |
| B12 | Numerical-freq progress could display 100% before completion: the tracker capped at 0.999 but `Math.round` showed "100%" (and a full bar) during the final displacement | D2, D43 | resolved (0.4.3-beta — displayed percent is floored) |
| B13 | The SCF graph panel runs a 14px rhythm (panel padding + `.graph-divider`) against the 16px principle | D30 | resolved — legislated as the compact-panel rhythm (§9.1/§9.2) |
| B14 | Dead/unused spec: `.step-tab`/`.step-tabs`/`.step-body` and `.btn-block` were referenced by no markup or render code; tokens `--radius-lg` and `--destructive`/`--destructive-foreground` (`.btn-danger` uses `--err`) were defined but unused | D31 | resolved (0.4.3-beta — dead rules and `--destructive` removed; `--radius-lg` wired into the modal) |
| B15 | `.brand .logo` hardcodes a pure-black `#000` chip in both themes | D11, D10 | accepted (deliberate contrast plate for the logo image) — recorded here |
| B16 | Desktop off-scale/literal radii: `.modal` 10px (canonical: `--radius-lg`), `.ver-badge` 5px (canonical: `--radius-sm`), `.mlip-env-row` literal `8px`, `.freq-warn` literal `6px` (tokens existed); 4px stays as the sanctioned micro radius (`.qerror`, `.scf-prog-bar`) | D31, §9.2 | resolved (0.4.3-beta — normalized onto the radius tokens) |
| B17 | `.mlip-env-row` deviated from the canonical list-row recipe (§11.9): gap 10 vs 12, padding 8×10 vs 12×14, radius literal, no card background | §11.9 | resolved (0.4.3-beta — restyled onto the list-row recipe) |
| B18 | No `:focus-visible` styles on buttons, tabs, segmented toggles, or combo items — only text fields got the focus ring; keyboard navigation had no visible focus | D40, §11.3 | resolved (0.4.3-beta — `:focus-visible` ring consistent with the input ring on buttons/tabs/toggles) |
| B19 | Segmented toggles shipped in two sizes (`.log-mode-toggle` 12px / `6px 14px` vs `.graph-subtoggle` 11px / `5px 12px`) against the canonical §11.5 spec (12px, `6px 12px`); and the Build-mode toggle reused `.graph-subtoggle` at page level, where its `--background` button surface equalled the page background (inactive buttons lost their surface) | §11.5 | resolved (0.4.3-beta — both converged on the canonical size; the Build-mode toggle uses the page-level `--card`-surface variant) |
| B20 | Copy normalization sweep against §14: `Couldn't`/`Cannot` variants → `Could not {verb}: …` for failures; periodless toasts (`Drop failed`, `Geometry copied as .xyz`, `Copy failed — clipboard unavailable`); `MLIP:`-colon pill format; `a .inp` → `an .inp`; `TDDFT` calc-kind option label → `TD-DFT`; modal title `Existing results found` → question form; `calculation(s)` → programmatic plural; `12 atoms loaded.` → inline-status form `loaded (12 atoms)`; `triplets` checkbox → `Triplets`; two divergent post-load log templates → `Next: {step}, then {action}.`; `No queued jobs` static empty state (B8); `Load product .xyz…` → drop the trailing `…`; `--- starting queue ---` → run vocabulary; IRC hint `a TS structure` → `a TS geometry`; pace-chip tooltip `average wall-clock time per SCF iteration` → Sentence case + `cycle`; `maxcore (MB)` (Build) vs `maxcore (MB / core)` (Settings) → converge on the latter | §14, D70, D72 | resolved (0.4.3-beta — sweep applied) |
| B21 | Layout/chart micro-conformance sweep: card header-row margins varied (canonical 8px; `.atoms-head` 10px, geometry header gap-only); `.status-pill .dot` base default was `--err` (neutral only via per-id overrides — a new pill would have shown a bare red dot, D42); NMR results table lacked the `max-height` scroll wrapper; static Results-chart ticks were 10px sans inline (canonical: mono); FEP chart overrides: axis title inline 11px, `0` tick 10px, zero-line dash `4 4` (canonical `3 3`) | §11, §12, D20 | resolved (0.4.3-beta — sweep applied; the dot base rule now defaults neutral) |
| B22 | `renderSummary`'s value-coloring used three sequential non-exclusive regex `if`s, so the later `/converged\|Normal/i` re-matched the substrings of `NOT converged` and `ABNORMAL / incomplete` — **both failure verdicts rendered green (.ok)** instead of red | D2, §12 | resolved (0.4.3-beta — exclusive else-if chain, failure patterns first) |
| B23 | Feedback-channel conformance (§13.2): many failure call sites were single-channel — toast-only (`Couldn't read that .inp`, `Drop failed`, `Could not save settings/theme: …`, `Could not add environment: …`, reorder/remove constraints, `Copy failed`) or log-only (`Could not start: …`, `Could not generate .inp: …`); `clearQueue` failure logged at `warn` instead of `err` | §13.2, D65 | resolved (0.4.3-beta — failures route through the shared `failNotify` helper: toast + `err` log) |
| B24 | Five 0.5.0-beta CREST/MLIP `.card-desc` strings drifted from the copy spec: the raw-`.inp` and Results descs used *job* for a queued **calculation** (§14.2 reserves *job* for the running process); the CREST-build, MLIP-environment, and CREST-settings descs were multi-sentence/imperative rather than a one-line noun-form fragment (§11.1/D70). The B20 sweep (0.4.3-beta) predates this copy. | §14, D70, §11.1 | resolved (0.5.0-beta — reworded to spec; found by a conformance review of the design previews, which confirmed the rest of the preview set is clean) |
| B25 | The Liquid-Glass `vivid`/`maximal` intensity levels glassify the `.card` layer, crossing the "glass = chrome only; content opaque" rule (§16.1) | §16.1 | accepted (0.5.0-beta — opt-in exploratory extremes at the top of the intensity ladder; the recommended `restrained`/`moderate`/`bold` levels keep content opaque, and inner data surfaces stay opaque even at `maximal`; moot since the classic front-end and §16 were retired, unreleased) |
| B26 | Second 0.5.0-beta copy sweep (post-B24 strings, mostly the Appearance card): the free-energy-profile placeholder said *FREQ jobs* (§14.2 job/calculation + kind-label casing); the Appearance `.card-desc` and three hints were multi-sentence verb-form (§11.1/§14.1 noun-form one-liners); two glass-intensity tooltips were subject-verb clauses among noun-phrase siblings (§14.1); and the `#card-mlip` title "MLIP pre-optimization" misdescribed the card's `mlip_sp` single-point task (§14.1 accurate noun phrase / D2) | §11.1, §14.1, §14.2, D2 | resolved (0.5.0-beta — all eight strings rewritten to conform; card retitled "MLIP calculation") |
| B27 | The MLIP/CREST card lock disabled only a hand-written list of field ids, so CREST's whole *Advanced settings* block (preset, NCI, solvent model, the MD/MTD numbers, the five toggles) and both cards' geometry-source radios stayed enabled under the grey — pretend-disabled chrome around live controls | D41 | resolved (0.6.1-beta — the lock disables every control the card's own DOM contains, so the list cannot drift from the markup again; the CUDA `<option>`'s separate disabled state is deliberately left to `refreshMlipDeviceOptions`) |
| B28 | Settings → CREST's *Install CREST* button was pretend-enabled: it disabled only once CREST was already installed, never when there was **no WSL distro to install into** (the one prerequisite ORCAdesk cannot script). The click's only feedback was a Log-tab line, so a failed install read as a dead button — and the installer's actionable diagnostics never reached the card | D41, D2, §13.2 | resolved (0.7.0-beta — the button disables with the reason in its tooltip when WSL or a distro is absent, or while a probe is in flight; the install outcome is published on `CrestStatusPayload.install_error` and surfaced as the card's detail line + a toast) |
| B29 | Two color literals had come back after B3/B5 removed the pattern: `renderInputEcho`'s `<pre>` used a raw `rgba(127,127,127,0.08)` (a theme-independent grey that is neither a token nor an alpha-ladder value), and the 3D viewer fell back to the literal `#18181b` — the DARK `--card` — when its token read came back empty, painting the stage near-black on a light theme | D10, D13, §15.1 | resolved (unreleased — the echo uses `--input-bg`; the viewer passes no background at all when the token is unreadable, rather than the wrong theme's value) |
| B31 | The notebook front-end still lacks three of the reference's blocks: the MLIP environment rows and the glass-intensity slider in Settings (`.envrow`, `.slider`), the builder's NEB-endpoint / per-element-basis / structure-findings blocks (`.nebrow`, `.nebslot`, `.basis-row`, `.snips`, `.finding`), and the pop-out 3D viewer window with its frame walker and ESP ramp (`.mvwin` …) | §17 | resolved (0.10.1 — all ten landed; the `OUTSTANDING` set in `tests/test_next_ui.py::test_the_preview_uses_the_references_own_component_vocabulary` is now empty and is kept that way, so dropping an element is a decision somebody took rather than something that quietly happened) |
| B30 | The notebook front-end (§17) draws flat — the Liquid-Glass appearance variants (§16) are set from its Settings but rendered only by the classic window | D2, §17.5 | resolved (unreleased — the classic window and the Liquid-Glass variants are removed, so there is nothing left to render elsewhere; the notebook is the only front-end, draws flat, and no longer offers a style setting. History: accepted in 0.10.1 and narrowed as the MLIP/CREST builders, the 3D viewer, the Visual panel and the natural-orbital analysis landed) |
| B32 | The notebook's top bar shows **all three backend pills** — ORCA, MLIP, CREST — whether or not MLIP/CREST were ever set up, where D41 hides an unadopted backend's pill. Asked for directly: the pills are how the user sees, at a glance and without opening Settings, what this machine can run. They follow D42's four states; CREST's probe `error` (a shell with no target carrying CREST, the never-installed case) reads *not set*, not *error*. Below 1320px the state word folds into the tooltip and the screen-reader text, the name and dot stay | D41 | accepted (0.10.1; unreleased — the classic window, which kept D41's hide-until-adopted, is removed, so this is now the desktop's only behaviour) |
| B33 | §8 (tokens) and Part II (§9–§15) were extracted from the classic front-end's `web/style.css`, which is removed; they have not been re-mirrored against the notebook's `web/next/app.css`, so a value or a class name there may describe nothing that ships | P52, §8 | stale-doc (unreleased — Part I still binds; the notebook's exact values are its approved design's, §17.0) |
| B34 | Results › Visual's **2D structure** tile is shown whether or not the 2D-drawing add-on was ever set up, where D41's scope clause hides an unadopted optional feature. Asked for directly: the tile is how the feature is found at all, and its half of the stage says what is needed and links to Settings rather than offering a pretend-enabled control (the controls stay disabled until the probe says ready) | D41 | accepted (unreleased; §17.4x) |
