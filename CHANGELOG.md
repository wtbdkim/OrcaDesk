# Changelog

All notable changes to ORCAdesk are documented here.
This project loosely follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **You can see the structure you are about to run, on the Build tab.** Loading
  an `.xyz` now draws it in 3D right under the loader, with a one-line census
  beside it (`C8H10N4O2 · 24 atoms · 102 electrons · 1 fragment`). Until now the
  molecule was a line of text until the job was over.
- **The mistakes that cost a whole run are caught in the form, not by ORCA.**
  Under the preview, a screening panel reports what it finds and nothing more —
  it never edits your structure:
  - **A multiplicity the electron count cannot produce.** An even number of
    electrons cannot have an even multiplicity, and ORCA aborts on it at the
    first SCF step. The message says how many electrons the formula and charge
    actually give and which multiplicity is nearest.
  - **Atoms on top of each other** — the same atom pasted twice, or a pair far
    closer than any bond between those elements (the threshold clears a real
    C≡C triple bond, so a short bond is never flagged).
  - **Coordinates in Bohr.** They read as a molecule with no bonds at all; the
    warning names that cause rather than leaving you with an empty-looking
    structure.
  - **Disconnected fragments**, with their sizes — correct for a complex, a
    sign of a truncated file otherwise. It is a warning, not an error: it never
    blocks the queue.
  - Every finding highlights the atoms it is about, in the preview.
- **The NEB endpoint pair has its own card, and you can see both structures.**
  *NEB endpoints* sits next to *Geometry source* and holds the product loader,
  the verdict and the comparison:
  - A **matched / atom mismatch** badge replaces the old one-line note, and a
    table lists **every** atom that differs — not only the first one — so a
    single swap is distinguishable from a whole shifted block at a glance.
  - The two structures are drawn **side by side** in one scene (or overlaid,
    with the product ghosted). They rotate together, and hovering an atom names
    it in *both* — the check that is impossible to do by eye. Mismatching atoms
    are marked in red in both structures.
  - The verdict is now taken by the same code the input generator's gate uses,
    so the badge and the queue can never disagree.
  - With the reactant taken from another calculation the card says the order can
    only be checked when that geometry arrives, instead of judging a stale block.
- **A structure editor: set a bond length, angle or dihedral, or move a whole
  fragment.** *Edit structure…* opens the geometry in 3D; click 2 atoms for a
  distance, 3 for an angle, 4 for a dihedral, type a value, and the far side of
  the bond moves rigidly — every other bond length and angle untouched. A
  fragment can also be translated or spun about its own centroid. Undo, revert,
  and the atom count and edit count are on screen throughout.
  - **The atom order is preserved by construction**, which is the point:
    together with **Copy reactant → product**, a NEB product built here can
    never have the atom mismatch the checker exists to find. The old advice to
    "copy the reactant and move atoms" is now something the app does.
  - An edit that cannot be made rigidly is **refused with the reason** — a
    dihedral about a ring bond, four atoms that are not a chain — rather than
    silently deforming the structure to make the number come out.

### Changed
- The NEB product loader, its status and its verdict moved out of *Method &
  options* into the new *NEB endpoints* card; **Images** and **Endpoint
  pre-optimization** stay with the method, since they are band parameters.

### Fixed
- **A space in the path to your workspace no longer fails every ORCA job.**
  ORCA hands the input path it is given on to its own sub-programs without
  quoting it, so one space truncated it: *Error: Cannot open input file
  C:/Users/John*, an error termination in Startup, and a calculation left in
  the locked FAILED state — every job, serial and parallel alike. Since the
  default workspace sits under your Windows account folder, a **user name with
  a space in it broke ORCAdesk completely**. ORCA is now started from inside
  the job's own folder and given only the file name, which has no path to
  split. A space (or `&`) in the **calculation name** is a different matter —
  it is the file name itself — so those are now refused as you type them,
  alongside the characters Windows already disallows.

## [0.7.0-beta] — 2026-08-28

### Added
- **The Results tab lists every result in your workspace, not just the queue.**
  The picker now has two groups — *In the queue* and *In the workspace* — so a
  calculation you cleared from the queue, or one from last week, is one click
  away instead of a trip through a file dialog. Run folders were always kept;
  they just were not reachable. Everything a listed result offers works the
  same, including the 3D viewer below. The scan runs when you open the tab.
  - **Open .out is now Open file…**, and its dialog offers what ORCAdesk can
    actually read — an ORCA `.out`, an MLIP result, a CREST search — rather
    than naming only one of the three.
- **Molecular orbitals and electron density can now be viewed in 3D, from the
  Results tab.** The *Orbital energies* card gained a **View in 3D** button: it
  opens the structure viewer on the HOMO, with every parsed level in a list
  down the side (labelled *HOMO*, *LUMO−1*, … with its energy) plus the
  calculation's **electron density** — and its **spin density** when the
  calculation is open-shell. Positive and negative lobes are drawn as a
  blue/red pair; a density, being positive everywhere, gets a single surface.
  - **It runs on calculations you have already finished** — and on results that
    were never in the queue at all, including ones ORCA produced outside
    ORCAdesk. The surfaces are generated by ORCA's own `orca_plot` from the
    job's `.gbw` wavefunction, so nothing is re-computed and no job is re-run.
    One orbital takes about **0.2 s** on a 52-atom molecule, an electron
    density about **10 s**.
  - **The isovalue slider is instant.** Moving it re-draws the surface from the
    data already loaded, with no recalculation — so finding the level that shows
    what you mean is a drag, not a wait. A grid selector (40³ / 60³ / 80³) is
    there for the rarer case where the sampling itself is too coarse.
  - Generated surfaces are kept in a `cubes/` folder inside the run, so
    reopening an orbital you have already looked at is immediate.
  - Requires the calculation's `.gbw` **and** ORCA's `.densities` files to still
    be in the run folder; if they are missing, the viewer says so plainly
    instead of failing silently.
