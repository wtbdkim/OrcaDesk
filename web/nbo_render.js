// @ts-check
// Results › Output: the natural-orbital analysis card (NPA charges, Wiberg
// bond orders, the Lewis structure, the second-order table). Plain global
// script, loaded after results_render.js and before app.js.
//
// Unlike every other Output section this one is not read out of the parse
// payload: it is computed on demand from the run's .gbw, on a background
// thread, and cached beside the run by the backend. So the card opens as a
// single button, reports the wait, and renders in place when the answer lands.
// A result already analysed in this session renders straight from _nboCache.

/** @type {Object<string, NboResultPayload>} analyses by source string */
const _nboCache = {};
let _nboBusySource = "";      // source being analysed ("" = idle)
let _nboBusyAt = 0;
/** @type {any} */ let _nboTimer = null;

/** Append the card for the current result. Called from renderResultSections. */
function renderNboCard() {
  const body = document.getElementById("result-body");
  if (!body) return;
  const source = _mvPlotSource();
  if (!source) return;
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Natural bond orbital analysis</div>
    <div id="nbo-card">${_nboCardHtml(source)}</div>`;
}

/** Re-render just the card, without touching the rest of Output. */
function _nboRepaint() {
  const card = document.getElementById("nbo-card");
  const source = _mvPlotSource();
  if (card && source) card.innerHTML = _nboCardHtml(source);
}

/** @param {string} source */
function _nboCardHtml(source) {
  const cached = _nboCache[source];
  if (cached) return _nboBodyHtml(cached);
  const busy = _nboBusySource === source;
  const face = busy ? "Analysing… " + _fmtElapsed(Date.now() - _nboBusyAt) : "Run analysis";
  return `
    <div class="hint">NPA charges, Wiberg bond orders, the Lewis structure with its
      hybrids, and the second-order donor → acceptor table — computed by ORCAdesk
      from this run's wavefunction. Seconds on most molecules; kept beside the run.</div>
    <div style="margin-top:8px">
      <button class="btn btn-sm ${busy ? "vis-busy" : ""}" ${busy ? "disabled" : ""}
              onclick="runNboAnalysis()">${escapeHtml(face)}</button>
    </div>`;
}

/** @param {number} ms */
function _fmtElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

/** Start the analysis for the current result and poll it home. */
async function runNboAnalysis() {
  const source = _mvPlotSource();
  if (!source || _nboBusySource) return;
  _nboBusySource = source; _nboBusyAt = Date.now();
  _nboRepaint();
  _nboTimer = setInterval(() => { if (_nboBusySource) _nboRepaint(); }, 1000);
  try {
    /** @type {NboJobPayload} */ let job;
    try { job = /** @type {NboJobPayload} */ (JSON.parse(await bridge.run_nbo_analysis(source))); }
    catch (e) { failNotify("Could not start the analysis."); return; }
    // The backend runs one analysis at a time; a refusal hands back the
    // in-flight job's status, which is not ours unless it names our source.
    while (job && job.state === "running") {
      await new Promise(r => setTimeout(r, 250));
      try { job = /** @type {NboJobPayload} */ (JSON.parse(await bridge.get_nbo_status())); }
      catch (e) { failNotify("Lost contact with the analysis."); return; }
    }
    if (!job || job.state !== "done" || job.source !== source) {
      failNotify((job && job.error) || "The analysis did not finish.");
      return;
    }
    /** @type {NboResultPayload} */ let result;
    try { result = /** @type {NboResultPayload} */ (JSON.parse(await bridge.get_nbo_result())); }
    catch (e) { failNotify("Could not read the analysis."); return; }
    if (!result || !result.ok) { failNotify((result && result.error) || "Could not read the analysis."); return; }
    _nboCache[source] = result;
  } finally {
    clearInterval(_nboTimer); _nboTimer = null;
    _nboBusySource = "";
    _nboRepaint();
  }
}

// ---------- rendering ----------

/** @param {number} x @param {number} [digits] */
function _f(x, digits) { return Number(x).toFixed(digits === undefined ? 4 : digits); }

/** @param {NboResultPayload} a */
function _nboBodyHtml(a) {
  const open = !a.restricted;
  let html = "";
  // headline: what was analysed and how well the Lewis picture fits
  const lewis = a.lewis || [];
  const fit = lewis.map(l => (l.spin ? l.spin + " " : "") +
    (100 * l.lewis_fraction).toFixed(2) + "%" + (l.complete ? "" : " (incomplete)")).join(", ");
  html += `<div class="kv" style="margin-bottom:8px">
    <span class="k">Electrons</span><span class="v">${_f(a.n_electrons, 1)}</span>
    <span class="k">Basis functions</span><span class="v">${a.n_basis}</span>
    <span class="k">Minimal basis holds</span><span class="v">${(100 * (a.diagnostics.minimal_fraction || 0)).toFixed(2)}%</span>
    <span class="k">Lewis structure holds</span><span class="v">${escapeHtml(fit)}</span>
    ${a.has_ecp ? `<span class="k">ECP</span><span class="v">present</span>` : ""}
  </div>`;
  for (const w of (a.warnings || []))
    html += `<div class="hint" style="color:var(--warn)">${escapeHtml(w)}</div>`;

  html += _nboChargesHtml(a, open);
  html += _nboBondsHtml(a);
  for (const l of lewis) html += _nboLewisHtml(l, a);
  for (const l of lewis) html += _nboInteractionsHtml(l);
  html += `<div class="hint" style="margin-top:8px">Computed by ORCAdesk from the published
    NPA/NBO methods (Reed, Weinstock & Weinhold 1985; Reed, Curtiss & Weinhold 1988).
    Not the NBO program's output: the weighted orthogonalization is under-specified in
    the literature, so small differences from it are expected.</div>`;
  return html;
}

/** @param {NboResultPayload} a @param {boolean} open */
function _nboChargesHtml(a, open) {
  let rows = "";
  for (const r of a.atoms) {
    rows += `<tr><td>${r.index + 1} ${escapeHtml(r.element)}</td>
      <td>${_f(r.charge)}</td>${open ? `<td>${_f(r.spin)}</td>` : ""}
      <td>${_f(r.core, 3)}</td><td>${_f(r.valence, 3)}</td><td>${_f(r.rydberg, 3)}</td>
      <td>${_f(r.valence_index, 2)}</td>
      <td class="mono" style="text-align:left">${escapeHtml(r.configuration)}</td></tr>`;
  }
  const sum = a.atoms.reduce((s, r) => s + r.charge, 0);
  return `
    <div class="card-title" style="margin-top:12px">NPA charges and natural electron configuration</div>
    <div style="max-height:320px;overflow:auto">
      <table class="data">
        <thead><tr><th>Atom</th><th>NPA charge (e)</th>${open ? "<th>Spin</th>" : ""}
          <th>Core</th><th>Valence</th><th>Rydberg</th><th>Valence index</th>
          <th style="text-align:left">Configuration</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Sum of charges = ${sum.toFixed(4)} e. NPA charges are nearly
      basis-set independent, where Mulliken and Löwdin drift — compare the sections above.</div>`;
}

/** @param {NboResultPayload} a */
function _nboBondsHtml(a) {
  if (!a.bonds || !a.bonds.length) return "";
  const el = (i) => `${escapeHtml(a.atoms[i].element)}${i + 1}`;
  const rows = a.bonds.map(([i, j, o]) =>
    `<tr><td>${el(i)} – ${el(j)}</td><td>${_f(o, 3)}</td></tr>`).join("");
  return `
    <div class="card-title" style="margin-top:12px">Wiberg bond orders (NAO basis)</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>Bond</th><th>Order</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Pairs above 0.1; 1,3 neighbours across a ring or a carboxyl come
      through at 0.1–0.2. ORCA's Mayer orders (above) are a different definition of the
      same idea.</div>`;
}

/** @param {LewisSummaryPayload} l @param {NboResultPayload} a */
function _nboLewisHtml(l, a) {
  const spin = l.spin ? ` (${l.spin})` : "";
  const kinds = { CR: "core", LP: "lone pair", BD: "bond", "BD*": "antibond", "LP*": "empty valence" };
  const hyb = (o) => (o.hybrids || []).map(h =>
    `${(100 * h.share).toFixed(0)}% ${escapeHtml(h.element)}(${escapeHtml(h.label)})`).join("  ");
  const rows = l.orbitals.map(o => `<tr>
      <td class="mono" style="text-align:left">${escapeHtml(o.label)}</td>
      <td>${kinds[o.kind] || escapeHtml(o.kind)}</td>
      <td>${_f(o.occupancy, 4)}</td>
      <td>${(o.energy * 27.211386).toFixed(2)}</td>
      <td class="mono" style="text-align:left">${hyb(o)}</td></tr>`).join("");
  const rung = l.threshold.toFixed(2);
  return `
    <div class="card-title" style="margin-top:12px">Lewis structure${spin}</div>
    <div class="hint">Occupancy threshold ${rung}${l.threshold < (l.spin ? 0.95 : 1.90) - 1e-9
      ? " — lowered from the usual " + (l.spin ? "0.95" : "1.90") + " to reach a complete structure" : ""};
      ${_f(l.lewis_electrons, 3)} of ${_f(l.total_electrons, 1)} electrons in Lewis orbitals,
      ${_f(l.rydberg_electrons, 3)} in ${l.rydberg_count} Rydberg orbitals (not listed).</div>
    <div style="max-height:360px;overflow:auto">
      <table class="data">
        <thead><tr><th style="text-align:left">Orbital</th><th>Kind</th><th>Occupancy</th>
          <th>Energy (eV)</th><th style="text-align:left">Hybrids</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/** @param {LewisSummaryPayload} l */
function _nboInteractionsHtml(l) {
  if (!l.interactions || !l.interactions.length) return "";
  const spin = l.spin ? ` (${l.spin})` : "";
  const rows = l.interactions.map(x => `<tr>
      <td class="mono" style="text-align:left">${escapeHtml(x.donor)}</td>
      <td class="mono" style="text-align:left">${escapeHtml(x.acceptor)}</td>
      <td>${_f(x.energy_kcal, 2)}</td>
      <td>${_f(x.gap_hartree, 3)}</td>
      <td>${_f(x.fock_hartree, 4)}</td></tr>`).join("");
  return `
    <div class="card-title" style="margin-top:12px">Second-order donor → acceptor interactions${spin}</div>
    <div class="hint">ΔE(2) = −q·F²/(ε<sub>j</sub> − ε<sub>i</sub>): the stabilization from
      letting a filled Lewis orbital leak into an empty one. Entries above 0.5 kcal/mol,
      strongest first (at most 100).</div>
    <div style="max-height:360px;overflow:auto">
      <table class="data">
        <thead><tr><th style="text-align:left">Donor</th><th style="text-align:left">Acceptor</th>
          <th>ΔE(2) (kcal/mol)</th><th>ε<sub>j</sub> − ε<sub>i</sub> (Eh)</th><th>F<sub>ij</sub> (Eh)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}
