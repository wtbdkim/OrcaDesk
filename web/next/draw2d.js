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
   *  @param {{threshold?: number, allH?: boolean, lobes?: any[]|null,
   *           lobeMin?: number}} [opts] lobes: MoLobeAtomPayload per stage
   *    atom (the orbital overlay, lobeShapes), drawn on hidden H too -- a σ
   *    orbital's share on a hydrogen is part of the orbital
   *  @returns {{atoms: any[], bonds: any[], radius: number, endOn: number,
   *             lobes: {shapes: any[], reach: number}|null}}
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
    const lobes = o.lobes && o.lobes.length
      ? lobeShapes(atoms, o.lobes, view, { min: o.lobeMin }) : null;
    return { atoms: out, bonds: ob, radius: radius, endOn: endOn, lobes: lobes };
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

  // ------------------------------------------------ the orbital overlay (pure)
  //
  // A chosen MO drawn the textbook way, under the bonds: on every atom with
  // at least LOBE_MIN of the orbital (orcamgr/nbo/molobes.py: Löwdin shares),
  // a pair of lobes along the atom's p vector -- the + side in --orb-pos, the
  // other in --orb-neg -- or, where s outweighs p, one circle in its sign's
  // colour; a d-block atom whose share is mostly d gets a dashed ring (its
  // shape is not a p pair, and drawing one would be a claim). On a main-group
  // atom the d functions are polarisation, and in a small share they can
  // outweigh s and p (measured: 2-5 % carbons beside a π node, d ~70 % of
  // it) -- that is a neighbour's tail, not d chemistry, so such an atom is
  // drawn by its s and p like any other. Sizes go as √share, scaled per
  // orbital so its largest share gets LOBE_LEN (a lobe's AREA goes as the
  // share; one constant for every orbital would leave a delocalised one's
  // 10 % atoms as specks beside a lone pair's 95 %). Each lobe is a prolate spheroid (semi-axes
  // a = L/2 along p, b = LOBE_WIDTH·L/2 across), centred L/2 out from the
  // atom, projected orthographically into the same view as the bonds: its
  // outline is an ellipse with semi-axes b and √(a²·t² + b²·u_z²), t the
  // in-screen length of the unit axis u. Edge-on the two lobes meet at the
  // atom; face-on (u along the view axis) both shrink to circles of radius b
  // on the atom, and the nearer one is drawn last -- a π orbital seen from
  // above reads as one disc in the front lobe's colour. Every lobe of every
  // atom is drawn back to front by its centre's depth.

  /** Lobe length (atom to tip), Å, of the orbital's largest share; the rest
   *  go as √(share / largest). About a bond length, as textbooks draw them. */
  const LOBE_LEN = 1.3;
  /** A lobe's width over its length. */
  const LOBE_WIDTH = 0.62;
  /** An s circle's (or a d ring's) radius at the largest share, Å. */
  const S_RADIUS = 0.7;
  /** Elements whose d shell is valence (groups 3-12): only these get the
   *  "mostly d" ring. */
  const D_BLOCK = new Set(("Sc Ti V Cr Mn Fe Co Ni Cu Zn Y Zr Nb Mo Tc Ru Rh Pd Ag Cd "
    + "La Hf Ta W Re Os Ir Pt Au Hg Ac Rf Db Sg Bh Hs Mt Ds Rg Cn").split(" "));
  /** Default share below which an atom gets no lobe (2 %). */
  const LOBE_MIN = 0.02;

  /** The overlay's shapes for one orientation, in view-space Å, back to front.
   *  @param {{el?: string, x: number, y: number, z: number}[]} atoms the stage's atoms
   *  @param {any[]} lobes MoLobeAtomPayload per atom, same order
   *  @param {{R: number[], c: number[]}} view
   *  @param {{min?: number}} [opts]
   *  @returns {{shapes: any[], reach: number}} shapes: {kind: "lobe"|"s"|"d",
   *    atom, sign (+1 / −1, 0 for "d"), x, y, z, rx, ry, angle (degrees,
   *    counter-clockwise from +x, y up)}; reach: the farthest any shape gets
   *    from the centre, which does not change as the structure turns. */
  function lobeShapes(atoms, lobes, view, opts) {
    const o = opts || {};
    const min = o.min != null ? o.min : LOBE_MIN;
    const R = view.R;
    const v = viewCoords(atoms, R, view.c);
    /** @type {any[]} */
    const shapes = [];
    let reach = 0;
    const drawn = (/** @type {any} */ lb, /** @type {number} */ i) =>
      !!lb && i < atoms.length && lb.share >= min;
    const top = (lobes || []).reduce((m, lb, i) => drawn(lb, i) ? Math.max(m, lb.share) : m, 0);
    (lobes || []).forEach((lb, i) => {
      if (!drawn(lb, i)) return;
      const p = v[i];
      const r0 = Math.hypot(p[0], p[1], p[2]);
      const root = Math.sqrt(lb.share / top);
      const pv = lb.p || [0, 0, 0];
      const pn = Math.hypot(pv[0], pv[1], pv[2]);
      const round = (/** @type {string} */ kind, /** @type {number} */ sign) => {
        const r = S_RADIUS * root;
        shapes.push({ kind: kind, atom: i, sign: sign, x: p[0], y: p[1], z: p[2],
                      rx: r, ry: r, angle: 0 });
        reach = Math.max(reach, r0 + r);
      };
      if (D_BLOCK.has(String(atoms[i].el || ""))
          && (lb.d_share || 0) > (lb.s_share || 0) + (lb.p_share || 0)) { round("d", 0); return; }
      if ((lb.s_share || 0) >= (lb.p_share || 0) || !(pn > 1e-9)) {
        round("s", lb.s < 0 ? -1 : 1);
        return;
      }
      const u0 = pv[0] / pn, u1 = pv[1] / pn, u2 = pv[2] / pn;
      const u = [R[0] * u0 + R[1] * u1 + R[2] * u2,
                 R[3] * u0 + R[4] * u1 + R[5] * u2,
                 R[6] * u0 + R[7] * u1 + R[8] * u2];
      const L = LOBE_LEN * root, a = L / 2, b = LOBE_WIDTH * L / 2;
      const t = Math.hypot(u[0], u[1]);
      const A = Math.sqrt(a * a * t * t + b * b * u[2] * u[2]);
      const angle = t > 1e-9 ? Math.atan2(u[1], u[0]) * 180 / Math.PI : 0;
      [1, -1].forEach(sg => shapes.push({
        kind: "lobe", atom: i, sign: sg,
        x: p[0] + sg * u[0] * a, y: p[1] + sg * u[1] * a, z: p[2] + sg * u[2] * a,
        rx: Math.max(A, b), ry: b, angle: angle }));
      reach = Math.max(reach, r0 + L);
    });
    shapes.sort((s, t) => s.z - t.z);
    return { shapes: shapes, reach: reach };
  }

  /** Is the structure on the stage the one the wavefunction belongs to? Same
   *  atom count, elements and order, every atom within `tol` Å.
   *  @param {{el: string, x: number, y: number, z: number}[]} atoms
   *  @param {string[]} elements @param {number[][]} coords Å
   *  @param {number} [tol] */
  function sameStructure(atoms, elements, coords, tol) {
    const t = tol != null ? tol : 0.05;
    if (!atoms || !elements || !coords || atoms.length !== elements.length
        || atoms.length !== coords.length) return false;
    return atoms.every((a, i) => {
      const c = coords[i];
      return String(a.el).toLowerCase() === String(elements[i]).toLowerCase()
        && Math.hypot(a.x - c[0], a.y - c[1], a.z - c[2]) <= t;
    });
  }

  /** A colour mixed toward white: the lobes' fill, so a front lobe HIDES the
   *  one behind it (a translucent fill would mix the two phases into a third
   *  colour face-on) while still reading as a light wash on the white paper.
   *  @param {string} hex "#rrggbb" or "#rgb" @param {number} f colour fraction */
  function tint(hex, f) {
    let h = String(hex || "").trim().replace(/^#/, "");
    if (/^[0-9a-f]{3}$/i.test(h)) h = h.split("").map(ch => ch + ch).join("");
    if (!/^[0-9a-f]{6}$/i.test(h)) return hex;
    const mix = (/** @type {number} */ k) => {
      const c = parseInt(h.slice(k, k + 2), 16);
      return Math.round(255 - (255 - c) * f).toString(16).padStart(2, "0");
    };
    return "#" + mix(0) + mix(2) + mix(4);
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
   *  The orbital overlay (lay.lobes), when there is one, goes first -- under
   *  the bonds -- in `pos` / `neg` (the --orb-pos / --orb-neg tokens).
   *  @param {{frame?: string, background?: string, ink?: string,
   *           pos?: string, neg?: string}} [opts]
   *  @returns {string} */
  function toSvg(lay, opts) {
    const o = opts || {};
    const ink = o.ink || "#111111";
    const shapes = (lay.lobes && lay.lobes.shapes) || [];
    let under = "";
    if (shapes.length) {
      /** @type {Record<string, string>} */
      const col = { "1": o.pos || "#4f86f7", "-1": o.neg || "#f2555a" };
      under = `<g stroke-width="1.2" data-layer="orbital">` + shapes.map(sh => {
        const X = sh.x * PX, Y = -sh.y * PX;
        if (sh.kind === "d") {
          return `<circle cx="${f1(X)}" cy="${f1(Y)}" r="${f1(sh.rx * PX)}" fill="none"`
            + ` stroke="#8a8f98" stroke-dasharray="3 2"/>`;
        }
        const c = col[String(sh.sign)];
        const paint = ` fill="${tint(c, 0.42)}" stroke="${c}" data-sign="${sh.sign > 0 ? "+" : "-"}"`;
        if (sh.kind === "s") return `<circle cx="${f1(X)}" cy="${f1(Y)}" r="${f1(sh.rx * PX)}"${paint}/>`;
        // screen y points down, so a counter-clockwise view angle is negative
        return `<ellipse cx="${f1(X)}" cy="${f1(Y)}" rx="${f1(sh.rx * PX)}" ry="${f1(sh.ry * PX)}"`
          + ` transform="rotate(${f1(-sh.angle)} ${f1(X)} ${f1(Y)})"${paint}/>`;
      }).join("") + `</g>`;
    }
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
      const r = Math.max(lay.radius, lay.lobes ? lay.lobes.reach : 0, 1) * PX + 16;
      x0 = -r; y0 = -r; x1 = r; y1 = r;
    } else {
      x0 = Infinity; y0 = Infinity; x1 = -Infinity; y1 = -Infinity;
      pts.forEach((p, k) => {
        const m = clip[k] || 4;
        x0 = Math.min(x0, p[0] - m); y0 = Math.min(y0, p[1] - m);
        x1 = Math.max(x1, p[0] + m); y1 = Math.max(y1, p[1] + m);
      });
      shapes.forEach(sh => {
        const X = sh.x * PX, Y = -sh.y * PX, m = Math.max(sh.rx, sh.ry) * PX + 1;
        x0 = Math.min(x0, X - m); y0 = Math.min(y0, Y - m);
        x1 = Math.max(x1, X + m); y1 = Math.max(y1, Y + m);
      });
      if (!isFinite(x0)) { x0 = -10; y0 = -10; x1 = 10; y1 = 10; }
      x0 -= 12; y0 -= 12; x1 += 12; y1 += 12;
    }
    const w = x1 - x0, h = y1 - y0;
    const bg = o.background
      ? `<rect x="${f1(x0)}" y="${f1(y0)}" width="${f1(w)}" height="${f1(h)}" fill="${o.background}"/>` : "";
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${f1(x0)} ${f1(y0)} ${f1(w)} ${f1(h)}"`
      + ` width="${Math.round(w)}" height="${Math.round(h)}">${bg}${under}`
      + `<g stroke="${ink}" stroke-width="1.5" stroke-linecap="round" fill="none">`
      + parts.filter(s => s.indexOf("<text") < 0).join("") + `</g>`
      + `<g fill="${ink}" font-family="Arial, Helvetica, sans-serif" font-size="${font}"`
      + ` text-anchor="middle" dominant-baseline="central">`
      + parts.filter(s => s.indexOf("<text") === 0).join("") + `</g></svg>`;
  }

  const core = { THRESHOLD: THRESHOLD, END_ON: END_ON, viewCoords: viewCoords, neighbours: neighbours,
                 visibleAtoms: visibleAtoms, outOfPlane: outOfPlane,
                 anchorFirst: anchorFirst, stereoFlag: stereoFlag, layout: layout,
                 molInput: molInput, toSvg: toSvg, chargeText: chargeText,
                 LOBE_LEN: LOBE_LEN, LOBE_WIDTH: LOBE_WIDTH, S_RADIUS: S_RADIUS,
                 LOBE_MIN: LOBE_MIN, D_BLOCK: D_BLOCK, lobeShapes: lobeShapes,
                 sameStructure: sameStructure, tint: tint };

  // ---------------------------------------------------------------- the panel

  /** The add-on's last known state (Draw2dStatusPayload), shared by every
   *  pane so a second mount does not start from "checking" again. @type {any} */
  let _status = null;
  /** RDKit's answer per structure (and charge), so a frame revisited -- or
   *  the same structure re-mounted by a report re-render -- draws at once.
   *  @type {Map<string, any>} */
  const _cache = new Map();
  /** The user's choices, kept for the session across panes. lobeMin is in
   *  percent of the orbital (the overlay's share threshold). */
  const _prefs = { threshold: THRESHOLD, allH: false, lobeMin: LOBE_MIN * 100 };
  /** get_mo_lobes answers per result and orbital: fetched once, drawn every
   *  frame from here (the projection is the only per-frame work).
   *  @type {Map<string, any>} */
  const _lobeCache = new Map();

  /** The Orbital select's options: per manifold, the frontier window
   *  (HOMO−4 … LUMO+4) first, then every other orbital -- the names, values
   *  and order the Orbitals tile uses (its picks: {index, operator, short,
   *  label}). @param {any[]} picks @returns {string} */
  function moOptions(picks) {
    const near = /^(α |β )?(HOMO(−[1-4])?|LUMO(\+[1-4])?)$/;
    /** @type {Record<string, any[][]>} */
    const groups = {};
    /** @type {string[]} */
    const order = [];
    (picks || []).forEach(p => {
      const op = String(p.operator || 0);
      if (!groups[op]) { groups[op] = [[], []]; order.push(op); }
      groups[op][near.test(p.short || "") ? 0 : 1].push(p);
    });
    const tag = (/** @type {string} */ op) => order.length > 1 ? (op === "1" ? "β " : "α ") : "";
    const opt = (/** @type {any} */ p) =>
      `<option value="${p.operator || 0}:${p.index}">${esc(p.label || p.short)}</option>`;
    return `<option value="">None</option>` + order.map(op =>
      (groups[op][0].length ? `<optgroup label="${tag(op)}near the frontier">${
        groups[op][0].map(opt).join("")}</optgroup>` : "")
      + (groups[op][1].length ? `<optgroup label="${tag(op)}${groups[op][0].length
        ? "other orbitals" : "orbitals"}">${groups[op][1].map(opt).join("")}</optgroup>` : "")
    ).join("");
  }

  /** @param {any[]} atoms @param {number|null} charge */
  function keyOf(atoms, charge) {
    return (charge == null ? "auto" : String(charge)) + "|" + atoms.map(a =>
      a.el + a.x.toFixed(4) + "," + a.y.toFixed(4) + "," + a.z.toFixed(4)).join(";");
  }

  /** Mount the 2D half into `host`, following `stage` (a MOL.mount stage).
   *  @param {HTMLElement} host
   *  @param {any} stage
   *  @param {{source: string, name: string, orbitals?: any[], wfReason?: string,
   *           onStructure?: () => boolean}} ctx orbitals: the Orbitals tile's
   *    MO picks (the overlay's choices); wfReason: why there are none (no
   *    .gbw); onStructure: whether the stage holds the run's final geometry
   *    -- the only structure its wavefunction belongs to
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
    // the orbital overlay: the pick, its lobes once fetched, and what stops it
    /** @type {{op: number, index: number, label: string, key: string}|null} */
    let mo = null;
    /** @type {any} */ let lobeData = null;
    let lobeErr = "";
    let lobeBusy = false;
    let locked = true;                   // the bar's lock (add-on not ready)
    /** @type {any[]|null} */ let fitAtoms = null;   // atoms lobeData was checked against
    let fits = false;
    const css = getComputedStyle(document.documentElement);
    const colours = { pos: css.getPropertyValue("--orb-pos").trim() || "#4f86f7",
                      neg: css.getPropertyValue("--orb-neg").trim() || "#f2555a" };

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
      <div class="d2bar" data-d="mobar">
        <label class="d2ctl">Orbital
          <select class="d2sel" data-d="mo" aria-label="Molecular orbital drawn as lobes under the bonds">${
            moOptions(ctx.orbitals || [])}</select></label>
        <label class="d2ctl" title="An atom with less of the orbital than this gets no lobe">Lobes from
          <input type="number" class="mono d2num" min="0.5" max="50" step="0.5" data-d="lmin"
            value="${_prefs.lobeMin}" aria-label="Smallest share of the orbital an atom needs for a lobe, percent">%</label>
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
      locked = on;
      syncMo();
    }

    /** Why the orbital overlay cannot be drawn here, or "" when it can.
     *  @returns {string} */
    function moReason() {
      if (!ctx.orbitals || !ctx.orbitals.length) {
        return ctx.wfReason
          || "This result lists no molecular orbitals, so there is none to draw.";
      }
      if (ctx.onStructure && !ctx.onStructure()) {
        return "Orbitals belong to the run's final geometry — open 2D structure from "
          + "the Structure tile to draw one on it.";
      }
      return "";
    }

    /** The Orbital controls' enabled state, after the bar's lock and the
     *  structure on the stage. */
    function syncMo() {
      const why = moReason();
      const sel = q("mo");
      sel.disabled = locked || !!why;
      sel.title = why || "Draw a molecular orbital as lobes under the bonds";
      q("lmin").disabled = locked || !!why || !mo;
    }

    /** The lobes to draw on these atoms, or null (none picked, not here, not
     *  fetched yet, or not this structure). @param {any[]} atoms */
    function activeLobes(atoms) {
      if (!mo || !lobeData || moReason()) return null;
      if (fitAtoms !== atoms) {
        fitAtoms = atoms;
        fits = sameStructure(atoms, lobeData.elements, lobeData.coords, 0.05);
        paintNote();
      }
      return fits ? lobeData.atoms : null;
    }

    /** Fetch one orbital's lobes (once per result and orbital).
     *  @param {number} op 0 alpha, 1 beta @param {number} index */
    async function loadLobes(op, index) {
      const key = mo ? mo.key : "";
      lobeData = null; lobeErr = ""; fitAtoms = null;
      const hit = _lobeCache.get(key);
      if (hit) { lobeData = hit; paintNote(); lastSvg = ""; schedule(); return; }
      lobeBusy = true;
      paintNote(); lastSvg = ""; schedule();
      const spin = op ? "beta" : "alpha";
      let r = await NB.call("get_mo_lobes", ctx.source, spin, index);
      while (alive && mo && mo.key === key && r && r.ok && r.state === "running") {
        await new Promise(res => setTimeout(res, 300));
        r = await NB.call("get_mo_lobes", ctx.source, spin, index);
      }
      if (!alive || !mo || mo.key !== key) return;    // the pick moved on
      lobeBusy = false;
      if (!r || !r.ok || r.state !== "done" || !r.atoms) {
        lobeErr = (r && r.error) || "The orbital could not be read.";
      } else {
        _lobeCache.set(key, r);
        if (_lobeCache.size > 32) _lobeCache.delete(_lobeCache.keys().next().value);
        lobeData = r;
      }
      paintNote(); lastSvg = ""; schedule();
    }

    /** The overlay's line in the note: what is drawn and at what scale, or
     *  why nothing is. @returns {{text: string, warn: boolean}} */
    function lobeNote() {
      if (!mo) return { text: "", warn: false };
      const why = moReason();
      if (why) return { text: why, warn: true };
      if (lobeBusy) return { text: `Reading the wavefunction for ${mo.label}…`, warn: false };
      if (lobeErr) return { text: `${mo.label}: ${lobeErr}`, warn: true };
      if (!lobeData) return { text: "", warn: false };
      if (fitAtoms && !fits) {
        return { text: `${mo.label}: the wavefunction's geometry is not the structure drawn `
          + "here, so its lobes are not drawn on it.", warn: true };
      }
      const min = _prefs.lobeMin / 100;
      const els = lobeData.elements || [];
      const dAtoms = (lobeData.atoms || []).map((/** @type {any} */ a, /** @type {number} */ i) =>
        a.share >= min && D_BLOCK.has(els[i]) && a.d_share > a.s_share + a.p_share
          ? els[i] + (i + 1) : "")
        .filter(Boolean);
      return { text: `${mo.label} (${Number(lobeData.energy_ev).toFixed(2)} eV): lobes on atoms `
        + `holding at least ${_prefs.lobeMin} % of the orbital, sized by √share (the largest `
        + `share's lobes ${LOBE_LEN} Å long); blue and red `
        + "are its two phases (which is which is arbitrary for the orbital as a whole)."
        + (dAtoms.length ? ` Mostly d on ${dAtoms.slice(0, 6).join(", ")}${
          dAtoms.length > 6 ? "…" : ""} (dashed ring).` : "")
        + " Copy / Save SVG / PNG include the lobes; Save MOL leaves them out.", warn: false };
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
      const ln = lobeNote();
      q("note").textContent = [how, per.note || "", endOn, ln.text].filter(Boolean).join(" ");
      q("note").classList.toggle("warn", per.method !== "bonds" || !!per.note || !!endOn
                                         || ln.warn);
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
                       { threshold: _prefs.threshold, allH: _prefs.allH,
                         lobes: activeLobes(perAtoms), lobeMin: _prefs.lobeMin / 100 });
      if (!!lastLay.endOn !== wasEndOn) paintNote();
      const svg = toSvg(lastLay, { frame: "sphere", pos: colours.pos, neg: colours.neg });
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
      if (v.atoms !== perAtoms) { syncMo(); perceive(v.atoms); return; }
      schedule();
    }

    q("mo").addEventListener("change", () => {
      const val = String(q("mo").value || "");
      if (!val) {
        mo = null; lobeData = null; lobeErr = ""; lobeBusy = false;
        syncMo(); paintNote(); lastSvg = ""; schedule();
        return;
      }
      const [op, index] = val.split(":").map(Number);
      const opt = q("mo").selectedOptions[0];
      const label = opt ? String(opt.textContent || "").replace(/\s*\(.*$/, "") : val;
      mo = { op: op, index: index, label: label, key: ctx.source + "|" + val };
      syncMo();
      loadLobes(op, index);
    });
    q("lmin").addEventListener("change", () => {
      const v = Number(q("lmin").value);
      if (!(v > 0) || v > 100) { q("lmin").value = String(_prefs.lobeMin); return; }
      _prefs.lobeMin = v;
      paintNote(); lastSvg = ""; schedule();
    });

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
      return lastLay ? toSvg(lastLay, { frame: "tight", background: "#ffffff",
                                        pos: colours.pos, neg: colours.neg }) : "";
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
