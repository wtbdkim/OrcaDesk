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
   @typedef names declared here are visible to web/next/*.js and
   web/scf_graph.js without imports. It defines no runtime values.
   ============================================================ */

/* ---------- queue / calculations (orcamgr/state/store.py) ---------- */

/**
 * One calculation as it appears in the polled queue snapshot.
 * Mirror of calc_to_dict() in orcamgr/state/store.py — 28 keys, all
 * always present.
 * @typedef {Object} CalcSummary
 * @property {string} name              unique; doubles as the on-disk folder name
 * @property {string} kind              "opt"|"ts_opt"|"freq"|"ts_freq"|"tddft"|"sp"|"nmr"|"neb_ts"|...
 * @property {number} charge
 * @property {number} multiplicity
 * @property {"direct"|"reference"} geometry_source
 * @property {string} ref_name          referenced calc name ("" for direct)
 * @property {boolean} is_raw
 * @property {"pending"|"running"|"done"|"failed"|"blocked"|"cancelled"|"filtered"} state
 *                                      "filtered": terminal, no output -- the
 *                                      energy condition on its incoming edge
 *                                      (filter_de / filter_top) left it out, or
 *                                      what it depends on was; frozen like done;
 *                                      `message` says why
 * @property {string} message
 * @property {string} output_path
 * @property {string} scf_convergence   from the config; "" when no config
 * @property {string} meta              one-line list-row summary, built server-side
 * @property {string} mlip_model        mlip* kinds: MACE model label; "" otherwise
 * @property {string} crest_method      crest* kinds: tight-binding method; "" otherwise
 * @property {boolean} faulted          running only: ORCA reported a fatal error
 *                                      but its process is still alive
 * @property {boolean} disconnected     running only: its remote device stopped
 *                                      answering -- the orange "Disconnected" box
 * @property {string} system            queue editor lane (system id); "" = standalone
 * @property {string} node              node id within the lane; stack members share it
 * @property {number} col               grid column of the node
 * @property {number} row               grid row of the node
 * @property {string} after             calc this one runs after (edge source); "" = root
 * @property {"auto"|"approval"} gate   the edge's gate
 * @property {boolean} auto_pass        approval gate after a Freq passes by itself
 *                                      on the expected imaginary-mode count
 * @property {boolean} approved         the user approved the gate
 * @property {string} ref_point         which geometry of ref_name: "" final,
 *                                      "irc_f" / "irc_b" the IRC's forward /
 *                                      backward end, "conf:<k>" (k >= 1) the
 *                                      CREST ensemble's k-th conformer (1 = the
 *                                      lowest, as {name}_c1.xyz; ref_name a
 *                                      crest_conf calc), "conf:all" every
 *                                      conformer of a CREST that has not run (a
 *                                      placeholder the engine replaces by one
 *                                      copy per conformer once it is done)
 * @property {string} set_name          display only: the inp set it came from
 * @property {number|null} filter_de    the energy condition on the edge INTO it
 *                                      (graph field, like `gate`): pass only
 *                                      within this many kcal/mol of the lowest
 *                                      of its stack (same system + node); null = off
 * @property {number} filter_top        ...and/or only the N lowest of its
 *                                      stack pass; 0 = off
 * @property {boolean} waiting          derived: its `after` is DONE but the
 *                                      approval gate is closed — the run skips
 *                                      it and leaves it pending
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
 * @property {DeviceUsage[]} devices    each enabled remote device's own pool (the
 *                                      figures above are this machine's); [] idle
 */

/**
 * One remote device's share of a run. Mirror of state/schemas.py DeviceUsage.
 * @typedef {Object} DeviceUsage
 * @property {string} id                the ~/.ssh/config Host alias
 * @property {number} cores_used
 * @property {number} cores_budget
 * @property {number} ram_used_mb
 * @property {number} ram_budget_mb
 * @property {number} jobs
 * @property {number} max_jobs
 * @property {boolean} online           false: off, unreachable, or a job lost contact
 */

