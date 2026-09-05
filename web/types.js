// @ts-check
/* ============================================================
   Shared payload typedefs for everything that crosses the
   QWebChannel bridge (and the phone HTTP API, which serves the
   same shapes).

   MIRROR CONTRACT: these typedefs mirror the Python serialization
   layer FIELD BY FIELD and must be kept in sync with it:
     - orcamgr/state/store.py        calc_to_dict / calc_to_session_dict /
                                     calc_from_dict / snapshot / log_since
     - orcamgr/core/input_generator.py  StepConfig / Solvation / BasisAssignment
     - orcamgr/gui/bridge.py         per-slot response payloads
   orcamgr/state/schemas.py is the Python-side single source of truth for
   the non-Calculation payloads (TypedDicts the bridge and HTTP server
   build their responses through) — these typedefs are its JS mirror.

   This file is a plain global script: under jsconfig.json all
   @typedef names declared here are visible to web/app.js and
   web/scf_graph.js without imports. It defines no runtime values.
   ============================================================ */

/* ---------- queue / calculations (orcamgr/state/store.py) ---------- */

/**
 * One calculation as it appears in the polled queue snapshot.
 * Mirror of calc_to_dict() in orcamgr/state/store.py — 14 keys, all
 * always present.
 * @typedef {Object} CalcSummary
 * @property {string} name              unique; doubles as the on-disk folder name
 * @property {string} kind              "opt"|"ts_opt"|"freq"|"ts_freq"|"tddft"|"sp"|"nmr"|"neb_ts"|...
 * @property {number} charge
 * @property {number} multiplicity
 * @property {"direct"|"reference"} geometry_source
 * @property {string} ref_name          referenced calc name ("" for direct)
 * @property {boolean} is_raw
 * @property {"pending"|"running"|"done"|"failed"|"blocked"|"cancelled"} state
 * @property {string} message
 * @property {string} output_path
 * @property {string} scf_convergence   from the config; "" when no config
 * @property {string} meta              one-line list-row summary, built server-side
 * @property {string} mlip_model        mlip* kinds: MACE model label; "" otherwise
 * @property {string} crest_method      crest* kinds: tight-binding method; "" otherwise
 */

/**
 * Queue snapshot returned by get_queue() and in the "snapshot" key of
 * queue-mutating slots. Mirror of QueueStore.snapshot().
 * @typedef {Object} RunResources
 * @property {number} cores_used        cores the in-flight jobs reserved
 * @property {number} cores_budget      the run's core budget
 * @property {number} ram_used_mb       estimated memory the in-flight jobs hold
 * @property {number} ram_budget_mb     the run's memory budget
 * @property {number} jobs              calculations in flight
 * @property {number} max_jobs          how many may be in flight at once
 */

/**
 * @typedef {Object} QueueSnapshot
 * @property {boolean} running
 * @property {number} version           monotonically increasing change counter
 * @property {CalcSummary[]} calculations
 * @property {RunResources} resources   live occupancy; ALL ZEROS while idle —
 *                                      the budgets live on the engine, so an
 *                                      idle client shows the limits from
 *                                      SettingsPayload instead
 */

/**
 * Solvation sub-config. Mirror of Solvation in input_generator.py.
 * @typedef {Object} SolvationPayload
 * @property {string} model             "" (none) | "CPCM" | "SMD"
 * @property {string} solvent
 */

/**
 * Per-element basis/ECP row. Mirror of BasisAssignment in input_generator.py.
 * @typedef {Object} BasisAssignmentPayload
 * @property {string} element
 * @property {string} basis
 * @property {string} ecp
 */

