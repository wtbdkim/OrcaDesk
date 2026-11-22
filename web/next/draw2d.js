// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the 2D drawing (an add-on).

   Results › Visual's "2D structure" tile splits the stage: the 3D view on one
   side, and on the other a ChemDraw-style drawing of the same structure IN THE
   SAME ORIENTATION, redrawn on every frame of a drag. It is what saves drawing
   a 3D-looking structure by hand in ChemSketch: no re-layout, stereo shown by
   wedges and hashes chosen from how far each bond leaves the screen.

   Two halves, on purpose:

     RDKit (in the user's own environment, orcamgr/draw2d/worker.py) answers
     the question that needs chemistry -- which bonds are double, which atoms
     are charged, which are stereocentres -- ONCE per structure.

     Everything that changes as the structure turns -- the projection, the
     out-of-plane stereo rule, the SVG -- is here, as pure functions
     (NB.draw2d.core, unit-tested by tests/web/draw2d.test.js with no DOM),
     so a drag never waits on a subprocess.

   The rule, in full:
     · view space is mol.js's own: v = R·(p − c), x right, y UP, z TOWARD the
       viewer (mol.js draws X = ox + x·s, Y = oy − y·s, and magnifies larger
       z) -- so the drawing is the screen, not its mirror image;
     · each drawn single bond makes an angle θ = asin(|Δz| / L) with the
       screen plane (L its 3D length); θ ≤ the threshold (25° by default) is a
       plain line, otherwise a wedge when the far end comes toward the viewer
       and a hash when it goes away;
     · the narrow end sits on the better stereo anchor: a stereocentre first,
       then a heavy atom over a hydrogen, then the atom with more heavy-atom
       neighbours -- and the bond is written anchor-first, which is what a MOL
       file's wedge means;
     · hydrogens are hidden except on stereocentres and on heteroatoms (or
       all of them, when asked); carbon is an unlabelled vertex unless it
       stands alone or carries a charge.

   The SVG is drawn here rather than by RDKit's MolDraw2DSVG: a drag redraws it
   sixty times a second, and a round trip to a Python subprocess per frame
   would make "real time" a slideshow. Save MOL sends the coordinates and
   wedge flags this file just drew to the backend's writer
   (orcamgr/draw2d/molfile.py), so the file and the picture are one
   projection, not two.
   ============================================================ */

(function () {

  // ---------------------------------------------------------------- the pure core

  /** Default out-of-plane threshold, degrees. */
  const THRESHOLD = 25;
  /** px per Å in the SVG's own units: a 1.5 Å C–C bond is 36 px. */
  const PX = 24;
  /** A bond at a stereocentre leaving the screen by more than this points
   *  almost straight at (or away from) the viewer: its far atom is drawn
   *  nearly ON the centre, and a reader -- or a program reading the wedges
   *  back -- can then take the centre for its mirror image. Measured: over 60
   *  random views of L-alanine read back by RDKit, the one misread had its
   *  methyl at 80° (drawn 0.27 Å long). The panel says so rather than hiding
   *  it; turning the structure a little resolves it. */
  const END_ON = 72;

  /** Atoms rotated into view space: [x, y, z] each, x right, y up, z toward
   *  the viewer -- the same matrix and centre mol.js projects with.
   *  @param {{x: number, y: number, z: number}[]} atoms
   *  @param {number[]} R row-major 3×3 @param {number[]} c centre [cx, cy, cz]
   *  @returns {number[][]} */
  function viewCoords(atoms, R, c) {
    return atoms.map(a => {
      const x0 = a.x - c[0], y0 = a.y - c[1], z0 = a.z - c[2];
      return [R[0] * x0 + R[1] * y0 + R[2] * z0,
              R[3] * x0 + R[4] * y0 + R[5] * z0,
              R[6] * x0 + R[7] * y0 + R[8] * z0];
    });
  }

  /** Neighbour lists from perceived bonds. @param {number} n
   *  @param {number[][]} bonds [i, j, order] @returns {number[][]} */
  function neighbours(n, bonds) {
    const nb = [];
    for (let i = 0; i < n; i++) nb.push([]);
    bonds.forEach(b => { nb[b[0]].push(b[1]); nb[b[1]].push(b[0]); });
    return nb;
  }

  /** Which atoms the drawing shows: every heavy atom; a hydrogen only on a
   *  stereocentre or a heteroatom (anything not carbon), unless `allH`. A
   *  hydrogen bonded to nothing is shown -- hiding it would hide the atom.
   *  @param {string[]} els @param {number[][]} nb
   *  @param {Set<number>} centres @param {boolean} allH @returns {boolean[]} */
  function visibleAtoms(els, nb, centres, allH) {
    return els.map((el, i) => {
      if (el !== "H" || allH) return true;
      if (!nb[i].length) return true;
      return nb[i].some(j => centres.has(j) || els[j] !== "C");
    });
  }

  /** The angle a bond makes with the screen plane, degrees.
   *  @param {number[]} p view coords @param {number[]} q @param {number} L 3D length */
  function outOfPlane(p, q, L) {
    if (!(L > 0)) return 0;
    return Math.asin(Math.min(1, Math.abs(q[2] - p[2]) / L)) * 180 / Math.PI;
  }

  /** Order a bond anchor-first: the atom the wedge's narrow end sits on.
   *  Stereocentre, then heavy over H, then more heavy neighbours; ties keep
   *  the lower index, so the choice is stable while the structure turns.
   *  @returns {[number, number]} */
  function anchorFirst(i, j, els, nb, centres) {
    const heavy = (k) => nb[k].filter(m => els[m] !== "H").length;
    const key = (k) => [centres.has(k) ? 1 : 0, els[k] !== "H" ? 1 : 0, heavy(k)];
    const a = key(i), b = key(j);
    for (let t = 0; t < 3; t++) {
      if (a[t] !== b[t]) return a[t] > b[t] ? [i, j] : [j, i];
    }
    return i < j ? [i, j] : [j, i];
  }

  /** The stereo flag for one bond seen from here: 0 plain, 1 wedge (the far
   *  end comes toward the viewer), 6 hash (it goes away) -- MOL's own codes.
   *  @param {number[]} pa anchor view coords @param {number[]} pb far end
   *  @param {number} L @param {number} threshold degrees */
  function stereoFlag(pa, pb, L, threshold) {
    if (outOfPlane(pa, pb, L) <= threshold) return 0;
    return pb[2] > pa[2] ? 1 : 6;
  }

  /** The drawing for one orientation.
   *  @param {{el: string, x: number, y: number, z: number}[]} atoms the stage's atoms
   *  @param {{bonds: number[][], charges?: number[], radicals?: number[],
   *           stereocentres?: number[]}} per RDKit's perception
   *  @param {{R: number[], c: number[]}} view mol.js's matrix and centre
   *  @param {{threshold?: number, allH?: boolean}} [opts]
   *  @returns {{atoms: any[], bonds: any[], radius: number, endOn: number}}
   *    atoms: the shown atoms, in a new order ({src, el, x, y, z, charge,
   *    radical, label}); bonds index into that list ({a, b, order, stereo,
   *    theta}), anchor-first when stereo is set; radius: the bounding radius
   *    of the shown atoms about the centre, which does not change as the
   *    structure turns (the preview's frame); endOn: how many bonds at a
   *    stereocentre point nearly straight at the viewer (END_ON). */
  function layout(atoms, per, view, opts) {
    const o = opts || {};
    const threshold = o.threshold != null ? o.threshold : THRESHOLD;
    const n = atoms.length;
    const els = atoms.map(a => a.el);
    const bonds = (per.bonds || []).filter(b => b[0] >= 0 && b[1] >= 0 && b[0] < n
                                               && b[1] < n && b[0] !== b[1]);
    const nb = neighbours(n, bonds);
    const centres = new Set(per.stereocentres || []);
    const vis = visibleAtoms(els, nb, centres, !!o.allH);
    const v = viewCoords(atoms, view.R, view.c);
    const map = new Array(n).fill(-1);
    const out = [];
    let radius = 0;
    atoms.forEach((a, i) => {
      if (!vis[i]) return;
      map[i] = out.length;
      const p = v[i];
      radius = Math.max(radius, Math.hypot(p[0], p[1], p[2]));
      out.push({ src: i, el: a.el, x: p[0], y: p[1], z: p[2],
                 charge: (per.charges && per.charges[i]) || 0,
                 radical: (per.radicals && per.radicals[i]) || 0, label: "" });
    });
    const ob = [];
    const deg = new Array(n).fill(0);
    let endOn = 0;
    bonds.forEach(b => {
      const i = b[0], j = b[1];
      if (!vis[i] || !vis[j]) return;
      deg[i]++; deg[j]++;
      const A = atoms[i], B = atoms[j];
      const L = Math.hypot(B.x - A.x, B.y - A.y, B.z - A.z);
      const order = b[2] === 2 || b[2] === 3 ? b[2] : 1;
      let s = 0, a = i, c = j;
      if (order === 1) {
        [a, c] = anchorFirst(i, j, els, nb, centres);
        s = stereoFlag(v[a], v[c], L, threshold);
        if (!s) { a = i; c = j; }
      }
      const theta = outOfPlane(v[i], v[j], L);
      if (theta > END_ON && (centres.has(i) || centres.has(j))) endOn++;
      ob.push({ a: map[a], b: map[c], order: order, stereo: s, theta: theta });
    });
    // labels: heteroatoms always; carbon only alone, charged or a radical;
    // a labelled atom counts the hydrogens the drawing hides, as ChemDraw does
    out.forEach(at => {
      const i = at.src;
      const hidden = nb[i].filter(j => !vis[j] && els[j] === "H").length;
      const lone = deg[i] === 0;
      if (at.el !== "C" || lone || at.charge || at.radical) {
        at.label = at.el + (hidden ? "H" + (hidden > 1 ? hidden : "") : "");
      }
    });
    return { atoms: out, bonds: ob, radius: radius, endOn: endOn };
  }

  /** The drawing as the MOL writer's input (orcamgr/draw2d/molfile.py):
   *  coordinates in Å, y up, exactly as drawn. @param {any} lay layout()
   *  @param {string} title */
  function molInput(lay, title) {
    return {
      title: title,
      atoms: lay.atoms.map(a => ({ el: a.el, x: +a.x.toFixed(4), y: +a.y.toFixed(4),
                                   charge: a.charge, radical: a.radical })),
      bonds: lay.bonds.map(b => ({ a: b.a, b: b.b, order: b.order, stereo: b.stereo })),
    };
  }

  /** @param {string} s */
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  /** @param {number} v */
  const f1 = (v) => (Math.round(v * 10) / 10).toString();

  /** A charge as a superscript: +, 2+, −, 3−. @param {number} c */
  function chargeText(c) {
    if (!c) return "";
    const m = Math.abs(c);
    return (m > 1 ? String(m) : "") + (c > 0 ? "+" : "−");
  }

  /** The SVG for a drawing. `frame` "sphere" keeps a frame that does not move
   *  while the structure turns (the live preview: the drawing must not jump in
   *  scale on every frame); "tight" crops to the ink (what is saved).
   *  @param {any} lay layout()
   *  @param {{frame?: string, background?: string, ink?: string}} [opts]
   *  @returns {string} */
  function toSvg(lay, opts) {
    const o = opts || {};
    const ink = o.ink || "#111111";
    const font = 13;
    const pts = lay.atoms.map(a => [a.x * PX, -a.y * PX]);
    // how far a bond stops short of a label: half its width, roughly
    const clip = lay.atoms.map(a => a.label
      ? Math.max(7, 0.36 * font * (a.label.length + (a.charge ? 0.6 : 0))) : 0);
    const parts = [];
    /** @param {number} k @param {number} toward @returns {number[]} */
    const end = (k, toward) => {
      const p = pts[k], q = pts[toward];
      const d = clip[k];
      if (!d) return p;
      const L = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1;
      const t = Math.min(0.45, d / L);
      return [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
    };
    /** @param {number[]} p @param {number[]} q @param {number} [w] */
    const line = (p, q, w) => parts.push(
      `<line x1="${f1(p[0])}" y1="${f1(p[1])}" x2="${f1(q[0])}" y2="${f1(q[1])}"`
      + `${w ? ` stroke-width="${w}"` : ""}/>`);
    const nb = lay.atoms.map(() => []);
    lay.bonds.forEach(b => { nb[b.a].push(b.b); nb[b.b].push(b.a); });

    lay.bonds.forEach(b => {
      const P = end(b.a, b.b), Q = end(b.b, b.a);
      const dx = Q[0] - P[0], dy = Q[1] - P[1];
      const L = Math.hypot(dx, dy);
      if (L < 0.5) return;                       // seen end-on: nothing to draw
      const nx = -dy / L, ny = dx / L;            // unit normal (screen px)
      if (b.stereo === 1) {
        const w = 3.2;
        parts.push(`<polygon points="${f1(P[0])},${f1(P[1])} ${f1(Q[0] + nx * w)},${
          f1(Q[1] + ny * w)} ${f1(Q[0] - nx * w)},${f1(Q[1] - ny * w)}" fill="${ink}" stroke="none"/>`);
        return;
      }
      if (b.stereo === 6) {
        const steps = Math.max(3, Math.round(L / 3.2));
        for (let k = 1; k <= steps; k++) {
          const t = k / steps, w = 0.4 + 2.9 * t;
          const cx = P[0] + dx * t, cy = P[1] + dy * t;
          line([cx + nx * w, cy + ny * w], [cx - nx * w, cy - ny * w], 1.1);
        }
        return;
      }
      if (b.order === 1) { line(P, Q); return; }
      const gap = Math.max(3.2, 0.16 * Math.hypot(pts[b.b][0] - pts[b.a][0],
                                                   pts[b.b][1] - pts[b.a][1]));
      if (b.order === 3) {
        line(P, Q);
        line([P[0] + nx * gap, P[1] + ny * gap], [Q[0] + nx * gap, Q[1] + ny * gap]);
        line([P[0] - nx * gap, P[1] - ny * gap], [Q[0] - nx * gap, Q[1] - ny * gap]);
        return;
      }
      // a double bond: the second line goes to the side the neighbours are on
      // (inside a ring), shortened at unlabelled ends; with nothing to lean
      // toward (CO2, a terminal C=O drawn alone) the pair is centred
      let side = 0;
      [[b.a, b.b], [b.b, b.a]].forEach(([k, other]) => nb[k].forEach(m => {
        if (m === other) return;
        const vx = pts[m][0] - pts[k][0], vy = pts[m][1] - pts[k][1];
        side += Math.sign(vx * nx + vy * ny);
      }));
      if (!side) {
        const h = gap / 2;
        line([P[0] + nx * h, P[1] + ny * h], [Q[0] + nx * h, Q[1] + ny * h]);
        line([P[0] - nx * h, P[1] - ny * h], [Q[0] - nx * h, Q[1] - ny * h]);
        return;
      }
      const sg = side > 0 ? 1 : -1;
      line(P, Q);
      const ta = clip[b.a] ? 0 : 0.14, tb = clip[b.b] ? 0 : 0.14;
      line([P[0] + dx * ta + nx * gap * sg, P[1] + dy * ta + ny * gap * sg],
           [Q[0] - dx * tb + nx * gap * sg, Q[1] - dy * tb + ny * gap * sg]);
    });

    lay.atoms.forEach((a, k) => {
      if (!a.label) return;
      const p = pts[k];
      const sup = chargeText(a.charge) + (a.radical ? "•" : "");
      parts.push(`<text x="${f1(p[0])}" y="${f1(p[1])}">${esc(a.label)}${sup
        ? `<tspan dy="-5" font-size="${font - 3}">${esc(sup)}</tspan>` : ""}</text>`);
    });

    // the frame
    let x0, y0, x1, y1;
    if (o.frame === "sphere") {
      const r = Math.max(lay.radius, 1) * PX + 16;
      x0 = -r; y0 = -r; x1 = r; y1 = r;
    } else {
      x0 = Infinity; y0 = Infinity; x1 = -Infinity; y1 = -Infinity;
      pts.forEach((p, k) => {
        const m = clip[k] || 4;
        x0 = Math.min(x0, p[0] - m); y0 = Math.min(y0, p[1] - m);
        x1 = Math.max(x1, p[0] + m); y1 = Math.max(y1, p[1] + m);
      });
      if (!isFinite(x0)) { x0 = -10; y0 = -10; x1 = 10; y1 = 10; }
      x0 -= 12; y0 -= 12; x1 += 12; y1 += 12;
    }
    const w = x1 - x0, h = y1 - y0;
    const bg = o.background
      ? `<rect x="${f1(x0)}" y="${f1(y0)}" width="${f1(w)}" height="${f1(h)}" fill="${o.background}"/>` : "";
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${f1(x0)} ${f1(y0)} ${f1(w)} ${f1(h)}"`
      + ` width="${Math.round(w)}" height="${Math.round(h)}">${bg}`
      + `<g stroke="${ink}" stroke-width="1.5" stroke-linecap="round" fill="none">`
      + parts.filter(s => s.indexOf("<text") < 0).join("") + `</g>`
      + `<g fill="${ink}" font-family="Arial, Helvetica, sans-serif" font-size="${font}"`
      + ` text-anchor="middle" dominant-baseline="central">`
      + parts.filter(s => s.indexOf("<text") === 0).join("") + `</g></svg>`;
  }

  const core = { THRESHOLD: THRESHOLD, END_ON: END_ON, viewCoords: viewCoords, neighbours: neighbours,
                 visibleAtoms: visibleAtoms, outOfPlane: outOfPlane,
                 anchorFirst: anchorFirst, stereoFlag: stereoFlag, layout: layout,
                 molInput: molInput, toSvg: toSvg, chargeText: chargeText };

  // ---------------------------------------------------------------- the panel

  /** The add-on's last known state (Draw2dStatusPayload), shared by every
   *  pane so a second mount does not start from "checking" again. @type {any} */
  let _status = null;
  /** RDKit's answer per structure (and charge), so a frame revisited -- or
   *  the same structure re-mounted by a report re-render -- draws at once.
   *  @type {Map<string, any>} */
  const _cache = new Map();
  /** The user's choices, kept for the session across panes. */
  const _prefs = { threshold: THRESHOLD, allH: false };

  /** @param {any[]} atoms @param {number|null} charge */
  function keyOf(atoms, charge) {
    return (charge == null ? "auto" : String(charge)) + "|" + atoms.map(a =>
      a.el + a.x.toFixed(4) + "," + a.y.toFixed(4) + "," + a.z.toFixed(4)).join(";");
  }

  /** Mount the 2D half into `host`, following `stage` (a MOL.mount stage).
   *  @param {HTMLElement} host
   *  @param {any} stage
   *  @param {{source: string, name: string}} ctx
   *  @returns {{destroy: () => void, refresh: () => void}} */
  function mount(host, stage, ctx) {
    let alive = true;
    let raf = 0;
    /** @type {any} */ let per = null;      // perception for the atoms on show
    /** @type {any[]|null} */ let perAtoms = null;
    /** @type {number|null} */ let charge = null;   // null = the calculation's
    let lastKey = "";
    let lastSvg = "";
    /** @type {any} */ let lastLay = null;
    let pollTimer = 0;

    host.innerHTML = `
      <div class="d2paper" data-d="paper" role="img" aria-label="2D drawing"></div>
      <div class="d2bar" data-d="bar">
        <label class="d2ctl" title="A bond leaving the screen by more than this is drawn as a wedge (toward you) or a hash (away)">Wedge above
          <input type="range" min="5" max="60" step="1" data-d="thr"
            value="${_prefs.threshold}" aria-label="Out-of-plane angle above which a bond is a wedge or a hash, degrees">
          <output class="d2val" data-d="thrv">${_prefs.threshold}°</output></label>
        <label class="d2chk"><input type="checkbox" data-d="allh"${_prefs.allH ? " checked" : ""}> All H</label>
        <label class="d2ctl" title="The total charge RDKit assigns bond orders for">Charge
          <input type="number" class="mono d2num" step="1" data-d="chg" aria-label="Total charge"></label>
        <span class="sp"></span>
        <button class="btn btn-sm" type="button" data-d="copy">Copy image</button>
        <button class="btn btn-sm btn-ghost" type="button" data-d="svg">Save SVG</button>
        <button class="btn btn-sm btn-ghost" type="button" data-d="png">Save PNG</button>
        <button class="btn btn-sm btn-ghost" type="button" data-d="mol">Save MOL</button>
      </div>
      <p class="d2note" data-d="note"></p>`;
    const q = (k) => /** @type {any} */ (host.querySelector(`[data-d="${k}"]`));

    /** A message where the drawing goes, on the card rather than the paper.
     *  @param {string} html */
    function paperMsg(html) {
      q("paper").classList.add("need");
      q("paper").innerHTML = html;
      lastSvg = "";
    }

    /** @param {boolean} on */
    function lockBar(on) {
      q("bar").querySelectorAll("input,button").forEach(
        (/** @type {any} */ el) => { el.disabled = on; });
    }

    /** What is needed, in place of the drawing (D41: the tile stays, says
     *  what is missing, and links to where it is set up). */
    function showNeed() {
      const st = _status;
      const checking = !st || st.state === "checking";
      const msg = checking ? "Checking the RDKit environment…"
        : st.state === "error"
          ? `The 2D-drawing environment does not work: ${st.message}`
          : "2D drawing is an add-on: it needs RDKit, in a Python environment of its own "
            + "(ORCAdesk can create one — about 50 MB from PyPI).";
      paperMsg(`<div class="d2need"><p>${NB.esc(msg)}</p>${checking ? ""
        : `<button class="btn btn-sm" type="button" data-d="settings">Set up in Settings</button>`}</div>`);
      const b = q("settings");
      if (b) b.addEventListener("click", () => NB.settingsView.open());
      q("note").textContent = "";
      lockBar(true);
      lastKey = "";
    }

    async function checkStatus() {
      const st = await NB.call("get_draw2d_status");
      if (!alive) return;
      if (st && st.state) _status = st;
      if (!_status || _status.state !== "ready") {
        showNeed();
        clearTimeout(pollTimer);
        // set up in Settings meanwhile: notice it without a re-render
        pollTimer = window.setTimeout(checkStatus, 2000);
        return;
      }
      lockBar(false);
      follow();
    }

    /** The atoms on the stage changed (a frame, a tile): perceive them once. */
    async function perceive(atoms) {
      const key = keyOf(atoms, charge);
      lastKey = key;
      const hit = _cache.get(key);
      if (hit) { per = hit; perAtoms = atoms; paintNote(); schedule(); return; }
      per = null; perAtoms = atoms;
      paperMsg(`<div class="d2need"><p>Perceiving bonds with RDKit…</p></div>`);
      q("note").textContent = "";
      let r = await NB.call("draw2d_perceive", JSON.stringify({
        source: ctx.source, charge: charge,
        atoms: atoms.map(a => ({ el: a.el, x: a.x, y: a.y, z: a.z })) }));
      const job = r && r.job;
      while (alive && r && r.state === "running" && r.job === job && lastKey === key) {
        await new Promise(res => setTimeout(res, 200));
        r = await NB.call("get_draw2d_job");
      }
      if (!alive || lastKey !== key) return;      // the structure moved on
      if (!r || r.job !== job || r.state !== "done") {
        paperMsg(`<div class="d2need"><p>${NB.esc(
          (r && r.error) || "Bond perception failed.")}</p></div>`);
        q("note").textContent = "";
        return;
      }
      _cache.set(key, r);
      if (_cache.size > 64) _cache.delete(_cache.keys().next().value);
      per = r; perAtoms = atoms;
      paintNote();
      schedule();
    }

    function paintNote() {
      if (!per) return;
      const chg = q("chg");
      if (document.activeElement !== chg) chg.value = String(per.charge);
      const from = { calculation: "the calculation's charge",
                     output: "the charge in its output", request: "the charge set here",
                     default: "charge 0 (not known for this file)" }[per.charge_from] || "";
      const how = per.method === "bonds"
        ? `Bond orders by RDKit ${per.rdkit}, for ${from}.`
        : `Connectivity only — every bond single.`;
      const endOn = lastLay && lastLay.endOn
        ? "A bond at a stereocentre points almost straight at you — turn the structure a "
          + "little so the wedges read unambiguously." : "";
      q("note").textContent = [how, per.note || "", endOn].filter(Boolean).join(" ");
      q("note").classList.toggle("warn", per.method !== "bonds" || !!per.note || !!endOn);
    }

    /** Draw on the next animation frame -- a drag repaints the 3D stage on
     *  every pointer move, and the drawing needs at most one per frame. */
    function schedule() {
      if (raf || !alive) return;
      raf = requestAnimationFrame(() => { raf = 0; draw(); });
    }

    function draw() {
      if (!alive || !per || !perAtoms) return;
      const v = stage.view();
      if (!v || v.atoms !== perAtoms) { follow(); return; }
      const wasEndOn = !!(lastLay && lastLay.endOn);
      lastLay = layout(perAtoms, per, { R: v.R, c: [v.cx, v.cy, v.cz] },
                       { threshold: _prefs.threshold, allH: _prefs.allH });
      if (!!lastLay.endOn !== wasEndOn) paintNote();
      const svg = toSvg(lastLay, { frame: "sphere" });
      if (svg === lastSvg) return;
      lastSvg = svg;
      q("paper").classList.remove("need");
      q("paper").innerHTML = svg;
    }

    /** Called after every paint of the 3D stage. */
    function follow() {
      if (!alive || !_status || _status.state !== "ready") return;
      const v = stage.view();
      if (!v || !v.atoms || !v.atoms.length) return;
      if (v.atoms !== perAtoms) { perceive(v.atoms); return; }
      schedule();
    }

    q("thr").addEventListener("input", () => {
      _prefs.threshold = Number(q("thr").value);
      q("thrv").textContent = _prefs.threshold + "°";
      lastSvg = ""; schedule();
    });
    q("allh").addEventListener("change", () => {
      _prefs.allH = !!q("allh").checked;
      lastSvg = ""; schedule();
    });
    q("chg").addEventListener("change", () => {
      const c = parseInt(q("chg").value, 10);
      if (!isFinite(c)) { if (per) q("chg").value = String(per.charge); return; }
      if (per && c === per.charge) return;
      charge = c;
      perAtoms = null;          // re-perceive at the new charge
      follow();
    });

    /** The drawing as it would be saved: cropped, on white. */
    function exportSvg() {
      return lastLay ? toSvg(lastLay, { frame: "tight", background: "#ffffff" }) : "";
    }
    /** @param {string} svg @returns {Promise<string>} */
    function toPng(svg) {
      return new Promise(res => {
        const img = new Image();
        const scale = 3;
        img.onload = () => {
          const cv = document.createElement("canvas");
          cv.width = Math.max(1, Math.round(img.width * scale));
          cv.height = Math.max(1, Math.round(img.height * scale));
          const g = cv.getContext("2d");
          if (!g) { res(""); return; }
          g.drawImage(img, 0, 0, cv.width, cv.height);
          try { res(cv.toDataURL("image/png")); } catch (e) { res(""); }
        };
        img.onerror = () => res("");
        img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
      });
    }
    /** @param {string} fmt */
    async function save(fmt) {
      if (!lastLay) { NB.fail("Nothing drawn yet."); return; }
      const svg = exportSvg();
      /** @type {any} */
      const payload = { format: fmt, source: ctx.source, name: ctx.name };
      if (fmt === "svg") payload.svg = svg;
      else if (fmt === "png") {
        payload.png = await toPng(svg);
        if (!payload.png) { NB.fail("The drawing could not be turned into a PNG."); return; }
      } else payload.drawing = molInput(lastLay, ctx.name);
      const r = await NB.call("draw2d_save", JSON.stringify(payload));
      if (r && r.cancelled) return;
      if (!r || !r.ok) { NB.fail((r && r.error) || "Could not save the drawing."); return; }
      NB.toast("Saved " + r.path, "ok");
    }
    q("copy").addEventListener("click", async () => {
      if (!lastLay) { NB.fail("Nothing drawn yet."); return; }
      const svg = exportSvg();
      const png = await toPng(svg);
      if (!png) { NB.fail("The drawing could not be turned into an image."); return; }
      const r = await NB.call("copy_draw2d_image", png, svg);
      if (!r || !r.ok) { NB.fail((r && r.error) || "The clipboard refused the copy."); return; }
      NB.toast("Copied the drawing.", "ok");
    });
    q("svg").addEventListener("click", () => save("svg"));
    q("png").addEventListener("click", () => save("png"));
    q("mol").addEventListener("click", () => save("mol"));

    stage.onView(follow);
    if (_status && _status.state === "ready") { lockBar(false); follow(); }
    else showNeed();
    checkStatus();

    return {
      destroy() {
        alive = false;
        if (raf) cancelAnimationFrame(raf);
        clearTimeout(pollTimer);
        stage.onView(null);
        host.innerHTML = "";
      },
      refresh() { lastSvg = ""; follow(); },
    };
  }

  NB.draw2d = { core: core, mount: mount };
})();
