// @ts-check
// Results-tab section renderers (renderResult/renderResultSections, renderSummary,
// renderGeometry … renderFreeEnergyProfile) — split out of app.js. Plain global
// script, loaded before app.js.

// ---------- results ----------
function refreshResultSelect() {
  const sel = document.getElementById("result-select");
  const prev = sel.value;
  sel.innerHTML = "";
  const names = Object.keys(calcResults);
  if (!names.length) { sel.innerHTML = `<option>—</option>`; return; }
  for (const n of names) { const e = document.createElement("option"); e.value=n; e.textContent=n; sel.appendChild(e); }
  if (names.includes(prev)) sel.value = prev;
}
function showSelectedResult() {
  const name = document.getElementById("result-select").value;
  if (!name || name === "—") return;
  _currentResultName = name;   // a queued calc — enables the conformer->ORCA action
  _currentResult = _resultExtras[name] || null;
  renderResult(_currentResult);
}

/** Render summary + every applicable section for a parsed payload. @param {ParsePayload} [d] */
function renderResult(d) {
  if (!d) return;
  if (d.summary) renderSummary(d.summary, d);
  renderResultSections(d);
}

/** Toggle "Show all": reveal every parsed value regardless of calc-type gating. */
function toggleShowAll() {
  showAllResults = !showAllResults;
  const btn = document.getElementById("btn-showall");
  if (btn) btn.classList.toggle("on", showAllResults);   // tinted box when on
  if (_currentResult) renderResult(_currentResult);
}

/**
 * Render every non-summary section we have for a parsed result, in a fixed
 * order. Accepts a ParsePayload (Open .out) or the cached payload of a queue
 * result. renderSummary must be called first — these all append to the body.
 * @param {ParsePayload} [d]
 */
function renderResultSections(d) {
  if (!d) return;
  // "Final geometry" → opt jobs; electronic-structure sections → sp/opt jobs.
  // "Show all" overrides both. Specialty sections (freq/tddft/nmr/neb) are
  // present-only — they only exist for their kind, so no extra gating needed.
  const geom = showAllResults || d.is_optimization;
  const elec = showAllResults || d.show_elec;
  // CREST conformer ensemble: a selectable list with a batch "re-optimize in
  // ORCA" action. Present-only (only crest_conf results carry it).
  if (d.is_conformer_search && d.conformers && d.conformers.length) renderConformers(d.conformers);
  if (geom && d.geometry && d.geometry.length) renderGeometry(d.geometry);
  if (elec && d.orbitals && d.orbitals.length) renderOrbitals(d.orbitals);
  if (elec && d.mulliken && d.mulliken.length) renderMulliken(d.mulliken);
  if (elec && d.loewdin && d.loewdin.length) renderLoewdin(d.loewdin);
  if (elec && ((d.mayer_bonds && d.mayer_bonds.length) || (d.mayer_valences && d.mayer_valences.length)))
    renderMayer(d.mayer_bonds || [], d.mayer_valences || []);
  if (d.frequencies && d.frequencies.length) renderFreqSpectrum(d.frequencies, d.n_imaginary);
  if (d.transitions && d.transitions.length) renderSpectrum(d.transitions);
  if (d.tddft_states && d.tddft_states.length) renderTddftStates(d.tddft_states);
  if (d.nmr && d.nmr.length) renderNmr(d.nmr);
  if (d.neb_path && d.neb_path.length) renderNebPath(d.neb_path, d.neb_path_kind || "neb");
  if (d.input_keywords || d.input_block) renderInputEcho(d.input_keywords, d.input_block);
}
/** @param {[string, string, string][]} rows @param {ParsePayload} [d] */
function renderSummary(rows, d) {
  const body = document.getElementById("result-body");
  // electronic-structure rows (category "elec") only show for sp/opt jobs,
  // unless "Show all" is on
  const showElec = showAllResults || !!(d && d.show_elec);
  let html = `<div class="kv">`;
  for (const row of rows) {
    const k = row[0], v = row[1], cat = row[2] || "";
    if (cat === "elec" && !showElec) continue;
    // Failure patterns FIRST, and else-if so later tests can't overwrite:
    // "NOT converged" contains "converged" and "ABNORMAL" contains "Normal",
    // so sequential ifs let the success test repaint failures green.
    let cls = "";
    if (/ABNORMAL|NOT converged/i.test(String(v))) cls = "err";
    else if (/imaginary/i.test(k) && !/^0$/.test(String(v))) cls = "warn";
    else if (/converged|Normal/i.test(String(v))) cls = "ok";
    html += `<div class="k">${escapeHtml(k)}</div><div class="v ${cls}">${escapeHtml(String(v))}</div>`;
  }
  html += `</div>`;
  body.innerHTML = html;
}