/**
 * Method configuration. Mirror of StepConfig in input_generator.py
 * (to_dict()/from_dict are a plain asdict round-trip) — 48 fields.
 * The [optional] markers describe the JS→Python SEND direction (the build
 * forms omit keys that don't apply, e.g. crest_* on a DFT calc); in the
 * Python→JS direction to_dict() = asdict(), so all 48 keys always arrive.
 * @typedef {Object} StepConfigPayload
 * @property {string} kind
 * @property {string} functional
 * @property {string} basis_set
 * @property {string} ri_approximation
 * @property {string} scf_convergence
 * @property {string} calculation_type
 * @property {string} options
 * @property {number} maxcore_mb
 * @property {number} nprocs
 * @property {number} max_iter
 * @property {SolvationPayload} solvation
 * @property {BasisAssignmentPayload[]} basis_assignments
 * @property {number} freq_temp_k
 * @property {number} freq_pressure_atm
 * @property {boolean} nmr_jcoupling
 * @property {number} tddft_nroots
 * @property {number} tddft_maxdim
 * @property {boolean} tddft_tda
 * @property {boolean} tddft_triplets
 * @property {number} irc_maxiter
 * @property {string} irc_direction
 * @property {string} irc_init_hess
 * @property {string} irc_hess_file
 * @property {string} neb_product_xyz
 * @property {number} neb_nimages
 * @property {boolean} neb_preopt_ends
 * @property {string} neb_ts_guess_xyz
 * @property {string} mlip_model       MACE model, e.g. "MACE-OFF medium" (kind "mlip*")
 * @property {string} mlip_env_id      registered MLIP env to run in ("" = first ready)
 * @property {string} [mlip_device]    torch device: "" = auto (GPU if available) | "cpu" | "cuda"
 * @property {string} [crest_method]   CREST tight-binding method (kind "crest*"): gfn2|gfnff|gfn0
 * @property {string} [crest_solvent]  ALPB implicit-solvent name ("" = gas phase)
 * @property {number} [crest_ewin]     conformer energy window (kcal/mol)
 * @property {number} [crest_threads]  CREST thread count (-T)
 * @property {string} [crest_env_id]   preferred WSL distro ("" = first with CREST)
 * @property {string} [crest_preset]   search speed: "" | quick | squick | mquick
 * @property {boolean} [crest_nci]     --nci ellipsoid wall (keep a complex intact)
 * @property {string} [crest_solvent_model]  implicit-solvent model: "alpb" | "gbsa"
 * @property {number} [crest_mdlen_mult]  --mdlen x<mult> (0 = default)
 * @property {number} [crest_tstep_fs]    --tstep <fs> (0 = default)
 * @property {number} [crest_tnmd_k]      --tnmd <K> (0 = default)
 * @property {number} [crest_mddump_fs]   --mddump <fs> (0 = default)
 * @property {number} [crest_vbdump_ps]   --vbdump <ps> (0 = default)
 * @property {boolean} [crest_norotmd]  --norotmd (skip extra regular MD)
 * @property {boolean} [crest_cbonds]   --cbonds (auto bond constraints)
 * @property {boolean} [crest_subrmsd]  --subrmsd (exclude constrained from RMSD)
 * @property {boolean} [crest_cluster]  --cluster (PCA+k-means; not for complexes)
 * @property {boolean} [crest_keepdir]  --keepdir (keep per-step directories)
 */

/**
 * Full-fidelity calculation, as returned by get_calc() ("calc" key).
 * Mirror of calc_to_session_dict() in orcamgr/state/store.py — 15 keys.
 * @typedef {Object} CalcFull
 * @property {string} name
 * @property {string} kind
 * @property {StepConfigPayload|{}} config   {} when the calc has no config
 * @property {number} charge
 * @property {number} multiplicity
 * @property {"direct"|"reference"} geometry_source
 * @property {string} xyz
 * @property {string} ref_name
 * @property {boolean} is_raw
 * @property {string} raw_text
 * @property {"pending"|"running"|"done"|"failed"|"blocked"|"cancelled"} state
 * @property {string} message
 * @property {string} output_path
 * @property {number|null} pid              detached ORCA pid while RUNNING
 * @property {number|null} create_time      process create time (pid-reuse guard)
 */

/**
 * Calculation payload the JS SENDS to add_calc / update_calc /
 * build_inp_preview. Mirror of what calc_from_dict() in
 * orcamgr/state/store.py accepts (extra keys like state/message are
 * ignored server-side).
 * @typedef {Object} CalcInput
 * @property {string} name
 * @property {string} [kind]
 * @property {Partial<StepConfigPayload>} [config]
 * @property {number} [charge]
 * @property {number} [multiplicity]
 * @property {"direct"|"reference"} [geometry_source]
 * @property {string} [xyz]
 * @property {string} [ref_name]
 * @property {boolean} [is_raw]
 * @property {string} [raw_text]
 * @property {string} [state]    sent by the UI, ignored by calc_from_dict
 * @property {string} [message]  sent by the UI, ignored by calc_from_dict
 */