- **An MLIP environment can now be created with one click, for CPU or GPU.**
  *Settings → MLIP environments* gained a **Create** button: pick a backend
  (MACE / SevenNet) and a device, and ORCAdesk builds a private virtual
  environment, installs PyTorch for that device, installs the backend, and
  registers the result — no shell, no conda, no pip commands to copy. The
  environment lands in `%APPDATA%\ORCAdesk\mlip_envs\` and is probed like any
  other, so the *MLIP ready* indicator turns green on its own when it finishes.
  - **CPU (~150 MB) and GPU/CUDA (~2.5 GB) are separate, explicit choices**, and
    the size is on the button before you press it. Picking GPU on a machine with
    no NVIDIA GPU says so first, rather than after the download.
  - **The CUDA build is matched to your actual graphics card.** A PyTorch wheel
    only carries kernels for the GPU generations its CUDA version knew about,
    and picking one too old fails *late and confusingly*: it installs, imports,
    reports your GPU as available, and then dies on the first calculation with
    `CUDA error: no kernel image is available for execution on the device`.
    ORCAdesk now reads the card's compute capability before downloading and
    fetches the right build (an RTX 50-series card gets CUDA 12.8 wheels, not
    12.4), and the card names the build and the GPU it matched.
  - The base Python it builds on is **auto-detected**; if none is found (or none
    in the range PyTorch publishes wheels for) the button is disabled and says
    why — creating one is the single step ORCAdesk cannot do for you, the same
    way a WSL distribution is for CREST.
  - It takes minutes, so there is a live step counter and a **Cancel** button;
    pip's full output goes to the Log tab.
  - **A name that would break Windows' path limit is refused before the
    download, not after it.** PyTorch nests files 189 characters deep, which
    leaves about 18 characters for the environment name on a default Windows
    install; overflowing it used to surface as an opaque `OSError` at the very
    end of a completed download. The check says how many characters to cut, and
    lifts entirely if Win32 long paths are enabled.
  - Registering an environment you built yourself still works exactly as before.
- **The queue can run several calculations at once.** Settings → *Parallel runs*
  takes a **core budget** and a **memory budget**, and starts as many
  calculations as fit in them, in queue order — the next one the moment one
  finishes. (*Run at once* is there if you also want a hard cap on the job
  count; leave it at 0 and the budgets decide.) Admission is by budget, not by
  lanes: each
  calculation declares what it occupies (ORCA `%pal nprocs`, CREST `-T`, the
  MLIP thread cap) and starts only while it still fits — so *two 8-core jobs*
  and *four 4-core jobs* are the same setting, decided by the calculations
  themselves. ORCAdesk never rewrites a calculation to make it fit; a raw `.inp`
  keeps its own `%pal`, and its cost is read from that text. Both budgets
  default to **auto** (physical cores / 75% of installed RAM) and *Run at once*
  defaults to **1**, so nothing changes until you raise it.
  - A calculation that references another now **waits** for its parent instead
    of failing when it happens to be picked first — sequential running got
    that for free.
  - Memory is budgeted because ORCA's `%maxcore` is **per core**: a 6-core job
    at 2400 MB reserves 14.4 GB, and two of those would push a 32 GB machine
    into swap.
  - A calculation larger than the whole budget still runs, on its own, rather
    than waiting forever.
  - An MLIP calculation now keeps to the cores it was charged: its worker set
    no thread limit, so torch took **every** core on the machine — invisible
    while jobs ran one at a time, a blown budget the moment two do. The MLIP
    build card gains a **CPU threads** field (seeded from the *nprocs* default)
    so that number is yours to set; a GPU run is charged one core but is not
    throttled to one thread.
  - **Cancel** stops every job in flight; **Stop after current** lets the
    running ones finish and starts no more.
  - **The machine's real free memory is checked before a second job starts.**
    The memory budget is spent against estimates, and they are wrong both ways:
    ORCA's `%maxcore` is a guideline it *may exceed*, while a CREST search is
    charged far more than it takes (measured: ~20&nbsp;MB for a small molecule,
    at any thread count). A mixed queue is where that matters most, so the
    budget is no longer the only defence — the first job always starts, but
    adding to it also requires the memory to actually be there. CREST's estimate
    now follows its thread count instead of a flat 2&nbsp;GB it never used.
  - **One GPU calculation at a time.** An MLIP job set to *GPU (CUDA)* is
    charged a single core, so the core budget alone would put a dozen of them on
    one card — where they run out of video memory rather than queueing. GPU work
    gets a lane of its own; CPU calculations still run alongside it.
  - Every log line records which calculation produced it, so interleaved output
    can be told apart: the Log tab gains a **job filter**, the convergence graph
    a **job picker** — both one button per job, so switching is a single click —
    and the queue a live *4 running / 16 of 16 cores* readout.
    A single-job run looks exactly as it did — the filter and picker only appear
    once a second calculation has produced output.
- **A two-stage run keeps both of its graphs.** An `opt_freq` optimizes and
  then runs frequencies, and the moment the Hessian started the frequency
  stepper took the Graph tab over — the optimization curve it had just
  finished was gone for the rest of the run. The graph now has a view
  toggle per stage the job actually ran — *Optimization* / *Frequencies* (or
  *TD-DFT* / *Conformers*) / *Current SCF* — so either half is one click
  away. It still opens on the live edge of the run, and a job with only one
  view (a plain frequency job, a CREST search) shows no toggle at all.
- **An analytical frequency run now counts its nuclei as it goes.** The
  *Derivative integrals* step — the long one, hours on a large molecule —
  showed only the molecule's size (*144 nuclei*), so there was no way to tell
  a run one nucleus in from one nearly finished. It now reads
  *135/144 nuclei*, counted from ORCA's own per-batch progress, and settles
  back to the plain total once the step completes.

### Fixed
- **Clicking a long-running button a second time no longer freezes the whole
  window.** *Create MLIP environment* and *Install CREST* both answered a
  second click by asking their own status slot for a progress report — while
  already holding that slot's lock. The lock is not reentrant and the caller is
  the interface thread, so instead of the click being ignored, ORCAdesk stopped
  responding entirely until it was killed. Both now report progress without
  re-entering, and a test walks the code for the same shape so it cannot come
  back. (Found while building the 3D orbital viewer, which had been written the
  same way.)
- **The Log tab's job buttons no longer sit there doing nothing on the Graph
  tab.** The job *filter* belongs to the raw log — it hides lines — but it
  stayed on screen in Graph mode, where it looks exactly like the graph's own
  job picker and clicking it changed nothing at all. It is now shown only in
  Raw mode. (The graph's picker is the strip inside the panel, labelled
  *Showing*, and it lists only calculations that actually have a curve — so a
  job that merely logged *already done – skipping* never appears there.)
- **Restarting no longer loses a running job's progress panel.** Reopening
  ORCAdesk while a calculation runs reattaches to it, but the Graph tab came
  back **empty** for a frequency or TD-DFT run: only the optimization curve
  was rebuilt from the output file, and a phase chain's next banner can be
  hours away on a large molecule, so there was nothing to show until then.
  The frequency and TD-DFT step panels are now rebuilt from the output too —
  stage, nuclei counted so far, method and cores — and this applies to any
  ORCA calculation, not only the optimization kinds. Per-stage times stay
  blank for a rebuilt panel: the history is replayed in one burst, so those
  clocks would be fiction.
- **The Raw log comes back too.** Reopening ORCAdesk on a job that had been
  running for hours showed a single line — *reattaching to ORCA still
  running* — because the log is a live tail of the output file, and the tail
  starts where the app came back. The last 500 lines of that job's output
  are now put back at the top of the Raw tab, dimmed and fenced by
  *── restored history · NAME.out · last 500 lines ──* and *── live output
  continues below ──*, so recovered text is never mistaken for what is
  happening now. It is read straight off the file — nothing is re-streamed
  through the log buffer, so it cannot push out the rest of the log — it
  follows the job filter like any other line, and only a job ORCAdesk
  actually reattached to gets it.
- **The MLIP card now says what *CPU threads* does for the device you picked.**
  The one field has two jobs and the device decides which apply, which the
  screen never said: it always caps the worker's threads, but it is only what
  the queue reserves off the GPU — an explicit **GPU (CUDA)** job is charged one
  core and takes the single GPU lane instead. Next to *Device: GPU*, a
  *CPU threads: 6* therefore read as either a no-op or six reserved cores, and
  it is neither: the threads still cap the real CPU-side work (neighbour lists,
  the optimizer, the finite-difference steps of a frequency run). A line under
  the row now spells this out per device, including that **Auto** is charged as
  a CPU job — ORCAdesk cannot know what the worker will pick, so taking the GPU
  lane needs an explicit GPU choice.
- **An MLIP calculation now runs in an environment that actually works.** With
  more than one environment registered, every MLIP job went to the **first
  registered** one — even when that environment was broken and the green *MLIP
  ready* indicator was reporting a **different**, working one. The result was a
  queue that failed every MLIP job while the top bar said it was ready. The
  first *ready* environment is now the one that runs, as documented; ties keep
  registration order, so a single-environment setup is unchanged. (Registering
  several environments is the normal case — MACE and SevenNet pin conflicting
  dependencies and cannot share one.)
- ***Install CREST* no longer offers a click that can only fail.** With WSL
  present but **no Linux distribution installed**, the button stayed live;
  pressing it reported nothing but a line in the Log tab, so the install simply
  appeared to do nothing. Creating a distribution needs a Linux account and is
  the one step ORCAdesk cannot script, so the button now **disables** when there
  is nothing to install into — and says why on hover. When an install does run,
  its outcome is reported where the click happened: the reason a failure failed
  ("`xz` missing", "download failed") now reaches the Settings card and a toast
  instead of only the log.
- **CREST could not run at all.** A refactor moved the output-tailing loop into
  a shared helper but left `crest/runner.py` without the import, so every CREST
  conformer search died immediately with an internal error — and two references
  to the deleted local helper survived in the give-up branch. Every CREST test
  replaces the runner with a fake, so nothing failed. Both are fixed, the real
  tail loop is now exercised by tests, and a new check walks every module for
  names that are read but never bound.
- **A locked MLIP/CREST build card is now fully inert.** The card greys out and
  says why when its toolchain isn't ready, but only a hand-listed subset of its
  fields was actually disabled: CREST's entire *Advanced settings* block
  (preset, NCI mode, solvent model, every MD/MTD number, all five toggles) and
  both cards' *geometry source* radio buttons stayed live under the grey, so
  they could be changed on a card that refuses to accept the calculation. Every
  control inside a locked card is disabled now, and the lock reads the card's
  own controls instead of a hand-maintained id list that could drift again.

- **The CREST energy window and thread count are now validated like every other
  numeric option.** They were the only two `crest_*` numbers that skipped the
  trust boundary: an out-of-range value rode straight onto the command line
  (`-T 999999`), and a non-numeric one made the flag vanish entirely — so the
  setting was silently ignored instead of applied. Both are now coerced and
  clamped (energy window ≤ 1000 kcal/mol, threads ≤ 1024), and a junk value
  falls back to the default *with the flag still emitted*.

- **Editing an MLIP/CREST calculation no longer offers the calc itself as its
  geometry reference.** The DFT card already excluded the calc being edited from
  the reference dropdown; the MLIP and CREST cards now share that logic (the
  three geometry-source selectors are one implementation), so a self-reference
  can no longer be picked during an in-place edit.

### Removed
- **The "~N s / SCF cycle" pace figure is gone from the Log tab.** It sat in
  the progress panel restating what the graph beside it already showed, and a
  per-cycle average is the least useful of the timings on that panel — the
  measured per-step rate and the ETA stay.
- The unused `list_crest_distros` bridge slot (the Settings distro picker is
  populated from `get_crest_status()`).

## [0.6.0-beta] — 2026-07-25

### Removed
- **The per-conformer track fan-out (Conformer handoff "all") is gone.** The
  CREST build card no longer has a *Conformer handoff* selector; referencing a
  conformer search from another calculation's geometry source now always hands
  off the **lowest-energy conformer** (this was the previous default). The
  engine-side track expansion (cloned `name_c1`, `name_c2`, … chains) and its
  queue-substitution machinery were removed with it. Existing queues saved with
  the "all" scope load fine and simply run on the lowest conformer; already
  fanned-out `_c1/_c2` clones remain ordinary calculations. Every conformer is
  still available on disk (the automatic per-conformer `.xyz` export and the
  Results-tab 3D viewer are unchanged).

## [0.5.2-beta] — 2026-07-24

### Changed
- **The CREST progress stepper (Log → Graph) is much more detailed.** The
  Metadynamics-sampling row now follows the whole in-iteration pipeline instead
  of freezing on the MTD count: the MTD batch (`iteration 2 · 4/6 MTDs · 17 ps
  each`) and the multilevel ensemble optimization that follows it (`optimizing
  1014 structures (crude/tight)` — the multi-minute stretch that used to look
  stalled), then the CREGEN ensemble state appended right on the same line
  (best `E lowest` so far, structures in the energy window, the latest "new
  lower conformer" improvement). Once finished, a footer strip under the
  stepper shows the ensemble statistics (population of the lowest conformer,
  ensemble ΔG, MTD/optimization runtime split). The meta line
  now also carries the solvent (`ALPB(acetone)`) and NCI mode. Fixed alongside:
  an MTD that "terminated EARLY" now counts toward the batch, so the counter no
  longer sticks below N/N.
- **CREST auto-exports every conformer as a separate `.xyz`.** When a conformer
  search finishes, ORCAdesk now automatically splits `crest_conformers.xyz` into
  one standalone `.xyz` per conformer under a `conformers/` subfolder of the run
  (`{name}_c1.xyz`, `{name}_c2.xyz`, … energy-sorted, `c1` = the best conformer)
  — no manual step. This happens for every finished search (a fresh run, a
  mid-run reattach, and a search judged done after the app was reopened),
  regardless of the *Conformer handoff* scope. The Results-tab **Export as
  .xyz** button remains and simply re-runs the same split. The export is
  best-effort: a missing or empty ensemble is logged and never turns a
  completed search into a failure.

### Added
- **MLIP and CREST calculations are now editable in the queue.** The queue-row
  **edit** button on a pending MLIP or CREST calculation reopens it in its own
  build card (MACE or CREST) with every field pre-filled, and the card's button
  becomes **Update** — no more remove-and-re-add. Editing preserves the
  calculation's position in the queue (and a conformer-fan-out clone's
  provenance). Same editable states as before (pending / cancelled / blocked);
  a running, done, or failed calculation stays locked.
- **In-app 3D structure viewer with arrow-key browsing.** The Results tab can
  now show structures in 3D (powered by 3Dmol.js, bundled locally) and flip
  through many with the **← / →** keys — no external program. **View in 3D** on a
  CREST conformer result steps through the whole ensemble; **Browse .xyz…** in
  the Results header opens any folder of `.xyz` files (e.g. a `conformers/`
  folder) as one browsable set. The caption shows each structure's ΔE relative to
  the lowest-energy one; drag to rotate, Esc to close.
- **Favorite (★) structures in the 3D viewer.** Star the conformers worth
  following up (the **F** key or the star in the list) — stars persist across
  sessions. **★ only** steps the ← / → keys through starred structures alone, and
  **Export ★** writes just the starred ones to a `favorites/` folder next to the
  source.
- **MACE-MH-1 omol head.** A new MLIP model option, *MACE-MH-1 omol*, selects
  the `omol` head of the multi-head MACE-MH-1 model (wB97M-VV10, organic /
  organometallic) instead of its default inorganic-materials head — the head
  best suited to molecular and host–guest energetics (it is the strongest
  MACE-MH-1 head on the S30L supramolecular benchmark). Like MACE-OMOL and
  MACE-MH-1 it reads the calc's charge / multiplicity for ions and radicals.
- **MLIP frequencies & thermochemistry.** The MLIP build card gains two new
  tasks, *Frequencies* and *Opt + Frequencies*, alongside Optimization and
  Single point. They run an ASE finite-difference vibrational analysis with
  the MACE model and, for a true minimum, ideal-gas thermochemistry (ZPE,
  enthalpy H, Gibbs G, entropy term T·S, inner energy U) — rendered on the
  Results tab exactly like an ORCA frequency job. Temperature and pressure
  are configurable per calc (shown only for the frequency tasks; symmetry
  number 1 is assumed). Validation matches ORCA: a frequency result with any
  imaginary mode is not a true minimum and the calculation is marked FAILED
  (`mlip_freq` / `mlip_opt_freq`, the latter also requiring convergence).
- **MLIP GPU support.** MLIP jobs can now run on a CUDA GPU instead of CPU — a
  large speed-up for the many force evaluations a frequency job needs. The
  build card has a Device selector (*Auto* — GPU when available, else CPU —
  or explicit CPU / GPU), and the environment probe now reports whether the
  registered interpreter's PyTorch sees a GPU (shown on the MLIP status and in
  Settings → MLIP). Existing MLIP calculations default to Auto.

### Changed
- **Stopping a run no longer cancels the pending queue.** Pressing Stop now
  cancels only the calculation that is actually running and leaves every
  pending calculation PENDING (runnable as-is) instead of stamping them all
  CANCELLED — stopping a run no longer discards the queued plan. (Stop still
  kills the in-flight job; "stop after current" still lets it finish.)

### Fixed
- **Memory-leak audit fixes.** A pass over the long-session memory behavior
  closed several small leaks/retentions: display caches in the UI are now
  swept when a calculation leaves the queue through a non-desktop path
  (conformer fan-out substitution, a phone remove) — this also stops a
  reused name from serving the removed calc's cached result; opening a
  modal over an unresolved one now dismisses the first (its Escape handler
  and pending promise used to linger and could close the new modal);
  switching Liquid Glass back to shadcn releases the wallpaper canvas's
  backing store (~tens of MB); `get_graph_lines` now really caps its
  payload as documented; removed MLIP environments drop their probe
  bookkeeping; a force-killed MLIP worker is reaped; the phone-client
  heartbeat prunes stale entries; and a server thread that ignores a stop
  is kept tracked instead of leaked untracked. (For the WSL/CREST
  multi-GB memory growth, see the `.wslconfig` note in the README.)

## [0.5.1-beta] — 2026-07-22

### Added
- **The queue can now be edited while it is running.** Previously every
  structural change was refused mid-run ("Cannot add to the queue while it
  is running"). The engine now walks the store's live queue, so while a run
  is in progress you can add new calculations (they execute in the *same*
  run), and remove, edit, or reorder pending/cancelled/blocked rows. Still
  protected: the in-flight calculation, finished (DONE) and failed rows,
  and Clear all. Guard rails that replace the old freeze: a mid-run
  add/edit can't reference a calculation that isn't in the queue, a
  referenced parent can't be removed or renamed mid-run, and adding a name
  whose results already exist on disk asks before overwriting (the
  Run-click overwrite check can't see calcs added after the run started).
- **CREST conformer searches can start from another calculation's
  geometry.** The CREST build card gains the same geometry-source selector
  as the MLIP card: load an `.xyz` directly or reference a queued
  calculation (e.g. an MLIP or ORCA pre-optimization) — the optimized
  geometry is injected at run time.

### Changed
- **Queue-row text is selectable again.** Rows were fully draggable, so
  clicking calculation names/details started a drag instead of a text
  selection. Dragging now arms only from the ≡ handle; the rest of the row
  supports normal select/copy.
- **The packaged build now really excludes phone-sync.** The docs have always
  said phone-sync is not part of the packaged build, but nothing enforced it:
  PyInstaller follows the optional fastapi/uvicorn/qrcode imports, so whether
  the whole server stack (and its LAN-binding Server toggle) shipped depended
  on the build machine's site-packages — the shipped 0.5.0-beta actually
  contains it. `build.spec` now excludes `fastapi`/`uvicorn`/`starlette`/
  `pydantic`/`anyio`/`qrcode`/`PIL` explicitly; the app degrades exactly as
  on a machine without those packages (`ServerController.is_available()`).
  Drop the excludes deliberately when phone-sync ships for real.

### Fixed
- **NEB/IRC reaction-path parsing: a path distance can no longer be read as an
  energy.** The plain `PATH SUMMARY` table (NEB/NEB-CI, and printed during
  every NEB-TS run) has an extra `Dist.(Ang.)` column before `E(Eh)`; the
  parser assumed the energy sat right after the image label, so any NEB-TS
  run inspected before its final table (cancelled, crashed, unconverged), any
  plain NEB/NEB-CI job, and any such drag-dropped `.out` plotted distances
  (0.0, 3.7, …) as energies and absolute energies as ΔE. Column positions are
  now read from the table header. Verified against real corpus files.
- **Multi-word solvent names now generate input ORCA accepts.** ORCA's
  simple-input parser splits on whitespace, so `CPCM(Diethyl Ether)` was an
  instant `INPUT ERROR` — every job with such a solvent failed at launch.
  Space-named solvents are now selected through the quoted `%cpcm` block
  (`solvent "..."`, or `smd true` + `smdsolvent "..."` for SMD) with only the
  activation keyword on the `!` line. Verified by live ORCA 6.1.1 runs.
- **Solvent picker names ORCA doesn't know are aliased or removed.** Verified
  against ORCA 6.1.1's own embedded solvent table: 10 picker labels are now
  mapped to the spelling ORCA accepts (e.g. `Ethylene Glycol` →
  `1,2-ethanediol`, `Methyl Ethyl Ketone` → `butanone`, `DMA` →
  `n,n-dimethylacetamide`; `input_generator.normalize_solvent`), and 13 names
  with no ORCA equivalent (e.g. Glycerol, NMP, Pyrrole, tert-Butanol) are
  dropped from `data/solvents.json` — each previously aborted the run at
  start with "Solvent name not found".
- **Correlated wavefunction methods get a correlation-fitting aux basis.**
  CCSD, CCSD(T), DLPNO-CCSD(T), QCISD(T), NEVPT2, CASPT2, STEOM/EOM-CCSD,
  ADC2 … under the default RIJCOSX received only `def2/J`, which ORCA rejects
  *after* paying for the full SCF ("A /J-Basis is not an appropriate auxiliary
  basis set for correlated methods"). They now get `AutoAux` like MP2 and the
  double hybrids (the 0.5.0-beta fix stopped at MP2/MP3). Verified by a live
  DLPNO-CCSD(T) run.
- **UTF-16 `.out` files parse instead of silently reading as empty.** ORCA
  itself writes 8-bit output, but `orca job.inp > job.out` under Windows
  PowerShell 5.1 redirects as UTF-16 — such a file (2 of 273 real outputs in
  the validation corpus) parsed to an empty result labeled "did not terminate
  normally" with no hint why. The parser now sniffs the BOM.
- **The NEB reactant/product check compares element symbols
  case-insensitively.** A product `.xyz` written in caps (`CL`, common from
  legacy tools) was refused with a confusing "composition differs — C1, Cl1 vs
  C1, CL1" message for chemically identical, correctly ordered structures.
  ORCA itself is case-insensitive.
- **A corrupt frame mid-way through a CREST ensemble no longer swallows the
  conformers after it.** When a frame declared more atoms than it had, the
  multi-XYZ scanners (`crest/parser.py`, `crest/export.py`) skipped it but
  advanced the cursor by the *declared* count — jumping into the next frame's
  header and silently dropping every remaining valid conformer.
- **Reattaching after a workspace change no longer destroys a finished run.**
  A RUNNING calc restored from a previous session was tailed at a path derived
  from the *current* workspace root; if the workspace setting changed while
  the detached ORCA kept running elsewhere, the monitor tailed a nonexistent
  file, the finished job came back FAILED-locked, and the reattach overwrote
  the one `output_path` pointer the finished-while-closed → DONE judgment
  needs. The persisted path now wins.
- **Session restore upholds the case-insensitive name-uniqueness invariant.**
  A `session.json` written by a pre-0.5.0 build (or merged externally) could
  hold `water` and `Water` — one on-disk folder on Windows; the restore path
  now dedups (first occurrence wins), so the shared-folder corruption the
  0.5.0-beta uniqueness fix closed can't be resurrected through restore.
- **Conformer fan-out: a follow-up chain queued child-before-parent expands
  correctly.** With a queue like `crest ← freq(ref=opt) ← opt(ref=crest)` (the
  dependent added before its parent), expansion re-pointed the child clones at
  the parent's *original* name — which the substitution removes — leaving K
  clones FAILED on a dangling reference. Clone names are now assigned for a
  whole track before any re-pointing. The clone uniquifier also matches the
  store's case-insensitive collision rule (a case-colliding queued name used
  to abort the entire substitution instead of suffixing).
- **A dangling geometry reference on a DONE/FAILED calc no longer vetoes the
  whole run.** `find_dangling_reference` skips locked states — neither will
  ever run again, but their stale reference used to refuse every subsequent
  Run (desktop and phone) until the rows were removed by hand.
- **Trust-boundary hardening (P32).** Wrong-typed payloads and corrupted
  files now degrade instead of crashing: a non-string calc `name` or an
  infinite `charge`/`multiplicity`/numeric-config value from the phone API or
  a bridge call returns the standard `{"error"}`/400 envelope (previously an
  uncaught `AttributeError`/`OverflowError` — HTTP 500); a `session.json`
  with a wrong-typed `pid`/`create_time` or a device `output_path` like `CON`
  no longer crash-loops (or hangs) every startup; `settings.json` with
  wrong-typed scalar fields (e.g. a numeric `orca_path`) or `mlip_envs`
  entries missing a string `id` self-heals to defaults — the latter was a
  guaranteed `KeyError` crash loop at launch.
- **MLIP environment probes can't die silently anymore.** A probe crash (e.g.
  an interpreter whose last stdout line is valid JSON but not the probe
  object) killed the worker thread without publishing, pinning the env at
  "Checking…" — and the build card locked — forever. The probe guards the
  JSON shape, and the bridge worker publishes an error payload on any
  unexpected failure.
- **CREST install failures now say why.** The auto-installer computes
  actionable diagnostics ("xz missing — sudo apt install xz-utils", download
  failed, …) that the bridge silently discarded, leaving only the generic
  "CREST not found in this distro." after a failed install. The result (or
  the success + version) now lands in the log.
- **An open edit can no longer save over the wrong calculation.** The Build
  tab tracked the edited calc by *queue index*; removing or reordering
  another row (or a phone add / conformer fan-out) while the edit was open
  shifted the queue under it, so Update silently replaced whichever calc had
  slid into that slot — or refused a legitimate update with a spurious
  "name already in the queue" clash. The edit target is now tracked by name
  and re-resolved at Update time (a vanished target falls back to a plain
  add).
- **An all-CREST queue runs without ORCA from the desktop too.** The
  front-end's pre-run mirror of `queue_needs_orca` excluded only `mlip*`, so
  a queue whose only runnable calcs were CREST was wrongly blocked with
  "ORCA path not set" before the backend — which allows it — was ever asked.
- **Editing a conformer-track clone keeps its provenance.** Saving any edit
  of a fan-out clone erased its "from crest · conformer k" queue-row label
  (the edit payload never carried `conformer_origin`); the origin now rides
  along, and the `CalcInput`/`CalcFull` typedefs document the field.
- **NEB reactant/product indicator: a ⚠ verdict can't render green.** The ✓
  branch's inline green survived into later mismatch warnings (inline style
  beats the error class); the color is reset per check. The JS-side check
  also compares element symbols case-insensitively, matching the engine.
- **SCF graph x-axis labels the real SCF iteration.** Tick labels used the
  filtered-array index (the ΔE=0 first row is dropped from the plot), so
  every label read one cycle off against the raw log.
- **Cosmetics:** the `.inp` viewer no longer double-escapes `&` in the modal
  title; the queue row's kind field is escaped like every other
  user-influenced string.

## [0.5.0-beta] — 2026-07-21

CREST conformer search as a third execution backend. CREST has no native
Windows build, so ORCAdesk runs its statically-linked Linux binary through WSL —
launched detached (it survives the app closing, like a detached ORCA run) and
kept off ORCA's `/mnt` performance cliff by running in an ext4 scratch dir and
copying results back. A finished search exposes its whole ranked conformer
ensemble in the Results tab, from which selected conformers are re-optimized with
ORCA in one action.

### Added
- **Liquid Glass theme.** A second theme *style*, selectable in **Settings →
  Appearance** alongside the default **shadcn (flat)** look: **Liquid Glass**
  (Apple "Liquid Glass", WWDC25) floats a refracting frosted top bar and tab
  strip — and, at the top intensities, cards — over a wallpaper. It is
  orthogonal to light/dark (both work in either). A **Glass intensity** control
  offers five levels — **Restrained → Moderate → Bold → Vivid → Maximal** (rising
  blur / refraction, mapping to the design2..design6 previews); Restrained→Bold
  keep content opaque (recommended for readability), Vivid/Maximal also glassify
  cards and add chromatic dispersion. A **Wallpaper** picker offers six
  procedural presets (aurora / aqua / sunset / grape / graphite / ocean) plus a
  custom-image upload; the glass chrome refracts it. shadcn stays the default and
  is untouched (the whole Liquid-Glass CSS layer is gated on the variant). Honors
  `prefers-reduced-transparency`. New settings `theme_variant` / `glass_level` /
  `wallpaper` (the custom image is stored beside the app, not in `settings.json`).
  See DESIGN.md §16.
- **Export CREST conformers as separate `.xyz` files.** The Results tab's
  conformer list gains an **Export as .xyz** button: it splits the run's
  `crest_conformers.xyz` into one standalone file per conformer (verbatim — atom
  count + the energy comment line + coordinates) in a `conformers/` subfolder of
  the run folder, named `{name}_c{k}.xyz` (zero-padded; `c1` = the best
  conformer). Verified against a real 100-conformer aspirin ensemble
  (`crest/export.py`, `bridge.export_conformers`).
- **MLIP single-point energy** (`mlip_sp`): the MLIP build card gains a **Task**
  selector (Optimization / Single point). A single point evaluates the MACE
  energy (and max force) at the given geometry with no relaxation — the worker
  branches on a `task` field, `parse_mlip_result` marks it non-optimization (so
  validation requires only that it finished), and it routes through the same
  MLIP pipeline (`startswith("mlip")`) as `mlip_opt`. Useful for a quick MACE
  energy on a fixed structure, or a MACE energy on an ORCA/CREST geometry via the
  reference source.
- **OMol25-based MACE models** in the MLIP model picker: **MACE-OMOL**
  (the dedicated OMol25 model — ωB97M-V, 83 elements, charged + open-shell —
  loaded via `mace_omol`) and **MACE-MH-1** (multi-head, includes an OMol head,
  via `mace_mp(model='mh-1')`). `parse_mace_model` now maps a label to
  `(loader, model_arg)` so these non-size models are expressible. The MLIP build
  card gains **charge / multiplicity** inputs: they flow to the worker as
  `atoms.info["charge"]`/`["spin"]` (spin = the multiplicity 2S+1, MACE's
  convention) so OMol25 / multi-head models handle ions and radicals correctly;
  MACE-OFF / MACE-MP ignore them. A non-neutral-singlet MLIP queue row now shows
  its charge/mult.
- **CREST backend** (`orcamgr/crest/`, kept off the ORCA pipeline like
  `orcamgr/mlip/`): a `crest_conf` calc kind for conformer search (v1 scope).
  `runner.py` launches CREST detached in WSL (`setsid`, ext4 scratch, results +
  a `.rc` marker copied back to the Windows workspace folder), tails the `.out`
  for live progress, and cancels via process-group kill / reattaches across a
  restart using the Linux pid + start-time (the WSL analogue of psutil identity).
  `parser.py` reads `crest_conformers.xyz` + `crest.energies` into the shared
  `ParseResult` (new `conformers` ensemble + `Conformer`), so a chosen conformer
  hands off to ORCA through the existing geometry path. Validated end to end
  against a real CREST 3.0.2 install in WSL Ubuntu.
- **CREST build mode** — a fourth Build-tab mode (`beginner` / `expert` / `mlip`
  / `crest`) with a self-contained card (name, charge/multiplicity, method
  `GFN2`/`GFN-FF`/`GFN0`, ALPB solvent, energy window, threads), locked until a
  WSL distro with CREST is ready.
- **"CREST ready" top-bar indicator** following the MLIP pill's four-state
  standard, backed by a background WSL probe (`Bridge.get_crest_status` /
  `check_crest`).
- **Auto-install of CREST** into a WSL distro (`Bridge.install_crest` →
  `crest/installer.py`): downloads the static release tarball, extracts, and
  symlinks it — no user shell interaction (the one manual prerequisite is a WSL
  distro existing). Distro selection + install live in **Settings → CREST**.
- **Conformer → follow-up pipeline fan-out** — the CREST build card gains a
  **Conformer handoff** scope: *lowest conformer only* (a reference receives the
  best conformer, the classic single-geometry handoff) or *all conformers*.
  Follow-up MLIP/ORCA calculations are built normally on the Build tab by
  referencing the CREST search from their geometry source; with the "all"
  scope, the moment the search finishes with K conformers every pending
  calculation chain referencing it (e.g. opt → freq) is substituted by one
  clone chain per conformer (`{name}_c1 … {name}_cK`, track by track), each
  clone carrying its conformer's geometry (first hop) or a same-track
  reference (deeper hops) — so a single queued template pipeline runs once per
  conformer, with per-track failure isolation, editing, and session
  save/restore behaving like ordinary queue rows. The Results tab's conformer
  list is now **read-only**: results are for interpretation, building happens
  on the Build tab.
- **Tests** — 25 CREST tests (now 262 pytest total): the ensemble parser against
  a real ethanol corpus (`tests/crest/fixtures/`), CLI-flag building
  (`--uhf = multiplicity − 1`), per-kind validation, the QueueEngine path via a
  fake runner (no WSL needed), and the conformer→ORCA batch builder.

### Changed
- **Lower idle footprint from the embedded browser.** The WebEngine view now
  disables web-platform features the UI never uses (WebGL — the wallpaper canvas
  is 2D — and Chromium's built-in PDF viewer), and **minimizing the window
  freezes the page** so Chromium runs its memory-pressure GC and drops raster
  layers while ORCAdesk sits in the taskbar during a long run (measured: main
  process ~750 → ~600 MB, renderer ~135 → ~123 MB while minimized; restore is
  instant and the log/queue catch up from where they left off). No behavior
  change while the window is visible.
- **The Build-tab mode toggle is now backend-first: `DFT / MLIP / CREST`, with a
  `Beginner / Expert` sub-toggle inside DFT** (shown only while DFT is active;
  the persisted `build_mode` values are unchanged, so saved settings load as-is).
  Switching **Beginner → Expert converts the current form to a generated `.inp`**
  in the editor (the former "Edit raw .inp" button, now removed, was this
  conversion) — the linkage is deliberately one-way: raw text can never be
  converted back to the form, so **Expert → Beginner asks to discard** the editor
  content (name, type, charge/multiplicity, and geometry stay — they live outside
  the editor). A failed conversion (duplicate name, generator error) stays on the
  filled form instead of switching; with no name yet, Expert opens as a plain
  empty editor (said in the log) for the paste/load workflow. The **method form
  survives the round trip** — Expert/MLIP/CREST excursions only hide it, so
  coming back never resets it to defaults — and re-clicking the already-active
  mode button is a strict no-op (it used to silently drop an in-progress edit).
  Editing a raw calculation opens the Expert editor; editing a form
  calculation opens the Beginner form (the old "Beginner with a dimmed, locked
  form" hybrid raw state is gone), and backing out of a not-yet-saved
  Beginner → Expert conversion of an edit reopens the form edit (confirmed,
  editor text discarded) — only a **saved** raw calc is locked to raw. Raw
  `.inp` text now also survives an MLIP/CREST-mode excursion instead of being
  silently cleared.
- **MLIP build card gains a geometry source selector** (`.xyz` file / from
  another calculation), mirroring the ORCA build card. An `mlip_opt`
  pre-optimization can now take its starting geometry from another queued calc's
  optimized result (a CREST best conformer or an ORCA/MLIP opt), injected at run
  time through the same `_resolve_geometry` reference path — the engine already
  supported it; only the build UI had been `.xyz`-only.
- **Settings → CREST: the Install CREST button is disabled once CREST is present
  in the target distro** (auto-detect ⇒ any distro; a specific pick ⇒ that
  distro), so a redundant re-install isn't offered.
- **Loading a `.xyz` adopts its folder as the workspace.** Every `.xyz` loader
  (ORCA / MLIP / CREST build cards, NEB product, drag-and-drop) opens the file
  dialog at the current workspace and, on load, sets the workspace to the chosen
  file's folder — so a project's inputs and its calculation output land together
  without a manual Settings edit. The adopted path is echoed back on `LoadResult`
  (new `workspace` field) to keep the Settings field in sync, with a toast.
- **Queue rows show an execution-backend badge** — `MLIP` / `CREST` / `DFT`
  (ORCA) — next to each calculation name, generalizing the former MLIP-only badge.
- **Number inputs no longer show the native up/down spinner arrows** (global
  style) — the value is typed, and the steppers only cluttered the field.
- **Live CREST progress visualization in the Log → Graph tab.** A running CREST
  conformer search shows a phase-chain HUD (initial opt → metadynamics sampling →
  additional MD → final opt → done, with the per-round MTD count, the iMTD-GC
  iteration, and the conformer count). A new `CrestTracker` (`web/scf_graph.js`,
  unit-tested in `tests/web/scf_graph.test.js`) parses the streamed `.out`;
  `bridge.get_graph_lines` also keeps CREST markers so a reattached run rebuilds
  its progress. Validated against real CREST 3.0.2 output.
- **The staged-pipeline display (CREST, analytical frequency, TD-DFT) is a
  vertical stepper** — an all-mono title + `RUNNING`/`DONE`/`STOPPED` pill over a
  meta line (`method · cores · elapsed`), then one row per stage showing the
  stage label, its **key result** (e.g. `1,204 structures collected`, frozen as
  each stage ends), and the stage's **wall time**, with a dot rail that fills
  green as stages complete and the current stage pulsing. The rail **follows the
  window height**; when the panel is too short to fit the rows it falls back to a
  compact strip (the old HUD frame minus its hazard stripes, same text). Replaces
  the earlier horizontal timeline; per-stage times come from the tracker's
  stage-entry stamps (suppressed on a reattach-rebuilt tracker).
- **CREST progress now tracks the full iMTD-GC pipeline** — the stepper's stages
  are Initial optimization → Metadynamics sampling → Regular MD → **Genetic
  crossing (GC)** → Final optimization → **Ensemble sorting (CREGEN)**, adding the
  GC structure-crossing and terminal CREGEN sort phases that were previously
  folded away. Genetic crossing is conditional (CREST skips it when there's
  nothing to cross, e.g. a one-conformer molecule); a skipped stage now reads as
  `skipped` (hollow dashed node) instead of a misleading instant "done". Stage
  set verified against the real CREST 3.0.2 / ORCA 6.1.1 output corpus, which
  also confirmed the analytical-frequency (7-stage) and TD-DFT (5-stage) chains
  are complete.
- **Per-conformer track clones show their provenance** in the queue — a track
  clone whose geometry was baked in from a conformer search reads
  `from {search} · conformer {k}` instead of a bare `xyz` (new display-only
  `Calculation.conformer_origin`, persisted across restarts).
- **Log tab: the graph toggle is renamed "SCF graph" → "Graph"** (it now also
  serves optimization, frequency, TD-DFT and CREST views), and the empty-graph
  placeholder reads "waiting for data…" instead of "waiting for SCF data…".

- **CREST failure diagnosis is subdivided into named, actionable causes.**
  Beyond the segfault case, a **topology-change safety termination** (CREST
  stopping because the initial optimization changed the molecule's bonding — a
  strained/unphysical input geometry, e.g. a flat ring) is now reported with the
  cause and the fix (pre-optimize and reference it, or use GFN-FF), instead of a
  generic exit-code message. The signature list (`crest/parser.py`) is extensible
  as more real failure modes are observed.

- **More CREST conformer-search options.** The build card adds **GFN1-xTB** to
  the method list, a **Search speed** preset (`--quick` / `--squick` / `--mquick`),
  and an **NCI mode** toggle (`--nci` — an ellipsoid wall potential that keeps a
  non-covalent complex from dissociating during sampling). An **Advanced settings**
  section adds the solvent model (ALPB/GBSA), MD/MTD knobs (`--mdlen` multiplier,
  `--tstep`, `--tnmd`, `--mddump`, `--vbdump`), and toggles for `--cbonds`,
  `--subrmsd`, `--norotmd`, `--keepdir`, and `--cluster` (with the manual's
  single-molecule-only caveat noted inline). All are optional (unset = CREST
  default) and validated/clamped at the deserialization boundary.

### Fixed
- **Backend-pass fixes** (a bug-hunt sweep over the Python layers, mirroring
  the earlier GUI passes):
  - *Queue names are now unique case-insensitively.* `water` and `Water` are
    the same folder on Windows, so both being accepted made two calculations
    silently share one on-disk `.out` — the first calc's Results tab,
    geometry handoff, and keep-existing check all served the second calc's
    numbers.
  - *A restored RUNNING calculation whose detached ORCA already exited is
    judged from its output, never relaunched.* If the startup reattach was
    declined (e.g. invalid ORCA path) and the job finished later, the next
    Run would truncate the completed `.out` and recompute it from scratch;
    it now adopts the result (DONE if terminated normally + valid, FAILED
    otherwise), exactly like the startup reconcile and the CREST path.
  - *Stop pressed in the pre-launch window is no longer swallowed.* The
    runners cleared their cancel/detach signals at launch/adopt, so a Stop
    that landed while the engine was still resolving geometry / writing the
    input let that calculation run to completion anyway. Events are now
    sticky for the run, and the engine forwards a Stop to a CREST/MLIP
    runner registered after the signal.
  - *Stop now reaches a silent MLIP worker.* Cancel/shutdown were only acted
    on when the worker printed a line, so a worker busy downloading a model
    or inside a long optimizer step ignored Stop indefinitely (and could
    outlive the app as an orphan). A watcher thread now terminates it on the
    signal itself.
  - *A crashed MLIP worker can no longer resurrect a removed same-named
    calc's result.* The stale `{name}.mlip.json` is deleted before launch
    and a worker that exits without writing a fresh result fails the calc
    (folders survive removal, so the old JSON used to be adopted as DONE —
    wrong molecule included).
  - *The cancel sweep leaves a reattach-pending RUNNING row alone.* Stamping
    it CANCELLED dropped the pid — the only handle to the live detached
    ORCA — without killing anything, orphaning the process and inviting a
    second launch into the same folder.
  - *A transient WSL hiccup can no longer FAILED-lock a healthy CREST run.*
    One failed `wsl.exe` liveness call (timeout, service restart) made the
    monitor give up tailing; the resulting no-ensemble parse locked the calc
    FAILED while CREST later delivered a good result. Only a definitive
    "process gone" (or ~2 min of continuous WSL failure) ends the tail now.
  - *The CREST probe now searches a login-shell PATH*, so a user-managed
    install (conda, `~/.profile` PATH) is found — the probe claimed to but
    ran a non-login shell.
  - *Cancelling a CREST run cleans its WSL scratch directory* (previously
    each cancelled search leaked its MD trajectories — easily hundreds of
    MB — inside the WSL disk forever), and *re-exporting a smaller conformer
    ensemble removes the previous export first* (mixed zero-padding used to
    leave stale `_c10..` files that looked current).
  - *A corrupted `session.json`/`settings.json` can no longer crash every
    startup.* Valid-JSON-but-not-an-object session files (and wrong-typed
    `mlip_envs` settings entries) now degrade to defaults instead of raising
    before the window exists — a crash loop only deleting the file by hand
    could break.
  - *Environment-probe results can no longer go stale-over-fresh.* A slow
    MLIP/CREST probe finishing late overwrote a newer probe's result (the
    indicator flipped back to error and re-locked the build card); probes
    now carry a generation and late results are discarded.
  - *Cancelling the "Open ORCA .out" dialog no longer logs a parse error*,
    and the `.inp` viewer, graph reseed, and conformer export now resolve a
    restored calc's folder through its persisted output path (after a
    workspace change they failed for a calc whose results still displayed
    fine).
  - *Shutdown stops the phone server first*, closing the window where
    `/api/run` could start a run on the already-paused store mid-teardown.
  - *Input generation: choosing AutoAux as the RI approximation no longer
    emits the keyword twice* for MP2/double-hybrid methods (ORCA aborts on
    duplicated simple-input keywords).
  - *Parser: a single-root TD-DFT run no longer double-counts state 1* (the
    velocity-gauge table below was absorbed into the transitions list);
    *with Triplets enabled the singlet excited-state compositions are kept*
    (last-wins used to keep only the triplet block, mislabeled against the
    singlet absorption table); *long IRC profiles are no longer truncated*
    at ~200 path rows.
- **An ORCA job that finishes while ORCAdesk is closed is now restored as
  DONE.** The reconcile step always had a "terminated normally + valid →
  DONE" branch, but it was unreachable: the output path it judges from was
  only recorded after a *monitored* finish, so a detached job that completed
  while the app was closed came back as a locked FAILED ("Interrupted while
  ORCAdesk was closed") despite a perfect result on disk. The output path is
  now persisted at launch (and on reattach), like the CREST runner always did.
- **Closing the app mid-MLIP-run no longer locks the calculation FAILED on
  the next start.** Shutdown deliberately terminates the MLIP worker (it has
  no detach/reattach machinery), but the calc stayed RUNNING in the saved
  session, so the next launch judged it "interrupted" and locked it. It is
  now stamped CANCELLED ("Stopped on shutdown.") at detach — and the
  reconcile step applies the same judgment for sessions where the app exited
  before the stamp landed (a worker that raced to completion still restores
  as DONE from its result file).
- **Reusing a removed calculation's name no longer shows the removed
  calculation's results.** The Results tab's per-name cache was never
  invalidated (and a cache hit suppressed re-fetching), so after remove →
  re-add-same-name → run, the tab kept serving the OLD molecule's numbers for
  the new DONE calc until an app restart — and the Results picker kept listing
  long-removed calcs. Removing a calc (or clearing the queue) now drops its
  cached result too.
- **Queued results are now parsed by calculation kind, not folder contents.**
  The Results tab routed a queued calc's output through the same folder
  heuristic used for externally dropped files, so an ORCA calc reusing a
  removed CREST calc's folder (leftover `crest_conformers.xyz`) was parsed as
  a conformer search. Queued results now go through a name-based bridge call
  that dispatches on the calc's kind (the heuristic remains for external
  files, which have no kind).
- **"Export as .xyz" no longer targets the wrong calculation after a drag-and-
  dropped .out.** Dropping an external `.out` kept the previously viewed
  queued calc's identity, so exporting from a dropped CREST result wrote the
  *queued* calc's conformers instead. The drop path now clears the queued-calc
  association exactly like the Open-.out dialog path.
- **Reference dropdowns stay live on poll-delivered queue changes.** A calc
  added by a phone client or created by the conformer fan-out never appeared
  in the "From another calculation" selects until a local add/remove — making
  dangling references easy to queue (they were only refused at run start).
  The poll's queue-change branch now refreshes both selects like local
  mutations do; the phone's `/api/run` also validates references up front now,
  through the same shared check as the desktop Run.
- **Dropping a `.xyz` on the DFT form now switches the geometry source to
  "From .xyz file".** With the form on "From another calculation" the dropped
  coordinates were loaded invisibly (the status label lives in the hidden
  branch) and silently discarded on Add — inconsistent with the MLIP card's
  drop behavior.
- **A stale "Skipped: a dependency failed." row is reset at run start.** After
  re-pointing a BLOCKED calc's reference (or removing its failed parent), a
  re-run left the old BLOCKED badge on rows that were about to run, until the
  engine's walk happened to reach them. Rows whose ancestry is clean are now
  normalized to PENDING up front.
- **Clearing the log mid-run no longer replaces the live graph with a
  finished CREST search.** Clear resets the graph trackers; the disk re-seed
  fallback then picked the most recent DONE conformer search even while an
  ORCA/MLIP job was running, and its tracker took over the graph panel for
  the rest of the run. The done-calc fallback now only fires while nothing is
  running. Also, a graph re-seeded from a finished opt+freq output no longer
  reads "running frequencies / properties" — the stage is labelled complete
  once the output's termination banner is seen.
- **CREST card Reset now restores the basic fields too** (charge,
  multiplicity, method, solvent, energy window, threads) — previously only
  the geometry, handoff, and advanced knobs were reset, unlike the MLIP and
  DFT cards.
- **MLIP results now open with the MLIP parser in the Results tab.** A finished
  MLIP calculation's result file (`{name}.mlip.json`) was fed to the ORCA text
  parser, so the Results tab showed **ABNORMAL / incomplete** (red) plus the raw
  JSON dumped as an Error row — for a successfully converged calculation whose
  queue row said DONE. `bridge._parse_path` now dispatches per backend
  (`.mlip.json` → the MLIP parser), the path-based mirror of the engine's
  `result_from_output` kind dispatch. The free-energy profile's parse-on-miss
  now uses that same dispatch too — previously it could cache a wrong ORCA
  parse onto a restored MLIP/CREST calc's `result`, which the engine's
  reference resolution would then trust.
- **Dropping a `.xyz` while the MLIP or CREST build card is shown now loads
  that card.** The drop used to write the hidden DFT card's geometry: the log
  said "Dropped .xyz loaded" while the visible card stayed empty and **Add to
  queue** refused with "Load an .xyz file first." The drop now routes to the
  card the user is looking at (selecting the direct-geometry branch on the
  MLIP card), and a locked card explains itself at drop time instead of at Add.
- **Queue rows now show the MLIP model and CREST method.** The backend always
  sent them, but the desktop row rendered only kind/source/charge — with MLIP
  in-place editing unsupported and no `.inp` view, the chosen MACE model was
  invisible anywhere on desktop. MLIP rows now append the model, CREST rows the
  tight-binding method plus an "all conformers" tag when that handoff is set
  (new explicit `CalcSummary` fields, rendered escaped).
- **IRC outputs are no longer titled "NEB-TS reaction path".** ORCA's
  `IRC PATH SUMMARY` table matches the same parser scan as the NEB-TS one; the
  parser now records which table it read (`neb_path_kind`) and the Results tab
  titles an IRC profile **"IRC path (N steps)"**, dropping the
  reactant/product endpoint captions and the NEB-specific barrier/reaction-energy
  hint that don't apply to an IRC.
- **Thermochemistry parsed from an output without a frequencies table now shows.**
  Gibbs/enthalpy/entropy rows were gated behind the `VIBRATIONAL FREQUENCIES`
  table, so e.g. an IRC job reading a Hessian had its parsed **Final Gibbs G**
  silently dropped from the summary — invisible even with **Show all** (the
  gate was server-side, violating the "all rows always emitted" contract). Each
  thermo row is now emitted whenever its value was parsed.
- **Front-end `[web]` log lines now survive long runs.** ORCA's stdout flows
  into the same capped log ring (trim to the newest 4 000 lines), so a single
  long run could evict every console-captured `[web]` error before it was ever
  read — defeating the capture's diagnostic purpose. The trim now retains older
  `[web]` lines preferentially (up to 500) alongside the newest 4 000.
- **CREST/MLIP results no longer show an "ORCA version: ?" row.** The row is
  emitted only when a version was actually parsed — it is meaningless for
  non-ORCA backends.
- **Copy sweep (second 0.5.0 pass, DESIGN.md B26).** Eight strings brought onto
  the copy spec: the free-energy-profile placeholder ("FREQ jobs" → "Freq
  calculations"), the Appearance card description and three hints (multi-sentence
  → one-line noun form), two glass-intensity tooltips (verb clauses → noun
  phrases), and the MLIP build card title — **"MLIP pre-optimization" →
  "MLIP calculation"**, since the card also runs single points (`mlip_sp`).
- **Liquid Glass chrome now self-heals from compositor layer drops.** Even
  with the compositor-safe glass architecture, an external GPU event
  (sleep/resume, driver reset, GPU memory pressure) could still make Qt
  WebEngine drop one of the chrome bars' composited layers from the
  on-screen picture — seen in the field at Vivid as an empty top-bar slab
  (text gone, tint intact) with the tab strip fully invisible — and, because
  nothing ever repaints a static bar, the loss was permanent until restart.
  While Liquid Glass is active the app now pulses an imperceptible paint
  delta (±0.4% alpha/saturation, `--lg-pulse`) through all three composited
  pieces of both bars every 250 ms, so any dropped layer is re-rastered
  within a quarter second (DESIGN.md §16.5 rule 4). Measured on screen: the
  pulse changes at most 4/255 on <0.01% of the bars' pixels — invisible.
- **Uploading a custom wallpaper no longer swallows the ＋ upload tile.**
  The uploaded image used to become the ＋ tile's own thumbnail, removing the
  only affordance for picking a *different* image (and re-selecting the
  existing image took two clicks). The custom image now gets its own swatch
  in the wallpaper grid (hidden until an image exists) and ＋ is a pure
  upload action that always opens the file picker.
- **Changing the calculation Type no longer silently resets fields the user
  set.** The method form re-render now preserves the kind-independent
  resources (**maxcore**, **nprocs**) and the numeric fields shared between
  kinds (**MaxIter** across the opt kinds; **Temperature/Pressure** across the
  freq kinds — previously e.g. freq → opt+freq quietly dropped a 310.15 K
  setting back to 298.15 K, and since the default omits the `%freq` block the
  loss was invisible in the `.inp`). **SCF convergence** and **Extra options**
  are preserved kind-awarely: an explicit user override survives every switch,
  while an untouched default still follows the new kind — so switching to
  NEB-TS regains its default `FREQ` option (previously it was clobbered by the
  old kind's empty options, silently skipping the one-imaginary-frequency TS
  validation) and opt → freq still bumps TightSCF → VeryTightSCF.
- **Raw NEB-TS runs get the same pre-launch guards as form runs.** A raw
  calc whose text keeps the generated `NEB_End_XYZFile "product.xyz"`
  reference but has no stored product geometry now fails fast with a clear
  message instead of launching ORCA doomed (or silently reusing a stale
  `product.xyz` left in the folder by an earlier same-named run; pointing the
  file at your own path remains allowed). When a product *is* stored, the
  reactant/product atom-order check now runs for raw calcs too — against the
  coordinate block that actually executes (the rendered text).
- **NEB product state can no longer desync from the UI.** Editing a calc
  syncs the stored product unconditionally, so a product-less calc can't
  silently inherit the previously handled calc's product on Update; a kind
  round-trip (NEB-TS → other → NEB-TS) no longer shows "no product loaded"
  while a loaded product silently stays in effect; and re-loading the
  *reactant* re-runs the reactant/product match check, so a stale green
  "✓ match" can't survive a mismatched replacement. Adding a raw NEB-TS that
  references `product.xyz` without a loaded product is refused at Add time.
- **"Keep existing (skip these)" no longer adopts a foreign result.**
  Workspace folders are never deleted, so a new calc reusing a removed calc's
  name could be stamped DONE with the *old* calc's energies/geometry. The
  keep-existing path now verifies the on-disk result structurally belongs to
  the calc (atom element sequence) and falls back to running on mismatch.
- **IRC "read from .hess file" is honored, staged, or refused — never
  silently substituted.** A blank filename used to silently generate
  `InitHess calc_anfreq` (a different, potentially far more expensive method);
  it is now refused at Add time and at the generator. A named `.hess` that
  isn't in the run folder is auto-copied from the referenced calc's folder
  before launch; if it can't be found, the run fails fast with the paths it
  looked in, instead of a cryptic ORCA abort.
- **The queue's "view input" shows the input that will actually run.** For a
  still-editable calc (pending/cancelled/blocked) the preview is now built
  from the calc itself first — previously a leftover `.inp` from a removed
  same-named calc was shown as if it were this calc's input. Running/finished
  calcs still show the on-disk file that actually launched.
- **Raw-mode geometry can no longer silently desync from the text.** Loading
  a `.xyz` while editing raw input without a `{{GEOMETRY}}` placeholder is
  refused (the embedded `* xyz` block runs verbatim — the loaded file was
  stored but ignored); editing a *reference*-geometry calc clears the leftover
  direct-geometry state, so flipping the edit to Direct can't silently adopt
  another calc's coordinates; and a raw calc's queue row now shows the
  charge/multiplicity from its own `* xyz C M` (or `* xyzfile C M`) line,
  not the hidden form fields' 0/1.
- **A frequency Temperature/Pressure of exactly 0 is no longer coerced back
  to the default.** The form reader treated 0 as "unset" (falsy), so a 0 K
  request (ZPE-only thermochemistry) silently became 298.15 K with no trace
  in the `.inp`.
- **Liquid Glass no longer loses the top bar / tab strip.** With enough
  backdrop-filter surfaces on one tab (the Settings or Build tab at
  Vivid/Maximal: card lenses + frosted buttons + both chrome bars), Qt
  WebEngine's Windows compositor dropped whole promoted layers from the
  on-screen composite — the tab strip (and sometimes the top bar) rendered
  fully invisible and stayed that way, since nothing re-invalidated it.
  Reproduced deterministically and fixed by a layer-budget redesign
  (DESIGN.md §16.5): the SVG refraction lens is now chrome-only (cards frost
  with native blur), buttons get a glass tint instead of a backdrop filter,
  and every backdrop chain moved to a decorative `::before` overlay so a
  failed filter pass can no longer take a bar's tint/border/text with it.
  Verified 12/12 clean runs on the previously 6/6-failing reproducer.
- **Loading a `.xyz` no longer moves the workspace.** The geometry loaders used
  to adopt the loaded file's parent folder as the workspace. Because an
  optimization writes its result `.opt.xyz` inside the calc's own run folder
  (`{workspace}/{name}/`), reloading that geometry for a follow-up step silently
  descended the workspace into that calc folder — and each further run nested
  again (`{workspace}/{name}/{next}/…`). Loading a geometry is now fully
  decoupled from the workspace: the workspace is set **only** from Settings.
  (`LoadResult` drops its `workspace` field; the front-end no longer syncs or
  toasts a "Workspace set to …".)
- **The CREST top-bar pill's dot turns red on an error.** `renderCrest` toggles
  the `.err` class like the MLIP pill, but the `#crest-status.err .dot` style
  rule was missing — an error pill showed "CREST error" text over a grey dot.