/** CREST conformer ensemble: a read-only ranked list. The Results tab is for
 *  interpreting results — follow-up calculations are built on the Build tab by
 *  referencing the CREST search (a reference receives the lowest-energy
 *  conformer's geometry). @param {ConformerPayload[]} confs */
function renderConformers(confs) {
  const body = document.getElementById("result-body");
  let rows = "";
  for (const c of confs) {
    rows += `<tr>
      <td>${c.index}</td>
      <td>${c.rel_kcal.toFixed(3)}</td>
      <td>${c.energy_eh.toFixed(8)}</td>
      <td>${c.n_atoms}</td></tr>`;
  }
  // Export / 3D view are only meaningful for a queued CREST calc (they need its
  // workspace folder server-side); an externally opened .out has no
  // _currentResultName.
  const actions = _currentResultName
    ? `<div class="btn-group">
         <button class="btn btn-sm" onclick="viewConformers3D()">View in 3D</button>
         <button class="btn btn-sm btn-ghost" onclick="exportConformers()">Export as .xyz</button>
       </div>` : "";
  body.innerHTML += `
    <div class="divider"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">
      <div class="card-title">Conformers (${confs.length})</div>
      ${actions}
    </div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th><th>ΔE (kcal/mol)</th><th>E (Eh)</th><th>atoms</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint" style="margin-top:6px">Follow-up calculations are built on the Build tab: reference this search from a geometry source — the referencing calculation runs on the lowest-energy conformer. <b>View in 3D</b> flips through every conformer with the ← / → keys; <b>Export as .xyz</b> writes each one (c1 = the best) to a <code>conformers/</code> subfolder of the run.</div>`;
}

/** Split the shown CREST search's ensemble into per-conformer .xyz files
 *  (in a conformers/ subfolder of the run folder). */
async function exportConformers() {
  if (!_currentResultName) return;
  try {
    const r = /** @type {ExportResult} */ (JSON.parse(await bridge.export_conformers(_currentResultName)));
    if (!r.ok) { failNotify(r.error || "Could not export conformers."); return; }
    toast(`Exported ${r.count} conformer${r.count === 1 ? "" : "s"} to ${r.folder}`);
    appendLog(`Exported ${r.count} conformer .xyz files to ${r.folder}`, "ok");
  } catch (e) { failNotify("Could not export conformers."); }
}