/* ---------- log (orcamgr/state/store.py log_since) ---------- */

/**
 * @typedef {Object} LogLine
 * @property {number} seq
 * @property {string} level   "info" | "warn" | "err" | "ok" (job-done lines) |
 *                            "orca" (every tailed ORCA stdout line during a run,
 *                            see core/queue.py); JS-local appendLog reuses the
 *                            same values
 * @property {string} msg
 * @property {string} calc    name of the calculation this line came from, ""
 *                            for engine-level lines. Several calculations can
 *                            run at once and their tailed output interleaves in
 *                            one buffer, so this tag is what routes a line back
 *                            to its job (Log-tab filter, per-job graphs)
 */

/**
 * @typedef {Object} LogPayload
 * @property {LogLine[]} lines
 * @property {number} latest
 */

/* ---------- settings / about (orcamgr/gui/bridge.py) ---------- */

/**
 * Mirror of Bridge.get_settings(). save_settings() returns this on
 * success but {error} on bad input — see SaveSettingsResult.
 * @typedef {Object} SettingsPayload
 * @property {string} orca_path
 * @property {string} workspace_root
 * @property {number} default_nprocs
 * @property {number} default_maxcore_mb
 * @property {number} max_concurrent_jobs  calculations that may run at once (1 = sequential)
 * @property {number} max_total_cores      total cores the queue may occupy (0 = auto)
 * @property {number} max_total_ram_mb     total memory the queue may occupy, MB (0 = auto)
 * @property {number} auto_cores           what "auto" resolves to here (physical cores)
 * @property {number} auto_ram_mb          what "auto" resolves to here (75% of RAM)
 * @property {string} theme
 * @property {"shadcn"|"liquidglass"} theme_variant
 * @property {"restrained"|"moderate"|"bold"|"vivid"|"maximal"} glass_level
 * @property {string} wallpaper     aurora|aqua|sunset|grape|graphite|ocean|custom
 * @property {"conservative"|"eager"} eta_mode
 * @property {"all5"|"maxgrad"} geo_graph_mode
 * @property {"beginner"|"expert"|"mlip"|"crest"} build_mode
 * @property {"in_app"|"system"} viewer_target  where a Visual row opens:
 *   ORCAdesk's own 3D viewer, or the program the OS associates with the file
 * @property {string} crest_distro     preferred WSL distro for CREST ("" = auto-detect)
 * @property {boolean} orca_valid
 * @property {string} save_error  why the settings on screen are not on disk ("" = they are)
 */

/**
 * One MLIP backend detected inside a registered environment.
 * @typedef {Object} MlipBackend
 * @property {string} key       registry key, e.g. "mace"
 * @property {string} label     display label, e.g. "MACE"
 * @property {string} version   installed package version, e.g. "0.3.6"
 */

/**
 * One registered MLIP environment (config merged with its live probe).
 * ORCAdesk does not install the MLIP toolchain; the user points each env at
 * their own Python and this reports which backends actually import.
 * @typedef {Object} MlipEnvPayload
 * @property {string} id
 * @property {string} name
 * @property {string} python    configured interpreter path
 * @property {"checking"|"ready"|"error"} state
 * @property {string} version   interpreter Python version, or ""
 * @property {MlipBackend[]} backends   auto-detected backends present
 * @property {boolean|null} cuda   torch sees a CUDA GPU (null = unknown/not probed)
 * @property {string} cuda_name    GPU name when cuda is true, else ""
 * @property {string} message   human-readable status / error detail
 */

/**
 * Mirror of Bridge.get_mlip_status() / check_mlip() / add_mlip_env() /
 * remove_mlip_env(). Aggregate MLIP picture: top-bar state + every env.
 * @typedef {Object} MlipStatusPayload
 * @property {"unset"|"checking"|"ready"|"error"} state
 * @property {MlipEnvPayload[]} envs
 */

/**
 * One interpreter usable as the base for a new MLIP venv.
 * @typedef {Object} BasePythonPayload
 * @property {string} python     absolute path to python.exe
 * @property {string} version    "3.12"
 * @property {boolean} supported torch publishes wheels for this CPython
 */

