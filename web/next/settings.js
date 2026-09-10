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
          <div class="scard-title">About</div>
          <div class="kvline"><span>Version</span>
            <span class="v">${NB.esc((NB.about && NB.about.version) || "")}</span></div>
          <div class="kvline"><span>Front-end</span><span class="v">notebook (preview)</span></div>
        </div>

        <div class="scard">
          <div class="scard-title">Interface</div>
          <p class="scard-desc">Which window layout ORCAdesk opens with. Switching applies
            immediately — a running calculation keeps running. <b>Classic</b> is the finished
            tabbed window, and the only one with the MLIP and CREST setup, the 3D viewer, the
            natural-orbital analysis and the appearance variants. <b>Notebook</b> is this one,
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
