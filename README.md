# ORCAdesk

A desktop GUI for building, queuing, running, and parsing ORCA computational
chemistry jobs. PyQt6 + QWebEngine front-end (shadcn-style dark **or light** UI),
Python core.

> **Status: 0.7.0 beta** (`0.7.0-beta`). Desktop app: build → queue → run →
> parse, validated against real ORCA 6.1.1 output. A running calculation
> **survives closing the app** and is reattached on the next launch; the UI ships
> with both a **dark and a light theme** (plus an optional **Liquid Glass**
> style); you can **drag a `.inp`/`.xyz`/`.out` onto the window** to load it; and
> the Log graph shows **live progress for optimizations and frequency runs**
> (numerical *and* analytical/CP-SCF). Beyond ORCA, you can **pre-optimize a
> structure with a MACE model (MLIP)** in your own Python environment, and run a
> **CREST conformer search through WSL** — both hand their geometries off to ORCA
> jobs through the normal reference mechanism. Results open in an **in-app 3D
> structure viewer** where conformers can be flipped through and starred. The
> queue can **run several calculations at once**, admitted against a core and
> memory budget you set — so the next one starts the moment one finishes. Run
> from source, or build a
> standalone Windows app with `build.bat`. (Phone-sync is in development and not
> part of this build.) See [CHANGELOG.md](CHANGELOG.md) for details.

## Requirements

- Python 3.10+
- ORCA 6.x installed (the app calls your `orca` executable)

## Install & run (development)

```bash
pip install -r requirements.txt
python main.py
```

On first launch the app tries to auto-detect ORCA. If it can't, open the
**Settings** tab and point it at your `orca.exe`.

## How it works

- **Build**: create one calculation at a time, picking the execution backend
  first — **DFT** (ORCA), **MLIP** (MACE pre-optimization, see below), or
  **CREST** (conformer search, see below). DFT has two sub-modes: **Beginner**
  (guided form) and **Expert** (edit the full `.inp`). Switching Beginner →
  Expert converts your current form into a generated `.inp` you can keep
  editing; the reverse conversion doesn't exist (raw text can't become a form
  again), so switching back asks before discarding the editor text. Give the
  calculation a unique name (used as its folder), pick the type, set
  charge/multiplicity, choose a geometry source, configure the method, and add
  it to the queue.
  - **Geometry source** is either an `.xyz` file, or a **reference** to another
    queued calculation — in which case that calculation's optimized geometry is
    injected automatically at run time.
  - A loaded `.xyz` is **drawn in 3D right there**, with its formula and atom /
    electron count beside it, and is **screened before it can cost you hours**:
    a spin multiplicity the electron count cannot produce (ORCA aborts on that
    at the first SCF step), atoms sitting on top of each other, coordinates
    that are really in Bohr, disconnected fragments. Findings are reported and
    the atoms they name are highlighted — your structure is never edited for
    you.
  - **Edit structure…** opens the geometry in 3D: click 2 atoms for a distance,
    3 for an angle, 4 for a dihedral, type a value, and the far side of that
    bond moves rigidly; a fragment can also be translated or spun about its own
    centre. The **atom order never changes**, and an edit that cannot be made
    rigidly (a dihedral about a ring bond) is refused with the reason.
  - **NEB-TS** gets its own *NEB endpoints* card: load a product, or **copy the
    reactant** and edit it into one; see both structures side by side (or
    overlaid) in a single scene, hover an atom to name it in *both*, and get a
    **matched / atom mismatch** badge with a table of every atom that differs.
    Building the product with the editor makes a mismatch impossible.
  - Calculation types: **Opt, Opt + Freq, TS Opt, TS Opt + Freq, Freq, TS Freq**
    (expects one imaginary mode), **NEB-TS** (find a TS), **IRC** (verify a TS),
    **TDDFT, NMR, SP**, and **General** (any). Freq variants accept a
    temperature/pressure (the `%freq` block is emitted only when they differ from
    298.15 K / 1.0 atm).
  - **Method fields** (functional / basis / solvent) are searchable
    comboboxes: type to filter the grouped list, or enter any value not in the
    list (e.g. a LibXC functional or a custom basis) — it's used verbatim.
  - **Expert (raw `.inp`)** mode lets you hand-edit the full input for anything the
    form doesn't cover (e.g. per-element basis/ECP via `%basis newgto/newecp`,
    `%plots`, custom blocks). Use `{{GEOMETRY}}` where coordinates go.
