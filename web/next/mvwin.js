// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the pop-out 3D viewer.

   The inline stages (a cell's thumbnail, the report's Structure section, the
   Visual panel) are small on purpose: they sit inside a reading column. This is
   where a structure gets the window — and it is the only place that can show
   what an inline canvas cannot:

     · a TRAJECTORY walked frame by frame, with the frames worth keeping
       starred and exported as .xyz;
     · an ISOSURFACE — an orbital, the electron density, an ESP map — which is
       a volume, not a set of atoms.

   Two renderers, because those are two different problems:

     frames  ->  mol.js, the same painter as every other stage here, so drag,
                 click and right-drag-to-measure behave identically in the big
                 window and the small one.
     volume  ->  3Dmol (../vendor/3Dmol-min.js, the one the classic UI ships),
                 loaded on FIRST USE only. Marching cubes in the notebook's own
                 painter would be a second implementation of something already
                 in the repo, and nothing here needs WebGL until an isosurface
                 is asked for — a machine with no GL context can still walk a
                 trajectory.

   Everything it shows comes from the bridge: list_structure_sets /
   get_structure_frames for the frames, get_favorites / toggle_favorite /
   export_frames for the stars, get_plot_options / generate_cube /
   get_cube_status / get_cube_data for the surfaces.
   ============================================================ */

