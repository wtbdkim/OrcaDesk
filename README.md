# ORCAdesk

A desktop GUI for building, queuing, running, and parsing ORCA computational
chemistry jobs. PyQt6 + QWebEngine front-end (shadcn-style dark **or light** UI),
Python core.

> **Status: 0.2.0 beta** (`0.2.0-beta`). Desktop app: build → queue → run →
> parse, validated against real ORCA 6.1.1 output. A running calculation now
> **survives closing the app** and is reattached on the next launch, and the UI
> ships with both a **dark and a light theme**. Run from source, or build a
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

- **Build**: create one calculation at a time. Give it a unique name (used as
  its folder), pick the type (Opt / Freq / TDDFT / SP), set charge/multiplicity,
  choose a geometry source, configure the method, and add it to the queue.
  - **Geometry source** is either an `.xyz` file, or a **reference** to another
    queued calculation — in which case that calculation's optimized geometry is
    injected automatically at run time.
  - Calculation types: **Opt, TS Opt** (OptTS), **Freq, TS Freq** (expects one
    imaginary mode), **TDDFT, SP**. Freq/TS-Freq accept a temperature/pressure
    (the `%freq` block is emitted only when they differ from 298.15 K / 1.0 atm).
  - **Method fields** (functional / basis / solvent) are searchable
    comboboxes: type to filter the grouped list, or enter any value not in the
    list (e.g. a LibXC functional or a custom basis) — it's used verbatim.
  - **Raw .inp** mode lets you hand-edit the full input for anything the form
    doesn't cover (e.g. per-element basis/ECP via `%basis newgto/newecp`,
    `%plots`, custom blocks). Use `{{GEOMETRY}}` where coordinates go.
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
  (|ΔE| per cycle) and, for optimizations, MAX gradient vs step with a progress
  bar and a live **time estimate (ETA)**. A small `s / SCF cycle` pace readout
  and a "jump to latest" button keep long runs readable. ETA is a research-tuned
  estimator; pick Conservative or Eager mode in Settings.
- **Theme**: toggle **dark / light** from the top bar (☀/☽); the choice is
  remembered across launches.
- **Results**: per-calculation summary (energy, HOMO/LUMO, gap, frequencies with
  imaginary-mode warnings, thermochemistry, TD-DFT transitions + a UV-Vis plot).
  You can also open any external `.out` file.
- **Settings**: ORCA path, workspace folder, default resources, and ETA mode.

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
    runner.py                 detached ORCA subprocess; tail .out + reattach
    procutil.py               psutil process identity + tree-kill (reattach)
    queue.py                  multi-job pipeline orchestration
  server/
    store.py                  shared queue + session persistence (autosave)
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