/**
 * One remote device (Bridge.get_devices). Mirror of state/schemas.py DevicePayload.
 * @typedef {Object} DevicePayload
 * @property {string} alias
 * @property {"unknown"|"checking"|"ready"|"error"} state
 * @property {string} message           plain-language reason when not ready
 * @property {string} os
 * @property {number} cores
 * @property {number} mem_total_mb
 * @property {number} mem_avail_mb
 * @property {string} orca_path
 * @property {string} orca_version      "" when the install folder does not say
 * @property {boolean} has_mpi
 * @property {boolean} has_setsid
 * @property {string} workdir
 * @property {number} free_disk_mb
 * @property {boolean} in_config        still a Host in ~/.ssh/config
 * @property {number} cleanup_pending   remote jobs a cancel left to tidy up there
 * @property {boolean} enabled
 * @property {number} max_jobs          0 = as many as its cores allow
 * @property {number} cores_cap         0 = the probed cores
 * @property {number} ram_cap_mb        0 = 75% of the probed memory
 * @property {string} orca_path_setting "" = find it
 * @property {string} workdir_setting
 * @property {string[]} prefer        ORCA kinds it takes first ([] = no preference); soft --
 *                                     it takes other kinds when none it prefers is ready
 * @property {boolean} spot             spot / preemptible VM: lost jobs are re-queued
 * @property {number} requeue_after_min continuous silence before a spot job is re-queued
 */

/**
 * Bridge.get_devices / detect_devices / set_remote_device / remove_remote_device.
 * @typedef {Object} DevicesPayload
 * @property {boolean} ssh              an OpenSSH client is on this machine
 * @property {string} config_path
 * @property {DevicePayload[]} devices
 * @property {string} save_error        "" = the settings change was written
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
 * @property {SystemSummary[]} systems  the queue editor's lanes, in order
 *                                      (inputs by label; get_system has the xyz)
 */

/**
 * @typedef {Object} SystemInputSummary
 * @property {string} label
 */

/**
 * A lane as the poll carries it. Mirror of store.system_summary.
 * @typedef {Object} SystemSummary
 * @property {string} id
 * @property {string} name              prefix of every calc name in the lane
 * @property {number} charge
 * @property {number} multiplicity
 * @property {SystemInputSummary[]} inputs
 */

/**
 * @typedef {Object} SystemInput
 * @property {string} label
 * @property {string} xyz               coordinate block (no .xyz header)
 */

/**
 * A lane in full (get_system; the add_system / update_system payloads).
 * Mirror of store.system_from_dict.
 * @typedef {Object} SystemFull
 * @property {string} id
 * @property {string} name
 * @property {number} charge
 * @property {number} multiplicity
 * @property {SystemInput[]} inputs
 */

/**
 * A saved input template. Mirror of InpSet in schemas.py (state/inpsets.py).
 * @typedef {Object} InpSet
 * @property {string} id
 * @property {string} name
 * @property {"ORCA"|"MLIP"|"CREST"} kind
 * @property {string} text   ORCA (a level of theory): a whole input with {{JOB}}
 *                           on its ! line and {{GEOMETRY}}, optionally
 *                           {{CHARGE}} {{MULT}} {{HESS}}; MLIP: "key value"
 *                           lines (model, device, nprocs, env); CREST: "key
 *                           value" lines (method, solvent, ewin, threads,
 *                           preset, nci, solvent_model, env)
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
 * One %geom constraint. Mirror of the dicts input_generator.normalize_constraints
 * keeps: kind C (a fixed atom) / B (bond) / A (angle, vertex in the middle) /
 * D (dihedral), naming 1 / 2 / 3 / 4 atoms, 0-based as ORCA counts them.
 * @typedef {Object} GeomConstraintPayload
 * @property {"C"|"B"|"A"|"D"} kind
 * @property {number[]} atoms
 * @property {number|null} value       target (Å or °); null = hold the start value
 */

/**
 * One %geom Scan coordinate. Mirror of the dicts input_generator.normalize_scan
 * keeps: kind B / A / D naming 2 / 3 / 4 atoms, 0-based; start and end in Å
 * or °, both included; steps = the number of points (2 or more).
 * @typedef {Object} GeomScanPayload
 * @property {"B"|"A"|"D"} kind
 * @property {number[]} atoms
 * @property {number} start
 * @property {number} end
 * @property {number} steps
 */

