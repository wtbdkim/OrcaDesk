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
 * Mirror of calc_to_dict() in orcamgr/state/store.py — 12 keys, all
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
 */

/**
 * Queue snapshot returned by get_queue() and in the "snapshot" key of
 * queue-mutating slots. Mirror of QueueStore.snapshot().
 * @typedef {Object} QueueSnapshot
 * @property {boolean} running
 * @property {number} version           monotonically increasing change counter
 * @property {CalcSummary[]} calculations
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
 * (to_dict()/from_dict are a plain asdict round-trip) — 29 fields.
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
 * @property {string} [crest_method]   CREST tight-binding method (kind "crest*"): gfn2|gfnff|gfn0
 * @property {string} [crest_solvent]  ALPB implicit-solvent name ("" = gas phase)
 * @property {number} [crest_ewin]     conformer energy window (kcal/mol)
 * @property {number} [crest_threads]  CREST thread count (-T)
 * @property {string} [crest_env_id]   preferred WSL distro ("" = first with CREST)
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
 * @property {string} theme
 * @property {"conservative"|"eager"} eta_mode
 * @property {"all5"|"maxgrad"} geo_graph_mode
 * @property {"beginner"|"expert"|"mlip"|"crest"} build_mode
 * @property {string} crest_distro     preferred WSL distro for CREST ("" = auto-detect)
 * @property {boolean} orca_valid
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
 * @property {boolean} wsl
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
 * choice, not an error); ok=false means a real read failure.
 * @typedef {Object} LoadResult
 * @property {boolean} ok
 * @property {boolean} cancelled
 * @property {string} text
 * @property {string} name    filename stem, for auto-filling the calc name ("" if none)
 * @property {string} error   "" except on the read-failure branch
 * @property {string} workspace  folder auto-adopted as the workspace (xyz loads only), else ""
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
 * Parsed .out payload (parse_out_file / parse_out_path). On failure only
 * {error} is present; parse_out_file returns literally "{}" on a
 * cancelled file dialog, so every field is optional.
 * @typedef {Object} ParsePayload
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