/**
 * Mirror of Bridge.get_mlip_install_options(). What a one-click environment
 * creation can be built from on this machine; detection is a background probe.
 * @typedef {Object} MlipInstallOptionsPayload
 * @property {"checking"|"ready"} state
 * @property {BasePythonPayload[]} base_pythons
 * @property {boolean} gpu        an NVIDIA GPU is visible to the driver
 * @property {string} gpu_name    e.g. "NVIDIA GeForce RTX 5080", or ""
 * @property {string} cuda_index  torch wheel index its architecture needs, e.g. "cu128"
 * @property {{key: string, label: string}[]} backends  installable backends,
 *   in registry order — the install card's dropdown is built from this
 */

/**
 * Mirror of Bridge.get_mlip_install_status() / create_mlip_env() /
 * cancel_mlip_install(). Progress of the running (or last) env creation.
 * @typedef {Object} MlipInstallPayload
 * @property {"idle"|"running"|"done"|"error"} state
 * @property {number} step       1-based, 0 before the first step
 * @property {number} steps      total steps in the plan
 * @property {string} label      what that step is doing
 * @property {string} error      failure reason, or ""
 * @property {boolean} cancelled
 */

/**
 * One WSL distro probed for CREST (backs the "CREST ready" indicator).
 * @typedef {Object} CrestDistroPayload
 * @property {string} distro      distro name (`wsl -d <distro>`)
 * @property {boolean} ready      crest binary found + runnable
 * @property {string} crest_bin   resolved binary path inside the distro, or ""
 * @property {string} version     `crest --version` line, or ""
 * @property {string} error       detail when not ready
 */

/**
 * Mirror of Bridge.get_crest_status() / check_crest() / install_crest() /
 * set_crest_distro(). Aggregate CREST picture: top-bar state + every usable WSL
 * distro. wsl=false means wsl.exe is unavailable.
 * @typedef {Object} CrestStatusPayload
 * @property {"unset"|"checking"|"ready"|"error"} state
 * @property {CrestDistroPayload[]} distros
 * @property {boolean} wsl  the transport is usable (wsl.exe on Windows, bash locally)
 * @property {"wsl"|"local"} transport  which shell CREST runs in
 * @property {string} install_error  why the last install attempt failed, or ""
 */

/**
 * @typedef {Object} ErrorPayload
 * @property {string} error
 */

/** @typedef {SettingsPayload|ErrorPayload} SaveSettingsResult */

/**
 * autodetect_orca() result. A MUTATION slot despite the getter-ish name:
 * on success the found path is also written into settings and saved.
 * "path" is present on every branch ("" when nothing was found).
 * @typedef {Object} AutodetectResult
 * @property {boolean} ok
 * @property {string} path
 * @property {string} [error]   only when detection itself raised
 */

/**
 * Mirror of Bridge.get_about().
 * @typedef {Object} AboutPayload
 * @property {string} version
 * @property {string} author
 * @property {string} org
 * @property {string} email
 */

/* ---------- files / choices (orcamgr/gui/bridge.py) ---------- */

/**
 * Unified result of the four file-loader slots (load_xyz_file /
 * load_xyz_path / load_inp_file / load_inp_path) — 5 keys, all always
 * present. "cancelled" means the user closed the picker (a deliberate
 * choice, not an error); ok=false means a real read failure. Loading a
 * geometry never changes the workspace (a Settings-only action).
 * @typedef {Object} LoadResult
 * @property {boolean} ok
 * @property {boolean} cancelled
 * @property {string} text
 * @property {string} name    filename stem, for auto-filling the calc name ("" if none)
 * @property {string} error   "" except on the read-failure branch
 */

/**
 * load_choices(name) result: grouped option lists,
 * {category: [items]} (or {all: [...]} for list-shaped data files).
 * @typedef {Object<string, string[]>} ChoiceGroups
 */

/* ---------- parse results (Bridge._parse_path) ---------- */

/**
 * @typedef {Object} TransitionPayload
 * @property {number} state
 * @property {number} ev
 * @property {number} nm
 * @property {number} fosc
 */

