// @ts-check
// Build-tab structure view: the inline 3D preview, the pre-launch screening
// panel, and the NEB endpoint comparison.
// Plain global script, loaded after molviewer.js and before app.js.

/* ---------- what this file is for ----------
 * A loaded geometry used to be a line of text ("loaded (24 atoms)"), and the
 * first time anyone saw the molecule was after the run. These three surfaces
 * close that gap, and each answers from ONE place — the Bridge slots over
 * orcamgr/core/structure.py — so the verdict the badge shows is the verdict the
 * input generator enforces (P4):
 *
 *   1. an inline 3D preview of the geometry in the form;
 *   2. a screening panel (electron count vs multiplicity, overlaps, units,
 *      fragments) that reports, never corrects (P26);
 *   3. the NEB endpoint pair side by side, with every diverging atom listed.
 *
 * All three are READ-ONLY. Building and editing structures is what a molecular
 * editor is for — Avogadro2 and its peers do it far better than a side feature
 * here could, and a second-rate editor next to a good one is worse than none.
 * ORCAdesk's job is to tell you what the file you are about to run actually
 * contains, and to catch what would cost you the run.
 *
 * Both stages here own their own WebGL context, which is why the NEB card lives
 * in index.html rather than in the re-rendered method form: a container that
 * survives every calc-type switch is a viewer that is created once.
 */

// 3Dmol style shared with the Results-tab viewer, so a structure looks the same
// wherever it is drawn.
const _ST_STYLE = { stick: { radius: 0.14 }, sphere: { scale: 0.26 } };
// A ghosted second structure (the product, in overlay mode): one colour, thin,
// so the element-coloured reactant reads through it.
const _ST_GHOST = { stick: { radius: 0.08 }, sphere: { scale: 0.12 } };

/** @type {Object<string, any>} viewer per container id — created once each */
const _stViewers = {};

function _st3Dmol() { return window["$3Dmol"] || window["3Dmol"]; }

/** A CSS custom property as a colour literal 3Dmol can use for WebGL material.
 *  @param {string} name @returns {string} */