/**
 * Method configuration. Mirror of StepConfig in input_generator.py
 * (to_dict()/from_dict are a plain asdict round-trip) — 50 fields.
 * The [optional] markers describe the JS→Python SEND direction (the build
 * forms omit keys that don't apply, e.g. crest_* on a DFT calc); in the
 * Python→JS direction to_dict() = asdict(), so all 50 keys always arrive.
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
 * @property {GeomConstraintPayload[]} [geom_constraints]  %geom Constraints;
 *                                     sent only for the kinds that optimize
 * @property {GeomScanPayload[]} [geom_scan]  %geom Scan (a relaxed surface
 *                                     scan); sent only for the kinds that scan
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
 * @property {number} [crest_force_constant]  $constrain force constant, Eh/Bohr² (0.5; 0 = CREST's
 *   own) -- written only for a CREST whose parser takes it (CrestDistroPayload.cinp_extended)
 */

/**
 * Full-fidelity calculation, as returned by get_calc() ("calc" key).
 * Mirror of calc_to_session_dict() in orcamgr/state/store.py — 29 keys.
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
 * @property {"pending"|"running"|"done"|"failed"|"blocked"|"cancelled"|"filtered"} state
 * @property {string} message
 * @property {string} output_path
 * @property {number|null} pid              detached ORCA pid while RUNNING
 * @property {number|null} create_time      process create time (pid-reuse guard)
 * @property {string} system
 * @property {string} node
 * @property {number} col
 * @property {number} row
 * @property {string} after
 * @property {"auto"|"approval"} gate
 * @property {boolean} auto_pass
 * @property {boolean} approved
 * @property {string} ref_point     "" | "irc_f" | "irc_b" | "conf:<k>" | "conf:all" (see CalcSummary)
 * @property {string} set_name
 * @property {number|null} filter_de  edge condition: ΔE window in kcal/mol, null = off (see CalcSummary)
 * @property {number} filter_top      edge condition: only the N lowest of the stack pass, 0 = off
 * @property {boolean} rerun        put back from DONE / FAILED by rerun_calc (its old output is overwritten, never kept)
 * @property {string} run_dir       the folder it ran in, relative to the workspace ("sys/name" or "name"),
 *           recorded when it first started; "" = not started. Read-only: add_calc / update_calc ignore it
 * @property {string} device        where it ran: "" this machine, else a ~/.ssh/config Host alias. Read-only
 * @property {string} remote_dir    the absolute folder of that launch on the device; "" locally. Read-only
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
 * @property {string} [system]
 * @property {string} [node]
 * @property {number} [col]
 * @property {number} [row]
 * @property {string} [after]
 * @property {"auto"|"approval"} [gate]
 * @property {boolean} [auto_pass]
 * @property {boolean} [approved]
 * @property {string} [ref_point]   "" | "irc_f" | "irc_b" | "conf:<k>" | "conf:all"
 * @property {string} [set_name]
 * @property {number|null} [filter_de]
 * @property {number} [filter_top]
 */

/**
 * One queue-editor operation for apply_queue_ops (QueueStore.apply_ops).
 * update_calc's `calc` is a PATCH: keys it leaves out keep their value.
 * place_calc moves a row in the editor's layout only (lane, node, cell) and is
 * allowed in every state -- how a finished calc becomes a lane's first node.
 * remove_system also removes the lane's (unstarted) calcs.
 * rerun_calc puts a set on a DONE or FAILED calc and queues it again (P24:
 * the one way either is recomputed, behind the editor's overwrite warning; a
 * FAILED one's BLOCKED dependents go back to pending with it); undo_rerun
 * puts the old row back while the rerun has not started.
 * @typedef {{op: "add_system", system: Partial<SystemFull> & {name: string}}
 *   | {op: "update_system", id: string, patch: {name?: string, charge?: number, multiplicity?: number, inputs?: SystemInput[]}}
 *   | {op: "remove_system", id: string}
 *   | {op: "add_calc", calc: CalcInput}
 *   | {op: "update_calc", name: string, calc: Partial<CalcInput>}
 *   | {op: "place_calc", name: string, place: {system?: string, node?: string, col?: number, row?: number}}
 *   | {op: "rerun_calc", name: string, calc: {raw_text?: string, set_name?: string, set_id?: string, is_raw?: boolean, config?: Partial<StepConfigPayload>}}
 *   | {op: "undo_rerun", name: string}
 *   | {op: "remove_calc", name: string}
 *   | {op: "order", names: string[]}} QueueOp
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
 * Mirror of the Python Draw2dInstallPayload: progress of the running (or
 * last) 2D-drawing environment creation.
 * @typedef {Object} Draw2dInstallPayload
 * @property {"idle"|"running"|"done"|"error"} state
 * @property {number} step
 * @property {number} steps
 * @property {string} label
 * @property {string} error
 * @property {boolean} cancelled
 */

