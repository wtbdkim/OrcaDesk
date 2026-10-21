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
  /** The Visual panel's own plot state: the options for the result on screen,
   *  and the surface it is currently drawing (null = it is drawing atoms). The
   *  panel draws surfaces in place now; the pop-out is the same picture with
   *  the isovalue beside it, and the two share one renderer. */
  let _opts = null;
  /** @type {any} */ let _surfPick = null;
  /** the tile whose surfaces are listed: {picks, cap} @type {any} */
  let _kind = null;
  /** the grid each kind is drawn at, when the user has chosen one. Per KIND,
   *  because the costs are not comparable: an orbital at 80 is a second, an ESP
   *  map at 80 is minutes, so one number for both would be wrong twice.
   *  @type {Record<string, number>} */
  const _gridByKind = {};
  let _source = "";

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
      <div class="viewstage" data-v="stage">
        <canvas class="molcv" data-v="canvas"
          aria-label="Final geometry, ball and stick. Drag to turn, right-drag between two
            atoms to measure, click an atom or a bond for its numbers; the arrow keys turn
            it too, and x, y or z looks straight down that axis."></canvas>
        ${legend(atoms)}
        <div class="stageacts">
          <button class="act" type="button" data-v="open">Open in viewer</button>
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

    sec.querySelector('[data-v="open"]').addEventListener("click", () => NB.mv.open({
      source: source, title: title(source) + " \u2014 structure",
      orbitals: d.orbitals || [] }));
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
      <div class="vizpanel">
        <div class="vizside">
          <div class="vizlist" data-v="vlist" role="listbox" aria-label="Frames"></div>
          <div class="vizctl" data-v="vctl" hidden></div>
        </div>
        <div class="vizstage" data-v="vstage">
          <canvas class="molcv" data-v="vcanvas"
            aria-label="Structure viewer. Drag to turn; the arrow keys turn it too, and
              x, y or z looks straight down that axis."></canvas>
          <div class="stageacts">
            <button class="act" type="button" data-v="vopen">Open in viewer</button>
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
    // .vizcap writes the frame's name in this stage's bottom-left corner, which
    // is where the axis marker goes — so the marker sits above it
    _viz = MOL.mount(q("vcanvas"), { gizmoBottom: 60 });

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
    _opts = (surf && surf.opts) || null;
    _source = source;
    _surfPick = null;
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
      // the whole manifold is here, so the tile COUNTS it rather than naming
      // its ends: "HOMO-4 ... LUMO+4" described a window that no longer exists
      tiles.push({ n: "Orbitals",
                   d: `${mos.length} ${mos.length === 1 ? "orbital" : "orbitals"}`,
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
      // whatever is on the stage, bigger: a surface opens ON that surface, a
      // structure set on that set. "Open in viewer" means this one.
      if (_surfPick) {
        NB.mv.open({ source: source, orbitals: (d && d.orbitals) || [],
                     pick: _surfPick,
                     title: title(source) + " — " + _surfPick.label });
        return;
      }
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

  /** The surfaces of one kind, on the left. The stage draws them in place now,
   *  so a row here changes what is on screen rather than opening a window.
   *
   *  A kind with ONE instance gets no list: an ESP map is one map, and a
   *  one-row list is a list of one thing to choose between. The column says so
   *  and carries the controls instead — which is the useful half of it, and the
   *  reference's own answer here ("nothing to step through"). A single surface
   *  already on disk is simply drawn; one that is not offers the button,
   *  because plotting it costs an orca_plot run and a tile press is not
   *  permission to spend one (P26).
   *  @param {HTMLElement} sec @param {string} source @param {any} d @param {any} t */
  function loadPicks(sec, source, d, t) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    _setKey = "";
    _kind = t;
    const list = q("vlist");

    // "ready" is a question about the GRID too -- the same orbital at 40 and at
    // 80 are two different files -- so it is asked here rather than read off the
    // mark the tile strip was built with
    const isReady = (/** @type {any} */ p) => NB.mv.readyAt(_opts, p, gridOf(p.kind));

    if (t.picks.length === 1) {
      const p = t.picks[0];
      q("vmeta").textContent = isReady(p) ? "plotted" : "not plotted yet";
      list.onclick = null;
      list.innerHTML = `<div class="vizempty">One surface — nothing to step
        through.${isReady(p) ? "" : ` It has not been plotted yet: ${NB.esc(p.cost)}.`}
        <div style="margin-top:10px">${isReady(p) ? ""
          : `<button class="btn btn-sm" type="button" data-plot="1">Plot it</button>`}
        </div></div>`;
      q("vcap").textContent = t.cap;
      // the controls exist before the plot does, so the plot can put them in
      // step with the level it chose for itself
      buildControls(sec, source);
      const go = list.querySelector("[data-plot]");
      if (go) go.addEventListener("click", () => showSurface(sec, source, p));
      else showSurface(sec, source, p);
      return;
    }

    const ready = t.picks.filter(isReady).length;
    q("vmeta").textContent = `${t.picks.length} orbitals · ${ready} already plotted`;
    list.innerHTML = t.picks.map((p, i) =>
      `<button class="vizitem" type="button" role="option" aria-selected="false"
         data-pick="${i}" title="${NB.esc(isReady(p)
           ? "Already plotted — draws at once" : "Not plotted yet — " + p.cost)}">
         <span>${NB.esc(p.short || p.label)}</span>
         <span class="v">${NB.esc(p.ev != null ? p.ev.toFixed(2)
                                  : (isReady(p) ? "ready" : p.cost))}</span>
       </button>`).join("");
    // one handler on the list, not one per row: a whole orbital manifold is
    // hundreds of rows on a real basis set, and the list is rebuilt on every
    // tile press
    list.onclick = (e) => {
      const b = /** @type {HTMLElement|null} */ (
        /** @type {HTMLElement} */ (e.target).closest("[data-pick]"));
      if (!b || !list.contains(b)) return;
      list.querySelectorAll("[data-pick]").forEach(o => o.setAttribute(
        "aria-selected", String(o === b)));
      showSurface(sec, source, t.picks[Number(b.dataset.pick)]);
    };
    // the unit is said once, not on all of a few hundred rows: a core orbital
    // reads -520.12, and three characters of " eV" repeated per row is what
    // pushed the name that identifies it out of the column
    const unit = t.picks.some(p => p.ev != null) ? ", energies in eV" : "";
    q("vcap").textContent = `${t.cap}${unit} — pick one on the left`;
    buildControls(sec, source);
  }

  /** The controls under the list: what level to draw the surface at, and at
   *  what sampling. Two very different costs sit side by side here and the
   *  wording is what keeps them apart -- the isovalue re-meshes what is already
   *  in memory (instant, no backend), while the grid is a fresh orca_plot run
   *  over the wavefunction, which for an ESP map is minutes. So the grid asks
   *  before it spends, exactly as picking an unplotted surface does.
   *  @param {HTMLElement} sec @param {string} source */
  function buildControls(sec, source) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    const ctl = q("vctl");
    if (!ctl || !_opts || !_kind) return;
    const kind = _kind.picks[0].kind;
    const grids = _opts.grids || [_opts.default_grid];
    const grid = gridOf(kind);
    ctl.hidden = false;
    ctl.innerHTML = `
      <div class="field"><label for="viz-iso">Isovalue</label>
        <input id="viz-iso" class="mono" type="number" step="0.005" min="0"
               data-v="iso" value="${NB.mv.vol.iso().toFixed(4)}"></div>
      <div class="field" data-v="rangewrap" hidden>
        <label for="viz-range">Colour ± (a.u.)</label>
        <input id="viz-range" class="mono" type="number" step="0.01" min="0"
               data-v="range" value="${NB.mv.vol.espRange().toFixed(3)}"></div>
      <div class="field"><label for="viz-grid">Grid</label>
        <select id="viz-grid" data-v="grid">${grids.map(g =>
          `<option value="${g}"${g === grid ? " selected" : ""}>${g}³</option>`
        ).join("")}</select></div>`;
    q("iso").addEventListener("change", () => {
      const v = parseFloat(q("iso").value);
      if (!isFinite(v) || v <= 0) { q("iso").value = NB.mv.vol.iso().toFixed(4); return; }
      NB.mv.vol.setIso(v);
    });
    q("range").addEventListener("change", () => {
      const v = parseFloat(q("range").value);
      if (!isFinite(v) || v <= 0) { q("range").value = NB.mv.vol.espRange().toFixed(3); return; }
      NB.mv.vol.setEspRange(v);
    });
    q("grid").addEventListener("change", () => {
      const g = Number(q("grid").value);
      _gridByKind[kind] = g;
      refreshReady(sec, g);
      // a grid change is a re-plot, not a re-mesh: the same orbital at 40 and at
      // 80 are two different files on disk, so this goes back through the
      // question -- and at a grid nothing has been plotted at yet, it asks
      if (_surfPick) showSurface(sec, source, _surfPick);
    });
    syncControls(sec);
  }

  /** Every "ready" mark on screen answers a question about one grid, and the
   *  grid has just changed. The rows and the tile are re-marked in place rather
   *  than the list being rebuilt: rebuilding it would drop the row the user is
   *  looking at, which is the one about to be re-plotted.
   *  @param {HTMLElement} sec @param {number} g */
  function refreshReady(sec, g) {
    if (!_kind || !_opts) return;
    const mark = sec.querySelector('[data-tile][aria-pressed="true"] .d');
    if (mark && _kind.picks.length === 1) {
      mark.textContent = NB.mv.readyAt(_opts, _kind.picks[0], g)
        ? "ready" : _kind.picks[0].cost;
    }
    sec.querySelectorAll("[data-pick]").forEach(b => {
      const p = _kind.picks[Number(/** @type {HTMLElement} */ (b).dataset.pick)];
      if (!p) return;
      const ok = NB.mv.readyAt(_opts, p, g);
      b.setAttribute("title", ok ? "Already plotted — draws at once"
                                 : "Not plotted yet — " + p.cost);
      const v = b.querySelector(".v");
      if (v && p.ev == null) v.textContent = ok ? "ready" : p.cost;
    });
  }

  /** Put the controls back in step with what is actually on the stage: the
   *  isovalue a plot chose for itself (an orbital's is fitted to its own peak,
   *  a density's is the 0.002 convention), and whether a colour scale means
   *  anything at all here. @param {HTMLElement} sec */
  function syncControls(sec) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    const iso = q("iso"), range = q("range"), wrap = q("rangewrap");
    if (!iso) return;
    iso.value = NB.mv.vol.iso().toFixed(4);
    if (wrap) wrap.hidden = !NB.mv.vol.isEsp();
    if (range) range.value = NB.mv.vol.espRange().toFixed(3);
  }

  /** The grid this kind is drawn at: the user's choice for it, else its own
   *  default (an ESP map opens coarser than an orbital -- it costs minutes,
   *  not a second). @param {string} kind */
  function gridOf(kind) {
    return _gridByKind[kind] || NB.mv.gridFor(_opts, kind);
  }

  /** Draw one surface IN THE PANEL, asking first when it has to be computed.
   *
   *  A cube already on disk appears at once — there is nothing to decide. One
   *  that is not costs an orca_plot run, which for an ESP map is minutes of a
   *  machine the user may be running calculations on, so it is their call and
   *  not a side effect of a click (P26). The question names the cost rather
   *  than warning in the abstract.
   *
   *  It used to open the pop-out window instead, because this stage paints
   *  atoms and an isosurface is a volume. That was a renderer's limitation
   *  showing through as behaviour: you picked something on the left and the
   *  answer arrived somewhere else, over the report you were reading. The
   *  renderer is shared now (mvwin.js volumeView) and its box moves to whichever
   *  stage is drawing — so a pick lands where it was made, and the window is
   *  what you open when you want it big, with the isovalue beside it.
   *  @param {HTMLElement} sec @param {string} source @param {any} p */
  function showSurface(sec, source, p) {
    if (!p || !_opts) return;
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    const grid = gridOf(p.kind);
    const go = async () => {
      _surfPick = p;
      _setKey = "";
      // the atom painter steps aside rather than being cleared: what is on the
      // stage is replaced, not blanked, so the panel never shows an empty box
      q("vcanvas").hidden = true;
      NB.mv.vol.adopt(q("vstage"));
      q("vcap").textContent = `${p.label} \u2014 working\u2026`;
      const ok = await NB.mv.vol.plot(source, p, _opts,
                                      t => { q("vcap").textContent = t; }, grid);
      if (!ok) hideSurface(sec);
      else syncControls(sec);
    };
    // "ready" is a question about the grid too: the same orbital at 40 and at
    // 80 are two different files, so a grid change re-asks
    if (NB.mv.readyAt(_opts, p, grid)) { go(); return; }
    NB.modalRaw(`Plot ${p.label}?`,
      `<p>This has not been plotted yet, so ORCA's orca_plot has to run
        over the wavefunction at a ${grid}&times;${grid}&times;${grid} grid —
        ${NB.esc(p.cost)}${NB.esc(p.why || "")}. The cube is kept beside the run, so
        drawing it again is instant.</p>`,
      [{ label: "Not now", cls: "btn-ghost" },
       { label: "Plot it", cls: "btn-primary", act: go }]);
  }

  /** Give the stage back to the atom painter. @param {HTMLElement} sec */
  function hideSurface(sec) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    _surfPick = null;
    const box = NB.$("mv-gl");
    if (box) box.hidden = true;
    q("vcanvas").hidden = false;
    if (_viz) _viz.repaint();
  }

  /** Take the shared box back after the pop-out window has had it. Called by
   *  mvwin on close: the window borrowed the one renderer there is, and a panel
   *  left with an empty stage would read as the pick having been lost. The cube
   *  is on disk by now, so this is a redraw and not another orca_plot run. */
  function reclaimSurface() {
    const sec = NB.$("sec-visual");
    if (!sec || !_surfPick) return;
    showSurface(sec, _source, _surfPick);
  }

  /** @param {HTMLElement} sec @param {any} set */
  async function loadSet(sec, set) {
    const q = (k) => /** @type {any} */ (sec.querySelector(`[data-v="${k}"]`));
    // a structure tile takes the stage back from whatever surface had it, and
    // the surface controls go with it: there is no isovalue for a molecule
    if (_surfPick) hideSurface(sec);
    _kind = null;
    const ctl = q("vctl");
    if (ctl) ctl.hidden = true;
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
    reclaimSurface: reclaimSurface,
    structureSection: structureSection, mountStructure: mountStructure,
    visualSection: visualSection, mountVisual: mountVisual,
    nboSection: nboSection, mountNbo: mountNbo,
  };
})();
