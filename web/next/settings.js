// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — settings.

   A right-hand slide-over, not a third view: Settings is somewhere you go and
   come straight back from, and taking the whole window for it would throw away
   the queue you were watching.

   The last card is the switch back to the classic UI. The way out of a preview
   has to be inside the preview — and it also names what this front-end does
   not have yet, rather than letting it be discovered as a control that is not
   there.
   ============================================================ */

(function () {

  let _saving = false;
  /** the MLIP poll while the sheet is open, so a probe that finishes is seen
   *  without the user re-opening Settings @type {any} */
  let _mlipTimer = null;

  /** The five Liquid-Glass steps, in the order the slider walks them. The
   *  setting is a word, not a number: the slider is a way of picking one. */
  const GLASS = ["restrained", "moderate", "bold", "vivid", "maximal"];

  /** @param {string} label @param {string} key @param {string} [kind] @param {string} [hint] */
  function row(label, key, kind, hint) {
    const v = (NB.settings && NB.settings[key]) != null ? NB.settings[key] : "";
    return `<div class="field"><label>${NB.esc(label)}</label>
      <input type="${kind || "text"}" data-s="${key}" class="mono" value="${NB.esc(String(v))}">
      ${hint ? `<span class="hint">${hint}</span>` : ""}</div>`;
  }

  function open() {
    render();
    NB.$("scrim").hidden = false;
    NB.$("settings").hidden = false;
  }
  function close() {
    NB.$("settings").hidden = true;
    NB.$("scrim").hidden = true;
    // the MLIP poll belongs to the open sheet, not to the session
    clearInterval(_mlipTimer);
    _mlipTimer = null;
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
          <div class="scard-title">Appearance</div>
          <p class="scard-desc">Light and dark are the \u263d toggle in the top bar, in either
            style. <b>This front-end draws flat</b> \u2014 the style below is what the classic
            window uses, and it is saved from here so the choice does not depend on
            which front-end you are in.</p>
          <div class="field-row" style="margin-bottom:0">
            <div class="field mid"><label for="s-style">Style</label>
              <select id="s-style" data-s2="style">
                <option value="shadcn"${s.theme_variant !== "liquidglass" ? " selected" : ""}>shadcn (flat)</option>
                <option value="liquidglass"${s.theme_variant === "liquidglass" ? " selected" : ""}>Liquid Glass</option>
              </select></div>
            <div class="field"><label for="s-int">Glass intensity</label>
              <input id="s-int" class="slider" type="range" min="0" max="4" step="1"
                     data-s2="glass" value="${Math.max(0, GLASS.indexOf(s.glass_level || "moderate"))}"
                     ${s.theme_variant === "liquidglass" ? "" : "disabled"}
                     aria-label="Glass intensity">
              <span class="hint" style="margin:0" data-a="glass-hint"></span></div>
          </div>
        </div>

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
        </div>

        <div class="scard">
          <div class="scard-title">About</div>
          <div class="kvline"><span>Version</span>
            <span class="v">${NB.esc((NB.about && NB.about.version) || "")}</span></div>
          <div class="kvline"><span>Front-end</span><span class="v">notebook (preview)</span></div>
        </div>

        <div class="scard">
          <div class="scard-title">Interface</div>
          <p class="scard-desc">Which window layout ORCAdesk opens with. Switching applies
            immediately — a running calculation keeps running. <b>Classic</b> is the finished
            tabbed window, and still the only one that can BUILD an MLIP or CREST
            calculation or draw the Liquid-Glass styles. <b>Notebook</b> is this one,
            still in development.</p>
          <div class="field-row" style="margin-bottom:0">
            <div class="field mid"><label for="s-ui">Layout</label>
              <select id="s-ui" data-s2="ui">
                <option value="classic"${s.ui_variant !== "notebook" ? " selected" : ""}>Classic</option>
                <option value="notebook"${s.ui_variant === "notebook" ? " selected" : ""}>Notebook (preview)</option>
              </select></div>
          </div>
        </div>
      </div>`;

    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    q('[data-a="orca-state"]').textContent = s.orca_valid
      ? "ORCA is set." : "Not set — an ORCA calculation will refuse to start.";

    q('[data-a="close"]').addEventListener("click", close);
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
    q('[data-a="save"]').addEventListener("click", () => save(host));

    // --- appearance: the slider only means something in the glass style
    const style = q('[data-s2="style"]');
    const glass = q('[data-s2="glass"]');
    const glassHint = q('[data-a="glass-hint"]');
    const sayGlass = () => {
      glassHint.textContent = style.value === "liquidglass"
        ? GLASS.map((g, i) => i === Number(glass.value) ? g.toUpperCase() : g).join(" \u00b7 ")
        : "shadcn is flat \u2014 intensity applies to Liquid Glass.";
    };
    style.addEventListener("change", () => { glass.disabled = style.value !== "liquidglass"; sayGlass(); });
    glass.addEventListener("input", sayGlass);
    sayGlass();

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

  /** @param {HTMLElement} host */
  async function save(host) {
    if (_saving) return;
    _saving = true;
    const q = (sel) => /** @type {any} */ (host.querySelector(sel));
    const num = (k, d) => { const n = parseInt(q(`[data-s="${k}"]`).value, 10); return isFinite(n) ? n : d; };
    const prevUi = (NB.settings && NB.settings.ui_variant) || "classic";
    const payload = {
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
      ui_variant: q('[data-s2="ui"]').value,
      theme_variant: q('[data-s2="style"]').value,
      glass_level: GLASS[Number(q('[data-s2="glass"]').value)] || "moderate",
    };
    const res = await NB.call("save_settings", JSON.stringify(payload));
    _saving = false;
    if (!res || res.error) { NB.fail("Could not save settings: " + ((res && res.error) || "unknown")); return; }
    NB.settings = res;
    NB.$("ws-path").title = res.workspace_root;
    NB.$("ws-path").textContent = NB.shortPath(res.workspace_root);
    NB.renderPills();
    if (SCFGraph.setEtaMode) SCFGraph.setEtaMode(res.eta_mode);
    if (SCFGraph.setGeoMode) SCFGraph.setGeoMode(res.geo_graph_mode);
    if (res.save_error) { NB.fail(res.save_error); return; }
    const saved = q('[data-a="saved"]');
    saved.textContent = "Saved.";
    setTimeout(() => { const e = q('[data-a="saved"]'); if (e) e.textContent = ""; }, 2000);
    // Last, and only on a clean save: this replaces the page, so nothing after
    // it would run. The setting is already on disk, so the incoming front-end
    // reads the choice that brought it up.
    if (res.ui_variant !== prevUi) NB.bridge.reload_ui();
  }

  function init() {
    // the graph renderers read these two at draw time
    if (NB.settings) {
      if (SCFGraph.setEtaMode) SCFGraph.setEtaMode(NB.settings.eta_mode);
      if (SCFGraph.setGeoMode) SCFGraph.setGeoMode(NB.settings.geo_graph_mode);
    }
  }

  NB.settingsView = { init: init, open: open, close: close, render: render };
})();