/**
 * Mirror of the Python Draw2dStatusPayload: Bridge.get_draw2d_status() and
 * every 2D-drawing environment slot -- the add-on's whole state in one poll.
 * @typedef {Object} Draw2dStatusPayload
 * @property {"unset"|"checking"|"ready"|"error"} state
 * @property {string} python       registered interpreter, or ""
 * @property {string} version      its Python version, or ""
 * @property {string} rdkit        RDKit version when it imports, or ""
 * @property {string} message      one sentence: ready / why not
 * @property {"checking"|"ready"} bases_state
 * @property {BasePythonPayload[]} base_pythons   supported = RDKit has wheels
 * @property {string} env_dir      where Create builds the environment
 * @property {Draw2dInstallPayload} install
 */

/**
 * Mirror of the Python Draw2dPerceptionPayload: draw2d_perceive() /
 * get_draw2d_job() -- RDKit's bond perception for one structure.
 * @typedef {Object} Draw2dPerceptionPayload
 * @property {number} [job]
 * @property {"running"|"done"|"error"|"idle"} [state]
 * @property {string} [error]
 * @property {"bonds"|"connectivity"} [method]
 * @property {string} [note]
 * @property {string} [rdkit]
 * @property {number[][]} [bonds]          [i, j, order], 0-based
 * @property {number[]} [charges]
 * @property {number[]} [radicals]
 * @property {number[]} [stereocentres]
 * @property {number} [charge]
 * @property {number} [multiplicity]
 * @property {"calculation"|"output"|"request"|"default"} [charge_from]
 */

/**
 * Mirror of the Python MoLobeAtomPayload: one atom's part of one MO
 * (orcamgr/nbo/molobes.py), for the 2D drawing's orbital overlay.
 * @typedef {Object} MoLobeAtomPayload
 * @property {number} share     Löwdin share of the orbital (all atoms sum to 1)
 * @property {number} s         sqrt(s_share), signed by the largest s coefficient
 * @property {number[]} p       [x, y, z], length sqrt(p_share); + side = + phase
 * @property {number} s_share
 * @property {number} p_share
 * @property {number} d_share   d and higher: in `share`, never drawn as a lobe
 */

/**
 * Mirror of the Python MoListEntryPayload: one orbital of the wavefunction.
 * @typedef {Object} MoListEntryPayload
 * @property {number} index
 * @property {""|"a"|"b"} spin
 * @property {number} ev
 * @property {number} occ
 * @property {string} label     "HOMO", "LUMO+1", "α HOMO", "#12"
 * @property {"homo"|"lumo"|""} frontier
 */

/**
 * Mirror of the Python MoLobesPayload: get_mo_lobes(source, spin, index).
 * State "running" = the wavefunction is being read; call again.
 * @typedef {Object} MoLobesPayload
 * @property {boolean} [ok]
 * @property {"running"|"done"|"error"} [state]
 * @property {string} [error]
 * @property {"alpha"|"beta"} [spin]
 * @property {number} [index]
 * @property {number} [energy_ev]
 * @property {number} [occupation]
 * @property {number} [norm]
 * @property {MoLobeAtomPayload[]} [atoms]
 * @property {string[]} [elements]       the geometry the orbitals belong to
 * @property {number[][]} [coords]       in Å, same atom order
 * @property {MoListEntryPayload[]} [orbitals]   only when index < 0
 */

/**
 * Mirror of the Python Draw2dSaveResult: draw2d_save().
 * @typedef {Object} Draw2dSaveResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [path]
 * @property {boolean} [cancelled]   the user closed the Save dialog
 */

