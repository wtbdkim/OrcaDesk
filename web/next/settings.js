// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — settings.

   A right-hand slide-over, not a third view: Settings is somewhere you go and
   come straight back from, and taking the whole window for it would throw away
   the queue you were watching.
   ============================================================ */

(function () {

  /** the save in flight, so a second click (or the close prompt's Save) waits
   *  on it instead of sending another @type {Promise<boolean>|null} */
  let _saving = null;
  /** The form as it stood when it last matched what is saved -- read back out
   *  of the sheet by collect(), so the comparison is like for like: a field
   *  changed and changed back is not unsaved. null while the sheet is closed.
   *  @type {Record<string, any>|null} */
  let _baseline = null;
  /** the MLIP poll while the sheet is open, so a probe that finishes is seen
   *  without the user re-opening Settings @type {any} */
  let _mlipTimer = null;
  /** the CREST poll, same lifetime as the MLIP one @type {any} */
  let _crestTimer = null;
  /** an install was started from this sheet: report how it ended, once */
  let _crestInstallWatch = false;
  /** the 2D-drawing card's poll, same lifetime as the MLIP one @type {any} */
  let _d2Timer = null;
  /** a Create was started from this sheet: report how it ended, once */
  let _d2Watch = false;
  /** the remote-devices poll, same lifetime as the MLIP one @type {any} */
  let _devTimer = null;

  /** @param {string} label @param {string} key @param {string} [kind] @param {string} [hint] */
  function row(label, key, kind, hint) {
    const v = (NB.settings && NB.settings[key]) != null ? NB.settings[key] : "";
    return `<div class="field"><label>${NB.esc(label)}</label>
      <input type="${kind || "text"}" data-s="${key}" class="mono" value="${NB.esc(String(v))}">
      ${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
  }

  /** an open() waiting on its settings read, so a second click does not
   *  draw the sheet twice @type {boolean} */
  let _opening = false;
  async function open() {
    // already open: a re-render would throw away what is typed in it
    if (!NB.$("settings").hidden || _opening) return;
    // the other window may have saved Settings since this one last read them:
    // the sheet is drawn from what is saved NOW, or its Save writes old values
    // back (boot.js NB.refreshSettings)
    _opening = true;
    try { await NB.refreshSettings(); } finally { _opening = false; }
    if (!NB.$("settings").hidden) return;
    render();
    NB.$("scrim").hidden = false;
    NB.$("settings").hidden = false;
  }
  function close() {
    NB.$("settings").hidden = true;
    NB.$("scrim").hidden = true;
    _baseline = null;
    // the MLIP poll belongs to the open sheet, not to the session
    clearInterval(_mlipTimer);
    _mlipTimer = null;
    clearInterval(_crestTimer);
    _crestTimer = null;
    clearInterval(_d2Timer);
    _d2Timer = null;
    clearInterval(_devTimer);
    _devTimer = null;
  }

  function render() {
    const host = NB.$("settings");
    const s = NB.settings || {};
    if (!host) return;
    host.innerHTML = `
      <div class="sheethead">
        <h2>Settings</h2>
        <span class="sp"></span>
        <span class="hint" style="margin:0" data-a="saved"></span>
        <button class="btn btn-sm btn-primary" type="button" data-a="save">Save</button>
        <button class="iconbtn" type="button" data-a="close" aria-label="Close settings">×</button>
      </div>
      <div class="sheetbody">

        <div class="scard">
          <div class="scard-title">ORCA</div>
          <p class="scard-desc">The executable ORCAdesk launches. It is not bundled —
            this points at your install.</p>
          <div class="field-row">
            ${row("Path to orca", "orca_path")}
          </div>
          <div class="field-row" style="margin-top:10px">
            <button class="btn btn-sm" type="button" data-a="pick-orca">Browse…</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="detect">Auto-detect</button>
            <span class="sp" style="flex:1"></span>
            <span class="hint" style="margin:0" data-a="orca-state"></span>
          </div>
        </div>

        <div class="scard">
          <div class="scard-title">Workspace</div>
          <p class="scard-desc">Every calculation gets its own folder here.</p>
          <div class="field-row">${row("Folder", "workspace_root")}</div>
          <div class="field-row" style="margin-top:10px">
            <button class="btn btn-sm" type="button" data-a="pick-ws">Browse…</button>
          </div>
        </div>

        <div class="scard">
          <div class="scard-title">Defaults for a new calculation</div>
          <p class="scard-desc">What the builder starts from. Each calculation can still
            say otherwise.</p>
          <div class="field-row">
            ${row("nprocs", "default_nprocs", "number")}
            ${row("maxcore (MB / core)", "default_maxcore_mb", "number")}
          </div>
        </div>

        <div class="scard">
          <div class="scard-title">How much of this machine the queue may use</div>
          <p class="scard-desc">ORCA's maxcore is per core, so a 6-core job at 2400 MB
            reserves 14.4 GB — these caps are what keep two of them off the swap file.</p>
          <div class="field-row">
            ${row("Calculations at once", "max_concurrent_jobs", "number",
                  "0 = as many as the budgets below allow")}
          </div>
          <div class="field-row" style="margin-top:10px">
            ${row("Total cores", "max_total_cores", "number", `0 = auto (${s.auto_cores || "?"} here)`)}
            ${row("Total memory (MB)", "max_total_ram_mb", "number", `0 = auto (${s.auto_ram_mb || "?"} MB here)`)}
          </div>
        </div>

        <div class="scard">
          <div class="scard-title">Compute devices</div>
          <p class="scard-desc">Other computers the queue can send ORCA calculations to — a cloud
            VM or a Linux PC you reach over SSH. They are the hosts in your SSH config
            (<span class="mono" data-a="d-config"></span>): set one up there first, so that
            <span class="mono">ssh</span> logs in to it without asking for a password, and install
            ORCA on it. A calculation that does not fit on this machine goes to the first device
            with room; its whole folder comes back here before it counts as done.</p>
          <div data-a="devs"><p class="hint" style="margin:0">Checking…</p></div>
          <div class="btn-group" style="margin-top:10px;align-items:center">
            <button class="btn btn-sm btn-ghost" type="button" data-a="d-detect">Find devices</button>
            <span class="sp" style="flex:1"></span>
            <span class="hint" style="margin:0" data-a="d-state"></span>
          </div>
        </div>

        <div class="scard">
          <div class="scard-title">Optimization graph</div>
          <p class="scard-desc"><b>All 5 criteria</b> plots every convergence criterion as
            value ÷ its tolerance, sharing one goal line at 1 — below the line is met.
            <b>MAX gradient</b> plots that one criterion on an absolute axis instead, with its
            tolerance as the goal line.</p>
          <div class="field-row" style="margin-bottom:0">
            <div class="field mid"><label for="s-geo">Plot</label>
              <select id="s-geo" data-s2="geo">
                <option value="all5"${s.geo_graph_mode !== "maxgrad" ? " selected" : ""}>All 5 criteria</option>
                <option value="maxgrad"${s.geo_graph_mode === "maxgrad" ? " selected" : ""}>MAX gradient only</option>
              </select></div>
          </div>
        </div>

        <div class="scard">
          <div class="scard-title">Time remaining</div>
          <p class="scard-desc">Optimization cycle counts are genuinely hard to predict.
            <b>Conservative</b> waits until the run supports an estimate; <b>eager</b> gives one
            sooner and more often, and is wrong more often for it.</p>
          <div class="field-row" style="margin-bottom:0">
            <div class="field mid"><label for="s-eta">Estimate</label>
              <select id="s-eta" data-s2="eta">
                <option value="conservative"${s.eta_mode !== "eager" ? " selected" : ""}>Conservative</option>
                <option value="eager"${s.eta_mode === "eager" ? " selected" : ""}>Eager</option>
              </select></div>
          </div>
        </div>

        <div class="scard">
          <div class="scard-title">MLIP environments</div>
          <p class="scard-desc">One Python environment per MLIP \u2014 built here or registered.
            They are separate venvs because MLIPs pin conflicting dependencies; the backends
            each one actually carries are detected by importing them, not by asking.</p>
          <div data-a="envs"><p class="hint" style="margin:0">Checking\u2026</p></div>
          <div class="field-row" style="margin:14px 0 0;align-items:flex-end">
            <div class="field"><label for="e-name">New environment</label>
              <input id="e-name" data-a="e-name" type="text" placeholder="e.g. mace-cpu"></div>
            <div class="field mid"><label for="e-back">Backend</label>
              <select id="e-back" data-a="e-back"><option value="mace">MACE</option></select></div>
            <div class="field mid"><label for="e-dev">Device</label>
              <select id="e-dev" data-a="e-dev">
                <option value="cpu">CPU</option>
                <option value="cuda">GPU (CUDA)</option>
              </select></div>
            <button class="btn btn-sm btn-primary" type="button" data-a="e-create">Create</button>
          </div>
          <div class="btn-group" style="margin-top:10px;align-items:center">
            <button class="btn btn-sm btn-ghost" type="button" data-a="e-register">Register an existing one\u2026</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="e-recheck">Re-check</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="e-cancel" hidden>Cancel</button>
            <span class="sp" style="flex:1"></span>
            <span class="hint" style="margin:0" data-a="e-state"></span>
          </div>
          <p class="hint" style="margin:10px 0 0">Create downloads PyTorch (BSD-3-Clause; a GPU
            build also brings NVIDIA's CUDA libraries, under NVIDIA's own terms) and the backend
            — <span class="mono">mace-torch</span> or <span class="mono">sevenn</span>, both
            MIT — with their dependencies (ASE is LGPL-2.1+), from PyPI and
            <span class="mono">download.pytorch.org</span>. The MACE model weights download the
            first time a model runs, each under its own license: several are ASL, for academic,
            non-commercial use only. The builder shows each model's license.</p>
        </div>

        <div class="scard">
          <div class="scard-title" data-a="c-title">CREST</div>
          <p class="scard-desc" data-a="c-desc"></p>
          <div data-a="crest"><p class="hint" style="margin:0">Checking…</p></div>
          <div class="btn-group" style="margin-top:10px;align-items:flex-end">
            <div class="field mid" data-a="c-pick" style="margin:0"><label for="c-distro">WSL distribution</label>
              <select id="c-distro" data-a="c-distro"><option value="">auto-detect</option></select></div>
            <button class="btn btn-sm btn-primary" type="button" data-a="c-install">Install CREST</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="c-recheck">Re-check</button>
            <span class="sp" style="flex:1"></span>
            <span class="hint" style="margin:0" data-a="c-state"></span>
          </div>
          <p class="hint" style="margin:10px 0 0">Install CREST downloads a pinned CREST release
            (the static Linux build) from <span class="mono">github.com/crest-lab/crest</span> and
            checks its SHA-256 before unpacking it; a file that does not match is deleted, never
            run. CREST is LGPL-3.0, and so are the xTB methods built into it (tblite); the
            separate <span class="mono">xtb</span> program is not installed.</p>
        </div>

        <div class="scard">
          <div class="scard-title">2D drawing</div>
          <p class="scard-desc">An add-on. Results \u203a Visual\u2019s <b>2D structure</b> tile
            draws the structure in exactly the 3D view\u2019s orientation, with wedges and
            hashes, to copy or save as an image or a MOL file. Bond orders come from RDKit,
            which runs in a Python environment of its own \u2014 never in ORCAdesk\u2019s.</p>
          <div data-a="d2"><p class="hint" style="margin:0">Checking\u2026</p></div>
          <div class="btn-group" style="margin-top:10px;align-items:center">
            <button class="btn btn-sm btn-primary" type="button" data-a="d2-create">Create</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="d2-register">Register an existing one\u2026</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="d2-recheck">Re-check</button>
            <button class="btn btn-sm btn-ghost" type="button" data-a="d2-cancel" hidden>Cancel</button>
            <span class="sp" style="flex:1"></span>
            <span class="hint" style="margin:0" data-a="d2-state"></span>
          </div>
          <p class="hint" style="margin:10px 0 0">Create builds a venv under ORCAdesk\u2019s
            user folder and downloads RDKit (BSD-3-Clause) with NumPy (BSD-3-Clause) and Pillow
            (MIT-CMU) from PyPI \u2014 about 50 MB, ~150 MB on disk. Register points at any Python
            that already has <span class="mono">rdkit</span>.</p>
        </div>

        <div class="scard">
          <div class="scard-title">About</div>
          <div class="kvline"><span>Version</span>
            <span class="v">${NB.esc((NB.about && NB.about.version) || "")}</span></div>
          <div class="kvline"><span>Source code license</span>
            <span class="v">MIT</span></div>
          <p class="hint">The Windows build also bundles third-party software under its own
            licenses (PyQt6 under GPL-3.0, so the build as a whole is GPL-3.0; Qt and
            Chromium under LGPL and others). They are listed in THIRD_PARTY_NOTICES.txt and
            the licenses folder, in the ORCAdesk install folder.</p>
          <p class="hint">ORCAdesk is an independent personal project, not affiliated with
            the ORCA developers, FACCTs or any institution.</p>
          <p class="hint">Provided as is, without warranty of any kind.</p>
        </div>
      </div>`;

    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    q('[data-a="orca-state"]').textContent = s.orca_valid
      ? "ORCA is set." : "Not set — an ORCA calculation will refuse to start.";

    q('[data-a="close"]').addEventListener("click", () => requestClose());
    q('[data-a="pick-orca"]').addEventListener("click", async () => {
      const p = await NB.bridge.pick_orca_executable();
      if (p) q('[data-s="orca_path"]').value = p;
    });
    q('[data-a="detect"]').addEventListener("click", async () => {
      const r = await NB.call("autodetect_orca");
      if (r && r.ok && r.path) {
        q('[data-s="orca_path"]').value = r.path;
        NB.toast("Found ORCA at " + r.path, "ok");
      } else {
        NB.toast("No ORCA install found in the usual places — browse to it.");
      }
    });
    q('[data-a="pick-ws"]').addEventListener("click", async () => {
      const p = await NB.bridge.pick_workspace();
      if (p) q('[data-s="workspace_root"]').value = p;
    });
    q('[data-a="save"]').addEventListener("click", () => { save(host); });

    // --- MLIP
    q('[data-a="e-recheck"]').addEventListener("click", async () => {
      paintEnvs(host, await NB.call("check_mlip", ""));
    });
    q('[data-a="e-register"]').addEventListener("click", async () => {
      const python = await NB.bridge.pick_mlip_python();
      if (!python) return;
      const name = q('[data-a="e-name"]').value.trim();
      const r = await NB.call("add_mlip_env", JSON.stringify({ name: name, python: python }));
      if (r && r.error) { NB.fail(r.error); return; }
      q('[data-a="e-name"]').value = "";
      paintEnvs(host, r);
    });
    q('[data-a="e-create"]').addEventListener("click", () => createEnv(host));
    q('[data-a="e-cancel"]').addEventListener("click", () => NB.call("cancel_mlip_install"));
    loadEnvs(host);

    // --- CREST
    q('[data-a="c-recheck"]').addEventListener("click", async () => {
      _crestInstallWatch = false;
      paintCrest(host, await NB.call("check_crest"));
    });
    q('[data-a="c-install"]').addEventListener("click", async () => {
      const st = NB.backends.crest;
      const local = !!st && st.transport === "local";
      const distro = local ? "" : q('[data-a="c-distro"]').value;
      NB.toast((local ? "Installing CREST" : "Installing CREST into WSL")
        + " — this downloads ~8 MB…");
      _crestInstallWatch = true;
      paintCrest(host, await NB.call("install_crest", distro));
    });
    q('[data-a="c-distro"]').addEventListener("change", async () => {
      const v = q('[data-a="c-distro"]').value;
      if (NB.settings) NB.settings.crest_distro = v;
      paintCrest(host, await NB.call("set_crest_distro", v));
    });
    loadCrest(host);

    // --- 2D drawing
    q('[data-a="d2-recheck"]').addEventListener("click", async () => {
      paintD2(host, await NB.call("check_draw2d"));
    });
    q('[data-a="d2-register"]').addEventListener("click", async () => {
      const python = await NB.bridge.pick_draw2d_python();
      if (!python) return;
      const r = await NB.call("set_draw2d_python", python);
      if (r && r.error && !r.state) { NB.fail(r.error); return; }
      paintD2(host, r);
    });
    q('[data-a="d2-create"]').addEventListener("click", async () => {
      const r = await NB.call("create_draw2d_env", JSON.stringify({ base_python: "" }));
      if (r && r.error && !r.state) { NB.fail(r.error); return; }
      _d2Watch = true;
      NB.toast("Building the 2D-drawing environment \u2014 about 50 MB from PyPI.");
      paintD2(host, r);
    });
    q('[data-a="d2-cancel"]').addEventListener("click", async () => {
      paintD2(host, await NB.call("cancel_draw2d_install"));
    });
    loadD2(host);
    // --- remote compute devices (act at once, like the CREST card)
    q('[data-a="d-detect"]').addEventListener("click", async () => {
      paintDevices(host, await NB.call("detect_devices"));
    });
    loadDevices(host);

    // what "unsaved" is measured against: the form exactly as it was drawn
    // from the saved settings
    _baseline = collect(host);
  }

  // ------------------------------------------------------- remote devices

  /** Paint once, then poll while the sheet is open: a probe is an SSH call
   *  per device and finishes after the click that started it.
   *  @param {HTMLElement} host */
  async function loadDevices(host) {
    paintDevices(host, await NB.call("get_devices"));
    clearInterval(_devTimer);
    _devTimer = setInterval(async () => {
      if (NB.$("settings").hidden) { clearInterval(_devTimer); _devTimer = null; return; }
      const box = host.querySelector('[data-a="devs"]');
      // never repaint under the user's cursor: a number being typed would
      // lose its focus and its half-typed value
      if (box && box.contains(document.activeElement)) return;
      paintDevices(host, await NB.call("get_devices"));
    }, 1500);
  }

  /** One line saying what a device is, from its probe. @param {any} d */
  function deviceLine(d) {
    if (d.state === "unknown") return "Not checked yet — Find devices checks it.";
    if (d.state === "checking") return "Checking…";
    if (d.state !== "ready") return "";
    const gb = d.mem_total_mb ? Math.round(d.mem_total_mb / 1024) + " GB" : "";
    return [d.os, d.cores ? d.cores + " cores" : "", gb,
            d.orca_version ? "ORCA " + d.orca_version : "ORCA found",
            d.has_mpi ? "" : "no MPI: 1-core calculations only"].filter(Boolean).join(" · ");
  }

  /** One checkbox per ORCA kind, in the builder's words and order
   *  (NB.build.KINDS -- the labels the Guided builder shows), ticked for the
   *  kinds the device prefers. @param {string[]} prefer @returns {string} */
  function preferBoxes(prefer) {
    const kinds = /** @type {Record<string, any>} */ ((NB.build && NB.build.KINDS) || {});
    return Object.keys(kinds).map(k => `<label><input type="checkbox" data-f="prefer" data-k="${NB.esc(k)}"${
      prefer.indexOf(k) >= 0 ? " checked" : ""}> ${NB.esc(kinds[k].label || k)}</label>`).join("");
  }

  /** @param {HTMLElement} host @param {any} st DevicesPayload */
  function paintDevices(host, st) {
    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    const box = q('[data-a="devs"]');
    if (!box || !st) return;
    if (st.error) { box.innerHTML = `<div class="finding err">${NB.esc(st.error)}</div>`; return; }
    q('[data-a="d-config"]').textContent = st.config_path || "~/.ssh/config";
    q('[data-a="d-state"]').textContent = st.save_error || "";
    if (!st.ssh) {
      box.innerHTML = `<p class="hint" style="margin:0">No OpenSSH client found on this machine.
        On Windows it is an optional feature: Settings → Apps → Optional features → OpenSSH Client.</p>`;
      return;
    }
    const devs = st.devices || [];
    if (!devs.length) {
      box.innerHTML = `<p class="hint" style="margin:0">No hosts in your SSH config yet. Add one
        (a Host entry with its address, user and key), then press Find devices.</p>`;
      return;
    }
    box.innerHTML = devs.map((/** @type {any} */ d) => {
      const cls = d.state === "ready" ? "done" : d.state === "error" ? "failed"
        : d.state === "checking" ? "running" : "";
      const word = d.state === "ready" ? "ready" : d.state === "error" ? "unusable"
        : d.state === "checking" ? "checking" : "not checked";
      const num = (/** @type {string} */ f, /** @type {string} */ label, /** @type {number} */ v,
                   /** @type {string} */ tip) =>
        `<label title="${NB.esc(tip)}">${label}<input class="mono" type="number" min="0"
           data-f="${f}" value="${v}"></label>`;
      return `<div class="envrow devrow" data-dev="${NB.esc(d.alias)}">
        <div><div class="n">${NB.esc(d.alias)}</div>
          <div class="d">${NB.esc(deviceLine(d))}</div>
          ${d.in_config ? `<div class="ctl">
            <label><input type="checkbox" data-f="enabled"${d.enabled ? " checked" : ""}> Use</label>
            ${num("max_jobs", "at once", d.max_jobs, "Calculations at once on this device; 0 = as many as its cores allow")}
            ${num("cores", "cores", d.cores_cap, "Cores the queue may use there; 0 = all it has")}
            ${num("ram_mb", "memory (MB)", d.ram_cap_mb, "Memory the queue may use there; 0 = 75% of it")}
          </div>
          <div class="ctl" title="Calculations this device takes first. A preference, not a limit: with nothing of these kinds ready, it takes any other ORCA calculation. None ticked = no preference. Single points (SP, TD-DFT, NMR) never move the structure, so a spot VM loses only their own run.">
            <span class="devpref">prefers</span>${preferBoxes(d.prefer || [])}
          </div>
          <div class="ctl">
            <label title="A spot / preemptible cloud VM can be taken away under a job. A job there that stops answering for this many minutes in a row, or dies with no word from ORCA, is put back in the queue to run again, here or elsewhere."><input type="checkbox" data-f="spot"${d.spot ? " checked" : ""}> Spot / preemptible VM</label>
            ${d.spot ? num("requeue_after_min", "re-queue after (min)", d.requeue_after_min,
                "Minutes of continuous silence before a job there is put back in the queue; any answer starts the count again") : ""}
          </div>` : ""}</div>
        <span class="sp"></span>
        <span class="badge ${cls}">${word}</span>
        ${d.in_config ? "" : `<button class="btn btn-sm btn-ghost" type="button" data-a="d-remove">Remove</button>`}
      </div>` + (d.state === "error" && d.message
        ? `<div class="finding err" style="margin:-4px 0 8px">${NB.esc(d.message)}</div>` : "")
        + (d.cleanup_pending
        ? `<p class="hint" style="margin:-4px 0 8px">${d.cleanup_pending} cancelled
            calculation${d.cleanup_pending === 1 ? "" : "s"} to tidy up there when it is back.</p>` : "");
    }).join("");
    box.querySelectorAll(".devrow").forEach((/** @type {any} */ rowEl) => {
      const alias = rowEl.dataset.dev;
      rowEl.querySelectorAll("[data-f]").forEach((/** @type {any} */ inp) => {
        inp.addEventListener("change", async () => {
          /** @type {Record<string, any>} */ const patch = { alias: alias };
          const f = inp.dataset.f;
          if (f === "enabled" || f === "spot") patch[f] = !!inp.checked;
          else if (f === "prefer") patch.prefer = Array.from(
            rowEl.querySelectorAll('[data-f="prefer"]:checked')).map((/** @type {any} */ b) => b.dataset.k);
          else { const n = parseInt(inp.value, 10); patch[f] = isFinite(n) && n > 0 ? n : 0; }
          const r = await NB.call("set_remote_device", JSON.stringify(patch));
          if (r && r.error) { NB.fail(r.error); return; }
          /** @type {any} */ (document.activeElement || { blur() {} }).blur();
          paintDevices(host, r);
        });
      });
      const rm = rowEl.querySelector('[data-a="d-remove"]');
      if (rm) rm.addEventListener("click", async () => {
        paintDevices(host, await NB.call("remove_remote_device", alias));
      });
    });
  }

  // ---------------------------------------------------------------- CREST

  /** Same shape as the MLIP card: paint once, then keep polling while the
   *  sheet is open -- the probe runs `crest --version` in every WSL distro and
   *  an install downloads, so both finish after the click that started them.
   *  @param {HTMLElement} host */
  async function loadCrest(host) {
    paintCrest(host, await NB.call("get_crest_status"));
    clearInterval(_crestTimer);
    _crestTimer = setInterval(async () => {
      if (NB.$("settings").hidden) { clearInterval(_crestTimer); _crestTimer = null; return; }
      const st = await NB.call("get_crest_status");
      paintCrest(host, st);
      if (_crestInstallWatch && st && st.state !== "checking") {
        _crestInstallWatch = false;
        if (st.install_error) NB.fail("CREST install failed — " + st.install_error);
        else NB.toast("CREST installed.", "ok");
      }
    }, 1200);
  }

  /** @param {HTMLElement} host @param {any} st CrestStatusPayload */
  function paintCrest(host, st) {
    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    const box = q('[data-a="crest"]');
    if (!box || !st || !st.distros) return;
    // the top-bar pill reads the same payload; hand it over rather than wait
    NB.backends.crest = st;
    NB.renderPills();

    const local = st.transport === "local";
    const distros = st.distros;
    const checking = st.state === "checking";
    q('[data-a="c-title"]').textContent = local ? "CREST" : "CREST (WSL)";
    q('[data-a="c-desc"]').textContent = local
      ? "Conformer search. CREST runs natively here; ORCAdesk can install the static "
        + "binary into your home directory, or use one already on PATH."
      : "Conformer search. CREST has no Windows build, so it runs in a WSL distribution "
        + "you already have; ORCAdesk can install it there.";
    const noTarget = local ? "No POSIX shell found — CREST needs bash on PATH."
      : !st.wsl ? "WSL is not available on this machine."
      : "No usable WSL distribution found. Install one, e.g. wsl --install -d Ubuntu.";

    if (!distros.length) {
      box.innerHTML = `<p class="hint" style="margin:0">${checking
        ? (local ? "Looking for CREST…" : "Checking WSL for CREST…")
        : NB.esc(noTarget)}</p>`;
    } else {
      box.innerHTML = distros.map(d => {
        const name = local && d.distro === "local" ? "this machine" : d.distro;
        const detail = d.ready ? [d.version, d.crest_bin].filter(Boolean).join(" · ")
          : (d.error || "CREST is not installed here");
        return `<div class="envrow">
          <div><div class="n">${NB.esc(name)}</div>
            <div class="d">${NB.esc(detail)}</div></div>
          <span class="sp"></span>
          <span class="badge${d.ready ? " done" : ""}">${d.ready ? "ready" : "not installed"}</span>
        </div>`;
      }).join("");
    }

    // one target locally: nothing to pick
    q('[data-a="c-pick"]').hidden = local;
    const sel = q('[data-a="c-distro"]');
    if (!local && document.activeElement !== sel) {
      const prev = (NB.settings && NB.settings.crest_distro) || "";
      const names = distros.map(d => d.distro);
      if (prev && names.indexOf(prev) < 0) names.push(prev);
      sel.innerHTML = `<option value="">auto-detect</option>` + names.map(n =>
        `<option value="${NB.esc(n)}">${NB.esc(n)}</option>`).join("");
      sel.value = prev;
    }

    // Installing is pointless where CREST already is, and impossible with
    // nowhere to install into -- creating a distro needs a Linux account,
    // the one step ORCAdesk cannot script.
    const target = local ? "" : sel.value;
    const already = target ? distros.some(d => d.distro === target && d.ready)
                           : distros.some(d => d.ready);
    const btn = q('[data-a="c-install"]');
    btn.disabled = already || !st.wsl || checking || !distros.length;
    btn.title = already ? "CREST is already installed here" : "";

    q('[data-a="c-state"]').textContent = st.install_error
      ? "Install failed — " + st.install_error
      : checking ? (_crestInstallWatch ? "Installing…" : "Checking…")
      : "";
  }

  // ---------------------------------------------------------------- 2D drawing

  /** Paint once, then poll while the sheet is open: the probe imports RDKit
   *  and a Create runs pip, both finishing after the click that started them.
   *  @param {HTMLElement} host */
  async function loadD2(host) {
    paintD2(host, await NB.call("get_draw2d_status"));
    clearInterval(_d2Timer);
    _d2Timer = setInterval(async () => {
      if (NB.$("settings").hidden) { clearInterval(_d2Timer); _d2Timer = null; return; }
      paintD2(host, await NB.call("get_draw2d_status"));
    }, 1200);
  }

  /** @param {HTMLElement} host @param {any} st Draw2dStatusPayload */
  function paintD2(host, st) {
    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    const box = q('[data-a="d2"]');
    if (!box || !st || !st.state) return;
    const inst = st.install || {};
    const running = inst.state === "running";
    if (st.state === "unset") {
      box.innerHTML = `<p class="hint" style="margin:0">Not set up. Create one, or register
        a Python that already has RDKit.</p>`;
    } else {
      const cls = st.state === "ready" ? "done" : st.state === "error" ? "failed" : "running";
      const word = st.state === "ready" ? "ready" : st.state === "error" ? "unusable" : "checking";
      const detail = [st.rdkit ? "RDKit " + st.rdkit : "",
                      st.version ? "Python " + st.version : ""].filter(Boolean).join(" · ");
      box.innerHTML = `<div class="envrow">
          <div style="min-width:0"><div class="n">RDKit environment</div>
            <div class="d">${NB.esc(detail || (st.state === "checking" ? "checking…" : ""))}</div>
            <div class="d2env">${NB.esc(st.python)}</div></div>
          <span class="sp"></span>
          <span class="badge ${cls}">${word}</span>
          <button class="btn btn-sm btn-ghost" type="button" data-a="d2-remove">Remove</button>
        </div>` + (st.state === "error" && st.message
        ? `<div class="finding err" style="margin:-4px 0 8px">${NB.esc(st.message)}</div>` : "");
      q('[data-a="d2-remove"]').addEventListener("click", () => {
        NB.confirm("Remove the 2D-drawing environment",
          "ORCAdesk stops using this Python. Nothing is deleted from disk — an environment "
          + "Create built stays where it is, and Create picks it up again.",
          "Remove", async () => paintD2(host, await NB.call("remove_draw2d_env")));
      });
    }
    // Create: pointless with a working environment, impossible with no base
    // Python to build one from (D41: disabled with the reason, never pretend)
    const bases = (st.base_pythons || []).filter(b => b.supported);
    const create = q('[data-a="d2-create"]');
    const why = running ? "Building…"
      : st.state === "ready" ? "A working environment is already registered"
      : st.bases_state !== "ready" ? "Looking for a Python to build from…"
      : !bases.length ? "No Python 3.9–3.15 found on this machine — install one, then Re-check"
      : "";
    create.disabled = !!why;
    create.title = why || `Builds ${st.env_dir} from Python ${bases[0].version}`;
    q('[data-a="d2-cancel"]').hidden = !running;
    const state = q('[data-a="d2-state"]');
    if (running) {
      state.textContent = `${inst.step || 0}/${inst.steps || 3} · ${inst.label || "working…"}`;
    } else if (inst.state === "error" && (inst.error || inst.cancelled)) {
      state.textContent = inst.cancelled ? "Cancelled." : inst.error;
    } else if (!bases.length && st.bases_state === "ready" && st.state !== "ready") {
      state.textContent = "No Python 3.9–3.15 found — install one, then Re-check.";
    } else {
      state.textContent = "";
    }
    if (_d2Watch && !running && (inst.state === "done" || inst.state === "error")) {
      _d2Watch = false;
      if (inst.state === "done") NB.toast("2D-drawing environment built.", "ok");
      else if (!inst.cancelled) NB.fail("2D-drawing environment: " + (inst.error || "the build failed"));
    }
  }

  // ---------------------------------------------------------------- MLIP

  /** Ask once for everything the card needs, then keep polling while the sheet
   *  is open: a probe imports torch, which takes seconds, and an environment
   *  that finished checking after the sheet opened would otherwise sit at
   *  "Checking\u2026" until the user closed and re-opened Settings.
   *  @param {HTMLElement} host */
  async function loadEnvs(host) {
    const opts = await NB.call("get_mlip_install_options");
    const back = /** @type {any} */ (host.querySelector('[data-a="e-back"]'));
    if (back && opts && opts.backends && opts.backends.length) {
      back.innerHTML = opts.backends.map(b =>
        `<option value="${NB.esc(b.key)}">${NB.esc(b.label)}</option>`).join("");
    }
    const dev = /** @type {any} */ (host.querySelector('[data-a="e-dev"]'));
    if (dev && opts && !opts.gpu) {
      // a GPU env on a machine with no GPU is a multi-gigabyte download that
      // can only end in "torch sees no CUDA device"
      dev.querySelector('[value="cuda"]').disabled = true;
      dev.querySelector('[value="cuda"]').textContent = "GPU (CUDA) \u2014 none detected";
    } else if (dev && opts && opts.gpu_name) {
      dev.querySelector('[value="cuda"]').textContent = "GPU \u2014 " + opts.gpu_name;
    }
    paintEnvs(host, await NB.call("get_mlip_status"));
    clearInterval(_mlipTimer);
    _mlipTimer = setInterval(async () => {
      if (NB.$("settings").hidden) { clearInterval(_mlipTimer); _mlipTimer = null; return; }
      paintEnvs(host, await NB.call("get_mlip_status"));
      await pollInstall(host);
    }, 1200);
  }

  /** @param {HTMLElement} host @param {any} st MlipStatusPayload */
  function paintEnvs(host, st) {
    const box = /** @type {HTMLElement} */ (host.querySelector('[data-a="envs"]'));
    if (!box) return;
    const envs = (st && st.envs) || [];
    if (!envs.length) {
      box.innerHTML = `<p class="hint" style="margin:0">No MLIP environment yet. Build one
        below, or register a Python you already have one in.</p>`;
      return;
    }
    box.innerHTML = envs.map(e => {
      const cls = e.state === "ready" ? "done" : e.state === "error" ? "failed" : "running";
      const word = e.state === "ready" ? "ready" : e.state === "error" ? "unusable" : "checking";
      // what it IS, in one line: which MLIPs import, on what, under which Python
      const backs = (e.backends || []).map(b => b.label || b.key || b).join(", ");
      const detail = [backs || "no MLIP backend found",
                      e.cuda ? (e.cuda_name || "CUDA") : (e.cuda === false ? "CPU" : ""),
                      e.version ? "Python " + e.version : ""].filter(Boolean).join(" \u00b7 ");
      return `<div class="envrow">
        <div><div class="n">${NB.esc(e.name || e.id)}</div>
          <div class="d">${NB.esc(detail)}</div></div>
        <span class="sp"></span>
        <span class="badge ${cls}">${word}</span>
        <button class="btn btn-sm btn-ghost" type="button" data-env="${NB.esc(e.id)}">Remove</button>
      </div>` + (e.state === "error" && e.message
        ? `<div class="finding err" style="margin:-4px 0 8px">${NB.esc(e.message)}</div>` : "");
    }).join("");
    box.querySelectorAll("[data-env]").forEach(b => b.addEventListener("click", () => {
      const id = /** @type {HTMLElement} */ (b).dataset.env;
      const env = envs.filter(e => e.id === id)[0];
      NB.confirm("Remove this environment",
        `ORCAdesk stops using \u201c${(env && env.name) || id}\u201d. The Python itself is left
         alone \u2014 nothing is deleted from disk.`,
        "Remove", async () => paintEnvs(host, await NB.call("remove_mlip_env", id)));
    }));
  }

  /** @param {HTMLElement} host */
  async function createEnv(host) {
    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    const name = q('[data-a="e-name"]').value.trim();
    if (!name) { NB.fail("Give the environment a name \u2014 it becomes its folder."); return; }
    const device = q('[data-a="e-dev"]').value;
    const r = await NB.call("create_mlip_env", JSON.stringify({
      name: name, backend: q('[data-a="e-back"]').value, device: device }));
    if (r && r.error && !r.state) { NB.fail(r.error); return; }
    NB.toast(device === "cuda"
      ? "Building the environment \u2014 the CUDA torch wheel is a multi-gigabyte download."
      : "Building the environment \u2014 this takes a few minutes.");
    await pollInstall(host, r);
  }

  /** @param {HTMLElement} host @param {any} [known] */
  async function pollInstall(host, known) {
    const st = known || await NB.call("get_mlip_install_status");
    if (!st) return;
    const state = /** @type {HTMLElement} */ (host.querySelector('[data-a="e-state"]'));
    const cancel = /** @type {any} */ (host.querySelector('[data-a="e-cancel"]'));
    const create = /** @type {any} */ (host.querySelector('[data-a="e-create"]'));
    if (!state) return;
    const running = st.state === "running";
    cancel.hidden = !running;
    create.disabled = running;
    if (running) {
      state.textContent = `${st.step || 0}/${st.steps || 4} \u00b7 ${st.label || "working\u2026"}`;
    } else if (st.state === "error") {
      state.textContent = st.cancelled ? "Cancelled." : (st.error || "The build failed.");
    } else if (st.state === "done") {
      state.textContent = "Built.";
    } else {
      state.textContent = "";
    }
  }

  /** The settings the sheet would save, read out of its fields -- one reader
   *  for both the save and the unsaved-changes check, so the two cannot
   *  disagree about what a field means.
   *  @param {HTMLElement} host @returns {Record<string, any>} */
  function collect(host) {
    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    const num = (k, d) => { const n = parseInt(q(`[data-s="${k}"]`).value, 10); return isFinite(n) ? n : d; };
    return {
      orca_path: q('[data-s="orca_path"]').value.trim(),
      workspace_root: q('[data-s="workspace_root"]').value.trim(),
      default_nprocs: num("default_nprocs", 6),
      default_maxcore_mb: num("default_maxcore_mb", 2400),
      // 0 is meaningful for all three ("auto" / "as many as fit"), so an empty
      // or unparseable field must fall back to 0, not to a made-up limit
      max_concurrent_jobs: num("max_concurrent_jobs", 0),
      max_total_cores: num("max_total_cores", 0),
      max_total_ram_mb: num("max_total_ram_mb", 0),
      geo_graph_mode: q('[data-s2="geo"]').value,
      eta_mode: q('[data-s2="eta"]').value,
    };
  }

  /** What each collected key is called on the sheet, for the close prompt. */
  const LABELS = {
    orca_path: "Path to orca", workspace_root: "Workspace folder",
    default_nprocs: "Default nprocs", default_maxcore_mb: "Default maxcore",
    max_concurrent_jobs: "Calculations at once", max_total_cores: "Total cores",
    max_total_ram_mb: "Total memory", geo_graph_mode: "Optimization graph",
    eta_mode: "Time remaining",
  };

  /** The fields that differ from what is saved -- empty when closing loses
   *  nothing. The MLIP and CREST cards act at once and are not part of it.
   *  @param {HTMLElement} host @returns {string[]} */
  function unsaved(host) {
    if (!_baseline || !host || host.hidden || !host.querySelector('[data-s="orca_path"]')) return [];
    const now = collect(host);
    return Object.keys(now).filter(k => now[k] !== _baseline[k]);
  }

  /** Close the sheet -- after asking, when something on it is unsaved. Every
   *  way out (the ×, Escape, the backdrop, a reload) comes through here.
   *  Dismissing the question (Escape, its backdrop) is Cancel: the sheet stays.
   *  @param {() => void} [after] run once the sheet has actually closed */
  function requestClose(after) {
    const host = NB.$("settings");
    const done = () => { close(); if (after) after(); };
    const keys = unsaved(host);
    if (!keys.length) { done(); return; }
    const one = keys.length === 1;
    NB.modalRaw("Save changes to settings?",
      `<p>${one ? "This setting is" : "These settings are"} changed but not saved:
        ${keys.map(k => NB.esc(LABELS[k] || k)).join(", ")}.
        <b>Closing without saving discards ${one ? "the change" : "them"}.</b></p>`,
      [{ label: "Cancel", cls: "btn-ghost" },
       { label: "Discard", cls: "btn-danger", act: done },
       { label: "Save", cls: "btn-primary", focus: true,
         act: async () => { if (await save(host)) done(); } }]);
  }

  /** Save the sheet. Resolves true only on a clean save -- a refused one, or
   *  one the backend applied but could not write to disk, leaves the sheet
   *  open with its error toasted and its changes still counted as unsaved.
   *  @param {HTMLElement} host @returns {Promise<boolean>} */
  function save(host) {
    if (!_saving) _saving = doSave(host).finally(() => { _saving = null; });
    return _saving;
  }

  /** @param {HTMLElement} host @returns {Promise<boolean>} */
  async function doSave(host) {
    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    // Only what was changed on this sheet: a field left alone keeps whatever
    // is saved now, even if the other window saved it while this sheet was
    // open (sending the whole form wrote its old value back).
    const all = collect(host);
    /** @type {Record<string, any>} */ const payload = {};
    Object.keys(all).forEach(k => { if (!_baseline || all[k] !== _baseline[k]) payload[k] = all[k]; });
    const res = await NB.call("save_settings", JSON.stringify(payload));
    if (!res || res.error) { NB.fail("Could not save settings: " + ((res && res.error) || "unknown")); return false; }
    NB.settings = res;
    NB.$("ws-path").title = res.workspace_root;
    NB.$("ws-path").textContent = NB.shortPath(res.workspace_root);
    NB.renderPills();
    if (SCFGraph.setEtaMode) SCFGraph.setEtaMode(res.eta_mode);
    if (SCFGraph.setGeoMode) SCFGraph.setGeoMode(res.geo_graph_mode);
    if (res.save_error) { NB.fail(res.save_error); return false; }
    // The backend clamps the numbers (nprocs 0 is saved as 1): show what was
    // saved, then measure "unsaved" from it, so closing now asks nothing.
    host.querySelectorAll("[data-s]").forEach(el => {
      const inp = /** @type {any} */ (el);
      const v = res[inp.dataset.s || ""];
      if (v != null) inp.value = String(v);
    });
    _baseline = collect(host);
    const saved = q('[data-a="saved"]');
    saved.textContent = "Saved.";
    setTimeout(() => { const e = q('[data-a="saved"]'); if (e) e.textContent = ""; }, 2000);
    return true;
  }

  function init() {
    // the graph renderers read these two at draw time
    if (NB.settings) {
      if (SCFGraph.setEtaMode) SCFGraph.setEtaMode(NB.settings.eta_mode);
      if (SCFGraph.setGeoMode) SCFGraph.setGeoMode(NB.settings.geo_graph_mode);
    }
  }

  NB.settingsView = { init: init, open: open, close: close, requestClose: requestClose, render: render };
})();