/**
 * @typedef {Object} NmrPayload
 * @property {number} idx
 * @property {string} el
 * @property {number} iso
 * @property {number} aniso
 */

/**
 * @typedef {Object} NebPointPayload
 * @property {string} label
 * @property {number} e_eh
 * @property {number} de_kcal
 * @property {boolean} is_ts
 */

/**
 * @typedef {Object} GeomAtomPayload
 * @property {string} el
 * @property {number} x
 * @property {number} y
 * @property {number} z
 */

/**
 * @typedef {Object} OrbitalPayload
 * @property {number} idx
 * @property {number} occ
 * @property {number} ev
 * @property {string} spin      "" restricted, else "a"/"b" (two manifolds)
 * @property {string} frontier  "homo" | "lumo" | ""
 */

/**
 * @typedef {Object} TddftStatePayload
 * @property {number} state
 * @property {number} ev
 * @property {[string, string, number][]} contributions   [from, to, weight]
 */

/**
 * One CREST conformer for the Results ensemble list (geometry not sent — the
 * batch "generate ORCA jobs" action re-reads it server-side).
 * @typedef {Object} ConformerPayload
 * @property {number} index      1-based rank (1 = lowest energy)
 * @property {number} energy_eh  absolute energy (Hartree)
 * @property {number} rel_kcal   energy relative to the best conformer (kcal/mol)
 * @property {number} n_atoms
 */

/**
 * Parsed .out payload (parse_out_path / parse_calc_output). On failure only
 * {error} is present, so every field is optional.
 * @typedef {Object} ParsePayload
 * @property {boolean} [cancelled]   legacy cancel branch (no producer today)
 * @property {string} [path]         set only on the path-addressed parses, so a
 *                                   result opened from disk stays plottable
 * @property {[string, string, string][]} [summary]   label/value/category rows
 * @property {boolean} [is_optimization]         gates "Final geometry"
 * @property {boolean} [show_elec]               gates electronic-structure sections
 * @property {TransitionPayload[]} [transitions]
 * @property {number[]} [frequencies]            cm^-1, negatives = imaginary
 * @property {number} [n_imaginary]
 * @property {[string, number][]} [mulliken]
 * @property {[string, number][]} [loewdin]
 * @property {[number, string, number][]} [mayer_valences]   [idx, el, valence]
 * @property {[string, string, number][]} [mayer_bonds]      [atom_i, atom_j, order]
 * @property {NmrPayload[]} [nmr]
 * @property {NebPointPayload[]} [neb_path]
 * @property {string} [neb_path_kind]         "neb" | "irc" — titles the path profile
 * @property {GeomAtomPayload[]} [geometry]
 * @property {OrbitalPayload[]} [orbitals]
 * @property {TddftStatePayload[]} [tddft_states]
 * @property {string} [input_keywords]
 * @property {string} [input_block]
 * @property {boolean} [is_conformer_search]     gates the CREST conformer list
 * @property {ConformerPayload[]} [conformers]   CREST ensemble (ranked)
 * @property {string} [error]
 */

/* ---------- ok/error envelopes (orcamgr/gui/bridge.py) ---------- */

/**
 * Plain {ok[, error]} result (run_queue, cancel_queue, stop_after_current,
 * stop_server; cancel/stop never carry "error").
 * @typedef {Object} OkResult
 * @property {boolean} ok
 * @property {string} [error]
 */

/**
 * Queue-mutation result (add_calc, update_calc, remove_calc, clear_queue,
 * reorder_calc): a snapshot rides along on success.
 * @typedef {Object} MutationResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {QueueSnapshot} [snapshot]
 */

/**
 * get_calc(name) result.
 * @typedef {Object} GetCalcResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {CalcFull} [calc]
 */

/**
 * Text-bearing result (build_inp_preview, get_inp).
 * @typedef {Object} TextResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [text]
 */

/**
 * get_graph_lines(name) result; "lines" is always present.
 * @typedef {Object} GraphLinesResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string[]} lines
 */

/**
 * get_output_tail(name, maxLines) result; "lines" is always present.
 * @typedef {Object} LogTailResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string[]} lines
 * @property {string} [file]
 * @property {boolean} [truncated]
 */

