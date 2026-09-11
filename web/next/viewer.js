// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — Structure and Visual.

   Two sections of the report that are pictures rather than tables.

   Structure is the geometry the run ended on, and it is where the per-atom and
   per-bond numbers live: click an atom for its charges and valence, click a
   bond for its length and Mayer order, right-drag between two atoms to measure.
   That is why the report has no Mulliken/Löwdin/Mayer tables of its own — a
   charge belongs to an atom you can point at, not to row 27 of a list. (They
   are still reachable as tables under "Show all", for reading without a mouse.)

   Visual lists everything in the result's FOLDER that can be drawn — the
   trajectory, a CREST ensemble, exported conformers — and steps through it.
   What is on disk is discovered, never asked for.
   ============================================================ */

(function () {

  /** the interactive stage in the Structure section @type {any} */
  let _stage = null;
  /** the interactive stage in the Visual panel @type {any} */
  let _viz = null;
  /** @type {any[]} frames of the selected set */
  let _frames = [];
  let _frameIdx = 0;
  let _setKey = "";

  /** Elements present, for the legend — a key to colours that are actually on
   *  screen, rather than a periodic table. @param {any[]} atoms */
  function legend(atoms) {
    const seen = [];
    atoms.forEach(a => { if (seen.indexOf(a.el) < 0) seen.push(a.el); });
    return `<div class="viewlegend">${seen.slice(0, 8).map(el =>
      `<span><i style="background:${MOL.color(el)}${
        el === "H" ? ";outline:1px solid var(--border)" : ""}"></i>${NB.esc(el)}</span>`
    ).join("")}</div>`;
  }

  // ---------------------------------------------------------------- structure

  /** @param {any} d the ParsePayload */
  function structureSection(d) {
    const atoms = (d.geometry || []).map(a => ({ el: a.el, x: a.x, y: a.y, z: a.z }));
    if (!atoms.length) return "";
    return `<section class="secblock viewer" data-sec="sec-structure" id="sec-structure">
      <div class="sech"><h2>Structure</h2><span class="sp"></span>
        <span class="meta">${atoms.length} atoms</span></div>
      <p class="secdesc">Drag to turn it. Click an atom or a bond for its numbers,
        right-drag between two atoms to measure, and the axis button walks x, y, z.</p>
      <div class="viewstage" data-v="stage">
        <canvas class="molcv" data-v="canvas"
          aria-label="Final geometry, ball and stick. Drag to turn, right-drag between two
            atoms to measure, click an atom or a bond for its numbers; the arrow keys turn
            it too."></canvas>
        ${legend(atoms)}
        <div class="stageacts">
          <button class="act" type="button" data-v="open">Open in viewer</button>
          <button class="act" type="button" data-v="axis">View down x</button>
        </div>
      </div>
    </section>`;
  }

  /** Everything known about one atom, from the parse. @param {any} d @param {number} i */
  function atomFacts(d, i) {
    const rows = [];
    const a = d.geometry[i];
    if (d.mulliken && d.mulliken[i]) rows.push(["Mulliken charge", d.mulliken[i][1].toFixed(4) + " e"]);
    if (d.loewdin && d.loewdin[i]) rows.push(["Löwdin charge", d.loewdin[i][1].toFixed(4) + " e"]);
    (d.mayer_valences || []).forEach(v => {
      // the parse indexes Mayer rows from 0, like the geometry
      if (v[0] === i) rows.push(["Mayer valence", v[2].toFixed(4)]);
    });
    rows.push(["x, y, z", `${a.x.toFixed(3)}, ${a.y.toFixed(3)}, ${a.z.toFixed(3)}`]);
    return rows;
  }

  /** The Mayer bond order for i–j, if the parse has one. Its labels are of the
   *  form "0-N", so the index is what precedes the dash.
   *  @param {any} d @param {number} i @param {number} j */
  function mayerOrder(d, i, j) {
    const idx = (s) => parseInt(String(s).split("-")[0], 10);
    const hit = (d.mayer_bonds || []).filter(b => {
      const a = idx(b[0]), c = idx(b[1]);
      return (a === i && c === j) || (a === j && c === i);
    })[0];
    return hit ? hit[2] : null;
  }

  /** @param {HTMLElement} stage @param {any} pick @param {any} d */
  function showPick(stage, pick, d) {
    const old = stage.querySelector(".pickinfo");
    if (old) old.remove();
    if (!pick) return;
    let title = "", rows = [];
    if (pick.kind === "atom") {
      const a = d.geometry[pick.i];
      title = `${a.el}${pick.i + 1}`;
      rows = atomFacts(d, pick.i);
    } else if (pick.kind === "bond") {
      const A = d.geometry[pick.i], B = d.geometry[pick.j];
      title = `${A.el}${pick.i + 1} – ${B.el}${pick.j + 1}`;
      rows.push(["Length", MOL.distance(A, B).toFixed(3) + " Å"]);
      const o = mayerOrder(d, pick.i, pick.j);
      if (o != null) rows.push(["Mayer bond order", o.toFixed(4)]);
    } else if (pick.kind === "distance") {
      const A = d.geometry[pick.i], B = d.geometry[pick.j];
      title = `${A.el}${pick.i + 1} ⋯ ${B.el}${pick.j + 1}`;
      rows.push(["Distance", pick.value.toFixed(3) + " Å"]);
      const o = mayerOrder(d, pick.i, pick.j);
      rows.push(["Bonded", o != null ? "yes" : "not by covalent radii"]);
    }
    const box = NB.el("div", "pickinfo");
    box.innerHTML = `<button class="pclose" type="button" aria-label="Close">×</button>
      <div class="pt">${NB.esc(title)}</div>${
      rows.map(r => `<div class="pr"><span>${NB.esc(r[0])}</span><b>${NB.esc(r[1])}</b></div>`).join("")}`;
    box.querySelector(".pclose").addEventListener("click", () => {
      box.remove();
      if (_stage) _stage.clearPick();
    });
    stage.appendChild(box);
  }

  /** @param {any} d
   *  @param {string} source how the backend addresses this result:
   *    "calc:<name>" for a queued calculation, "file:<path>" for one on disk.
   *    The two are parsed by different routes, so the prefix is not decoration. */
  function mountStructure(d, source) {
    const sec = NB.$("sec-structure");
    if (!sec) return;
    const stage = /** @type {HTMLElement} */ (sec.querySelector('[data-v="stage"]'));
    const canvas = /** @type {any} */ (sec.querySelector('[data-v="canvas"]'));
    const atoms = (d.geometry || []).map(a => ({ el: a.el, x: a.x, y: a.y, z: a.z }));
    if (_stage) _stage.destroy();
    _stage = MOL.mount(canvas, { onPick: (p) => showPick(stage, p, d) });
    _stage.setAtoms(atoms);

    const axisBtn = /** @type {HTMLElement} */ (sec.querySelector('[data-v="axis"]'));
    axisBtn.addEventListener("click", () => {
      const ax = _stage.nextAxis();
      axisBtn.textContent = "View down " + nextAxisLabel(ax);
    });
    sec.querySelector('[data-v="open"]').addEventListener("click", () => NB.mv.open({
      source: source, title: title(source) + " \u2014 structure",
      orbitals: d.orbitals || [] }));
  }
  /** The button names the axis the NEXT press will show, so it says what it does.
   *  @param {string} shown */
  function nextAxisLabel(shown) {
    return shown === "x" ? "y" : shown === "y" ? "z" : "x";
  }

  /** What to call this result in a window title. The source is how the backend
   *  addresses it, which is not a name anyone reads. @param {string} source */
  function title(source) {
    if (source.indexOf("calc:") === 0) return source.slice(5);
    const p = source.indexOf("file:") === 0 ? source.slice(5) : source;
    return p.split(/[\\/]/).pop() || "Structure";
  }

  // ---------------------------------------------------------------- visual

  function visualSection() {
    return `<section class="secblock vizhalf" data-sec="sec-visual" id="sec-visual">
      <div class="sech"><h2>Visual</h2><span class="sp"></span>
        <span class="meta" data-v="vmeta"></span></div>
      <p class="secdesc">Everything in this result's folder that can be drawn. Pick what to
        look at along the bottom; step through it on the left. A surface that has not
        been plotted yet asks first — orca_plot has to run, and an ESP map takes
        minutes.</p>
      <div class="vizpanel">
        <div class="vizlist" data-v="vlist" role="listbox" aria-label="Frames"></div>
        <div class="vizstage" data-v="vstage">
          <canvas class="molcv" data-v="vcanvas"
            aria-label="Structure viewer. Drag to turn; the arrow keys turn it too."></canvas>
          <div class="stageacts">
            <button class="act" type="button" data-v="vopen">Open in viewer</button>
            <button class="act" type="button" data-v="vaxis">View down x</button>
          </div>
          <span class="vizcap" data-v="vcap"></span>
        </div>
        <div class="viztypes" data-v="vtypes" role="group" aria-label="What to draw"></div>
      </div>
    </section>`;
  }

  /** @param {string} source @param {any} [d] the parse payload, for its orbital
   *  list: the wavefunction's own picks need it and get_plot_options
   *  deliberately does not send a second copy (P4). */
  async function mountVisual(source, d) {
    const sec = NB.$("sec-visual");
    if (!sec) return;
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    if (_viz) _viz.destroy();
    _viz = MOL.mount(q("vcanvas"), {});
    const axisBtn = q("vaxis");
    axisBtn.addEventListener("click", () => {
      axisBtn.textContent = "View down " + nextAxisLabel(_viz.nextAxis());
    });

    // The two halves of "what can be drawn" are found in different ways — the
    // .xyz sets by listing the folder, the surfaces by asking the wavefunction —
    // so they are fetched together and land in one strip. A result can have
    // either, both or neither.
    const [r, surf] = await Promise.all([
      NB.call("list_structure_sets", source),
      NB.mv.surfaces(source, (d && d.orbitals) || []),
    ]);
    const sets = (r && r.ok && r.sets) ? r.sets : [];
    const picks = (surf && surf.ok && surf.picks) ? surf.picks : [];
    if (!sets.length && !picks.length) {
      q("vtypes").innerHTML =
        `<p class="hint" style="margin:0;grid-column:1/-1">Nothing in this result's folder
          can be drawn yet. A trajectory, a CREST ensemble or exported conformers appear
          here as the run writes them, and orbitals once there is a .gbw beside it.</p>`;
      q("vlist").innerHTML = `<div class="vizempty">No frames.</div>`;
      return;
    }
    // A tile is a KIND of thing to look at; the list on the left is what
    // enumerates the things inside it. Ten frontier orbitals or forty
    // conformers are list rows, not forty tiles — the strip is one line of the
    // panel, and putting instances in it is what made four orbitals fill it.
    const tiles = sets.map(s => ({
      n: s.label, d: `${s.count} ${s.count === 1 ? "frame" : "frames"}`, set: s }));
    const mos = picks.filter(p => p.kind === "mo");
    if (mos.length) {
      tiles.push({ n: "Orbitals",
                   d: `${mos[0].short} \u2026 ${mos[mos.length - 1].short}`,
                   picks: mos, cap: "molecular orbital" });
    }
    picks.filter(p => p.kind !== "mo").forEach(p => tiles.push({
      n: p.short || p.label, d: p.ready ? "ready" : p.cost,
      picks: [p], cap: p.label.toLowerCase() }));

    q("vtypes").innerHTML = tiles.map((t, i) =>
      `<button class="viztype" type="button" aria-pressed="${i === 0}" data-tile="${i}">
         <span class="n">${NB.esc(t.n)}</span>
         <span class="d">${NB.esc(t.d)}</span>
       </button>`).join("");
    sec.querySelectorAll("[data-tile]").forEach(b => b.addEventListener("click", () => {
      const t = tiles[Number(/** @type {HTMLElement} */ (b).dataset.tile)];
      if (!t) return;
      sec.querySelectorAll("[data-tile]").forEach(o => o.setAttribute(
        "aria-pressed", String(o === b)));
      if (t.set) loadSet(sec, t.set); else loadPicks(sec, source, d, t);
    }));
    // the viewer opens on the set that is on screen, not on the first one:
    // "Open in viewer" means this, not something else in the same folder. The
    // orbitals ride along so the pop-out's own Surface picker can offer them
    // too — without the list it can only ever draw a density, never an orbital.
    q("vopen").addEventListener("click", () => {
      const set = sets.filter(s => s.key === _setKey)[0] || sets[0];
      NB.mv.open({ source: source, path: set && set.path,
                   orbitals: (d && d.orbitals) || [],
                   title: title(source) + (set ? " — " + set.label : "") });
    });
    // A single-point run has a wavefunction to plot and no .xyz beside it. The
    // strip is not empty, so it opens on the first surface rather than the
    // section claiming there is nothing here — and the stage draws the geometry
    // the parse already holds, because a surface belongs to a structure and an
    // empty box beside an orbital list says the panel has lost its place.
    if (!sets.length) {
      const g = (d && d.geometry) || [];
      if (g.length) {
        const atoms = g.map(a => ({ el: a.el, x: a.x, y: a.y, z: a.z }));
        _viz.setAtoms(atoms);
        q("vstage").insertAdjacentHTML("beforeend", legend(atoms));
      }
      loadPicks(sec, source, d, tiles[0]);
      return;
    }
    loadSet(sec, sets[0]);
  }

  /** The surfaces of one kind, as the list on the left. This stage paints
   *  atoms, not volumes — an isosurface needs the pop-out viewer's renderer —
   *  so a row here opens that window rather than changing what is drawn below.
   *  The structure on the stage is left alone: it is what the surface belongs
   *  to, and blanking it would say the panel had lost its place.
   *  @param {HTMLElement} sec @param {string} source @param {any} d @param {any} t */
  function loadPicks(sec, source, d, t) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    _setKey = "";
    const ready = t.picks.filter(p => p.ready).length;
    q("vmeta").textContent = t.picks.length === 1
      ? (t.picks[0].ready ? "plotted" : "not plotted yet")
      : `${t.picks.length} orbitals \u00b7 ${ready} already plotted`;
    q("vlist").innerHTML = t.picks.map((p, i) =>
      `<button class="vizitem" type="button" role="option" aria-selected="false"
         data-pick="${i}" title="${NB.esc(p.ready
           ? "Already plotted — opens at once" : "Not plotted yet — " + p.cost)}">
         <span>${NB.esc(p.short || p.label)}</span>
         <span class="v">${NB.esc(p.ev != null ? p.ev.toFixed(2) + " eV"
                                  : (p.ready ? "ready" : p.cost))}</span>
       </button>`).join("");
    sec.querySelectorAll("[data-pick]").forEach(b => b.addEventListener("click", () => {
      sec.querySelectorAll("[data-pick]").forEach(o => o.setAttribute(
        "aria-selected", String(o === b)));
      openSurface(source, d, t.picks[Number(/** @type {HTMLElement} */ (b).dataset.pick)]);
    }));
    q("vcap").textContent = t.picks.length === 1
      ? `${t.cap} \u2014 opens in the 3D viewer`
      : `${t.cap} \u2014 pick one on the left; it opens in the 3D viewer`;
  }

  /** Open one surface in the pop-out viewer, asking first when it has to be
   *  computed. A cube already on disk opens at once — there is nothing to
   *  decide. One that is not costs an orca_plot run, which for an ESP map is
   *  minutes of a machine the user may be running calculations on, so it is
   *  their call and not a side effect of a click (P26). The question names the
   *  cost rather than warning in the abstract.
   *  @param {string} source @param {any} d @param {any} p */
  function openSurface(source, d, p) {
    if (!p) return;
    const go = () => NB.mv.open({
      source: source, orbitals: (d && d.orbitals) || [], pick: p,
      title: title(source) + " — " + p.label });
    if (p.ready) { go(); return; }
    NB.modalRaw(`Plot ${p.label}?`,
      `<p>This has not been plotted yet, so ORCA's orca_plot has to run
        over the wavefunction at a ${p.grid}&times;${p.grid}&times;${p.grid} grid —
        ${NB.esc(p.cost)}${NB.esc(p.why || "")}. The cube is kept beside the run, so
        opening it again is instant.</p>`,
      [{ label: "Not now", cls: "btn-ghost" },
       { label: "Plot it", cls: "btn-primary", act: go }]);
  }

  /** @param {HTMLElement} sec @param {any} set */
  async function loadSet(sec, set) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    _setKey = set.key;
    q("vlist").innerHTML = `<div class="vizempty">reading…</div>`;
    const r = await NB.call("get_structure_frames", set.path);
    _frames = (r && r.ok && r.frames) ? r.frames : [];
    q("vmeta").textContent = (r && r.title) ? r.title : set.label;
    if (!_frames.length) {
      q("vlist").innerHTML = `<div class="vizempty">No frames in this file.</div>`;
      return;
    }
    // energies are only comparable within a set, so the column is relative
    const es = _frames.map(f => f.energy).filter(e => e != null);
    const e0 = es.length ? Math.min(...es) : null;
    q("vlist").innerHTML = _frames.map((f, i) =>
      `<button class="vizitem" type="button" role="option" aria-selected="${i === 0}"
         data-frame="${i}"><span>${NB.esc(f.label)}</span>${
        (f.energy != null && e0 != null)
          ? `<span class="v">${((f.energy - e0) * 627.5094740631).toFixed(2)}</span>` : ""
      }</button>`).join("");
    sec.querySelectorAll("[data-frame]").forEach(b => b.addEventListener("click", () =>
      showFrame(sec, Number(/** @type {HTMLElement} */ (b).dataset.frame))));
    showFrame(sec, 0);
  }

  /** @param {HTMLElement} sec @param {number} i */
  function showFrame(sec, i) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    const f = _frames[i];
    if (!f) return;
    _frameIdx = i;
    sec.querySelectorAll("[data-frame]").forEach(b => b.setAttribute("aria-selected",
      String(Number(/** @type {HTMLElement} */ (b).dataset.frame) === i)));
    const atoms = MOL.parseXyz(f.xyz);
    _viz.setAtoms(atoms);
    q("vcap").textContent = f.label + " · " + atoms.length + " atoms";
    const old = q("vstage").querySelector(".viewlegend");
    if (old) old.remove();
    q("vstage").insertAdjacentHTML("beforeend", legend(atoms));
  }

  // ---------------------------------------------------------------- NBO

  function nboSection() {
    return `<section class="secblock" data-sec="sec-nbo" id="sec-nbo">
      <div class="sech"><h2>Natural orbitals</h2><span class="sp"></span>
        <span class="meta">from .gbw</span></div>
      <p class="secdesc">Not part of the parse — computed from the wavefunction file on
        demand, which takes seconds to minutes depending on the basis.</p>
      <div class="plate" data-v="nbo" style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
        <span style="font-size:13px;color:var(--muted-foreground)" data-v="nbostate">Not computed.</span>
        <span class="sp" style="flex:1"></span>
        <button class="btn btn-sm" type="button" data-v="nborun">Compute natural orbitals</button>
      </div>
    </section>`;
  }

  /** @param {string} source */
  function mountNbo(source) {
    const sec = NB.$("sec-nbo");
    if (!sec) return;
    const state = /** @type {HTMLElement} */ (sec.querySelector('[data-v="nbostate"]'));
    const btn = /** @type {any} */ (sec.querySelector('[data-v="nborun"]'));
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      state.textContent = "Reading the wavefunction…";
      const started = await NB.call("run_nbo_analysis", source);
      if (started && started.error) { state.textContent = started.error; btn.disabled = false; return; }
      poll();
    });

    async function poll() {
      const s = await NB.call("get_nbo_status");
      if (!s || s.state === "error") {
        state.textContent = (s && s.error) || "The analysis failed.";
        btn.disabled = false;
        return;
      }
      if (s.state === "running" || s.state === "queued") {
        state.textContent = "Computing… " + (s.seconds ? s.seconds.toFixed(0) + "s" : "");
        setTimeout(poll, 900);
        return;
      }
      const r = await NB.call("get_nbo_result");
      btn.disabled = false;
      if (!r || !r.ok) { state.textContent = (r && r.error) || "No result."; return; }
      render(r);
    }

    /** @param {any} r NboResultPayload */
    function render(r) {
      const lewis = (r.lewis || [])[0];
      const plate = /** @type {HTMLElement} */ (sec.querySelector('[data-v="nbo"]'));
      plate.remove();
      const head = lewis
        ? `${(lewis.lewis_fraction * 100).toFixed(2)}% of the electrons fit a Lewis structure`
          + (lewis.complete ? "" : " — the rest are delocalized")
        : "Natural population analysis";
      sec.insertAdjacentHTML("beforeend",
        `<div class="plate"><p class="hint" style="margin:0">${NB.esc(head)}${
          r.n_basis ? ` · ${r.n_basis} basis functions · ${r.n_electrons} electrons` : ""}</p></div>`
        + `<div class="tw"><table>
             <thead><tr><th>Atom</th><th style="text-align:right">Charge</th>
               <th style="text-align:right">Core</th><th style="text-align:right">Valence</th>
               <th style="text-align:right">Rydberg</th><th>Configuration</th></tr></thead>
             <tbody>${(r.atoms || []).map(a =>
               `<tr><td class="lab">${a.index + 1} ${NB.esc(a.element)}</td>
                  <td class="n">${a.charge.toFixed(4)}</td>
                  <td class="n">${a.core.toFixed(3)}</td>
                  <td class="n">${a.valence.toFixed(3)}</td>
                  <td class="n">${a.rydberg.toFixed(3)}</td>
                  <td class="lab">${NB.esc(a.configuration || "")}</td></tr>`).join("")}
             </tbody></table></div>`);
      (r.warnings || []).forEach(w => NB.toast(w));
    }
  }

  NB.viewer = {
    /** the Structure section's stage, for anything that needs to drive it */
    stage: () => _stage,
    structureSection: structureSection, mountStructure: mountStructure,
    visualSection: visualSection, mountVisual: mountVisual,
    nboSection: nboSection, mountNbo: mountNbo,
  };
})();
