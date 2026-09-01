// @ts-check
// Build-tab structure tools: the inline 3D preview, the pre-launch screening
// panel, the NEB endpoint comparison, and the rigid structure editor.
// Plain global script, loaded after molviewer.js and before app.js.

/* ---------- what this file is for ----------
 * Until now a loaded geometry was a line of text ("loaded (24 atoms)") and the
 * first time anyone saw the molecule was after the run. These four surfaces
 * close that gap, and every one of them answers from ONE place — the Bridge
 * slots over orcamgr/core/structure.py — so the verdict the badge shows is the
 * verdict the generator enforces (P4):
 *
 *   1. an inline 3D preview of the geometry in the form;
 *   2. a screening panel (electron count vs multiplicity, overlaps, units,
 *      fragments) that reports, never corrects (P26);
 *   3. the NEB endpoint pair side by side, with every diverging atom listed;
 *   4. an editor that sets a bond length, angle or dihedral, or moves a whole
 *      fragment — rigidly, and without ever touching the atom ORDER, which is
 *      what makes "copy the reactant and move atoms" a safe way to build a
 *      product.
 *
 * The three stages here own their own WebGL contexts, which is why the NEB card
 * lives in index.html rather than in the re-rendered method form: a container
 * that survives every calc-type switch is a viewer that is created once.
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
 *  container is not laid out yet (a hidden panel would size its canvas to 0).
 *  @param {string} id @returns {any} */