(function () {

  /** the mol.js stage, created once the window first opens @type {any} */
  let _stage = null;
  /** the 3Dmol GLViewer, created on the first volume plot @type {any} */
  let _gl = null;
  /** @type {any[]} frames of the open set */
  let _frames = [];
  let _idx = 0;
  /** starred labels for _source @type {Set<string>} */
  let _favs = new Set();
  let _favOnly = false;
  let _source = "";
  /** how export_frames addresses the destination: ["calc", name] / ["folder", path] */
  let _dest = ["folder", ""];
  let _mode = "frames";
  /** what returns focus when the window closes @type {any} */
  let _return = null;
  /** the plot picks built from get_plot_options + the parse's orbital list */
  let _picks = [];
  let _opts = null;
  /** the in-flight (or settled) get_plot_options for _source; see volumeReady */
  let _volReady = null;
  /** bumped on every plot request; a reply for an older one is dropped */
  let _seq = 0;
  let _volData = null, _espData = null, _volSigned = true, _volTitle = "";
  let _iso = 0.05, _espRange = 0.05;
  /** the parse's orbital list for the open result, which is what lets the
   *  Surface picker say HOMO and mean the orbital the PARSER called the HOMO
   *  @type {any[]} */
  let _orbitals = [];

  const q = (id) => /** @type {any} */ (NB.$(id));

  /** A CSS custom property's value, for a colour 3Dmol takes as a string.
   *  @param {string} name @param {string} fallback */
  function token(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v || "").trim() || fallback;
  }

  // ---------------------------------------------------------------- the shell

  function isOpen() { return !q("mvwin").hidden; }

  /** @param {KeyboardEvent} e */
  function onKey(e) {
    if (!isOpen()) return;
    if (e.key === "Escape") { close(); e.preventDefault(); return; }
    if (_mode !== "frames") return;
    // In volume mode a "frame" is not a thing on screen, and F starring
    // whatever structure was last shown would star something invisible.
    if (e.key === "ArrowLeft") { step(-1); e.preventDefault(); }
    else if (e.key === "ArrowRight") { step(1); e.preventDefault(); }
    else if (e.key === "f" || e.key === "F") {
      if (/** @type {any} */ (e.target).tagName === "INPUT") return;
      e.preventDefault(); toggleFav();
    }
  }

  function close() {
    // Release the GPU's buffers before the container is hidden: a render into a
    // display:none box draws into a 0x0 framebuffer, which leaves the old scene
    // on the card instead of clearing it.
    if (_gl) { try { _gl.clear(); _gl.render(); } catch (e) { /* context lost */ } }
    q("mvwin").hidden = true;
    q("mv-scrim").hidden = true;
    document.removeEventListener("keydown", onKey);
    _frames = [];
    _volData = null; _espData = null;
    _seq++;
    if (_return && _return.focus) _return.focus();
  }

  /** @param {string} m "frames" | "volume" */
  function setMode(m) {
    _mode = m;
    const vol = m === "volume";
    q("mv-frames").hidden = vol;
    q("mv-volume").hidden = !vol;
    q("mv-canvas").hidden = vol;
    q("mv-gl").hidden = !vol;
    q("mv-legend").hidden = vol;
    document.querySelectorAll("[data-mvmode]").forEach(b =>
      b.setAttribute("aria-pressed", String(/** @type {any} */ (b).dataset.mvmode === m)));
    if (!vol && _stage) _stage.repaint();
    if (vol) volumeReady();
  }

  // ---------------------------------------------------------------- frames

  /** Elements on screen, not a periodic table. @param {any[]} atoms */
  function legend(atoms) {
    const seen = [];
    atoms.forEach(a => { if (seen.indexOf(a.el) < 0) seen.push(a.el); });
    q("mv-legend").innerHTML = seen.slice(0, 8).map(el =>
      `<span><i style="background:${MOL.color(el)}${
        el === "H" ? ";outline:1px solid var(--border)" : ""}"></i>${NB.esc(el)}</span>`).join("");
  }

  /** The frames the walker may land on — every frame, or only the starred ones.
   *  @returns {number[]} */
  function visible() {
    const all = _frames.map((f, i) => i);
    if (!_favOnly) return all;
    return all.filter(i => _favs.has(_frames[i].label));
  }

  /** @param {number} d */
  function step(d) {
    const vis = visible();
    if (!vis.length) return;
    let at = vis.indexOf(_idx);
    at = at < 0 ? 0 : (at + d + vis.length) % vis.length;
    show(vis[at]);
  }

  /** @param {number} i */
  function show(i) {
    const f = _frames[i];
    if (!f || !_stage) return;
    _idx = i;
    const atoms = MOL.parseXyz(f.xyz);
    _stage.setAtoms(atoms);
    legend(atoms);
    const vis = visible();
    const at = vis.indexOf(i);
    q("mv-count").textContent = _frames.length
      ? `frame ${at >= 0 ? at + 1 : i + 1} of ${vis.length}`
        + (_favOnly ? " starred" : "")
      : "no frames";
    // energies are only comparable inside one set, so the caption is relative
    const es = _frames.map(x => x.energy).filter(e => e != null);
    const e0 = es.length ? Math.min(...es) : null;
    const de = (f.energy != null && e0 != null)
      ? ` · ΔE ${((f.energy - e0) * 627.5094740631).toFixed(2)} kcal/mol` : "";
    q("mv-cap").textContent = `${f.label} · ${atoms.length} atoms${de}`;
    syncFav();
  }

  function syncFav() {
    const f = _frames[_idx];
    const on = !!(f && _favs.has(f.label));
    const b = q("mv-fav");
    b.setAttribute("aria-pressed", String(on));
    b.innerHTML = (on ? "★" : "☆") + " Favourite <kbd>F</kbd>";
    q("mv-favonly").setAttribute("aria-pressed", String(_favOnly));
  }

  async function toggleFav() {
    const f = _frames[_idx];
    if (!f) return;
    const on = !_favs.has(f.label);
    if (on) _favs.add(f.label); else _favs.delete(f.label);
    syncFav();                                     // the star IS the feedback
    const r = await NB.call("toggle_favorite", _source, f.label, on);
    if (r && r.labels) _favs = new Set(r.labels);
    // A star that could not be written must say so: otherwise someone works
    // through a whole ensemble and finds every star gone at the next launch.
    if (r && r.ok === false && r.error) NB.fail(r.error);
    // Unstarring the last favourite while "favourites only" is on leaves the
    // walker with nothing to walk, so the filter lets itself go.
    if (_favOnly && !_frames.some(x => _favs.has(x.label))) _favOnly = false;
    syncFav();
    show(_idx);
  }

  // ---------------------------------------------------------------- volume

  /** Load 3Dmol once, on the first surface. @returns {Promise<any>} */
  function load3Dmol() {
    const have = window["$3Dmol"] || window["3Dmol"];
    if (have) return Promise.resolve(have);
    return new Promise(res => {
      const s = document.createElement("script");
      s.src = "../vendor/3Dmol-min.js";
      s.onload = () => res(window["$3Dmol"] || window["3Dmol"] || null);
      s.onerror = () => res(null);
      document.head.appendChild(s);
    });
  }

  /** What this result can be drawn as. Built from the backend's own answer —
   *  which kinds the wavefunction supports — and the parse's orbital list, so
   *  HOMO means the orbital the parser called the HOMO rather than one this
   *  file counted out again (P4).
   *  @param {any[]} orbitals @param {any} opts PlotOptionsResult */
  /** What a fresh plot of each kind costs, so the place that ASKS before
   *  spending it does not have to invent a number. Measured on ORCA 6.1.1,
   *  52 atoms / 987 basis functions: one MO at 60 = 0.17 s, an SCF density
   *  9.9 s, an ESP map 49 s at 40 and ~2.8 min at 60 (it is two cubes, and it
   *  scales with the basis as well as the grid). */
  const COST = {
    mo: "about a second", eldens: "a few seconds",
    spindens: "a few seconds", esp: "a few minutes",
  };
  /** What else is worth knowing before spending it, for the question that asks.
   *  Only the ESP has anything to add, and it is the one that costs enough to
   *  be worth saying: the surface and the colour on it are two separate cubes
   *  on one grid, and the potential is a Coulomb sum at every point of it. */
  const COST_WHY = {
    esp: " — it is two cubes on one grid, not one, and the potential is a sum "
         + "over every nucleus at every point",
  };

  function picksFor(orbitals, opts) {
    const kinds = opts.kinds || [];
    const out = [];
    if (kinds.indexOf("mo") >= 0 && orbitals && orbitals.length) {
      // one manifold at a time: an unrestricted run prints two, both indexed
      // from 0, and mixing them would plot the wrong orbital under the right name
      const spin = orbitals.filter(o => !o.spin || o.spin === "a");
      const homo = spin.filter(o => o.frontier === "homo")[0];
      const lumo = spin.filter(o => o.frontier === "lumo")[0];
      const at = (o, d) => o ? spin.filter(x => x.idx === o.idx + d)[0] : null;
      // `label` names it in the picker, where there is a line to spare; `short`
      // names it on a Visual tile, which is one grid cell wide and wraps an
      // orbital energy onto three lines if given one.
      const push = (o, label) => {
        if (!o) return;
        out.push({ kind: "mo", index: o.idx, operator: 0, short: label, ev: o.ev,
                   label: `${label} (${o.idx}, ${o.ev.toFixed(2)} eV)` });
      };
      // A frontier WINDOW, not the two edges: which orbital matters is a
      // question about the chemistry, and the one people reach for after the
      // HOMO is usually HOMO−2, not the density. The design's own tile says
      // "HOMO−4 … LUMO+4", and a list is what makes ten of them cost nothing.
      for (let k = 4; k >= 1; k--) push(at(homo, -k), "HOMO−" + k);
      push(homo, "HOMO");
      push(lumo, "LUMO");
      for (let k = 1; k <= 4; k++) push(at(lumo, k), "LUMO+" + k);
    }
    if (kinds.indexOf("eldens") >= 0)
      out.push({ kind: "eldens", index: 0, operator: 0,
                 label: "Electron density", short: "Electron density" });
    if (kinds.indexOf("spindens") >= 0)
      out.push({ kind: "spindens", index: 0, operator: 0,
                 label: "Spin density", short: "Spin density" });
    if (kinds.indexOf("esp") >= 0)
      out.push({ kind: "esp", index: 0, operator: 0,
                 label: "Electrostatic potential", short: "ESP map" });
    return out;
  }

  /** What this result can be drawn AS, without opening the viewer: the same
   *  picks the Surface selector holds, each marked with whether its cube is
   *  already on disk. The Visual section lists these beside the .xyz sets, so
   *  both live behind one definition of what a pick is and what its file is
   *  called (P4) — a second copy of the naming rule is how a plot and its
   *  "already generated" mark come to disagree.
   *  @param {string} source @param {any[]} orbitals */
  async function surfaces(source, orbitals) {
    const o = await NB.call("get_plot_options", source);
    if (!o || !o.ok || !o.has_gbw) {
      return { ok: false, picks: [],
               error: (o && o.error)
                 || "No .gbw beside this result — there is no wavefunction to plot from." };
    }
    return { ok: true, picks: picksFor(orbitals || [], o).map(p => Object.assign({},
      p, { ready: cached(o, p), grid: gridFor(o, p.kind),
           cost: COST[p.kind] || "a moment", why: COST_WHY[p.kind] || "" })) };
  }

  /** Fill the Surface picker once the options are known. Returns the promise so
   *  a caller that wants to plot straight away can wait for the list (opening
   *  on a pick does). Asked once per result: the guard is the promise, not
   *  `_opts`, which is only set when the reply lands. */
  function volumeReady() {
    if (_volReady) return _volReady;                // already asked
    const sel = q("mv-vol");
    sel.innerHTML = `<option>reading the wavefunction…</option>`;
    _volReady = NB.call("get_plot_options", _source).then(o => {
      _opts = o || { ok: false };
      if (!_opts.ok || !_opts.has_gbw) {
        sel.innerHTML = `<option value="">nothing to plot</option>`;
        q("mv-cap").textContent = _opts.error
          || "No .gbw beside this result — there is no wavefunction to plot from.";
        q("mv-plot").disabled = true;
        return;
      }
      _espRange = _opts.esp_range || 0.05;
      _picks = picksFor(_orbitals, _opts);
      if (!_picks.length) {
        sel.innerHTML = `<option value="">nothing to plot</option>`;
        q("mv-plot").disabled = true;
        return;
      }
      sel.innerHTML = _picks.map((p, i) => {
        const ready = cached(_opts, p);
        return `<option value="${i}">${NB.esc(p.label)}${ready ? " — ready" : ""}</option>`;
      }).join("");
      q("mv-plot").disabled = false;
      showRamp();
      q("mv-cap").textContent =
        "Pick a surface and press Plot. Anything marked “ready” is already on disk.";
    });
    return _volReady;
  }

  /** orca_plot's own filename for a pick, so "ready" and the fetch that follows
   *  it can never disagree about which file they mean.
   *  @param {any} p @param {number} [grid] */
  function cubeName(opts, p, grid) {
    const g = grid || gridFor(opts, p.kind);
    const b = opts.base;
    // an ESP is named after the DENSITY it came from: water.scfp.esp.cube
    const stem = p.kind === "mo"
      ? `${b}.mo${p.index}${p.operator ? "b" : "a"}`
      : (p.kind === "esp" ? `${b}.scfp.esp` : `${b}.${p.kind}`);
    return `${stem}.g${g}.cube`;
  }
  /** @param {string} kind */
  function gridFor(opts, kind) {
    return kind === "esp" ? (opts.esp_grid || opts.default_grid) : opts.default_grid;
  }
  /** @param {any} opts @param {any} p */
  function cached(opts, p) {
    const names = p.kind === "esp"
      ? [cubeName(opts, { kind: "eldens", index: 0, operator: 0 }, gridFor(opts, "esp")),
         cubeName(opts, p, gridFor(opts, "esp"))]
      : [cubeName(opts, p)];
    return names.every(n => (opts.cached || []).indexOf(n) >= 0);
  }

  /** Run orca_plot for one request and read the cube back.
   *  @param {any} part @param {number} grid @param {number} seq @param {string} wait */
  async function fetchCube(part, grid, seq, wait) {
    const payload = JSON.stringify({ source: _source, kind: part.kind,
      index: part.index, operator: part.operator, grid: grid });
    const mine = (j) => !!j && j.kind === part.kind && j.index === part.index
      && j.operator === part.operator && j.grid === grid;
    let job = null;
    for (;;) {
      job = await NB.call("generate_cube", payload);
      if (seq !== _seq) return null;
      if (mine(job)) break;
      // the backend serializes orca_plot — one folder, one fixed output name
      q("mv-cap").textContent = wait + " — waiting for the previous plot…";
      await new Promise(r => setTimeout(r, 250));
      if (seq !== _seq) return null;
    }
    while (job && job.state === "running") {
      q("mv-cap").textContent = `${wait} — ${job.seconds ? job.seconds.toFixed(0) + "s" : "working"}…`;
      await new Promise(r => setTimeout(r, 250));
      if (seq !== _seq) return null;
      job = await NB.call("get_cube_status");
    }
    if (!job || job.state !== "done" || !mine(job)) {
      NB.fail((job && job.error) || "orca_plot could not produce that surface.");
      return null;
    }
    const data = await NB.call("get_cube_data");
    if (seq !== _seq) return null;
    if (!data || !data.ok) {
      NB.fail((data && data.error) || "Could not read the cube file.");
      return null;
    }
    const n = cubeName(_opts, part, grid);
    if ((_opts.cached || []).indexOf(n) < 0) _opts.cached.push(n);
    return data;
  }

  async function plot() {
    const sel = q("mv-vol");
    const p = _picks[Number(sel.value)];
    if (!p) return;
    const $3Dmol = await load3Dmol();
    if (!$3Dmol) {
      NB.fail("The 3D renderer could not be loaded, so a surface cannot be drawn.");
      return;
    }
    if (!_gl) {
      const bg = token("--stage", "");
      try {
        _gl = $3Dmol.createViewer(q("mv-gl"), bg ? { backgroundColor: bg } : {});
      } catch (e) { _gl = null; }
      if (!_gl) {
        // a remote desktop, a VM, a blocklisted GPU: the frames half still works
        NB.fail("3D rendering is unavailable on this display — the graphics "
                + "driver would not provide a WebGL context.");
        return;
      }
    }
    _gl.resize();
    const seq = ++_seq;
    q("mv-plot").disabled = true;
    const grid = gridFor(_opts, p.kind);
    try {
      if (p.kind === "esp") {
        // Two fields on ONE grid: the density gives the surface, the potential
        // colours it per vertex. Different grids would be two different boxes.
        const dens = await fetchCube({ kind: "eldens", index: 0, operator: 0 },
                                     grid, seq, "ESP map — density surface");
        if (!dens) return;
        const esp = await fetchCube(p, grid, seq, "ESP map — potential");
        if (!esp) return;
        _volSigned = false;
        _volTitle = "ESP map";
        _gl.clear();
        _gl.addModel(dens.text, "cube");
        _gl.setStyle({}, { stick: { radius: 0.1 }, sphere: { scale: 0.18 } });
        _volData = new $3Dmol.VolumeData(dens.text, "cube");
        _espData = new $3Dmol.VolumeData(esp.text, "cube");
        // the ESP convention (~0.002 e/bohr³, the van-der-Waals-like contour),
        // not a fitted level: a total density's 0.002 surface is the same
        // physical surface in every molecule
        _iso = _opts.esp_surface_iso || 0.002;
      } else {
        const data = await fetchCube(p, grid, seq, p.label);
        if (!data) return;
        _espData = null;
        _volSigned = data.signed !== false;
        _volTitle = data.title || p.label;
        _gl.clear();
        _gl.addModel(data.text, "cube");
        _gl.setStyle({}, { stick: { radius: 0.1 }, sphere: { scale: 0.18 } });
        _volData = new $3Dmol.VolumeData(data.text, "cube");
        _iso = data.isovalue || 0.05;
      }
      q("mv-iso").value = _iso.toFixed(4);
      showRamp();
      draw(true);
    } finally {
      if (seq === _seq) q("mv-plot").disabled = false;
    }
  }

  /** The ramp is the ESP legend; it means nothing beside a signed orbital, and
   *  a legend that is always on screen is a legend nobody reads. */
  function showRamp() {
    const sel = q("mv-vol");
    const p = _picks[Number(sel.value)];
    const esp = !!(p && p.kind === "esp");
    q("mv-ramp").hidden = !esp;
    q("mv-ramphint").hidden = !esp;
  }

  /** Re-mesh what is already in memory. No backend call — which is the whole
   *  reason a coarse grid is enough to steer the isovalue by hand.
   *  @param {boolean} zoom */
  function draw(zoom) {
    const $3Dmol = window["$3Dmol"] || window["3Dmol"];
    if (!_gl || !_volData || !$3Dmol) return;
    _gl.removeAllShapes();
    // smoothness = Laplacian smoothing of the marching-cubes output: the knob
    // that makes a 40³ sampling look like a surface. Raising the GRID instead
    // costs cube-of-the-number bytes for detail an isosurface cannot show.
    const common = { opacity: 0.88, smoothness: 8 };
    if (_espData) {
      _gl.addIsosurface(_volData, Object.assign({
        isoval: _iso,
        voldata: _espData,
        volscheme: new $3Dmol.Gradient.CustomLinear(-_espRange, _espRange,
          [token("--esp-neg", "#f2555a"), token("--esp-mid", "#f5f5f5"),
           token("--esp-pos", "#4f86f7")]),
      }, common));
      q("mv-cap").textContent = `ESP map · density surface ${_iso.toFixed(4)} e/bohr³`
        + ` · scale ±${_espRange.toFixed(3)} a.u. · red −, blue +`;
    } else if (_volSigned) {
      _gl.addIsosurface(_volData, Object.assign({ isoval: _iso, color: token("--orb-pos", "#4f86f7") }, common));
      _gl.addIsosurface(_volData, Object.assign({ isoval: -_iso, color: token("--orb-neg", "#f2555a") }, common));
      q("mv-cap").textContent = `${_volTitle} · isovalue ${_iso.toFixed(4)} a.u. · blue +, red −`;
    } else {
      _gl.addIsosurface(_volData, Object.assign({ isoval: _iso, color: token("--orb-dens", "#5eead4") }, common));
      q("mv-cap").textContent = `${_volTitle} · isovalue ${_iso.toFixed(4)} a.u.`;
    }
    if (zoom) _gl.zoomTo();
    _gl.render();
  }

  // ---------------------------------------------------------------- opening

  /** Open the viewer.
   *  @param {{source: string, title?: string, path?: string, orbitals?: any[],
   *           mode?: string, pick?: any}} o
   *    source  how the backend addresses this result: "calc:<name>" or "file:<path>"
   *    path    one structure set to open on; otherwise the first one found
   *    mode    "frames" (default) or "volume"
   *    pick    a surface to draw at once ({kind, index, operator}); implies
   *            volume mode. The caller has already decided whether this is
   *            worth the wait — asking again here would be asking twice. */
  async function open(o) {
    const src = o.source || "";
    if (!src) return;
    _return = document.activeElement;
    _source = src;
    _opts = null;
    _volReady = null;
    _picks = [];
    _orbitals = o.orbitals || [];
    q("mv-title").textContent = o.title || "Structure viewer";
    q("mv-scrim").hidden = false;
    q("mvwin").hidden = false;
    document.addEventListener("keydown", onKey);
    q("mv-close").focus();

    if (!_stage) _stage = MOL.mount(q("mv-canvas"), {});
    else _stage.repaint();

    setMode((o.mode === "volume" || o.pick) ? "volume" : "frames");
    // Opening ON a surface: wait for the picker to be filled, then select the
    // one asked for and run it. Selecting by identity, not by position — the
    // list is built from the wavefunction and the caller cannot know its order.
    if (o.pick) {
      volumeReady().then(() => {
        const i = _picks.findIndex(x => x.kind === o.pick.kind
          && x.index === o.pick.index && x.operator === o.pick.operator);
        if (i < 0) { NB.fail("That surface is not available for this wavefunction."); return; }
        q("mv-vol").value = String(i);
        showRamp();
        plot();
      });
    }

    // export_frames addresses a queued calculation by name and anything else by
    // folder — the two land in different places, so the prefix is not decoration
    _dest = src.indexOf("calc:") === 0
      ? ["calc", src.slice(5)] : ["folder", ""];

    q("mv-count").textContent = "reading…";
    _frames = [];
    _favs = new Set();
    _favOnly = false;

    const r = await NB.call("list_structure_sets", src);
    const sets = (r && r.ok && r.sets) ? r.sets : [];
    const set = (o.path && sets.filter(s => s.path === o.path)[0]) || sets[0];
    if (!set) {
      q("mv-count").textContent = "no structures";
      // in volume mode the caption belongs to the surface; a single-point run
      // legitimately has no .xyz beside it and is still worth plotting
      if (_mode === "frames") {
        q("mv-cap").textContent =
          "Nothing in this result's folder can be drawn yet — a trajectory, a CREST "
          + "ensemble or exported conformers appear here as the run writes them.";
      }
      return;
    }
    const fr = await NB.call("get_structure_frames", set.path);
    _frames = (fr && fr.ok && fr.frames) ? fr.frames : [];
    if (_dest[0] === "folder") _dest[1] = (fr && fr.folder) || set.path;
    if (!_frames.length) {
      q("mv-count").textContent = "no frames";
      if (_mode === "frames") {
        q("mv-cap").textContent = (fr && fr.error) || "No .xyz structures there.";
      }
      return;
    }
    const f = await NB.call("get_favorites", src);
    _favs = new Set((f && f.labels) || []);
    // the LAST frame: a trajectory's point is where it ended up, and a
    // CREST ensemble is written best-first, so its last is its worst — the
    // arrows walk back from there either way
    if (_mode === "frames") show(_frames.length - 1);
    else _idx = _frames.length - 1;   // ready for a switch back to frames
  }

  // ---------------------------------------------------------------- wiring

  function init() {
    q("mv-scrim").addEventListener("click", close);
    q("mv-close").addEventListener("click", close);
    document.querySelectorAll("[data-mvmode]").forEach(b =>
      b.addEventListener("click", () => setMode(/** @type {any} */ (b).dataset.mvmode)));

    q("mv-prev").addEventListener("click", () => step(-1));
    q("mv-next").addEventListener("click", () => step(1));
    q("mv-fav").addEventListener("click", toggleFav);

    q("mv-favonly").addEventListener("click", () => {
      const any = _frames.some(f => _favs.has(f.label));
      if (!_favOnly && !any) { NB.toast("No favourites yet — star a structure first (F)."); return; }
      _favOnly = !_favOnly;
      syncFav();
      if (_favOnly && !_favs.has(_frames[_idx].label)) {
        const vis = visible();
        if (vis.length) show(vis[0]);
      } else show(_idx);
    });

    q("mv-export").addEventListener("click", async () => {
      const picked = _frames.filter(f => _favs.has(f.label))
        .map(f => ({ label: f.label, xyz: f.xyz }));
      if (!picked.length) { NB.toast("No favourites to export — star some first (F)."); return; }
      const r = await NB.call("export_frames", _dest[0], _dest[1], JSON.stringify(picked));
      if (!r || !r.ok) { NB.fail((r && r.error) || "Could not export the favourites."); return; }
      NB.toast(`Exported ${r.count} favourite${r.count === 1 ? "" : "s"} to ${r.folder}`, "ok");
    });

    q("mv-copyxyz").addEventListener("click", async () => {
      const f = _frames[_idx];
      if (!f) return;
      const text = `${MOL.parseXyz(f.xyz).length}\n${f.label}\n${f.xyz.trim()}\n`;
      try {
        await navigator.clipboard.writeText(text);
        NB.toast("Copied as .xyz.", "ok");
      } catch (e) { NB.fail("The clipboard refused the copy."); }
    });

    q("mv-vol").addEventListener("change", showRamp);
    q("mv-plot").addEventListener("click", plot);
    q("mv-iso").addEventListener("change", () => {
      const v = parseFloat(q("mv-iso").value);
      if (!isFinite(v) || v <= 0) { q("mv-iso").value = _iso.toFixed(4); return; }
      _iso = v;
      draw(false);
    });
  }

  NB.mv = { init: init, open: open, close: close, isOpen: isOpen,
            surfaces: surfaces };
})();