/**
 * export_conformers(name) / export_frames(...) result: how many .xyz files
 * were written and into which folder.
 * @typedef {Object} ExportResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {number} [count]
 * @property {string} [folder]
 */

/**
 * One 3D-viewer frame. Mirror of the Python MolFramePayload
 * (orcamgr/state/schemas.py; built by orcamgr/molview.py) — xyz is raw
 * .xyz text 3Dmol parses directly.
 * @typedef {Object} MolFrame
 * @property {string} label
 * @property {string} xyz
 * @property {number|null} energy    Hartree, or null when unknown
 */

/**
 * get_structure_frames(path) result. Mirror of the Python FramesResult.
 * title/frames are present on the ok branch; "folder" is the favorites/export
 * destination — the folder itself, or a file's parent.
 * @typedef {Object} FramesResult
 * @property {boolean} ok
 * @property {string} [title]
 * @property {MolFrame[]} [frames]
 * @property {string} [folder]
 * @property {string} [error]
 */

/**
 * One openable .xyz set found beside a result (list_structure_sets). Mirror of
 * the Python StructureSetPayload. "kind" is "folder" (a subfolder of .xyz
 * files, e.g. the conformers/ export) or "file" (one possibly multi-frame
 * .xyz); "count" is files for a folder and frames for a file, 0 when unknown.
 * @typedef {Object} StructureSetPayload
 * @property {string} key
 * @property {string} label
 * @property {string} kind
 * @property {string} path
 * @property {number} count
 */

/**
 * list_structure_sets(source) result — what Results › Visual can open for a
 * result without a file dialog. Mirror of the Python StructureSetsResult.
 * @typedef {Object} StructureSetsResult
 * @property {boolean} ok
 * @property {StructureSetPayload[]} [sets]
 * @property {string} [error]
 */

/**
 * pick_result_file() result — the Results tab's one Open file… button. Mirror
 * of the Python PickedResultPayload. "route" is "parse" (an .out/.mlip.json,
 * read by the parser) or "structure" (a .xyz, opened in the 3D viewer);
 * "cancelled" marks a closed picker, which is a choice rather than a failure.
 * @typedef {Object} PickedResultPayload
 * @property {boolean} ok
 * @property {boolean} [cancelled]
 * @property {string} [path]
 * @property {string} [route]
 * @property {string} [error]
 */

/**
 * get_favorites(source) / toggle_favorite(...) result. Mirror of the Python
 * FavoritesResult; "labels" is present on every branch — the backend's
 * in-memory list, so a star that could not be PERSISTED still reports
 * ok:false with an error and the labels the session is working with.
 * @typedef {Object} FavoritesResult
 * @property {boolean} ok
 * @property {string[]} labels
 * @property {string} [error]
 */

/* ---- structure screening + editing (Build tab) --------------------------- */

/**
 * One screening finding. Mirror of the Python StructureIssuePayload (4 keys).
 * "level" is "error" (ORCA will refuse this, or it is not the structure anyone
 * meant) or "warn" (legitimate, but worth a look); "atoms" are 0-based indices
 * the 3D preview highlights.
 * @typedef {Object} StructureIssue
 * @property {string} level
 * @property {string} code
 * @property {string} message
 * @property {number[]} atoms
 */

/**
 * check_structure(xyz, charge, multiplicity) result. Mirror of the Python
 * StructureCheckPayload (7 keys). "ok" is the VERDICT (no error-level issues),
 * not a call-succeeded flag — the slot cannot fail. "electrons" is null when a
 * symbol is not an element, so the count is unknown rather than guessed.
 * @typedef {Object} StructureCheck
 * @property {boolean} ok
 * @property {number} n_atoms
 * @property {string} formula
 * @property {number|null} electrons
 * @property {number} n_bonds
 * @property {number} n_fragments
 * @property {StructureIssue[]} issues
 */

/**
 * One diverging atom index in a NEB endpoint pair. Mirror of the Python
 * AtomOrderMismatchPayload (3 keys); "index" is 0-based.
 * @typedef {Object} AtomOrderMismatch
 * @property {number} index
 * @property {string} reactant
 * @property {string} product
 */