/**
 * One WSL distro probed for CREST (backs the "CREST ready" indicator).
 * @typedef {Object} CrestDistroPayload
 * @property {string} distro      distro name (`wsl -d <distro>`)
 * @property {boolean} ready      crest binary found + runnable
 * @property {string} crest_bin   resolved binary path inside the distro, or ""
 * @property {string} version     `crest --version` line, or ""
 * @property {string} error       detail when not ready
 * @property {boolean} [cinp_extended]  its --cinp parser takes "force constant=" /
 *   "reference=" (the 3.0.2 release crashes on them; the builder locks the field)
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
 * A relaxed scan's coordinate as ORCA's banner states it (parser _parse_scan);
 * atoms 0-based in ORCA's PRINTED order ("B 0 1" prints as (1, 0)).
 * @typedef {Object} ScanCoordPayload
 * @property {"B"|"A"|"D"} kind
 * @property {number[]} atoms
 * @property {number} start
 * @property {number} end
 * @property {number} steps
 */

/**
 * One finished point of a relaxed scan.
 * @typedef {Object} ScanPointPayload
 * @property {number[]} values   one per ScanCoordPayload, same order
 * @property {number} e_eh
 * @property {number} de_kcal    against the lowest point
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
 * The NBO program's printout (`! NBO`), read from the .out
 * (orcamgr/core/parser.py _parse_nbo_program, schemas.NboProgramPayload).
 * Not ORCAdesk's own analysis (NboResultPayload); an open-shell run carries
 * only `atoms`.
 * @typedef {Object} NboProgramPayload
 * @property {string} version
 * @property {boolean} open_shell
 * @property {{index: number, el: string, charge: number, core: number,
 *   valence: number, rydberg: number, total: number, config: string}[]} atoms
 * @property {Object<string, {e: number, pct: number, of: number}>} lewis
 *   core / valence_lewis / total_lewis / valence_nonlewis / rydberg_nonlewis / total_nonlewis
 * @property {string} accepted
 * @property {{index: number, type: string, label: string, atoms: number[],
 *   occ: number, energy_eh: number, lewis: boolean}[]} orbitals
 * @property {{donor: number, donor_label: string, acceptor: number,
 *   acceptor_label: string, e2_kcal: number, de_au: number, f_au: number}[]} interactions
 */

/**
 * ORCA's orbital localization (%loc, e.g. IAO-IBO) printout
 * (parser.py _parse_localization, schemas.LocalizationPayload). Delocalized
 * LMOs are counted (`n_deloc`), not listed.
 * @typedef {Object} LocalizationPayload
 * @property {string} method
 * @property {number[]} range               [first, last] MO localized
 * @property {number|null} strong_threshold
 * @property {number|null} bond_threshold
 * @property {number} n_strong
 * @property {number} n_bond
 * @property {number} n_deloc
 * @property {{mo: number, kind: string,
 *   parts: {atom: number, el: string, pop: number}[]}[]} orbitals
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
 * @property {number[]} [ir_intensities]         km/mol, one per frequencies entry
 *                                              (0 = no IR row); [] = no IR table
 * @property {number} [n_imaginary]
 * @property {[string, number][]} [mulliken]
 * @property {[string, number][]} [loewdin]
 * @property {[number, string, number][]} [mayer_valences]   [idx, el, valence]
 * @property {[string, string, number][]} [mayer_bonds]      [atom_i, atom_j, order]
 * @property {NmrPayload[]} [nmr]
 * @property {NebPointPayload[]} [neb_path]
 * @property {string} [neb_path_kind]         "neb" | "irc" — titles the path profile
 * @property {ScanCoordPayload[]} [scan_coords]  relaxed surface scan: what was scanned
 * @property {ScanPointPayload[]} [scan_points]  ...and each finished point
 * @property {NboProgramPayload|null} [nbo_program]  the NBO program's own printout
 * @property {LocalizationPayload|null} [localization]  ORCA's %loc printout
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
 * @property {string} [warning]  accepted despite this (add/update only)
 * @property {QueueSnapshot} [snapshot]
 */

/**
 * get_calc(name) result.
 * @typedef {Object} GetCalcResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {CalcFull} [calc]
 * @property {string[]} [levels]  the level texts its raw_text becomes under the inp-set migration (inpsets.level_forms)
 */