- **MLIP calculations**: relax a structure (or get a quick single-point energy)
  with a **MACE** model (MLIP) — fast, and a relaxed structure is a good starting
  geometry for the DFT job. It runs in a dedicated Python environment
  (PyTorch + mace-torch + ASE) managed under **Settings → MLIP environments**:
  press **Create** and ORCAdesk builds one for you — pick **CPU** (~150 MB) or
  **GPU/CUDA** (~2.5 GB) and it makes a private virtual environment, installs
  PyTorch for that device, installs the backend, and registers the result. It
  needs a Python already on your machine to build on (auto-detected; install one
  from python.org if there is none). You can equally register an environment you
  built yourself — ORCAdesk only ever shells out to it, and never installs into
  a Python you manage. The MLIP build card stays **locked until a MACE
  environment is ready**. An `mlip_opt`/`mlip_sp` calculation joins the same queue,
  and an optimized geometry can be referenced by a downstream ORCA calculation
  just like an opt→freq handoff.
- **CREST conformer search**: explore low-energy conformers with the xTB
  tight-binding methods. CREST has no native Windows build, so ORCAdesk runs its
  Linux binary through **WSL** — pick a distribution under **Settings → CREST**
  and ORCAdesk can install the (statically-linked) binary into it for you; the
  one manual prerequisite is having a WSL distro. A `crest_conf` job joins the
  same queue (it runs detached and survives the app closing, like an ORCA run);
  its finished ensemble is listed (read-only) in the **Results** tab. Follow-up
  calculations reference the search from their geometry source on the Build
  tab and run on the **lowest-energy conformer**; every conformer is also
  exported as its own `.xyz` under the run's `conformers/` subfolder for manual
  follow-ups. The CREST build card stays **locked until a distro with CREST is
  ready**.

  > **WSL memory note:** a CREST run is I/O-heavy, and by default WSL2 keeps
  > every byte it caches — its `Vmmem WSL` process can balloon to many GB
  > (up to 50 % of your RAM) and **not give the memory back** after the run.
  > That is WSL's design, not a leak in CREST or ORCAdesk. To make WSL return
  > cached memory when idle, create `C:\Users\<you>\.wslconfig` containing:
  >
  > ```ini
  > [experimental]
  > autoMemoryReclaim=gradual
  > ```
  >
  > and run `wsl --shutdown` once while **no CREST job is running** (it would
  > kill the job). Optionally add `memory=8GB` under a `[wsl2]` section to cap
  > the VM outright.
- **Queue**: calculations run in order. If one fails, anything that references
  it (directly or transitively) is skipped (blocked); unrelated calculations
  continue. Each calculation gets its own folder `{workspace}/{name}/`. The
  queue **autosaves and is restored on the next launch**; you can **Cancel**
  (kill the running job) or **Stop after current** (graceful drain), and
  irreversible actions ask for confirmation first.
- **Survives closing the app**: ORCA is launched detached and writes its own
  `.out`, so closing ORCAdesk leaves the running job going. On the next launch a
  still-running job is **reattached live** (its graph history rebuilt from the
  `.out`); a job that finished while you were away is read back from disk.
- **Log**: live ORCA stdout + events, plus a **convergence graph** view — SCF
  (|ΔE| per cycle) and, for optimizations, all five convergence criteria with a
  progress bar. The progress reads from the real ORCA timing, so the accurate
  signals (step, criteria met, per-step rate) lead and the inherently-uncertain
  time is shown as an honest order-of-magnitude estimate, not false precision.
  **Frequency and TD-DFT runs get their own live stage panel** — a HUD-style
  phase chain over ORCA's real pipeline (analytical Hessian: integrals →
  CP-SCF with a perturbation counter → Hessian → modes → thermochemistry;
  TD-DFT: setup → diagonalization with an iteration counter → states →
  spectra); numerical frequencies keep a displacement progress bar. A
  `s / SCF cycle` pace readout and a "jump to latest" button keep long runs
  readable.
- **Theme**: toggle **dark / light** from the top bar (☀/☽), and pick a theme
  *style* in **Settings → Appearance** — the default **shadcn (flat)** look or
  **Liquid Glass** (a refracting frosted top bar / tabs over a wallpaper, five
  intensity levels + six wallpaper presets or a custom image). Both styles work
  in dark and light; the choice is remembered across launches.