let _lastGeomXyz = "";   // last rendered geometry, for the Copy .xyz button
/** @param {GeomAtomPayload[]} geom */
function renderGeometry(geom) {
  const body = document.getElementById("result-body");
  let rows = "";
  geom.forEach((a, i) => {
    rows += `<tr><td>${i+1}</td><td>${escapeHtml(a.el)}</td><td>${a.x.toFixed(6)}</td><td>${a.y.toFixed(6)}</td><td>${a.z.toFixed(6)}</td></tr>`;
  });
  _lastGeomXyz = `${geom.length}\n\n` + geom.map(a => `${a.el}  ${a.x.toFixed(6)}  ${a.y.toFixed(6)}  ${a.z.toFixed(6)}`).join("\n");
  body.innerHTML += `
    <div class="divider"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px">
      <div class="card-title">Final geometry (${geom.length} atoms, Å)</div>
      <button class="btn btn-sm btn-ghost" onclick="copyGeometryXyz()">Copy .xyz</button>
    </div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th><th>El</th><th>x</th><th>y</th><th>z</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}
async function copyGeometryXyz() {
  if (!_lastGeomXyz) return;
  try { await navigator.clipboard.writeText(_lastGeomXyz); toast("Geometry copied as .xyz."); }
  catch (e) { failNotify("Copy failed — clipboard unavailable."); }
}

/** @param {OrbitalPayload[]} orbs */
function renderOrbitals(orbs) {
  const body = document.getElementById("result-body");
  let homoI = -1;
  orbs.forEach((o, i) => { if (o.occ > 0.01) homoI = i; });
  let rows = "";
  orbs.forEach((o, i) => {
    let tag = "", color = "";
    if (i === homoI) { tag = " ← HOMO"; color = "color:var(--ok);font-weight:600"; }
    else if (i === homoI + 1) { tag = " ← LUMO"; color = "color:var(--warn);font-weight:600"; }
    rows += `<tr><td>${o.idx}</td><td>${o.occ.toFixed(3)}</td><td style="${color}">${o.ev.toFixed(4)}${tag}</td></tr>`;
  });
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Orbital energies (${orbs.length} levels)</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>#</th><th>Occ</th><th>E (eV)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/** @param {[string, number][]} mulliken */
function renderMulliken(mulliken) {
  const body = document.getElementById("result-body");
  let rows = "", sum = 0;
  mulliken.forEach(([el, q], i) => {
    sum += q;
    rows += `<tr><td>${i+1} ${escapeHtml(el)}</td><td>${q.toFixed(6)}</td></tr>`;
  });
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Mulliken atomic charges</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>Atom</th><th>Charge (e)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Sum of charges = ${sum.toFixed(4)} e</div>`;
}

/** @param {[string, number][]} loewdin */
function renderLoewdin(loewdin) {
  const body = document.getElementById("result-body");
  let rows = "", sum = 0;
  loewdin.forEach(([el, q], i) => {
    sum += q;
    rows += `<tr><td>${i+1} ${escapeHtml(el)}</td><td>${q.toFixed(6)}</td></tr>`;
  });
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Löwdin atomic charges</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>Atom</th><th>Charge (e)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Sum of charges = ${sum.toFixed(4)} e</div>`;
}

/** @param {[string, string, number][]} bonds @param {[number, string, number][]} valences */
function renderMayer(bonds, valences) {
  const body = document.getElementById("result-body");
  let bRows = "";
  bonds.forEach(([i, j, order]) => {
    const cls = order >= 0.6 ? "" : "color:var(--muted-foreground)";
    bRows += `<tr><td style="${cls}">${escapeHtml(i)} – ${escapeHtml(j)}</td><td style="${cls}">${order.toFixed(4)}</td></tr>`;
  });
  let vRows = "";
  valences.forEach(([idx, el, va]) => {
    vRows += `<tr><td>${idx} ${escapeHtml(el)}</td><td>${va.toFixed(4)}</td></tr>`;
  });
  const bondsTbl = bonds.length ? `
    <div class="card-title" style="font-size:13px;margin-top:8px">Bond orders</div>
    <div style="max-height:240px;overflow:auto">
      <table class="data">
        <thead><tr><th>Bond</th><th>Order</th></tr></thead>
        <tbody>${bRows}</tbody>
      </table>
    </div>` : "";
  const valTbl = valences.length ? `
    <div class="card-title" style="font-size:13px;margin-top:8px">Total valence (VA)</div>
    <div style="max-height:240px;overflow:auto">
      <table class="data">
        <thead><tr><th>Atom</th><th>Valence</th></tr></thead>
        <tbody>${vRows}</tbody>
      </table>
    </div>` : "";
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Mayer population analysis</div>
    ${bondsTbl}${valTbl}`;
}