function _stViewer(id) {
  const node = document.getElementById(id);
  if (!node || !node.clientWidth) return null;
  if (!_stViewers[id]) {
    const $3Dmol = _st3Dmol();
    if (!$3Dmol) return null;
    _stViewers[id] = $3Dmol.createViewer(node, { backgroundColor: _stCss("--card") });
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

/** Count the atom lines of a coordinate block. @param {string} block */
function _stAtomCount(block) {
  return (block || "").split("\n").filter(l => l.trim().split(/\s+/).length >= 4).length;
}

/** Element symbol of atom i (for labels and the selection list).
 *  @param {string} block @param {number} i @returns {string} */
function _stSymbol(block, i) {
  const rows = (block || "").split("\n")
    .map(l => l.trim().split(/\s+/)).filter(p => p.length >= 4);
  return rows[i] ? rows[i][0] : "?";
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
  const kindEl = /** @type {HTMLSelectElement} */ (document.getElementById("calc-kind"));
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
  // D41: the copy is locked, with the reason on it, until there is something
  // to copy — rather than offered and then refused.
  const copy = /** @type {HTMLButtonElement} */ (document.getElementById("neb-copy-btn"));
  if (copy) {
    copy.disabled = !react;
    copy.title = react
      ? "Seed the product from the reactant, then edit it — the atom order is preserved by construction"
      : "Load the reactant as a .xyz above first";
  }
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

/** Seed the product from the reactant. This is the whole point of the editor
 *  route: a product built by copying and moving can never have a mismatched
 *  atom order, so the checker above has nothing left to find. */
async function copyReactantToProduct() {
  if (geomSourceFor("") !== "direct" || !directXyz) {
    failNotify("Load the reactant as a .xyz first — the product is a copy of it.");
    return;
  }
  if (_nebProductXyz) {
    const ok = await confirmModal({
      title: "Replace the loaded product?",
      body: "The product geometry currently loaded is replaced by a copy of the reactant.",
      confirm: "Replace", danger: true,
    });
    if (!ok) return;
  }
  _nebProductXyz = directXyz;
  setNebProductStatus();
  appendLog(`Product seeded from the reactant (${_stAtomCount(directXyz)} atoms) — edit it to build the product.`, "ok");
  await refreshNebPanel();
  openStructureEditor("product");
}

// ===========================================================================
// 4. the structure editor
// ===========================================================================

let _seSlot = "";               // "direct" (reactant) | "product"
let _seXyz = "";                // the working coordinate block
let _seOriginal = "";           // what the editor opened with (Revert all)
/** @type {string[]} */ let _seStack = [];      // undo stack of previous blocks
/** @type {number[]} */ let _seSel = [];        // selected atoms, in click order
/** @type {number[]} */ let _seFrag = [];       // fragment of the first selection
let _seKeyHandler = null;
let _seClosing = false;         // a discard confirm is already on screen

/** What a 2-, 3- or 4-atom selection names, and how it reads in the form.
 *  @type {Object<number, {label: string, digits: number, step: string, name: string}>} */
const _SE_KINDS = {
  2: { label: "Distance (Å)", digits: 3, step: "0.01", name: "Bond length" },
  3: { label: "Angle (°)", digits: 2, step: "1", name: "Angle" },
  4: { label: "Dihedral (°)", digits: 2, step: "1", name: "Dihedral" },
};

/** Open the editor on the reactant or the NEB product. @param {string} slot */
function openStructureEditor(slot) {
  const source = slot === "product" ? _nebProductXyz : directXyz;
  if (!source) {
    failNotify(slot === "product"
      ? "Load or copy a product geometry first."
      : "Load a .xyz geometry first.");
    return;
  }
  if (!_st3Dmol()) { failNotify("3D viewer failed to load."); return; }
  _seSlot = slot;
  _seXyz = source;
  _seOriginal = source;
  _seStack = [];
  _seSel = [];
  _seFrag = [];
  const title = document.getElementById("se-title");
  if (title) title.textContent = slot === "product"
    ? "Edit structure — NEB product" : "Edit structure — geometry";
  const overlay = document.getElementById("struct-editor");
  if (overlay) overlay.style.display = "flex";
  if (!_seKeyHandler) {
    _seKeyHandler = (/** @type {KeyboardEvent} */ e) => {
      const o = document.getElementById("struct-editor");
      if (!o || o.style.display === "none") return;
      if (e.key === "Escape") { closeStructureEditor(); e.preventDefault(); }
    };
    document.addEventListener("keydown", _seKeyHandler);
  }
  _seDraw(true);
  _seRenderSelection();
}

/** Close, confirming first when there are edits that would be lost (D61). The
 *  confirm sits above the editor panel (§11.10's z-index), and _seClosing keeps
 *  a second Escape — which the modal's own handler also sees — from raising a
 *  second dialog over the first. */
async function closeStructureEditor() {
  if (_seClosing) return;
  if (_seStack.length) {
    _seClosing = true;
    let ok;
    try {
      ok = await confirmModal({
        title: "Discard the structure edits?",
        body: `${_seStack.length} edit${_seStack.length === 1 ? "" : "s"} to this structure would be lost. ` +
              `<b>Use this structure</b> keeps them instead.`,
        confirm: "Discard", cancel: "Keep editing", danger: true,
      });
    } finally { _seClosing = false; }
    if (!ok) return;
  }
  _seClose();
}

/** Hide and release the scene without asking — the path an adopted edit takes,
 *  where there is nothing to lose. */
function _seClose() {
  _seStack = [];
  const overlay = document.getElementById("struct-editor");
  if (overlay) overlay.style.display = "none";
  const v = _stViewers["se-gl"];
  if (v) { v.clear(); v.render(); }       // release the scene, keep the context
}

/** Redraw the editor stage from _seXyz, with the selection and the fragment
 *  marked. Rebuilt from scratch every time: an edit can move any atom, and a
 *  full redraw of a hand-built structure is far below a frame.
 *  @param {boolean} [zoom] reset the camera — true only when the editor opens,
 *  so watching an edit happen never also spins the molecule (D60) */
function _seDraw(zoom) {
  const v = _stViewer("se-gl");
  if (!v) return;
  v.clear();
  v.removeAllLabels();
  _stStamp(v.addModel(_stXyzFile(_seXyz), "xyz"), "e");
  v.setStyle({}, _ST_STYLE);
  if (_seFrag.length && _seFrag.length < _stAtomCount(_seXyz)) {
    v.setStyle({ odIdx: _seFrag },
      { stick: { radius: 0.14 }, sphere: { scale: 0.30, color: _stCss("--warn") } });
  }
  if (_seSel.length) {
    v.setStyle({ odIdx: _seSel },
      { stick: { radius: 0.16 }, sphere: { scale: 0.38, color: _stCss("--ok") } });
    _seSel.forEach((idx, n) => {
      const a = v.selectedAtoms({ odIdx: idx })[0];
      if (!a) return;
      v.addLabel(`${n + 1}`, {
        position: { x: a.x, y: a.y, z: a.z }, inFront: true,
        backgroundColor: _stCss("--card"), backgroundOpacity: 0.9,
        fontColor: _stCss("--ok"), fontSize: 11, borderThickness: 0,
      });
    });
  }
  v.setClickable({}, true, (/** @type {any} */ atom) => {
    if (atom.odIdx != null) _seToggleSelect(atom.odIdx);
  });
  if (zoom) v.zoomTo();
  v.render();
  const cap = document.getElementById("se-caption");
  if (cap) {
    const edits = _seStack.length;
    cap.textContent = `${_stAtomCount(_seXyz)} atoms · atom order unchanged · ` +
      (edits ? `${edits} edit${edits === 1 ? "" : "s"}` : "no edits yet");
  }
  const undo = /** @type {HTMLButtonElement} */ (document.getElementById("se-undo-btn"));
  if (undo) undo.disabled = !_seStack.length;
  const revert = /** @type {HTMLButtonElement} */ (document.getElementById("se-revert-btn"));
  if (revert) revert.disabled = !_seStack.length;
}

/** Click an atom: add it to the selection, or drop it if it was already in.
 *  A fifth click starts over rather than silently ignoring the click.
 *  @param {number} i */
function _seToggleSelect(i) {
  const at = _seSel.indexOf(i);
  if (at >= 0) _seSel.splice(at, 1);
  else if (_seSel.length >= 4) _seSel = [i];
  else _seSel.push(i);
  _seRenderSelection();
  _seDraw();
}

/** The side column: the selection list, the measurement it names, and which
 *  fragment a move would carry. */
async function _seRenderSelection() {
  const host = document.getElementById("se-sel");
  if (host) {
    host.innerHTML = _seSel.length
      ? _seSel.map((idx, n) =>
        `<div class="mv-row"><span class="mv-row-num">${n + 1}</span>` +
        `<span class="mv-row-label">${escapeHtml(_stSymbol(_seXyz, idx))} #${idx + 1}</span>` +
        `<button class="rm" onclick="_seToggleSelect(${idx})" title="Remove from the selection">×</button></div>`).join("")
      : `<div class="hint" style="margin-top:0">Click atoms in the view — 2 for a distance, 3 for an angle, 4 for a dihedral.</div>`;
  }
  await _seUpdateMeasure();
  await _seUpdateFragment();
}

/** Show the internal coordinate the current selection names, with its current
 *  value in the input ready to be edited. */
async function _seUpdateMeasure() {
  const field = document.getElementById("se-measure-field");
  const label = document.getElementById("se-measure-label");
  const input = /** @type {HTMLInputElement} */ (document.getElementById("se-measure-value"));
  if (!field || !label || !input) return;
  const spec = _SE_KINDS[_seSel.length];
  if (!spec) { field.style.display = "none"; return; }
  let res;
  try {
    res = /** @type {MeasureResult} */ (JSON.parse(
      await bridge.measure_structure(_seXyz, JSON.stringify(_seSel))));
  } catch (e) { field.style.display = "none"; return; }
  if (!res.ok) { field.style.display = "none"; return; }
  field.style.display = "";
  label.textContent = spec.label;
  input.step = spec.step;
  input.value = res.value.toFixed(spec.digits);
}

/** Which atoms a fragment move would carry — resolved in Python (it needs bond
 *  perception) and highlighted here before the user commits to anything. */
async function _seUpdateFragment() {
  const hint = document.getElementById("se-frag-hint");
  if (!_seSel.length) {
    _seFrag = [];
    if (hint) hint.textContent = "Select an atom to choose the fragment a move acts on.";
    return;
  }
  const anchor = _seSel[0];
  try {
    const res = /** @type {FragmentResult} */ (JSON.parse(
      await bridge.structure_fragment(_seXyz, anchor)));
    _seFrag = res.ok ? (res.indices || []) : [];
  } catch (e) { _seFrag = []; }
  if (hint) {
    hint.textContent = _seFrag.length
      ? `Moves the fragment of atom #${anchor + 1} — ${_seFrag.length} of ${_stAtomCount(_seXyz)} atoms.`
      : "";
  }
}

/** Run one edit through the Bridge and adopt the result, or surface the reason
 *  it was refused. Every refusal is a real one (a ring bond, a selection that
 *  is not a chain): the structure is never quietly deformed to make the number
 *  come out (P2).
 *  @param {Object} payload @param {string} what for the log line */
async function _seApplyEdit(payload, what) {
  let res;
  try {
    res = /** @type {StructureEdit} */ (JSON.parse(
      await bridge.edit_structure(JSON.stringify(Object.assign({ xyz: _seXyz }, payload)))));
  } catch (e) { failNotify("The structure edit could not be applied."); return; }
  if (!res.ok) { failNotify(res.error || "That edit cannot be made rigidly."); return; }
  _seStack.push(_seXyz);
  _seXyz = res.xyz;
  appendLog(`${what} (${_seSlot === "product" ? "NEB product" : "geometry"}).`, "ok");
  _seDraw();
  await _seRenderSelection();
}

/** Set the selected internal coordinate to the typed value. */
async function seSetInternal() {
  const input = /** @type {HTMLInputElement} */ (document.getElementById("se-measure-value"));
  const spec = _SE_KINDS[_seSel.length];
  if (!input || !spec) return;
  const value = parseFloat(input.value);
  if (!isFinite(value)) { failNotify("Enter a number to set."); return; }
  await _seApplyEdit({ op: "set", indices: _seSel.slice(), value },
    `${spec.name} set to ${input.value}`);
}

/** Translate the selected atom's fragment. */
async function seTranslate() {
  if (!_seSel.length) { failNotify("Select an atom in the fragment to move."); return; }
  const num = (id) => {
    const e = /** @type {HTMLInputElement} */ (document.getElementById(id));
    const x = e ? parseFloat(e.value) : 0;
    return isFinite(x) ? x : 0;
  };
  const v = [num("se-dx"), num("se-dy"), num("se-dz")];
  if (!v[0] && !v[1] && !v[2]) { failNotify("Enter a distance to move by."); return; }
  await _seApplyEdit({ op: "translate", anchor: _seSel[0], vector: v },
    `Fragment moved by (${v.join(", ")}) Å`);
}

/** Rotate the selected atom's fragment about its own centroid. */
async function seRotate() {
  if (!_seSel.length) { failNotify("Select an atom in the fragment to rotate."); return; }
  const axisEl = /** @type {HTMLSelectElement} */ (document.getElementById("se-axis"));
  const degEl = /** @type {HTMLInputElement} */ (document.getElementById("se-deg"));
  const deg = degEl ? parseFloat(degEl.value) : NaN;
  if (!isFinite(deg) || !deg) { failNotify("Enter an angle to rotate by."); return; }
  const axis = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] }[axisEl ? axisEl.value : "z"];
  await _seApplyEdit({ op: "rotate", anchor: _seSel[0], axis, degrees: deg },
    `Fragment rotated ${deg}° about ${axisEl ? axisEl.value.toUpperCase() : "Z"}`);
}

/** Step back one edit. */
async function seUndo() {
  if (!_seStack.length) return;
  _seXyz = _seStack.pop();
  _seDraw();
  await _seRenderSelection();
}

/** Back to the structure the editor opened with. */
async function seRevert() {
  if (!_seStack.length) return;
  _seXyz = _seOriginal;
  _seStack = [];
  _seDraw();
  await _seRenderSelection();
}

/** Adopt the edited structure into the form and close. */
async function seApply() {
  const n = _stAtomCount(_seXyz);
  if (!n) { failNotify("Nothing to use — the structure is empty."); return; }
  if (_seSlot === "product") {
    _nebProductXyz = _seXyz;
    setNebProductStatus();
  } else {
    directXyz = _seXyz;
    const st = document.getElementById("xyz-status");
    if (st) st.textContent = `edited (${n} atoms)`;
  }
  appendLog(`Edited structure adopted (${n} atoms, ${_seStack.length} edit${_seStack.length === 1 ? "" : "s"}, atom order unchanged).`, "ok");
  _seClose();     // adopted, so there is nothing to confirm discarding
  await refreshGeometryPanel();
  await refreshNebPanel();
}