/**
 * get_inp_sets() result.
 * @typedef {Object} InpSetsResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {InpSet[]} [sets]
 * @property {Object<string, string>} [defaults]  set kind -> its seeded text ("+ New")
 * @property {Object<string, string>} [jobs]  calc kind -> what {{JOB}} becomes for it
 *           ("" for tddft: its %tddft block is the job)
 * @property {string[]} [mlip_jobs]  the kinds a block made from an MLIP set may run
 * @property {string[]} [crest_jobs] the kinds a block made from a CREST set runs (["crest_conf"])
 * @property {Object<string, string>} [next_kind]  calc kind -> the kind a level placed
 *           after it runs by default ("" = a row's start; not listed: "sp")
 */

/**
 * save_inp_set(json) / delete_inp_set(id) result; `set` (save only) is the
 * set as stored, with its id.
 * @typedef {Object} InpSetResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {InpSet} [set]
 */

/**
 * A copied workflow kept by name (state/workflows.py): the queue editor's
 * workflow step list, replayed as add_calc ops.
 * @typedef {Object} SavedWorkflow
 * @property {string} id
 * @property {string} name
 * @property {number} v
 * @property {string} [from]   the row it was copied from
 * @property {Object[]} steps
 */

/**
 * get_workflows() result.
 * @typedef {Object} WorkflowsResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {SavedWorkflow[]} [workflows]
 */

/**
 * save_workflow(json) / delete_workflow(id) result; `workflow` (save only)
 * is the workflow as stored, with its id.
 * @typedef {Object} WorkflowResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {SavedWorkflow} [workflow]
 */

/**
 * get_system(id) result.
 * @typedef {Object} SystemResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {SystemFull} [system]
 */

/**
 * apply_queue_ops(json) result: everything applied (with a fresh snapshot),
 * or nothing. op_index = the 0-based operation refused (-1 = the batch as a
 * whole, e.g. it would leave a reference dangling while the queue runs).
 * @typedef {Object} QueueOpsResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {number} [op_index]
 * @property {QueueSnapshot} [snapshot]
 */

/**
 * One picked / dropped .xyz, read (store.xyz_file_entries).
 * @typedef {Object} XyzFileEntry
 * @property {string} path
 * @property {string} label   the file name
 * @property {string} name    its stem: the system name it gives ("" if none)
 * @property {string} xyz     its one structure as a coordinate block, "" if refused
 * @property {string} error   why it cannot start a row, "" if it can
 * @property {number|null} [charge]        stated on its comment line ("charge=1"), else null
 * @property {number|null} [multiplicity]  stated there ("mult=2", or xtb's "uhf=1"), else null
 */

/**
 * pick_xyz_files() / read_xyz_paths(json) result.
 * @typedef {Object} XyzFilesResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {boolean} [cancelled]
 * @property {XyzFileEntry[]} [files]
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
 * @property {boolean} [pending] the file is still being read; ask again
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
 * get_output_info(name) result: the shape of a whole .out.
 * @typedef {Object} OutputInfoResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [file]
 * @property {number} [bytes]
 * @property {number} [lines]
 * @property {number} [max_len]
 */

/**
 * get_output_lines(name, start, count) result; "lines" is always present.
 * @typedef {Object} OutputLinesResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {number} [start]
 * @property {string[]} lines
 */

/**
 * find_in_output(name, query) result: the line of every match, in order;
 * "hits" is always present, "total" counts past the cap.
 * @typedef {Object} OutputFindResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {number[]} hits
 * @property {number} [total]
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
 * One structure file an IRC wrote. Mirror of the Python IrcStructureFile.
 * @typedef {Object} IrcStructureFile
 * @property {string} key      "F" | "B" | "F_trj" | "B_trj" | "Full_trj"
 * @property {string} label
 * @property {string} path
 * @property {number} count    frames (0: not counted)
 */

/**
 * get_irc_structures(name) result. Mirror of the Python IrcStructuresResult;
 * files are listed only once the IRC is done.
 * @typedef {Object} IrcStructuresResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {boolean} [done]
 * @property {IrcStructureFile[]} [files]
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
 * pick_hess_file() result — a file chosen to be used by path (copied into the
 * run folder at launch). Mirror of the Python PickedFilePayload.
 * @typedef {Object} PickedFilePayload
 * @property {boolean} ok
 * @property {boolean} [cancelled]
 * @property {string} [path]
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
 * apply_constraint_target(xyz, constraint) result. Mirror of the Python
 * ConstraintApplyPayload (7 keys). "xyz" is the whole coordinate text with only
 * the moved atoms' lines rewritten; "moved" their 0-based indices; "warnings"
 * the atoms the motion left too close to one that stayed.
 * @typedef {Object} ConstraintApply
 * @property {boolean} ok
 * @property {string} error
 * @property {string} xyz
 * @property {number[]} moved
 * @property {number|null} before
 * @property {number|null} after
 * @property {string[]} warnings
 */