/**
 * compare_structures(reactant, product) result. Mirror of the Python
 * AtomOrderPayload (9 keys). "mismatch_index" is the FIRST divergence (what
 * input_generator.check_neb_atom_order projects to); "mismatches" is the capped
 * table, "n_mismatches" the true total.
 * @typedef {Object} AtomOrder
 * @property {boolean} ok
 * @property {string} error
 * @property {number|null} mismatch_index
 * @property {number} n_reactant
 * @property {number} n_product
 * @property {number} n_mismatches
 * @property {AtomOrderMismatch[]} mismatches
 * @property {string} formula_reactant
 * @property {string} formula_product
 */

/**
 * One result found in the workspace. Mirror of the Python
 * WorkspaceResultPayload. "path" is the artifact to parse ({name}.mlip.json for
 * an MLIP run, else {name}.out); "queued" marks the ones the queue also holds —
 * those must be parsed by NAME (kind dispatch), never by path (the folder
 * heuristic), so the front-end keeps the two routes apart. "kind" is a display
 * hint only.
 * @typedef {Object} WorkspaceResult
 * @property {string} name
 * @property {string} path
 * @property {boolean} queued
 * @property {"orca"|"mlip"|"crest"} kind
 */

/**
 * list_workspace_results() result. Mirror of the Python WorkspaceResultsResult
 * — every result on disk under the workspace root, newest first and bounded.
 * "results" is present on every branch ([] on failure).
 * @typedef {Object} WorkspaceResultsResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [root]
 * @property {WorkspaceResult[]} results
 */

/**
 * get_plot_options(source) result. Mirror of the Python PlotOptionsResult —
 * what a finished calculation can be visualized as. "kinds" is a subset of
 * ["mo","eldens","esp","spindens"] (spin density only for an open-shell calc);
 * "cached" names the cube files already on disk, which open instantly.
 * The orbital list is NOT here — the Results tab already holds it.
 * The esp_* fields are the ESP map's own conventions: it costs minutes rather
 * than seconds so it opens at a coarser grid, and it is drawn from two cubes —
 * a density surface at esp_surface_iso, coloured over ±esp_range.
 * @typedef {Object} PlotOptionsResult
 * @property {boolean} ok
 * @property {string} [base]         filename stem the cubes are named from
 * @property {string} [folder]       the run folder these files live in
 * @property {boolean} [has_gbw]
 * @property {boolean} [open_shell]
 * @property {string[]} [kinds]
 * @property {number[]} [grids]
 * @property {number} [default_grid]
 * @property {string[]} [cached]
 * @property {number} [esp_grid]
 * @property {number} [esp_surface_iso]
 * @property {number} [esp_range]
 * @property {string} [error]
 */

/**
 * generate_cube(payload) / get_cube_status() payload. Mirror of the Python
 * CubeJobPayload. state is "idle" | "running" | "done" | "error";
 * "cached" marks a result served from an existing file, not a fresh run.
 * @typedef {Object} CubeJob
 * @property {string} state
 * @property {string} [label]
 * @property {string} [error]
 * @property {string} [kind]
 * @property {number} [index]
 * @property {number} [operator]
 * @property {number} [grid]
 * @property {boolean} [cached]
 * @property {number} [seconds]
 * @property {string} [path]         the finished cube file ("" until done)
 */

/**
 * save_structure_xyz(source, xyz) result. Mirror of the Python SavedFileResult:
 * the path comes back because the next move is to hand it to another program.
 * @typedef {Object} SavedFileResult
 * @property {boolean} ok
 * @property {string} [path]
 * @property {string} [error]
 */

/**
 * run_nbo_analysis / get_nbo_status payload. Mirror of the Python
 * NboJobPayload. state is "idle" | "running" | "done" | "error".
 * @typedef {Object} NboJobPayload
 * @property {string} state
 * @property {string} [source]
 * @property {string} [error]
 * @property {number} [seconds]
 */

/**
 * @typedef {Object} NboHybridPayload
 * @property {number} atom
 * @property {string} element
 * @property {number} share
 * @property {string} label
 */

/**
 * @typedef {Object} NboOrbitalPayload
 * @property {string} label
 * @property {number} index        position in its spin's NBO basis (the cube address)
 * @property {string} kind
 * @property {number[]} atoms
 * @property {number} occupancy
 * @property {number} energy
 * @property {NboHybridPayload[]} hybrids
 */