/** @param {TddftStatePayload[]} states */
function renderTddftStates(states) {
  const body = document.getElementById("result-body");
  let rows = "";
  for (const s of states) {
    const top = (s.contributions || []).slice(0, 4)
      .map(([f, t, w]) => `${escapeHtml(f)}→${escapeHtml(t)} (${(w*100).toFixed(0)}%)`)
      .join(", ");
    rows += `<tr><td>${s.state}</td><td>${s.ev.toFixed(3)}</td><td>${top}</td></tr>`;
  }
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">TD-DFT excited-state composition (${states.length})</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>State</th><th>E (eV)</th><th>Dominant orbital transitions</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/** @param {string} [keywords] @param {string} [block] */
function renderInputEcho(keywords, block) {
  const body = document.getElementById("result-body");
  const pre = (t) => `<pre class="mono" style="white-space:pre-wrap;word-break:break-word;font-size:12px;background:rgba(127,127,127,0.08);padding:10px;border-radius:var(--radius-sm);margin:6px 0 0;overflow:auto">${escapeHtml(t)}</pre>`;
  let html = `<div class="divider"></div><div class="card-title">Input echo</div>`;
  if (keywords) html += `<div class="hint" style="margin-top:8px">Keywords</div>${pre(keywords)}`;
  if (block) html += `<div class="hint" style="margin-top:8px">Input block</div>${pre(block)}`;
  body.innerHTML += html;
}

async function openOutFile() {
  const raw = await bridge.parse_out_file();
  /** @type {ParsePayload} */
  let data; try { data = JSON.parse(raw); } catch { return; }
  if (data.cancelled) return; // user closed the picker — not an error
  if (!data.summary) { appendLog("Could not parse file.", "err"); return; }
  _currentResultName = "";   // an external file, not a queued calc → no conformer->ORCA action
  _currentResult = data;
  renderResult(data);
  switchTab("results");
}
/** @param {TransitionPayload[]} transitions */
function renderSpectrum(transitions) {
  const body = document.getElementById("result-body");
  const maxF = Math.max(...transitions.map(t => t.fosc), 1e-6);
  const minNm = Math.min(...transitions.map(t => t.nm));
  const maxNm = Math.max(...transitions.map(t => t.nm));
  const W = 640, H = 200, pad = 30;
  const x = nm => pad + (nm - minNm) / (maxNm - minNm || 1) * (W - 2*pad);
  let bars = "";
  for (const t of transitions) {
    const h = (t.fosc / maxF) * (H - 2*pad);
    const bright = t.fosc > 0.5 * maxF;
    bars += `<rect class="spectrum-bar ${bright?'bright':''}" x="${x(t.nm).toFixed(1)}" y="${(H-pad-h).toFixed(1)}" width="2.5" height="${h.toFixed(1)}"></rect>`;
  }
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">UV-Vis (oscillator strength vs wavelength)</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="var(--border)"/>
      ${bars}
      <text class="mono" x="${pad}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${minNm.toFixed(0)} nm</text>
      <text class="mono" x="${W-pad-30}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${maxNm.toFixed(0)} nm</text>
    </svg>`;
}

/** @param {number[]} frequencies @param {number} [nImaginary] */
function renderFreqSpectrum(frequencies, nImaginary) {
  const body = document.getElementById("result-body");
  if (!frequencies || !frequencies.length) return;

  // Split out the (near-)zero translational/rotational modes so they don't
  // dominate the axis; they're shown faint near 0.
  const real = frequencies.filter(f => f > 0.01);
  const imag = frequencies.filter(f => f < -0.01);
  const zero = frequencies.filter(f => Math.abs(f) <= 0.01);

  const maxF = Math.max(...frequencies.map(Math.abs), 1);
  const minF = Math.min(0, ...imag);          // negative if any imaginary
  const W = 640, H = 200, padL = 36, padR = 16, padB = 28, padT = 14;
  const span = maxF - minF || 1;
  const x = f => padL + (f - minF) / span * (W - padL - padR);
  const baseY = H - padB;
  const stickH = H - padB - padT;

  let sticks = "";
  // zero line (x=0) marker if we have imaginary modes
  if (imag.length) {
    sticks += `<line x1="${x(0).toFixed(1)}" y1="${padT}" x2="${x(0).toFixed(1)}" y2="${baseY}" stroke="var(--border)" stroke-dasharray="3 3"/>`;
  }
  for (const f of zero)
    sticks += `<line class="freq-stick zero" x1="${x(f).toFixed(1)}" y1="${baseY}" x2="${x(f).toFixed(1)}" y2="${(baseY - stickH*0.25).toFixed(1)}"/>`;
  for (const f of real)
    sticks += `<line class="freq-stick" x1="${x(f).toFixed(1)}" y1="${baseY}" x2="${x(f).toFixed(1)}" y2="${padT}"/>`;
  for (const f of imag)
    sticks += `<line class="freq-stick imag" x1="${x(f).toFixed(1)}" y1="${baseY}" x2="${x(f).toFixed(1)}" y2="${padT}"/>`;

  const warn = nImaginary > 0
    ? `<div class="freq-warn">⚠ ${nImaginary} imaginary mode${nImaginary>1?"s":""} (negative frequency) — this is a saddle point, not a minimum. Re-optimize (try TightOpt / a tighter grid) before trusting thermochemistry.</div>`
    : `<div class="hint" style="margin-top:6px">No imaginary modes — a local minimum.</div>`;

  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">Vibrational frequencies (${real.length} real${imag.length?`, <span style="color:var(--err)">${imag.length} imaginary</span>`:""})</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${padL}" y1="${baseY}" x2="${W-padR}" y2="${baseY}" stroke="var(--border)"/>
      ${sticks}
      <text class="mono" x="${x(minF).toFixed(1)}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${minF.toFixed(0)}</text>
      <text class="mono" x="${(W-padR-50)}" y="${H-8}" fill="var(--muted-foreground)" font-size="10">${maxF.toFixed(0)} cm⁻¹</text>
    </svg>
    ${warn}`;
}

/** @param {NmrPayload[]} nmr */
function renderNmr(nmr) {
  const body = document.getElementById("result-body");
  let rows = "";
  for (const n of nmr) {
    rows += `<tr><td>${n.idx} ${escapeHtml(n.el)}</td><td>${n.iso.toFixed(3)}</td><td>${n.aniso.toFixed(3)}</td></tr>`;
  }
  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">NMR chemical shielding (ppm)</div>
    <div style="max-height:280px;overflow:auto">
      <table class="data">
        <thead><tr><th>Nucleus</th><th>Isotropic</th><th>Anisotropy</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="hint">Absolute shieldings — reference subtraction (e.g. TMS) for chemical shifts.</div>`;
}

/** @param {NebPointPayload[]} path @param {string} [kind] "neb" | "irc" */
function renderNebPath(path, kind) {
  const body = document.getElementById("result-body");
  if (!path || !path.length) return;
  // Both NEB-TS and IRC print a PATH SUMMARY table; the parser records which
  // one matched. An IRC profile is not made of NEB "images", and (for a one-
  // sided IRC) its endpoints aren't reactant/product — label per kind.
  const isIrc = kind === "irc";

  const de = path.map(p => p.de_kcal);
  const lo = Math.min(...de, 0), hi = Math.max(...de, 0);
  const span = (hi - lo) || 1;
  const W = 640, H = 240, padL = 48, padR = 20, padT = 20, padB = 40;
  const n = path.length;
  const x = i => padL + (n === 1 ? 0.5 : i / (n - 1)) * (W - padL - padR);
  const y = v => padT + (hi - v) / span * (H - padT - padB);

  // path line through the points
  const pts = path.map((p, i) => `${x(i).toFixed(1)},${y(p.de_kcal).toFixed(1)}`).join(" ");
  let dots = "";
  path.forEach((p, i) => {
    const cx = x(i).toFixed(1), cy = y(p.de_kcal).toFixed(1);
    if (p.is_ts) {
      dots += `<circle cx="${cx}" cy="${cy}" r="5.5" fill="var(--err)"/>` +
              `<text x="${cx}" y="${(y(p.de_kcal)-10).toFixed(1)}" fill="var(--err)" font-size="11" text-anchor="middle" font-weight="600">TS ${p.de_kcal.toFixed(1)}</text>`;
    } else {
      dots += `<circle cx="${cx}" cy="${cy}" r="3.5" fill="var(--foreground)"/>`;
    }
  });
  // reactant / product labels (first and last) — NEB only: an IRC's endpoints
  // depend on its direction (a forward- or backward-only IRC starts at the TS)
  const first = path[0], last = path[n - 1];
  const reactLbl = isIrc ? "" : `<text x="${x(0).toFixed(1)}" y="${H-padB+16}" fill="var(--muted-foreground)" font-size="10" text-anchor="middle">reactant</text>`;
  const prodLbl  = isIrc ? "" : `<text x="${x(n-1).toFixed(1)}" y="${H-padB+16}" fill="var(--muted-foreground)" font-size="10" text-anchor="middle">product</text>`;
  // zero baseline
  const zeroY = y(0).toFixed(1);

  const ts = path.find(p => p.is_ts);
  const barrier = ts ? ts.de_kcal : Math.max(...de);
  const dErxn = last.de_kcal - first.de_kcal;

  body.innerHTML += `
    <div class="divider"></div>
    <div class="card-title">${isIrc ? `IRC path (${n} steps)` : `NEB-TS reaction path (${n} images)`}</div>
    <svg class="spectrum" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <line x1="${padL}" y1="${zeroY}" x2="${W-padR}" y2="${zeroY}" stroke="var(--border)" stroke-dasharray="3 3"/>
      <polyline points="${pts}" fill="none" stroke="var(--muted-foreground)" stroke-width="1.5"/>
      ${dots}
      <text class="mono" x="14" y="${padT+4}" fill="var(--muted-foreground)" font-size="10">${hi.toFixed(1)}</text>
      <text class="mono" x="14" y="${(H-padB).toFixed(1)}" fill="var(--muted-foreground)" font-size="10">${lo.toFixed(1)}</text>
      <text x="14" y="${H/2}" fill="var(--muted-foreground)" font-size="10" transform="rotate(-90 14 ${H/2})" text-anchor="middle">ΔE (kcal/mol)</text>
      ${reactLbl}${prodLbl}
    </svg>
    <div class="hint">${isIrc
      ? `Relative energies along the intrinsic reaction coordinate (electronic, not free energies).`
      : `Forward barrier ≈ <b>${barrier.toFixed(1)} kcal/mol</b>; reaction energy ΔE ≈ <b>${dErxn.toFixed(1)} kcal/mol</b>. Energies are from the NEB path summary (electronic, not free energies).`}</div>`;
}

// ---- free energy profile (Results tab) ----
/** @type {FepPoint[]} */
let _fepPoints = [];   // cached [{name, gibbs_eh, ...}] in queue order

async function loadFreeEnergyProfile() {
  try {
    const res = /** @type {FepResult} */ (JSON.parse(await bridge.get_free_energy_profile()));
    _fepPoints = (res.ok && res.points) ? res.points : [];
  } catch (e) { _fepPoints = []; }
  // populate the reference dropdown (default: first point = RC)
  const refSel = document.getElementById("fep-ref");
  if (refSel) {
    const cur = refSel.value;
    refSel.innerHTML = _fepPoints.length
      ? _fepPoints.map((p, i) => `<option value="${i}">ref: ${escapeHtml(p.name)}</option>`).join("")
      : `<option value="">—</option>`;
    if (cur && _fepPoints[cur]) refSel.value = cur;
  }
  renderFreeEnergyProfile();
}

function renderFreeEnergyProfile() {
  const body = document.getElementById("fep-body");
  if (!body) return;
  if (!_fepPoints.length) {
    body.innerHTML = `<div class="hint">No finished frequency calculations yet — Freq calculations build the profile.</div>`;
    return;
  }
  const units = (document.getElementById("fep-units") || {}).value || "kcal";
  const factor = units === "kj" ? 2625.499639 : 627.5094740631;  // Hartree -> kJ/mol or kcal/mol
  const unitLabel = units === "kj" ? "kJ/mol" : "kcal/mol";
  const refIdx = parseInt((document.getElementById("fep-ref") || {}).value, 10) || 0;
  const ref = _fepPoints[refIdx] ? _fepPoints[refIdx].gibbs_eh : _fepPoints[0].gibbs_eh;

  // relative energies
  const pts = _fepPoints.map(p => ({ name: p.name, dg: (p.gibbs_eh - ref) * factor }));

  // SVG geometry
  const W = 660, H = 300, padL = 56, padR = 20, padT = 24, padB = 56;
  const n = pts.length;
  const dgs = pts.map(p => p.dg);
  let lo = Math.min(0, ...dgs), hi = Math.max(0, ...dgs);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.12 || 1;
  lo -= pad; hi += pad;
  const x = i => padL + (n === 1 ? 0.5 : i / (n - 1)) * (W - padL - padR);
  const y = v => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  const levelHalf = Math.min(34, (W - padL - padR) / (n * 2.4));  // half-width of each level bar

  let svg = "";
  // zero baseline ("3 3" dashes: same rhythm as every other zero/goal line)
  svg += `<line x1="${padL}" y1="${y(0).toFixed(1)}" x2="${W-padR}" y2="${y(0).toFixed(1)}" stroke="var(--border)" stroke-dasharray="3 3"/>`;
  svg += `<text x="${padL-8}" y="${y(0).toFixed(1)}" text-anchor="end" dominant-baseline="middle" class="scf-axis">0</text>`;
  // connectors (dashed, sloping between level ends)
  for (let i = 0; i < n - 1; i++) {
    svg += `<line x1="${(x(i)+levelHalf).toFixed(1)}" y1="${y(pts[i].dg).toFixed(1)}" x2="${(x(i+1)-levelHalf).toFixed(1)}" y2="${y(pts[i+1].dg).toFixed(1)}" stroke="var(--muted-foreground)" stroke-width="1" stroke-dasharray="3 3" opacity="0.6"/>`;
  }
  // level bars + labels
  for (let i = 0; i < n; i++) {
    const px = x(i), py = y(pts[i].dg);
    svg += `<line x1="${(px-levelHalf).toFixed(1)}" y1="${py.toFixed(1)}" x2="${(px+levelHalf).toFixed(1)}" y2="${py.toFixed(1)}" stroke="var(--foreground)" stroke-width="2.5"/>`;
    // dG value above the bar
    svg += `<text x="${px.toFixed(1)}" y="${(py-8).toFixed(1)}" text-anchor="middle" style="font-size:11px;fill:var(--foreground);font-weight:600">${pts[i].dg.toFixed(1)}</text>`;
    // name below, rotated if many
    const label = pts[i].name.length > 10 ? pts[i].name.slice(0, 9) + "…" : pts[i].name;
    svg += `<text x="${px.toFixed(1)}" y="${(H-padB+16).toFixed(1)}" text-anchor="middle" style="font-size:10px;fill:var(--muted-foreground)">${escapeHtml(label)}</text>`;
  }
  // y axis title
  svg += `<text x="14" y="${(padT+(H-padT-padB)/2).toFixed(1)}" text-anchor="middle" transform="rotate(-90 14 ${(padT+(H-padT-padB)/2).toFixed(1)})" class="scf-axis-title">ΔG (${unitLabel})</text>`;

  body.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${svg}</svg>
    <div class="hint">Relative to <b>${escapeHtml(_fepPoints[refIdx] ? _fepPoints[refIdx].name : "")}</b> (= 0). Each level is a finished frequency calculation; values are ΔG in ${unitLabel}. Order follows the queue.</div>`;
}