- **The option pickers no longer offer keywords ORCA 6.1.1 rejects.** Every
  keyword in `data/*.json` was probed against the real ORCA 6.1.1 simple-input
  parser (mirroring the app's own emission, including functional normalization);
  48 tokens that abort with `UNRECOGNIZED OR DUPLICATED KEYWORD(S) IN SIMPLE
  INPUT LINE` were removed so they can't be built into a failing `.inp`:
  - **SCF**: `MediumSCF` (no such tier) — also mapped to `NormalSCF` at the trust
    boundary (`StepConfig.from_dict`) for any calc/session still carrying it.
  - **Grids**: the legacy `Grid3`–`Grid7` / `FinalGrid4`–`6` (removed in ORCA 6;
    use `DefGrid1`–`3`).
  - **Calc types**: `OptFreq` (write `Opt Freq`), `MECPOpt` (use `MECP-Opt`),
    `ConicalIntersection`, `OvertonesFreq`, `QuasiRRHO`, `Gradient` (use
    `EnGrad`), `MTD`, and the block-only excited-state / property tokens
    `TDDFT`/`TDA`/`CIS`/`RPA`/`sTDA`/`sTDDFT`/`ROCIS`/`DFT-ROCIS` and
    `EPR`/`EPRNMR`/`MOSSBAUER`/`POL`/`Polarizability`/`HFC`/`HFC_NMR`/`PNMR`
    (these are configured in `%tddft`/`%cis`/`%eprnmr`/`%elprop` blocks — the
    dedicated TD-DFT and NMR build modes still work; `NMR`, `MD`, `EOM-CCSD`,
    `STEOM-CCSD`, `DLPNO-STEOM-CCSD`, `ADC2` remain, being valid on the `!` line).
  - **Options**: `AutoTRAH`, `FullLshift`, `FlipSpin`, `NoZORA`, `NoDKH`,
    `FC_ELECTRONS`/`FC_NONE`/`FC_EWIN`, `ReadGuess`, `ONIOM` (all block-only).
  - **RI**: `RIJ` (ORCA's RI-J for GGAs is `RI`).
  - **Functionals**: `MR-EOM-CC`, `INDO/1`, `INDO/2`, `CNDO/1`, `CNDO/2`
    (unsupported in 6.1.1; `ZINDO/1`, `ZINDO/2`, `ZINDO/S` remain).
- **The geometry-reference dropdowns stay in sync with the queue.** They were
  only rebuilt when the "From another calculation" radio was toggled, so a calc
  added while that source was already selected went missing from a stale list
  (and couldn't be picked as a reference). They now refresh on every queue update
  (selection preserved).
- **The `.inp` button no longer appears on MLIP/CREST queue rows.** Those backends
  produce no ORCA input, so the button fell through to a bogus generated ORCA
  `.inp` preview; it is now ORCA-only.
- **Copy conformance sweep on the new CREST/MLIP card descriptions (DESIGN
  §14/§11.1).** Five `.card-desc` strings introduced with the 0.5.0 CREST/MLIP
  work drifted from the copy spec: the raw-`.inp` and Results card descs called a
  queued **calculation** a *job* (§14.2 reserves *job* for the running process),
  and the CREST-build, MLIP-environment, and CREST-settings descs ran to
  multiple/imperative sentences instead of a one-line noun-form fragment
  (§11.1/D70). All five reworded to spec — the 0.4.3 copy sweep (B20) predates
  this copy. Surfaced by a shadcn/DESIGN conformance review of the design
  previews, which confirmed the rest of the preview set is clean.

### Notes
- An all-CREST (or all-MLIP) queue runs with **no ORCA executable configured**
  (`queue_needs_orca` excludes both) — the shared gate for the desktop and phone
  run entry points.
- A failed CREST run reports the real cause from its exit-code marker: a crash
  (e.g. segmentation fault, exit 139 — an intermittent CREST 3.0.2 bug) reads as
  a crash with a "retry" hint, not a bland "no conformer ensemble".

## [0.4.3-beta] — 2026-07-03

Constitution-compliance release: the amended failure-lock rule (PRINCIPLES.md
P24) is implemented end to end, the appendices' fix backlog (PRINCIPLES.md
Appendix A, DESIGN.md Appendix B) is worked off, and the project gains its
first automated test suite.

### Added
- **Automated test suite** (`tests/`, PRINCIPLES.md P56): 237 pytest tests
  over the framework-free layers — queue-engine semantics (the P24 failure
  lock, dependency-scoped blocking, keep-existing validation, the three stop
  verbs) driven by a fake runner, per-kind result validation, store/session
  persistence and the structural run-freeze, name validation and
  trust-boundary clamps, input-generator normalization (exact-map
  functionals, `D3BJ`, `_auto_aux`), the parser on synthetic fixtures (plus
  a real-corpus smoke test that auto-skips off-machine), the MLIP pipeline
  end to end with a **stdlib-only stub worker** (no MACE env needed), the
  phone HTTP API via `fastapi.testclient` (auto-skips without fastapi), and
  psutil process identity/tree-kill with real child processes — plus 23
  plain-Node tests for `scf_graph.js` trackers (idempotent cycle keying, the
  99 % cap, marker case-sensitivity, ETA buckets). `pip install -r
  requirements-dev.txt`, then `python -m pytest` and
  `node tests/web/scf_graph.test.js`; the suite runs in ~3 s with no ORCA,
  no MACE, and no network. On its first run it caught the three boundary
  bugs fixed below.

### Changed
- **A failed calculation is now locked at the moment of failure.** It can no
  longer be edited, re-run, or reordered: re-running the queue skips it (with
  a log note saying to build a new calculation to retry), its transitive
  dependents are blocked up front exactly as a live failure would block them,
  and Cancel never re-stamps it. Its remaining interactions are read-only
  diagnosis (state badge, failure message, its on-disk output) and **×
  removal** — which removes only the queue entry; **workspace folders are
  never deleted** (the app contains no file-deletion code). Only a
  **cancelled** calculation (a deliberate user stop, not a failure) re-runs.
  BLOCKED calculations are now editable, so a dependent can be re-pointed at
  another calculation after its failed parent is removed —
  `EDITABLE_STATES` is now pending / cancelled / blocked, mirrored by the
  UI's edit/drag affordances.
- **"Keep existing (skip these)" now verifies the result it keeps.** The kept
  output is parsed through the per-kind dispatcher (an MLIP calc's JSON
  result is no longer fed to the ORCA parser) and stamped `DONE` only if it
  passes per-kind validation — a crashed or chemically-bad output can no
  longer be resurrected as `DONE` (the 0.4.2 incident). If the existing
  result doesn't hold up, the calc simply runs instead.
- **The overwrite-conflict warning is scoped to PENDING/BLOCKED name reuse.**
  DONE never re-runs, FAILED is locked (nothing to overwrite), a CANCELLED
  retry overwriting its own partial `.out` is the point of a retry, and a
  RUNNING calc only ever appends to its own `.out` (reattach is not an
  overwrite) — none of those raises the dialog anymore. Cancelling a run
  also preserves a BLOCKED calc's "Skipped: a dependency failed." diagnosis
  instead of re-stamping it "Cancelled."
- **File-loader slots return one unified JSON envelope** (`LoadResult`) that
  distinguishes user-cancel from a real read failure — cancelling a file
  picker is no longer conflated with an I/O error — and `autodetect_orca`
  returns an explicit `AutodetectResult` envelope (the UI now also refreshes
  the ORCA-ready pill after a successful autodetect).
- **UI conformance sweep against DESIGN.md**: segmented toggles converge on
  one canonical size and the Build-mode toggle regains a visible surface at
  page level; MLIP environment rows restyle onto the canonical list-row
  recipe; the modal radius uses `--radius-lg` and off-scale literal radii are
  normalized; dead CSS is removed (`.step-tab*`, `.btn-block`, the unused
  `--destructive` tokens); copy is normalized per the spec (`Could not
  {verb}: …` failures, `TD-DFT`, programmatic plurals instead of
  `calculation(s)`, `loaded (12 atoms)` inline statuses, the question-form
  `Overwrite existing results?` modal title, the colon-free `MLIP ready`
  pill, one single-sourced empty-queue string); failures now land on **both**
  channels (toast + persistent `err` log line) through one shared helper;
  the NMR results table gets the standard scroll wrapper and Results-chart
  ticks/dashes match the chart spec.

### Fixed
- **A pre-launch failure is no longer misdiagnosed from a stale `.out`**
  (PRINCIPLES.md A22). When a run fails before ORCA launches — e.g. geometry
  resolution after a rejected keep-existing result — the failure message now
  keeps the real cause instead of parsing the previous run's `.out` left in
  the folder (which would report the *old* failure, like "SCF did not
  converge", and send the user chasing the wrong problem). The `.out` is
  consulted only once the current attempt has actually launched (or
  reattached to) its ORCA process.
- **Three trust-boundary crashes found by the new test suite**: a
  `settings.json` containing valid JSON that isn't an object (a list, a bare
  string) crashed startup instead of degrading to defaults; a phone-API
  payload with a non-string `basis_assignments` element crashed
  deserialization; and non-string values for string-typed `StepConfig`
  fields (`kind`, `options`, solvation) slipped through deserialization to
  crash later inside `build_input` — all three now degrade to defaults at
  the boundary, with regression tests.
- **Failure verdicts in the Results summary rendered green.** The value
  colorizer's success pattern (`/converged|Normal/i`) re-matched the
  substrings of `NOT converged` and `ABNORMAL`, repainting both failure
  verdicts as success; the checks are now exclusive, failure patterns first.
- **An MLIP job terminated on shutdown no longer logs "left running in the
  background."** MLIP jobs carry no detach/reattach machinery and are
  stopped on shutdown; the log now says so honestly ("stopped on shutdown").
- **An all-MLIP queue can now be started from the phone API — and from the
  desktop UI without an ORCA path.** The `/api/run` endpoint shares the
  desktop's ORCA-needed decision (`queue_needs_orca`, single-sourced in
  `state/store.py`) and passes the registered MLIP environments to the
  engine — previously it unconditionally required a valid ORCA path and MLIP
  calcs launched from the phone could not resolve an interpreter. The
  desktop's own Run button mirrors the same decision instead of always
  demanding an ORCA path, and locked FAILED ORCA calcs (which never launch
  ORCA) no longer count toward needing one. The shared `scf_graph.js`'s
  criterion-series tokens (`--crit-*`) are also defined in the mobile PWA
  now, so its graphs no longer render transparent series.
- **The server status no longer reports "available" when fastapi isn't
  installed** (`get_server_status` now consults
  `ServerController.is_available()` instead of the controller's mere
  existence).
- **TD-DFT excited-state composition is parsed from the right block.** The
  section marker is now case-sensitive (every freq thermochemistry section
  prints a lowercase look-alike, observed in 62 real non-TD-DFT outputs) and
  the **last** occurrence wins, per the parser-wide rule — validated against
  9 real TD-DFT `.out` files.
- **`Settings.save` is atomic** (tmp + `os.replace`, best-effort — the same
  treatment `session.json` already had), so a crash mid-save can no longer
  leave a half-written `settings.json` that loses every setting.
- **`charge`/`multiplicity` from clients are now range-clamped** (to ±100 /
  1–200) at deserialization, so a bogus payload can't emit a nonsense `.inp`
  coordinate header.
- **Escape gaps closed**: the overwrite-conflict name list, the
  remove-confirm calc name, and the queue row's `ref → name` label are now
  HTML-escaped.
- **The numerical-frequency progress no longer flashes "100%"** while the
  final displacement is still running (the displayed percent is floored, so
  the honesty cap actually shows).
- **Keyboard focus is visible** on buttons, tabs, segmented toggles, and
  remove buttons (a `:focus-visible` ring matching the input focus ring);
  previously only text fields showed focus.
- **Theme tints are token-derived.** Status-badge/toggle/error tints come
  from shared `--*-tint` tokens on the §11.19 alpha ladder (the light-theme
  raw-badge retint hack and its mismatched purple base are gone); the SCF
  graph's converged-zone shading re-colors in light theme (was a hardcoded
  dark-theme green); the combo dropdown dropped a stale pre-zinc fallback
  color; a BLOCKED badge now renders styled on desktop (neutral, like the
  other skipped states); the modal name list regains its monospace
  (undefined `var(--mono)` → `--font-mono`); new status pills default to a
  neutral dot instead of red.