function _stCss(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** The viewer for a container, created on first use. Returns null while the
 *  container is not laid out yet (a hidden panel would size its canvas to 0 —
 *  height as well as width: the Build tab's preview card keeps its width while
 *  it is collapsed).
 *  @param {string} id @returns {any} */
function _stViewer(id) {
  const node = document.getElementById(id);
  if (!node || !node.clientWidth || !node.clientHeight) return null;
  if (!_stViewers[id]) {
    const $3Dmol = _st3Dmol();
    if (!$3Dmol) return null;
    // glGuardViewer (molviewer.js) — 3Dmol's own window-resize listener redraws
    // every viewer it has made, so these two would keep drawing into a 0x0
    // canvas for as long as the app is open on any other tab.
    _stViewers[id] = glGuardViewer(
      $3Dmol.createViewer(node, { backgroundColor: _stCss("--card") }), node);
  }
  const v = _stViewers[id];
  // the card colour follows the theme toggle, and the viewer outlives it
  v.setBackgroundColor(_stCss("--card") || "#18181b");
  v.resize();
  return v;
}

/** Wrap a coordinate block as .xyz text for 3Dmol's parser.
 *  @param {string} block @returns {string} */
function _stXyzFile(block) {
  const lines = (block || "").split("\n").filter(l => l.trim().split(/\s+/).length >= 4);
  return `${lines.length}\nORCAdesk\n${lines.join("\n")}\n`;
}

/** Stamp each atom of a freshly added model with its 0-based index and which
 *  structure it came from. Both are plain properties, so 3Dmol's selection spec
 *  matches them directly ({odIdx: [...]} spans BOTH models — which is exactly
 *  what links the hover highlight across an endpoint pair).
 *  @param {any} model @param {string} side */
function _stStamp(model, side) {
  model.selectedAtoms({}).forEach((/** @type {any} */ a, /** @type {number} */ i) => {
    a.odIdx = i;
    a.odSide = side;
  });
}

/** Shift a coordinate block along x — how the endpoint pair is placed side by
 *  side without touching either structure's own coordinates.
 *  @param {string} block @param {number} dx @returns {string} */
function _stShiftX(block, dx) {
  return (block || "").split("\n").map(ln => {
    const p = ln.trim().split(/\s+/);
    if (p.length < 4) return ln;
    return `${p[0]} ${(parseFloat(p[1]) + dx).toFixed(6)} ${p[2]} ${p[3]}`;
  }).join("\n");
}

/** [min x, max x] of a coordinate block (0 / 0 when it has no atoms).
 *  @param {string} block @returns {number[]} */
function _stXSpan(block) {
  const xs = (block || "").split("\n")
    .map(l => l.trim().split(/\s+/))
    .filter(p => p.length >= 4)
    .map(p => parseFloat(p[1]))
    .filter(x => !isNaN(x));
  return xs.length ? [Math.min(...xs), Math.max(...xs)] : [0, 0];
}

// ===========================================================================
// 1 + 2. the geometry card: inline preview and pre-launch screening
// ===========================================================================

// Screening is a round trip, and charge/multiplicity change under fast typing;
// a stale answer must never overwrite a newer one (the _mvVolSeq pattern).
let _geomCheckSeq = 0;
// D60 (never yank the user): these panels re-render on many events — a charge
// keystroke, a tab switch, an edit — and a zoomTo on each would throw away the
// orientation the user just set with the mouse. The camera is only reset when
// the scene is genuinely a different one, tracked by these signatures.
let _geomSig = "";
let _nebSig = "";

/** Redraw the Build-tab geometry preview and re-run its screening. Safe to call
 *  from anywhere the geometry, the charge or the multiplicity may have changed;
 *  it hides both surfaces when there is no direct geometry to talk about. */
async function refreshGeometryPanel() {
  const panel = document.getElementById("struct-direct");
  const findings = document.getElementById("struct-findings-direct");
  if (!panel || !findings) return;
  const direct = geomSourceFor("") === "direct";
  const block = direct ? directXyz : "";
  if (!block) {
    panel.style.display = "none";
    findings.innerHTML = "";
    _geomSig = "";
    return;
  }
  panel.style.display = "";
  const v = _stViewer("struct-gl-direct");
  if (v) {
    v.clear();
    _stStamp(v.addModel(_stXyzFile(block), "xyz"), "r");
    v.setStyle({}, _ST_STYLE);
    if (_geomSig !== block) { v.zoomTo(); _geomSig = block; }
    v.render();
  }
  await _stRunCheck(block, "struct-cap-direct", "struct-findings-direct", v);
}

/** Screen a block and render its census + findings.
 *  @param {string} block @param {string} capId @param {string} findId
 *  @param {any} viewer viewer to highlight the offending atoms in (may be null) */
async function _stRunCheck(block, capId, findId, viewer) {
  const seq = ++_geomCheckSeq;
  const [charge, multiplicity] = readChargeMult();
  let res;
  try {
    res = /** @type {StructureCheck} */ (JSON.parse(
      await bridge.check_structure(block, charge, multiplicity)));
  } catch (e) {
    return;                       // screening is advisory; never block the form
  }
  if (seq !== _geomCheckSeq) return;            // a newer answer already landed
  const cap = document.getElementById(capId);
  if (cap) {
    const frags = res.n_fragments === 1 ? "1 fragment" : `${res.n_fragments} fragments`;
    const electrons = res.electrons == null ? "—" : `${res.electrons} electrons`;
    cap.textContent = `${res.formula} · ${res.n_atoms} atoms · ${electrons} · ${frags}`;
  }
  const host = document.getElementById(findId);
  if (host) {
    host.innerHTML = (res.issues || []).map(i =>
      `<div class="${i.level === "error" ? "qerror" : "qwarn"}">⚠ ${escapeHtml(i.message)}</div>`
    ).join("");
  }
  // point at the atoms a finding is about, so "atoms #7 and #8" is findable
  const flagged = [];
  (res.issues || []).forEach(i => (i.atoms || []).forEach(a => flagged.push(a)));
  if (viewer && flagged.length) {
    viewer.setStyle({ odIdx: flagged },
      { stick: { radius: 0.14 }, sphere: { scale: 0.34, color: _stCss("--err") } });
    viewer.render();
  }
}

// ===========================================================================
// 3. the NEB endpoint pair
// ===========================================================================

let _nebOverlay = false;        // false = side by side, true = both in place
let _nebCheckSeq = 0;

/** Show the NEB card only for a NEB-TS calculation. Called on every kind change
 *  and on every build-mode switch; the card lives outside the re-rendered method
 *  form, so its 3D stage survives both. */
function applyNebCard() {
  const card = document.getElementById("card-neb");
  if (!card) return;
  const kindEl = document.getElementById("calc-kind");
  const isNeb = !!kindEl && kindEl.value === "neb_ts";
  const dft = (buildMode === "beginner" || buildMode === "expert");
  card.style.display = (isNeb && dft) ? "" : "none";
  if (isNeb && dft) {
    setNebProductStatus();   // the card's first appearance must not start stale
    refreshNebPanel();
  }
}

/** Compare the loaded reactant and product, and render the verdict badge, the
 *  3D comparison and the mismatch table. Replaces the old one-line checker:
 *  same judgment, but taken in Python so the badge and the generator's gate can
 *  never disagree (P4). */
async function refreshNebPanel() {
  const verdict = document.getElementById("cfg-neb-verdict");
  const table = document.getElementById("cfg-neb-mismatch");
  const panel = document.getElementById("struct-neb");
  if (!verdict || !table || !panel) return;

  // The reactant is the DIRECT block. With the geometry taken from another
  // calculation there is nothing to compare yet — the coordinates arrive at run
  // time — so say that instead of showing a verdict about a stale block (P2).
  // The engine re-checks the pair for real once the geometry is injected.
  const react = geomSourceFor("") === "direct" ? directXyz : "";
  const prod = _nebProductXyz;
  panel.style.display = "none";
  table.innerHTML = "";
  if (geomSourceFor("") !== "direct") {
    verdict.innerHTML = `<div class="hint" style="margin:0">The reactant comes from another calculation, so the atom order can only be checked when that geometry arrives — the run does it then.</div>`;
    return;
  }
  if (!react || !prod) {
    verdict.innerHTML = "";
    return;
  }
  const seq = ++_nebCheckSeq;
  let res;
  try {
    res = /** @type {AtomOrder} */ (JSON.parse(
      await bridge.compare_structures(react, prod)));
  } catch (e) {
    return;
  }
  if (seq !== _nebCheckSeq) return;

  const badge = res.ok
    ? `<span class="qstate match">matched</span>`
    : `<span class="qstate mismatch">atom mismatch</span>`;
  const summary = res.ok
    ? `${res.n_reactant} atoms, same elements in the same order.`
    : escapeHtml(res.error);
  verdict.innerHTML =
    `<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">${badge}` +
    `<span class="hint" style="margin:0">${res.ok ? escapeHtml(summary) : ""}</span></div>` +
    (res.ok ? "" : `<div class="qerror" style="margin-top:4px">⚠ ${summary}</div>`);

  // Every diverging index, not just the first: with the counts equal the two
  // line up, so the shape of the error (one swap vs a whole shifted block) is
  // readable at a glance.
  const rows = res.mismatches || [];
  if (rows.length) {
    const shown = rows.map(m =>
      `<tr><td>#${m.index + 1}</td><td>${escapeHtml(m.reactant)}</td>` +
      `<td>${escapeHtml(m.product)}</td></tr>`).join("");
    const more = res.n_mismatches > rows.length
      ? `<div class="hint">${res.n_mismatches - rows.length} further mismatches not listed.</div>` : "";
    table.innerHTML =
      `<div class="hint" style="margin-bottom:4px">${res.n_mismatches} atom${res.n_mismatches === 1 ? "" : "s"} differ:</div>` +
      `<div style="max-height:280px;overflow:auto"><table class="data">` +
      `<thead><tr><th>Atom</th><th>Reactant</th><th>Product</th></tr></thead>` +
      `<tbody>${shown}</tbody></table></div>${more}`;
  }

  panel.style.display = "";
  _stDrawNebCompare(react, prod, rows.map(m => m.index));
}

/** Draw the two endpoints in one viewer — one scene, so they rotate together
 *  and a hover highlights the same atom index in both.
 *  @param {string} react @param {string} product
 *  @param {number[]} mismatched 0-based indices that differ */
function _stDrawNebCompare(react, product, mismatched) {
  const v = _stViewer("struct-gl-neb");
  if (!v) return;
  v.clear();
  v.removeAllLabels();
  // Side by side puts the product clear of the reactant along +x; overlay
  // leaves both in their own coordinates, which is the useful view exactly when
  // the product was built by editing the reactant (nothing is superimposed for
  // you — an alignment would be a claim about atom mapping we do not make).
  let prod = product;
  if (!_nebOverlay) {
    const [, rMax] = _stXSpan(react);
    const [pMin] = _stXSpan(prod);
    prod = _stShiftX(prod, rMax - pMin + 2.5);
  }
  _stStamp(v.addModel(_stXyzFile(react), "xyz"), "r");
  _stStamp(v.addModel(_stXyzFile(prod), "xyz"), "p");
  v.setStyle({}, _ST_STYLE);
  if (_nebOverlay) {
    v.setStyle({ odSide: "p" },
      Object.assign({}, _ST_GHOST, { stick: { radius: 0.08, color: _stCss("--muted-foreground") } }));
  }
  if (mismatched.length) {
    v.setStyle({ odIdx: mismatched },
      { stick: { radius: 0.14 }, sphere: { scale: 0.34, color: _stCss("--err") } });
  }
  // hovering an atom names it in BOTH structures — the check the eye cannot do
  v.setHoverable({}, true,
    (/** @type {any} */ atom) => {
      if (atom.odIdx == null) return;
      v.selectedAtoms({ odIdx: atom.odIdx }).forEach((/** @type {any} */ a) => {
        v.addLabel(`${a.elem || a.atom} #${atom.odIdx + 1}`, {
          position: { x: a.x, y: a.y, z: a.z },
          backgroundColor: _stCss("--card"), backgroundOpacity: 0.9,
          fontColor: _stCss("--foreground"), fontSize: 11, borderThickness: 0,
        });
      });
      v.render();
    },
    () => { v.removeAllLabels(); _stNebCaptionLabels(v); v.render(); });
  _stNebCaptionLabels(v);
  const sig = `${_nebOverlay}|${react}|${product}`;
  if (_nebSig !== sig) { v.zoomTo(); _nebSig = sig; }
  v.render();
  const cap = document.getElementById("struct-cap-neb");
  if (cap) {
    cap.textContent = _nebOverlay
      ? "overlay · product ghosted, both in their own coordinates · hover an atom to name it in both"
      : "side by side · reactant left, product right · hover an atom to name it in both";
  }
  const btn = document.getElementById("neb-mode-btn");
  if (btn) btn.textContent = _nebOverlay ? "Side by side" : "Overlay";
}

/** Name the two structures in the scene (side-by-side mode only — in overlay
 *  they share a position and a label pair there would say nothing).
 *  @param {any} v */
function _stNebCaptionLabels(v) {
  if (_nebOverlay) return;
  [["r", "Reactant"], ["p", "Product"]].forEach(([side, text]) => {
    const atoms = v.selectedAtoms({ odSide: side });
    if (!atoms.length) return;
    const cx = atoms.reduce((/** @type {number} */ s, /** @type {any} */ a) => s + a.x, 0) / atoms.length;
    const cz = atoms.reduce((/** @type {number} */ s, /** @type {any} */ a) => s + a.z, 0) / atoms.length;
    const yMax = Math.max(...atoms.map((/** @type {any} */ a) => a.y));
    v.addLabel(text, {
      position: { x: cx, y: yMax + 1.4, z: cz },
      backgroundColor: _stCss("--card"), backgroundOpacity: 0.85,
      fontColor: _stCss("--muted-foreground"), fontSize: 11, borderThickness: 0,
    });
  });
}

/** Side-by-side <-> overlay. */
function toggleNebCompareMode() {
  _nebOverlay = !_nebOverlay;
  refreshNebPanel();
}