/**
 * apply_constraint_targets(xyz, constraints) result. Mirror of the Python
 * ConstraintApplyAllPayload (7 keys): ConstraintApply with "before"/"after"
 * one value per constraint that had a target, in the order sent.
 * @typedef {Object} ConstraintApplyAll
 * @property {boolean} ok
 * @property {string} error
 * @property {string} xyz
 * @property {number[]} moved
 * @property {number[]} before
 * @property {number[]} after
 * @property {string[]} warnings
 */

/**
 * One vibration of a .hess. Mirror of the Python HessModePayload: "index"
 * addresses it in the file, "n" is its 1-based number among the vibrations,
 * "freq" in cm-1 (negative = imaginary).
 * @typedef {Object} HessMode
 * @property {number} index
 * @property {number} n
 * @property {number} freq
 */

/**
 * read_hess_modes(path) result. Mirror of the Python HessModesPayload.
 * @typedef {Object} HessModes
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string} [path]
 * @property {string} [name]
 * @property {number} [n_atoms]
 * @property {string[]} [symbols]
 * @property {string} [xyz]
 * @property {HessMode[]} [modes]
 */

/**
 * displace_mode(xyz, path, index, amplitude) result. Mirror of the Python
 * ModeShiftPayload.
 * @typedef {Object} ModeShift
 * @property {boolean} ok
 * @property {string} error
 * @property {string} xyz
 * @property {number[]} moved
 * @property {number} max_shift
 * @property {string} atom
 */

/**
 * get_vibration_mode(payload) result: one vibration of a finished result, for
 * the report to animate. Mirror of the Python VibModePayload. "n" is the
 * report's own mode number, "origin" the file the vector came from, "coords"
 * the modes' geometry moved onto the report's structure and "max_dev" how far
 * it still is from it (Å); "vec" is the displacement per atom.
 * @typedef {Object} VibMode
 * @property {boolean} ok
 * @property {string} [error]
 * @property {number} [n]
 * @property {number} [count]
 * @property {number} [freq]
 * @property {string} [origin]
 * @property {number[][]} [coords]
 * @property {number[][]} [vec]
 * @property {number} [max_dev]
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
 * ["mo","eldens","esp","spindens","pnbo"] (spin density only for an open-shell
 * calc; "pnbo" only when a closed-shell `! NBO` run left its {base}.nbo);
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
 * @property {boolean} [has_nbo]   {base}.nbo from a `! NBO` run (kind "pnbo")
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
 * One finished calculation that can stand in a reaction profile. `gibbs_eh` is
 * null when the run produced no thermochemistry — including the species that
 * cannot have any (a monatomic ion has no vibrations), so the UI picks the
 * basis and refuses to mix G with E. `atoms` and `charge` are what let it say
 * whether the two sides of a step hold the same matter before it prints a
 * difference between them.
 * @typedef {Object} FepPoint
 * @property {string} name
 * @property {number|null} gibbs_eh
 * @property {number|null} final_energy_eh
 * @property {number|null} zpe_eh            ZPE correction (Eh)
 * @property {number|null} total_thermal_eh  inner energy U (Eh)
 * @property {number|null} enthalpy_eh       H (Eh)
 * @property {number|null} entropy_term_eh   T*S (Eh)
 * @property {number|null} temperature_k
 * @property {string} kind
 * @property {Record<string, number>} atoms   element symbol -> count
 * @property {number} charge
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

/**
 * existing_outputs(names_json) result; "existing" is always present: the
 * given names whose workspace folder already holds an output (the
 * has_existing_output rule), in the order given.
 * @typedef {Object} ExistingOutputsResult
 * @property {boolean} ok
 * @property {string} [error]
 * @property {string[]} existing
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