- **Mobile PWA token bugs**: two uses of the undefined `var(--fg)`, `--muted`
  (a background tone) used as text color, and a hardcoded log background are
  fixed with the desktop's token names (`--foreground`,
  `--muted-foreground`, `--code-bg`).
- The dead `regenerate_token` method is removed and the token comment now
  states the truth: the phone-sync PIN is generated once per app launch and
  lives for the whole session. Stale docstrings were corrected (name
  uniqueness is enforced by the store, complete calc-kind lists, the MLIP
  package contents, six slots missing from the bridge's slot list), and a
  leftover Korean phrase in the store docstring is now English.

## [0.4.2-beta] — 2026-07-01

### Added
- **The MLIP build card is locked until a MACE environment is ready.** When no
  registered environment reports ready (checking / error / unset), the whole
  "MLIP pre-optimization" card greys out, its inputs and buttons are disabled, and
  a `Ready MACE environment required.` note appears. It unlocks automatically once
  a MACE environment becomes ready, and adding an MLIP job is guarded regardless.

### Changed
- **UI copy overhaul.** Card descriptions, hints, radio explanations,
  status/toast/log messages, and live-graph labels across Build / Queue / Log /
  Results / Settings were rewritten into concise **noun-form** phrasing, with
  redundant filler and restating removed (e.g. the `converged ≤ 1` graph label,
  the duplicated MLIP-card description, "Settings for this calculation.",
  "(e.g. heavy atoms)", "(the original view)"). No behavior change.
- **README** refreshed to match the current app — the three build modes
  (Beginner / Expert / MLIP), the full calc-type list (incl. NEB-TS, IRC, NMR,
  Opt+Freq…), the MLIP pre-optimization workflow, the Settings contents, and the
  `orcamgr/mlip/` package in the project layout.

## [0.4.1-beta] — 2026-06-30

### Added
- **The Results tab now surfaces every value the parser extracts.** In addition
  to the existing summary/spectra it shows the **final geometry** (coordinate
  table + a *Copy .xyz* button), the **full orbital-energy list** (HOMO/LUMO
  highlighted), **Mulliken and Löwdin atomic charges**, **Mayer population**
  (bond orders + per-atom valence), the **dipole moment**, **rotational
  constants**, the **SCF energy decomposition** (nuclear repulsion, electronic,
  one-/two-electron, kinetic, potential, virial ratio), an extended
  **thermochemistry breakdown** (inner energy *U*, enthalpy *H*, entropy term
  *T·S*, temperature, pressure), the **TD-DFT excited-state composition**
  (dominant orbital transitions per state), and the **input echo** (keywords +
  input block).
- **`Show all` toggle** in the Results header reveals every parsed value
  regardless of calculation type; the button is tinted while active.

### Changed
- **Result sections are gated to the relevant calculation type.** The final
  geometry shows only for optimizations; general electronic-structure properties
  (orbitals, charges, Mayer, dipole, rotational constants, SCF decomposition)
  show only for single-point / optimization jobs; freq/TD-DFT/NMR/NEB results
  show only their specialty. The `Show all` toggle overrides this gating.
- The parser (`parse_file`) now also extracts Löwdin charges, Mayer population,
  dipole moment, rotational constants, the SCF energy decomposition, the extended
  thermochemistry quantities, and the TD-DFT excited-state composition. Validated
  against real ORCA 6.1.1 output (sp / opt / freq / TD-DFT / NMR / NEB).
- **Queue cards are tidier:** charge/multiplicity are shown only for ORCA calcs
  (hidden for MLIP, where they are meaningless), the direct-geometry source label
  is simplified from `.xyz` to `xyz`, and the redundant "Completed." note is
  hidden for finished jobs (the `done` badge already conveys it).
- **Rolled back the convergence/SCF graph plot surface to the page tint**
  (`var(--background)`); it had been pure black/white since 0.3.4. The
  window-height fill from 0.3.4 is kept.

### Fixed
- **The top-bar version badge now derives from `APP_VERSION`** instead of a
  hardcoded string, so it can no longer drift from the actual version (the window
  title and About dialog already read `APP_VERSION`). The version is now
  single-sourced — bump only `APP_VERSION` in `orcamgr/paths.py`.
- Removed a duplicated *ZPE* row from the frequency summary.

## [0.4.0-beta] — 2026-06-28

### Fixed
- **The frequency/TD-DFT phase panel no longer lingers through `ts_opt` and
  `irc` runs.** ORCAdesk's `ts_opt` input sets `%geom Calc_Hess true` and
  `irc` sets `InitHess calc_anfreq`, so ORCA computes a full analytical
  Hessian inside optimization cycle 1 / before the IRC walk — the panel
  correctly appeared for that Hessian but then sat stale ("ASSEMBLING SCF
  HESSIAN") for the entire remaining run. A new `GEOMETRY OPTIMIZATION CYCLE`
  / `FORWARD IRC` / `BACKWARD IRC` banner now clears the chain, so the panel
  shows only while a Hessian (or, in raw-input excited-state opts, a TD-DFT
  stage) is actually being computed. Verified against freshly-run ORCA 6.1.1
  `OptTS Calc_Hess` and `IRC` outputs plus a per-kind exclusivity audit over
  263 classified real outputs (opt/opt_freq/ts_opt/ts_freq/irc/freq/tddft/
  nmr/neb_ts/sp): each panel appears only for its own stage, zero violations.
- **NMR runs no longer show the analytical-frequencies panel.** GIAO NMR (and
  polarizability) jobs solve their own CP-SCF equations and print the same
  "ORCA SCF RESPONSE CALCULATION" banner as the Hessian pipeline, which
  falsely activated the frequency phase panel. Shared banners now only
  *advance* an already-active chain; only the Hessian-specific banners
  (derivative integrals, SCF HESSIAN) can activate it. Verified against 286
  real ORCA outputs (60 opt, 38 opt+freq, 22 freq, 9 TD-DFT, NMR/SP/NEB/
  Docker/utility runs): zero false activations, zero missed stages.
- **A finished opt's graph seeded from disk now reads 100% / converged.**
  `get_graph_lines` defined the optimization-finished and post-opt-stage
  patterns but never included matching lines in its filter, so a graph rebuilt
  from the `.out` (reattach, or finished while ORCAdesk was closed) stayed at
  ~99% with no "✓ geometry converged" even for a converged opt.
- **The CP-SCF perturbation total no longer gets overwritten by later property
  solves.** Some runs print several "Number of perturbations" lines (the
  geometric 3N solve first, then smaller IR/EPR property solves); the first
  one now wins, and the Hessian-dimension / mode-count display uses the true
  3N from the atom count.
- **Opt-only runs no longer show the frequency-stage banner.** The freq/post-opt
  stage detectors matched ORCA's section banners case-insensitively, so
  mixed-case lines present in *every* output — the header credits ("pre 5.0
  version of the SCF Hessian") and the end-of-run property echo ("Properties
  with geometric perturbations:", "SCF Hessian ... NO") — falsely switched a
  plain optimization into the "Analytical frequencies" display and appended
  "running frequencies / properties…" to the converged line. The markers are
  now case-sensitive (the real banners are uppercase), verified against real
  opt-only and analytical-frequency ORCA 6.1.1 outputs.

### Added
- **"MLIP ready" status indicator + environment probe (multi-environment).** A
  second status pill in the top bar (next to "ORCA ready") reports whether any
  Machine-Learned Interatomic Potential environment is usable; hovering lists
  each registered environment and the backends it provides. Because ORCAdesk
  does **not** install the MLIP toolchain, the user registers their own Python
  environments under a new *MLIP environments* setting — **one per MLIP**, since
  different MLIPs pin conflicting dependencies (e.g. MACE and SevenNet need
  different `e3nn`) and cannot share a venv. For each, the indicator does an
  honest **import probe** rather than a mere file-exists check: it shells out to
  that interpreter, **auto-detects** which known MLIP backends import (MACE,
  SevenNet; the registry is extensible), and only goes green when the common
  deps (`torch`, `ase`) plus at least one backend actually load — showing the
  detected backend/Python versions, or naming what is missing otherwise. Probes
  run in background threads (importing torch is slow) and the UI polls, so it
  never blocks. New, deliberately ORCA-independent package `orcamgr/mlip/` holds
  the detection logic; new bridge slots `pick_mlip_python` / `add_mlip_env` /
  `remove_mlip_env` / `check_mlip` / `get_mlip_status`. This is the first piece
  of the planned MLIP→ORCA bridge (pre-optimize cheaply with an MLIP, then refine
  with ORCA).
- **MLIP build mode + MACE pre-optimization that actually runs.** The Build tab
  gains a third mode next to Beginner/Expert: **MLIP**. It hides the ORCA build
  form and shows a small dedicated form — pick a MACE model (MACE-OFF for
  organics or MACE-MP-0 for materials, three sizes each), load an `.xyz`, name
  it, and add a `mlip_opt` calculation to the same queue as ORCA jobs. Running
  the queue now executes MLIP jobs end to end: the queue engine shells out to the
  MACE interpreter registered in *MLIP environments*, runs an ASE `LBFGS`
  geometry optimization (CPU) with the chosen model, and streams its progress to
  the live log. The optimized geometry is parsed into the same result shape as an
  ORCA job, so a **downstream ORCA calc can reference an MLIP-optimized geometry**
  — pre-optimize cheaply with MACE, then refine with DFT — through the same
  "reference another calculation" mechanism as an opt→freq handoff (works across
  restarts too). An all-MLIP queue runs without ORCA configured. Validated end to
  end against a real MACE environment (MACE-OFF), including the MLIP→ORCA handoff.
- **JS console messages now land in the Log tab.** The WebEngine page forwards
  `console.*` output (and uncaught front-end errors) into the app log as
  `[web] level=... line=... source=...` lines, so UI failures are diagnosable
  in a deployed build without remote debugging. Identical repeated messages
  are rate-limited (once per 5 s with a suppressed-repeat count), so a JS
  error loop cannot flood the log buffer.

### Changed
- **TD-DFT runs get the same HUD-style phase panel** as analytical
  frequencies, with a 5-dot chain over ORCA's TD-DFT pipeline: XC-kernel
  setup → iterative diagonalization (live `RPA`/`DAVIDSON` label and
  iteration counter) → excited-state analysis → transition spectra → final
  CIS/TD-DFT total energy (banner order verified against 7 real ORCA 6.1.1
  TD-DFT outputs, both full TD-DFT and TDA). The panel center shows the
  requested root count.
- **The analytical-frequencies stage is now a HUD-style phase panel** —
  hazard-striped border, centered "ANALYTICAL FREQUENCIES" title, a `PHASE k/7`
  label, and a 7-dot phase chain (done = filled, current = pulsing) tracking
  ORCA's real Hessian pipeline: derivative integrals → CP-SCF response →
  Hessian assembly → frequencies → normal modes → IR spectrum →
  thermochemistry (banner order verified against 4 real ORCA 6.1.1 freq
  outputs). The panel center shows the stage's headline number (atom count,
  K/N perturbations with CP-SCF iteration, 3N×3N Hessian dimension, mode
  count, temperature) and the bottom status line names the running stage;
  `ORCA TERMINATED NORMALLY` completes the chain. Numerical frequencies keep
  the displacement progress bar.
- **The "~N s / SCF cycle" pace indicator moved from the Log-tab toolbar into
  the SCF panel's progress meta line** (right-aligned on the same row as the
  criteria/"✓ geometry converged" status), where the rest of the run pacing
  info lives.
- **Bridge/API payloads now have a single source of truth:**
  `orcamgr/state/schemas.py` defines TypedDicts for every payload crossing the
  QWebChannel bridge and the phone HTTP API (settings, log, queue snapshots,
  parse results, server status, ok/error envelopes), and `bridge.py` /
  `server/app.py` / `store.py` build their responses through them instead of
  ad-hoc dicts. The JSON wire format is byte-identical (verified against 40
  captured payloads); `web/types.js` cross-references it as the JS mirror.
- **The web front-end is now type-checked** (`// @ts-check` + JSDoc against
  `jsconfig.json`; payload typedefs in `web/types.js` mirror the Python
  serialization layer). Typing surfaced one real bug, now fixed: a failed
  settings save returned `{"error": ...}` and silently replaced the in-memory
  settings mirror; it now shows a toast and keeps the previous settings.
- **`QueueStore` moved from `orcamgr/server/store.py` to `orcamgr/state/store.py`.**
  The store is the single source of truth shared by the desktop Bridge *and* the
  phone-sync HTTP server, so living under `server/` misrepresented the dependency
  direction (now `gui -> state <- server`). The old import path still works through
  a deprecation shim.

## [0.3.4-beta] — 2026-06-11

### Changed
- **The convergence/SCF graph now fills the window height.** The viewBox height
  is computed per-render from the space left below the plot (and follows window
  resizes), so the graph ends flush with the bottom gutter instead of leaving a
  large empty band under a fixed-ratio chart.
- **The graph plot sits on a pure black/white surface** (`#000000` dark,
  `#FFFFFF` light) instead of the page tint, so the chart pops from the panel.

## [0.3.3-beta] — 2026-06-11

### Changed
- **The convergence/SCF graph now fills its box and is larger.** It was sized to
  the viewport height, so its width was fixed and left empty space on the right
  of the (wide) graph box. It now fills the box width on a flatter viewBox
  (1100×360), so it's bigger and the leftover space is gone, while still fitting
  without scrolling.

## [0.3.2-beta] — 2026-06-10

Light-theme layering and uniform spacing.

### Changed
- **Light theme inverted: ivory page (`#FFFFF0`) with slightly-darker beige
  cards (`#F5F5DC`)**, so boxes read as raised panels on a pale page (was the
  other way around).
- **The convergence/SCF graph now sits in its own nested box** — ivory in light
  (lighter than the beige panel), a darker inset in dark — so the chart has its
  own surface.
- **Uniform 16 px spacing**: the gap between boxes and the gap from a box to the
  window edge are now both 16 px, and the top bar, tabs and cards all align to
  the same 16 px gutter (the page gutter was 20 px while cards sat 16 px apart).

## [0.3.1-beta] — 2026-06-10

Light-theme warmth and graph/log sizing.

### Changed
- **Light theme is now warm, not stark white**: a beige page background
  (`#F5F5DC`) with ivory cards/inputs/popovers (`#FFFFF0`) and beige-tinted fills
  and borders, instead of white-on-white.
- **The "raw" badge is teal in light mode** (was purple, which clashed with the
  warm palette).
- **The convergence graph is sized to the viewport height** instead of a fixed
  920 px width cap, so it's much larger on a wide window while still always
  fitting without scrolling; the raw-log box reclaims the vertical space freed by
  dropping the old "Live output" header card.

### Fixed
- **The ORCA-status dot now turns green when ORCA is ready.** The "ready" class
  was already applied, but a more-specific `#orca-status .dot` rule overrode the
  `.ok` colour and kept the dot grey — added an `#id`-level ready rule.

## [0.3.0-beta] — 2026-06-10

Drag-and-drop, live frequency progress, and UI polish.

### Added
- **Drag & drop files from Explorer.** Drop a `.inp` onto the window to load it
  into the Build editor (the calculation name auto-fills from the filename), a
  `.xyz` to set the Build geometry, or a `.out` to open it in Results. Routed by
  extension; `.out` is parsed by path, so even multi-hundred-MB outputs stay off
  the JS heap.
- **Live frequency progress** in the graph panel:
  - *Numerical* frequencies — a displacement counter (K / 6N) with a reliable
    ETA (the total is known up front, unlike an optimization).
  - *Analytical* frequencies (CP-SCF) — a real progress bar driven by the
    coupled-perturbed-SCF "K / N perturbations done" counter (N = 3·atoms), with
    a stage label for the derivative-integral build. Verified against a 58-atom
    M06-2X/CPCM run.
- **View the input of any queued job — including a RUNNING one** — via a ".inp"
  button that shows the on-disk input read-only.

### Changed
- **An optimization flips to 100% the moment ORCA reports it finished**
  (`*** OPTIMIZATION RUN DONE ***`) instead of being stuck at 99% when the last
  criteria table reads e.g. 4/5 met, and it announces the next stage.
- **The optimization ETA is shown as an order-of-magnitude bucket** ("a few
  minutes" / "tens of minutes" / …) with the accurate signals (progress, step,
  criteria met, measured per-step rate) up front and the uncertain time estimate
  visually subordinate — the cycle count is irreducibly ~2× uncertain, so this
  avoids false precision.
- **Log tab layout.** Dropped the "Live output" header card, moved **Clear** to
  the top-right of the toggle row, and capped the convergence-graph width so it
  no longer grows to ~750 px tall and forces scrolling. The "converged ≤ 1" label
  moved to the left so the descending curves don't cover it.

### Fixed
- **Design consistency: badges/chips reused outside their original parent now
  render correctly.** The pill/box styling for `.qstate` (the Build "raw" tag),
  `.qerror` (the Build NEB atom-mismatch warning) and `.rm` (remove buttons) was
  scoped to one ancestor (`.queue-item …`), so the same class used elsewhere
  rendered unstyled. Hoisted the shared look to ancestor-free base rules and
  removed dead `.atom-row` styles.

## [0.2.1-beta] — 2026-06-10

Optimization ETA accuracy + honesty, tuned against 85 real ORCA opt runs.

### Changed
- **Per-cycle time now comes from ORCA's own `Time for complete geometry iter`
  timing** (steady median, excluding the one-time-expensive first cycle) instead
  of wall-clock poll gaps — eliminating UI-jitter / log-replay artifacts. On the
  85 runs this time model is accurate to ~8% (median) given the cycle count.
- **The ETA is shown as an honest range (≈[0.5×, 2×])** rather than a single
  number. Geometry-optimization cycle counts are intrinsically hard to predict —
  verified ~65% median error across heuristic *and* regression models on the same
  runs, because convergence has a long, unpredictable tail near the tolerance —
  so the estimate is presented as a calibrated band, not false precision.

### Notes
- Other methods (frequencies, scans, NEB) were evaluated but the available data
  showed no step-predictable structure to model honestly (DFT frequencies are a
  single analytical Hessian), so step-based ETA remains opt-only.

## [0.2.0-beta] — 2026-06-10

Theming release.

### Added
- **Light theme.** A full shadcn-zinc light palette alongside the existing dark
  theme, toggled from a ☀/☽ button in the top bar and remembered across launches.
  Every element themes through CSS variables; the optimization-graph series and
  the "raw" badge get darker, legible variants on the light card.

## [0.1.2-beta] — 2026-06-10

Survive-close + reliability release. A running calculation now keeps going when
ORCAdesk is closed and is re-attached on the next launch, plus a batch of
editing/robustness fixes.

### Added
- **Run survives closing the app.** ORCA is launched detached and writes its own
  `.out`; closing ORCAdesk no longer kills the running job — it is left running
  and reattached on the next launch.
- **Session restore.** The queue autosaves and is restored on startup; a job that
  finished while closed is reconciled from its `.out` (done/failed), and a still-
  running one is reattached live (the SCF/optimization graph history is rebuilt
  from the `.out`).
- **"Stop after current"** — finish the running job, then stop, leaving the rest
  pending (vs. Cancel, which kills the running job).
- **Editable raw calculations after queueing** — a raw `.inp` calc restored from a
  previous session or added from the phone can now be edited (full data is fetched
  on demand), not only same-session ones.
- **Log: jump-to-latest.** Scrolling up no longer yanks you back down; a "↓ Latest"
  button appears bottom-right when scrolled up.
- **Average "s / SCF cycle"** indicator on the Log tab.
- **Expert mode:** loading a complete `.inp` auto-fills the calculation name from
  the file name.

### Changed
- **Confirmation dialogs are themed** (no more system pop-ups) and irreversible
  actions — Cancel, Clear all, Remove, switch-to-raw — now confirm first.
- Shorter default raw-log box so the Log panel fits without an outer scroll.
- Cancellation hardened: psutil process-tree kill with confirm + escalation,
  bounded waits, and a centralized idempotent shutdown.

### Fixed
- Optimization-graph history is no longer truncated after a close/reopen of a long
  job (reattach tails from the current EOF; the graph is rebuilt from the `.out`).
- Startup no longer re-parses every finished `.out` on the UI thread (parsed on
  demand instead), avoiding a cold-start stall with a large restored queue.

## [0.1.1-beta] — 2026-06-07

Correctness and robustness release. Focus: ORCA input that this build actually
accepts, safer cancellation/shutdown, and a clearer optimization graph.

### Added
- **Composite calc kinds** `Opt + Freq` and `TS Opt + Freq` — one ORCA run that
  optimizes then runs frequencies, validated for convergence *and* the
  imaginary-frequency count (0 for a minimum, 1 for a TS).
- **Optimization graph: all five convergence criteria.** Each criterion is
  plotted as value ÷ its own tolerance, so they share a single goal line at 1
  (below the line = met). A Settings toggle switches between this and the
  original MAX-gradient-only view.
- **Build tab: Beginner / Expert modes.** Beginner is the guided form (and can
  now also load a complete `.inp` directly); Expert is a paste/load-a-full-`.inp`
  view where you only pick the calc kind (for parsing), with `{{GEOMETRY}}` +
  reference still supported. The chosen mode is remembered.
- Opt-in WebEngine remote debugging via `ORCADESK_REMOTE_DEBUG` (diagnostics).

### Changed
- **ORCA functional/basis compatibility.** Strict-name normalization on the
  keyword line (`M06-2X`→`M062X`, `M06-L`→`M06L`, `SCAN`→`SCANfunc`); combined
  dispersion tokens rewritten to the explicit separate keyword (`B3LYP-D3` →
  `B3LYP D3BJ`); `RIJK` gets a `/JK` aux and double hybrids/MP2 get `AutoAux`
  (skipped when RI is off). Functional/basis lists were validated against ORCA
  6.1.1 and entries this build rejects were removed.
- Optimization-graph step count is keyed to the real ORCA cycle number.

### Fixed
- **Cancellation/shutdown:** cancel now kills the whole ORCA process tree (no
  orphaned MPI workers); a cancelled calc is marked CANCELLED (not FAILED) and
  no longer blocks its dependents; closing the app waits for ORCA to stop so it
  doesn't orphan `orca.exe` or leave a half-written `.out`.
- **NEB-TS:** product/TS-guess side files are written in raw mode too,
  reactant/product atom order is checked before launch, and the result is
  validated (one imaginary mode) when frequencies were computed.
- **UI:** NEB-TS product `.xyz` loading (was JSON-parsing raw text and failing);
  "Reference another calculation" can enter raw mode without first picking a
  reference; per-element basis/ECP values are HTML-escaped on edit; the opt-ETA
  no longer flashes "~0s" after a burst of replayed log lines; log/graph
  repaints are skipped while the window is hidden.
- **Input validation / phone-sync (in development):** untrusted numeric fields
  are coerced/clamped; calc names are validated against path traversal and
  reserved names at the shared layer; the queue can't be edited while running;
  the loopback auth-bypass is honoured only when bound to loopback.

## [0.1.0-beta] — 2026-06-03

First beta of the **desktop** app. Core workflow (build → queue → run → parse)
is complete and has been validated against real ORCA 6.1.1 output. Phone-sync
is in active development by a contributor and is **not** part of this build.

### Added

**Building calculations**
- Visual form for opt, TS-opt, freq, TS-freq, TD-DFT, NMR, single-point, and a
  general free-form mode, each emitting ORCA 6.1.1-correct input.
- Searchable comboboxes for functional, basis set, and solvent: type to filter,
  grouped by level, and any value not in the list is accepted verbatim (so the
  full LibXC functional space and any custom basis are reachable).
- Per-element basis/ECP assignments, solvation (CPCM/SMD), SCF and RI options,
  and adjustable nprocs / maxcore.
- Raw-input mode for hand-written ORCA decks.

**Queue and execution**
- Drag-to-reorder queue with per-calculation folders.
- Geometry hand-off between steps (e.g. opt → freq reuses the optimized
  geometry); dependent steps are blocked automatically if a parent fails.
- Live streaming log with cancel support.

**Live convergence graphs**
- SCF convergence graph (|ΔE| per cycle, log scale) with axis labels and cycle
  ticks.
- Geometry-optimization graph (MAX gradient vs step) with a progress bar and a
  criteria-met counter; progress is capped at 99 % until all five convergence
  criteria are actually satisfied.
- **Optimization time estimate (ETA):** a research-tuned, non-linear estimator
  predicts remaining steps and time during a geometry optimization. It uses a
  per-file-normalized "worst-ratio" of the five convergence criteria, an
  ensemble of four predictors with agreement gating, and temporal smoothing, so
  it shows "estimating…" rather than a wrong number when the trajectory is
  erratic. Selectable **Conservative** (predict only when confident) or
  **Eager** (predict earlier, hold the estimate) mode in Settings.

**Results**
- Parsed energies, geometry, frequencies, and TD-DFT transitions, with a simple
  UV-Vis spectrum and an NMR summary view. Finished jobs load into Results
  automatically.

**Desktop app**
- Standalone Windows build (PyInstaller) and an Inno Setup installer, with a
  custom application icon.
- First-launch prompt to choose where calculation files are stored.

### Fixed
- NMR J-coupling input now emits valid `Nuclei … { shift, ssall }` blocks and
  places `%eprnmr` after the coordinates.
- TS-opt now requests an initial Hessian (`Calc_Hess true`).
- Solvation no longer emits empty `CPCM()` / `SMD()` when no solvent is chosen.
- Auxiliary basis (`def2/J`) is added automatically only when appropriate.
- Long runs no longer lag: the log DOM is capped and graph redraws are
  throttled.
- ETA mode is now persisted correctly through the desktop settings bridge.

### Known limitations
- ETA is meaningful only for geometry optimizations. Other calculation types
  (analytic frequencies, single point, TD-DFT, NTO) do not expose a usable
  progress signal, so no ETA is shown for them.
- ETA accuracy is inherently limited for difficult or erratic optimizations
  (transition-metal complexes, large flexible molecules); the estimate is
  labelled "rough" and may stay at "estimating…".
- Phone-sync (controlling the queue from a phone) is in development and not
  included in this build.
- Windows is the primary tested target for the packaged app.