/**
 * @typedef {Object} NboInteractionPayload
 * @property {string} donor
 * @property {string} acceptor
 * @property {number} energy_kcal
 * @property {number} gap_hartree
 * @property {number} fock_hartree
 */

/**
 * One spin's Lewis structure. Mirror of the Python NboLewisPayload.
 * @typedef {Object} LewisSummaryPayload
 * @property {string} spin
 * @property {number} threshold
 * @property {number} lewis_electrons
 * @property {number} total_electrons
 * @property {number} lewis_fraction
 * @property {boolean} complete
 * @property {number} rydberg_count
 * @property {number} rydberg_electrons
 * @property {NboOrbitalPayload[]} orbitals
 * @property {NboInteractionPayload[]} interactions
 */

/**
 * @typedef {Object} NboAtomPayload
 * @property {number} index
 * @property {string} element
 * @property {number} charge
 * @property {number} core
 * @property {number} valence
 * @property {number} rydberg
 * @property {number} total
 * @property {string} configuration
 * @property {number} spin
 * @property {number} valence_index
 */

/**
 * get_nbo_result() payload. Mirror of the Python NboResultPayload
 * (nbo.analysis.NboAnalysis.to_dict() plus the ok/error envelope).
 * @typedef {Object} NboResultPayload
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [base]
 * @property {number} [n_atoms]
 * @property {number} n_basis
 * @property {number} n_electrons
 * @property {number} [charge]
 * @property {boolean} restricted
 * @property {boolean} [has_ecp]
 * @property {NboAtomPayload[]} atoms
 * @property {number[][]} bonds
 * @property {LewisSummaryPayload[]} lewis
 * @property {Object<string, number>} diagnostics
 * @property {string[]} [warnings]
 */

/**
 * get_cube_data() result. Mirror of the Python CubeDataResult. "text" is the
 * cube file verbatim, for 3Dmol's addVolumetricData(text, "cube"); "title" is
 * ORCA's own description of what it plotted; isovalue/signed seed the surface
 * controls (signed = draw a ± pair).
 * @typedef {Object} CubeDataResult
 * @property {boolean} ok
 * @property {string} [text]
 * @property {string} [title]
 * @property {number} [npoints]
 * @property {number[]} [dims]
 * @property {number} [bytes]
 * @property {number} [isovalue]
 * @property {boolean} [signed]
 * @property {string} [error]
 */

/**
 * set_wallpaper_image(dataUri) result. Mirror of the Python WallpaperResult.
 * stored=false means the input was empty/invalid/oversize and the stored
 * image was cleared instead. The OSError branch returns the bare {error}
 * envelope (no "ok"), so every key is optional.
 * @typedef {Object} WallpaperResult
 * @property {boolean} [ok]
 * @property {boolean} [stored]
 * @property {string} [error]
 */

/**
 * @typedef {Object} FepPoint
 * @property {string} name
 * @property {number} gibbs_eh
 * @property {number|null} final_energy_eh
 * @property {string} kind
 */

/**
 * get_free_energy_profile() result (ok is always true).
 * @typedef {Object} FepResult
 * @property {boolean} ok
 * @property {FepPoint[]} points
 */

/**
 * check_overwrite_conflicts() result; "conflicts" is always present.
 * @typedef {Object} ConflictsResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string[]} conflicts
 */

/**
 * has_existing_output(name) result; "exists" is always present. Backs the
 * mid-run add warning (the live queue starts added calcs without the
 * Run-click conflicts modal).
 * @typedef {Object} ExistsResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {boolean} exists
 */

/* ---------- server control (phone sync) ---------- */

/**
 * get_server_status(): url/token/clients are absent when unavailable.
 * @typedef {Object} ServerStatusPayload
 * @property {boolean} available
 * @property {boolean} running
 * @property {string} [url]
 * @property {string} [token]
 * @property {number} [clients]
 */

/**
 * get_connect_qr(): failure variants raised after URL construction still
 * carry "url".
 * @typedef {Object} QrResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [data_uri]   data:image/png;base64,...
 * @property {string} [url]
 */

/**
 * start_server() result.
 * @typedef {Object} StartServerResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [url]
 * @property {string} [token]
 */
