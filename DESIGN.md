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
per-element overrides" (`style.css`). JS applies a theme with one
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
| neutral / pending / cancelled / blocked / raw ORCA text | grey (`--info`, `--orca`, `--muted-foreground`) | `blocked` styles like the other neutral states |
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
(ok-tinted) for active toggles; `.btn-sm` / `.btn-block` for size. Pick from
this vocabulary — don't style one-off buttons.

### D52 — Empty states answer "what's missing and where it comes from"

An empty region shows icon (border-colored) + title + a next-action hint
("No calculations queued / … from the Build tab"), with generous padding —
never a blank box, never just "empty".

### D53 — Motion is quiet

Interaction color transitions 0.12s; panel fade-in 0.15s (+3px translateY);
toast 0.2s; progress-bar width 0.3s. Nothing else animates. No entrance
choreography, no hover lifts (D32).

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
Queue tab; dropped `.out` → Results).

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

Complexity is revealed in layers: four mutually-exclusive build modes
(Beginner form / Expert raw / MLIP / CREST), form fields driven per calc-kind by
`KIND_DEFS` flags, conditional fields appearing only after their premise
(solvent model → solvent; InitHess=read → filename), Results gated per kind
with **Show all** as the override. Hiding is filtering, never data removal —
gating stays on the front-end so revealing everything is a re-render, not a
re-fetch (PRINCIPLES P45). The chosen disclosure level is remembered.

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

Source of truth: `web/style.css` `:root` and `html[data-theme="light"]`.
This table is a mirror for convenience — when they disagree, the CSS wins
and this file must be updated in the same commit (PRINCIPLES P52). Values
are the desktop's; mobile drift is tracked in B6.

### Core palette

| Token | Dark (default) | Light (warm) |
|---|---|---|
| `--background` | `#09090b` | `#fffff0` (ivory) |
| `--foreground` | `#fafafa` | `#09090b` |
| `--card` / `--popover` / `--muted` | `#18181b` | `#f5f5dc` (beige) / `#fffff0` / `#ece8ce` |
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

Each semantic color also carries **tint tokens** on the §11.19 alpha ladder
(`--ok-tint`/`--ok-tint-strong`, `--warn-tint`, `--err-tint`/`--err-tint-bg`/
`--err-tint-hover`/`--err-outline`/`--err-stripe`, `--raw-tint`), redefined
per theme next to their base colors — components consume the tint token,
never a hand-mixed rgba (D13).

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
  ui-monospace, monospace`.
- Type: base 14px/1.5 (mobile 15px); labels 12px/500; card titles
  13px/600/−0.01em; badges 10–11px uppercase; HUD numeral 26px/700
  tabular-nums.
- Spacing: 16px gutter/gap everywhere (desktop); card padding 18px;
  field gap 12px.
- Motion: hover/active 0.12s; panel fade 0.15s; toast 0.2s; progress bar
  0.3s; pulse/shimmer 1.2–1.4s (in-progress only).
- Timing constants: poll 1000ms; MLIP status re-poll 800ms; toast 2200ms;
  "Saved." 2000ms; log DOM cap 2000 lines; backend log buffer trim
  5000 → 4000 lines; `[web]` console rate-limit 5s/signature; log
  auto-follow threshold 40px.
- ETA buckets: under a minute (<45s) / a few minutes (<8m) / tens of minutes
  (<50m) / a few hours (<5h) / many hours (<24h) / a day or more.

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
min 220. Wide content always scrolls inside its own wrapper
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
| State badge (600, uppercase, +.03em), version badge (500, meta label), truncated paths (mono), progress meta/ETA/pace (mono) | 11px |
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

### 11.8 Error chip and warning panel

Two tiers, both `--err` text on an 8% err-tint:

- **Inline error chip** `.qerror` (row-level messages, NEB mismatch):
  `12px; padding 5px 8px; border-left 2px solid --err; radius 4px;
  lh 1.45`. Prefix `⚠ `.
- **Warning panel** `.freq-warn` (section-level, Results):
  `12px; padding 8px 10px; border 1px solid --err-outline; radius
  --radius-sm; lh 1.5; margin-top 8px`.

### 11.9 List row (canonical)

The queue row is the canonical list-row recipe: `flex; gap 12px; padding
12px 14px; border 1px --border; radius --radius-sm; background --card;
margin-bottom 8px`. Name block 13px/600 (+ tag), meta line 12px mono muted
(` · ` separators), right side = state badge then `.btn-sm .btn-ghost`
actions. Frozen rows keep an invisible drag-handle placeholder so columns
align (D64). Drag: `.dragging opacity .5`; `.drop-target` ring border +
inset bottom insertion line (`box-shadow 0 -2px 0 --ring inset`).

### 11.10 Modal

Overlay `rgba(0,0,0,.6)`, z-index 100. Box: `--card; border --border; radius
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
line 11px mono muted (accurate signals; pace chip right-aligned via
`margin-left:auto`); ETA line below at `opacity .72` italic (D2). Staged
pipeline (`.phase-track`): a horizontal timeline that fills the Graph-panel
width (no forced full-height wrapper — sizes to itself, so no empty area
below). Centered uppercase tracked title (15px/700, +.14em) + a status badge
(`STEP k/n` muted, or a `DONE`/`STOPPED` pill in `--ok`/`--err` tint). Below
it a full-width rail (`--border`, `6px, radius 3px`): the traversed portion is
a **discontinuous (stepped) fill** — one solid band per inter-node segment
(2px gap between bands, `radius 3px`), coloured `--ok` (`--err` when stopped)
at a per-band opacity ramp `.34→1` left→right (over the dark rail this reads
black→green). Node markers sit on the rail at even intervals, labels
alternating above/below; done = 16px `--ok` dot, current = 22px `--ok` dot +
`--ok-tint` ring + 1.4s pulse with a `--ok`-emphasised label, pending = hollow
`opacity .6`, stopped current = `--err`. The current stage carries an 11px
muted live detail (e.g. `iteration 2 · 14/14 MTDs`). Reuse this exact timeline
for any staged pipeline display (CREST / analytical freq / TD-DFT).

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
outline (`--err-outline`) · **55%** (`--err-stripe`, currently unused —
retired with the old HUD's hazard stripes). Consume
the token; adding a new tint means adding a token on this ladder first.

## 12. State → color matrix

| Surface | pending | running | done | failed | cancelled | blocked | raw/MLIP tag |
|---|---|---|---|---|---|---|---|
| Badge bg | `--accent` | warn-15% | ok-15% | err-15% | `--accent` | `--accent` | raw-15% |
| Badge text | muted | `--warn` | `--ok` | `--err` | muted | muted | `--raw` |

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

- **Hide** only what is *irrelevant* (fields for another calc kind,
  solvent when gas-phase) — progressive disclosure (D62).
- **Lock** (11.17, with the reason) what is *relevant but not ready*
  (MLIP card without a ready env) — never hide it (D41). Locking also
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
| Tab / mode / option | noun (ORCA abbreviations verbatim; `Opt + Freq`; `NEB-TS (find TS)`) | Sentence | none |
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
6. Readiness: not-ready features locked with a reason (13.3), never hidden.
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
| B7 | ~75 inline `style=""` attributes (37 in `index.html`, 38 in `app.js` templates) do layout micro-tuning outside classes | D50 | accepted (pragmatic for one-off layout; promote to a class the second time a style repeats) |
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