- **Results**: per-calculation summary plus every value the parser extracts —
  final geometry (with *Copy .xyz*), orbital energies (HOMO/LUMO), Mulliken &
  Löwdin charges, Mayer bond orders/valences, dipole moment, rotational
  constants, SCF energy decomposition, frequencies (with imaginary-mode
  warnings) and full thermochemistry, TD-DFT transitions + a UV-Vis plot and the
  excited-state composition, NMR shieldings, and the NEB path. Sections are shown
  for the relevant calculation type; a **`Show all`** toggle reveals everything
  regardless of type. The picker lists **every result in your workspace**, not
  only the queued ones — a calculation cleared from the queue, or one from an
  earlier session, opens in a click — and *Open file…* reads a result from
  anywhere on disk. A **free-energy
  profile** view plots relative Gibbs free energy across finished frequency
  calculations in queue order. A built-in **3D structure viewer** shows
  geometries in the window and lets you flip through many structures with the
  **← / →** keys: *View in 3D* steps through a CREST conformer ensemble, and
  *Browse .xyz…* opens any folder of `.xyz` files (e.g. a `conformers/` folder)
  as one browsable set — no external viewer needed. **Star** the ones worth
  keeping (the **F** key); starred structures persist, can be stepped through on
  their own, and exported to a `favorites/` folder. The same viewer draws
  **molecular orbitals and electron density**: *View in 3D* on the orbital
  energies card opens the HOMO, with every level listed beside it (plus the
  electron density, and the spin density for open-shell jobs). Surfaces are
  generated from the finished job's wavefunction — nothing is re-run — and the
  isovalue slider re-draws instantly.
- **Settings**: ORCA executable path, **MLIP environments** (create one for CPU
  or GPU in a click, or register your own MACE-capable Python interpreters;
  backends are auto-detected), workspace folder,
  per-step defaults (nprocs / maxcore), and two live-graph options — the
  optimization time-estimate mode and what the optimization graph plots.

## Build a standalone Windows app

```bat
build.bat
```

This installs dependencies + PyInstaller, then produces `dist\ORCAdesk\`
containing `ORCAdesk.exe` and its runtime files.

### Distributing to a friend

1. Run `build.bat` on a Windows machine.
2. Zip the **entire** `dist\ORCAdesk\` folder (not just the .exe — the
   QtWebEngine/Chromium runtime lives beside it).
3. Send the zip. The friend unzips and runs `ORCAdesk.exe`.
4. They still need ORCA installed; they set its path in the **Settings** tab
   on first launch (or it auto-detects).

Notes:
- Use the **folder** (onedir) build, not a single .exe — QtWebEngine is
  unreliable when compressed into one file.
- The app folder is large (~150–250 MB) because it bundles Chromium. That's
  expected; compress before sending.
- Settings and workspaces are stored per-user in `%APPDATA%\ORCAdesk`,
  not inside the app folder, so they survive updates.

## Project layout

```
main.py                       entry point
orcamgr/
  paths.py                    dev / frozen path resolution, user data dir
  config.py                   settings + ORCA auto-detection
  core/
    parser.py                 ORCA .out parser (verified vs ORCA 6.1.1)
    input_generator.py        .inp generation (+ CPCM/SMD solvation)
    structure.py              geometry screening + rigid, order-preserving edits
    runner.py                 detached ORCA subprocess; tail .out + reattach
    procutil.py               psutil process identity + tree-kill (reattach)
    queue.py                  multi-job pipeline orchestration
  state/
    store.py                  shared queue (single source of truth) + session autosave
  mlip/                       MLIP (MACE) env detection + relaxation runner/parser
  crest/                      CREST conformer search via WSL (env/installer/runner/parser)
  server/                     optional phone-sync HTTP layer (FastAPI; not in the build)
  gui/
    window.py                 QMainWindow + WebEngine
    bridge.py                 JS <-> Python bridge, worker thread
web/                          shadcn-style dark/light UI (html/css/js)
data/                         ORCA option lists (functionals, basis sets, ...)
```

## Notes

- Option lists in `data/*.json` are sourced from the ORCA 6.1.1 manual.
- The parser was validated against a set of real ORCA 6.1.1 outputs
  (geometry opt, frequency, TD-DFT, NTO) covering normal termination,
  convergence, HOMO/LUMO, imaginary-frequency detection, and absorption
  spectra.

## Contributors

See [CONTRIBUTORS.md](CONTRIBUTORS.md). Thanks to everyone who provided ORCA
output files for developing the optimization-time estimator.

## License

MIT License — Copyright (c) 2026 Taewoo Kim (Korea Science Academy of KAIST).
See the `LICENSE` file for details.
