// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — structure rendering.

   Ball-and-stick on a 2D canvas: the thumbnail in every stream cell, and the
   interactive stage in the report's Structure and Visual sections. Deliberately
   not WebGL and not 3Dmol.js — the thumbnails are stills, several on screen at
   once, and a GL context each would cost more than the picture is worth; the
   stage needs picking and measuring, which is easier against a projection this
   file already computes than against a library's scene graph.

   Painter's algorithm (sort by depth, draw back to front) with Jmol CPK
   colours, the same convention every other chemistry tool uses, so a structure
   here is recognisable next to one anywhere else.
   ============================================================ */

/** @type {any} */
var MOL = {};

(function () {

  // Jmol CPK — the subset an ORCA run actually contains, plus a neutral
  // fallback for anything more exotic.
  const CPK = {
    H: "#dcdcdc", He: "#d9ffff", Li: "#cc80ff", Be: "#c2ff00", B: "#ffb5b5",
    C: "#909090", N: "#3050f8", O: "#ff0d0d", F: "#90e050", Ne: "#b3e3f5",
    Na: "#ab5cf2", Mg: "#8aff00", Al: "#bfa6a6", Si: "#f0c8a0", P: "#ff8000",
    S: "#ffff30", Cl: "#1ff01f", Ar: "#80d1e3", K: "#8f40d4", Ca: "#3dff00",
    Ti: "#bfc2c7", Cr: "#8a99c7", Mn: "#9c7ac7", Fe: "#e06633", Co: "#f090a0",
    Ni: "#50d050", Cu: "#c88033", Zn: "#7d80b0", Br: "#a62929", Ru: "#248f8f",
    Pd: "#006985", Ag: "#c0c0c0", I: "#940094", Pt: "#d0d0e0", Au: "#ffd123",
  };
  // Covalent radii in Å — Cordero et al., Dalton Trans. 2008, 2832, the table
  // every chemistry viewer uses. H through Pu, because the previous 24-element
  // subset sent everything else to one fallback, and a fallback is a number
  // about an element nobody looked up.
  //
  // It answers TWO questions and they want different fallbacks, which is why
  // there are two accessors below. What is bonded to what is decided from the
  // sum of two radii, so an unknown element should guess GENEROUSLY — a missed
  // bond is a molecule drawn as dust. How big to draw an atom is a statement
  // about that element, so an unknown one should look ORDINARY rather than
  // claim to be larger than iodine, which a 1.2 fallback did.
  const RCOV = {
    H: 0.31, He: 0.28,
    Li: 1.28, Be: 0.96, B: 0.84, C: 0.76, N: 0.71, O: 0.66, F: 0.57, Ne: 0.58,
    Na: 1.66, Mg: 1.41, Al: 1.21, Si: 1.11, P: 1.07, S: 1.05, Cl: 1.02, Ar: 1.06,
    K: 2.03, Ca: 1.76, Sc: 1.70, Ti: 1.60, V: 1.53, Cr: 1.39, Mn: 1.39,
    Fe: 1.32, Co: 1.26, Ni: 1.24, Cu: 1.32, Zn: 1.22,
    Ga: 1.22, Ge: 1.20, As: 1.19, Se: 1.20, Br: 1.20, Kr: 1.16,
    Rb: 2.20, Sr: 1.95, Y: 1.90, Zr: 1.75, Nb: 1.64, Mo: 1.54, Tc: 1.47,
    Ru: 1.46, Rh: 1.42, Pd: 1.39, Ag: 1.45, Cd: 1.44,
    In: 1.42, Sn: 1.39, Sb: 1.39, Te: 1.38, I: 1.39, Xe: 1.40,
    Cs: 2.44, Ba: 2.15, La: 2.07, Ce: 2.04, Pr: 2.03, Nd: 2.01, Pm: 1.99,
    Sm: 1.98, Eu: 1.98, Gd: 1.96, Tb: 1.94, Dy: 1.92, Ho: 1.92, Er: 1.89,
    Tm: 1.90, Yb: 1.87, Lu: 1.87,
    Hf: 1.75, Ta: 1.70, W: 1.62, Re: 1.51, Os: 1.44, Ir: 1.41, Pt: 1.36,
    Au: 1.36, Hg: 1.32,
    Tl: 1.45, Pb: 1.46, Bi: 1.48, Po: 1.40, At: 1.50, Rn: 1.50,
    Fr: 2.60, Ra: 2.21, Ac: 2.15, Th: 2.06, Pa: 2.00, U: 1.96, Np: 1.90,
    Pu: 1.87,
  };
  const BOND_FALLBACK = 1.2;   // generous: rather a bond too many than a bond missed
  const DRAW_FALLBACK = 0.8;   // ordinary: about carbon, which is what "I don't know" looks like

  /** @param {string} s */
  function cap(s) {
    s = String(s || "");
    return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  }
  /** @param {string} el */
  function color(el) { return CPK[el] || CPK[cap(el)] || "#b0b0b0"; }
  /** The radius that decides BONDING. @param {string} el */
  function rcov(el) { return RCOV[el] || RCOV[cap(el)] || BOND_FALLBACK; }
  /** The radius an atom is DRAWN at, before the view's Å-per-pixel scale.
   *  Ball-and-stick sizes a ball by the element's covalent radius — so H (0.31)
   *  comes out at 41% of C (0.76), which is the whole point of drawing spheres
   *  rather than dots. @param {string} el */
  function rdraw(el) { return RCOV[el] || RCOV[cap(el)] || DRAW_FALLBACK; }
  MOL.color = color;
  MOL.radius = rdraw;

  /** Parse a coordinate block (with or without the two-line .xyz header).
   *  @param {string} text @returns {{el: string, x: number, y: number, z: number}[]} */
  MOL.parseXyz = function (text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    const out = [];
    for (const raw of lines) {
      const t = raw.trim();
      if (!t) continue;
      const p = t.split(/\s+/);
      if (p.length < 4) continue;
      const x = parseFloat(p[1]), y = parseFloat(p[2]), z = parseFloat(p[3]);
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) continue;
      out.push({ el: cap(p[0].replace(/[^A-Za-z]/g, "")), x: x, y: y, z: z });
    }
    return out;
  };

  /** Bonds, by the usual covalent-radius rule. O(n²), which is the right
   *  algorithm for the tens of atoms this draws.
   *  @param {any[]} atoms @returns {[number, number][]} */
  function bonds(atoms) {
    /** @type {[number, number][]} */
    const out = [];
    for (let i = 0; i < atoms.length; i++) {
      for (let j = i + 1; j < atoms.length; j++) {
        const dx = atoms[i].x - atoms[j].x;
        const dy = atoms[i].y - atoms[j].y;
        const dz = atoms[i].z - atoms[j].z;
        const d2 = dx * dx + dy * dy + dz * dz;
        const lim = (rcov(atoms[i].el) + rcov(atoms[j].el)) * 1.25;
        if (d2 < lim * lim) out.push([i, j]);
      }
    }
    return out;
  }
  MOL.bonds = bonds;

  /** Straight-line distance between two atoms, Å. @param {any} a @param {any} b */
  MOL.distance = function (a, b) {
    return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2);
  };

  /** @param {string} hex @param {number} k */
  function shade(hex, k) {
    const n = parseInt(hex.slice(1), 16);
    return `rgb(${Math.round(((n >> 16) & 255) * k)},${
      Math.round(((n >> 8) & 255) * k)},${Math.round((n & 255) * k)})`;
  }

  /** Project, sort and paint. Returns the screen positions and radii, which is
   *  what picking and measuring are done against — the projection is computed
   *  once per frame either way.
   *  @param {HTMLCanvasElement} canvas @param {any[]} atoms
   *  @param {{yaw: number, pitch: number, mark?: number[],
   *           measure?: {from: number|null, to?: number|null,
   *                      cur?: {x: number, y: number}|null}|null,
   *           labels?: boolean, gizmo?: boolean, hotAxis?: string,
   *           gizmoBottom?: number}} view */
  function render(canvas, atoms, view) {
    const box = canvas.getBoundingClientRect();
    if (!atoms || !atoms.length || box.width < 2 || box.height < 2) return null;
    // The bitmap is re-sized to the box every frame: a stale bitmap on a
    // resized box is what turns a molecule into ellipses.
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.round(box.width), H = Math.round(box.height);
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
      canvas.width = W * dpr; canvas.height = H * dpr;
    }
    const g = canvas.getContext("2d");
    if (!g) return null;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    let cx = 0, cy = 0, cz = 0;
    atoms.forEach(a => { cx += a.x; cy += a.y; cz += a.z; });
    cx /= atoms.length; cy /= atoms.length; cz /= atoms.length;

    const ca = Math.cos(view.yaw), sa = Math.sin(view.yaw);
    const cb = Math.cos(view.pitch), sb = Math.sin(view.pitch);
    const pts = atoms.map(a => {
      const x0 = a.x - cx, y0 = a.y - cy, z0 = a.z - cz;
      const x1 = x0 * ca + z0 * sa;
      const z1 = -x0 * sa + z0 * ca;
      const y1 = y0 * cb - z1 * sb;
      const z2 = y0 * sb + z1 * cb;
      return { x: x1, y: y1, z: z2, el: a.el };
    });

    // The scale is fitted to the molecule's BOUNDING SPHERE, not to how wide it
    // happens to look from here. A rotation preserves distance from the centre,
    // so this number does not change while you turn the structure — which is
    // the whole point: fitting the PROJECTED extent re-zoomed on every frame of
    // a drag, and an elongated molecule pumped 1.6x between end-on and
    // broadside (measured on Tf2O: a carbon went 21.1px -> 33.8px over one
    // yaw sweep). Turning a thing is not a reason for it to change size.
    // The cost is that a flat molecule no longer fills the box in its best
    // orientation; a stable size is worth more than the last few per cent.
    // `ball` is the room the OUTERMOST sphere needs, in the same A units as
    // span, so the fit is exact rather than a constant that had to be generous
    // enough for the largest atom anyone might load.
    let span = 1, ball = 0;
    pts.forEach(p => {
      span = Math.max(span, Math.hypot(p.x, p.y, p.z));
      ball = Math.max(ball, rdraw(p.el) * 0.32);
    });
    const s = (Math.min(W, H) / 2 - 18) / (span + ball);
    const sx = (p) => W / 2 + p.x * s;
    const sy = (p) => H / 2 - p.y * s;
    // A ball is never thinner than the stick it sits on — below that it reads
    // as a kink in the line, not an atom. That is the only floor: a flat 2.5px
    // one used to clamp every SMALL atom to the same size whenever the molecule
    // was big against its box, which is every cell thumbnail of a real
    // molecule — 47 atoms in a 220px thumb put H at 1.4px, so H, C, N and O all
    // came out 2.5 and the picture stopped saying which element was which. The
    // ratio between two elements is the thing being drawn; it survives now.
    const line = Math.max(1.6, s * 0.13);
    const rad = (el) => Math.max(line * 0.62, rdraw(el) * s * 0.32);

    const bs = bonds(atoms);
    const items = [];
    pts.forEach((p, i) => items.push({ z: p.z, kind: "atom", i: i }));
    bs.forEach((b, k) => items.push({
      z: (pts[b[0]].z + pts[b[1]].z) / 2, kind: "bond", a: b[0], b: b[1], k: k }));
    items.sort((u, v) => u.z - v.z);

    const mark = view.mark || [];
    items.forEach(it => {
      if (it.kind === "bond") {
        const A = pts[it.a], B = pts[it.b];
        const mx = (sx(A) + sx(B)) / 2, my = (sy(A) + sy(B)) / 2;
        const hot = mark.length === 2 && mark.indexOf(it.a) >= 0 && mark.indexOf(it.b) >= 0;
        g.lineCap = "round";
        g.lineWidth = hot ? line * 1.5 : line;
        g.strokeStyle = hot ? "#ffd166" : color(A.el);
        g.beginPath(); g.moveTo(sx(A), sy(A)); g.lineTo(mx, my); g.stroke();
        if (!hot) g.strokeStyle = color(B.el);
        g.beginPath(); g.moveTo(mx, my); g.lineTo(sx(B), sy(B)); g.stroke();
      } else {
        const p = pts[it.i], r = rad(p.el), X = sx(p), Y = sy(p);
        const grad = g.createRadialGradient(X - r * .35, Y - r * .35, r * .1, X, Y, r);
        grad.addColorStop(0, "#ffffff");
        grad.addColorStop(0.35, color(p.el));
        grad.addColorStop(1, shade(color(p.el), 0.62));
        g.fillStyle = grad;
        g.beginPath(); g.arc(X, Y, r, 0, Math.PI * 2); g.fill();
        if (mark.indexOf(it.i) >= 0) {
          g.strokeStyle = "#ffd166"; g.lineWidth = 2;
          g.beginPath(); g.arc(X, Y, r + 3, 0, Math.PI * 2); g.stroke();
        }
      }
    });

    // The measurement, drawn over everything - INCLUDING while the button is
    // still down. `to` is the atom under the cursor (null while over nothing),
    // `cur` is the cursor itself, so the drag is visible from the first pixel
    // and the distance appears only once there is one to state.
    const m = view.measure;
    if (m && m.from != null && pts[m.from]) {
      const A = pts[m.from];
      const ax = sx(A), ay = sy(A);
      let tx, ty, label = null;
      if (m.to != null && pts[m.to]) {
        tx = sx(pts[m.to]); ty = sy(pts[m.to]);
        label = MOL.distance(atoms[m.from], atoms[m.to]).toFixed(3) + " Å";
      } else if (m.cur) { tx = m.cur.x; ty = m.cur.y; }
      if (tx !== undefined) {
        const cssv = getComputedStyle(document.documentElement);
        const mc = (cssv.getPropertyValue("--crit-de") || "").trim() || "#4cc9f0";
        const ground = (cssv.getPropertyValue("--stage") || "").trim() || "#0e0e10";
        g.save();
        g.setLineDash([5, 4]); g.strokeStyle = mc; g.lineWidth = 1.6;
        g.beginPath(); g.moveTo(ax, ay); g.lineTo(tx, ty); g.stroke();
        g.restore();
        g.strokeStyle = mc; g.lineWidth = 2;
        g.beginPath(); g.arc(ax, ay, rad(A.el) + 4, 0, Math.PI * 2); g.stroke();
        if (m.to != null && pts[m.to]) {
          g.beginPath(); g.arc(tx, ty, rad(pts[m.to].el) + 4, 0, Math.PI * 2); g.stroke();
        }
        if (label) {
          const mono = (cssv.getPropertyValue("--font-mono") || "").trim() || "monospace";
          g.font = "11px " + mono;
          const lx = (ax + tx) / 2, ly = (ay + ty) / 2 - 7;
          const tw = g.measureText(label).width;
          g.fillStyle = ground;
          g.fillRect(lx - tw / 2 - 4, ly - 11, tw + 8, 15);
          g.fillStyle = mc; g.textAlign = "center"; g.textBaseline = "middle";
          g.fillText(label, lx, ly - 3);
          g.textAlign = "left"; g.textBaseline = "alphabetic";
        }
      }
    }

    // The axis marker, last so nothing is drawn over it. Only on a stage that
    // can be turned: on a thumbnail it would be chrome on a picture nobody can
    // steer, at a size that is a third of the box.
    if (view.gizmo) {
      const cssv = getComputedStyle(document.documentElement);
      const arms = gizmoArms(H, view.yaw, view.pitch, view.gizmoBottom);
      const mono = (cssv.getPropertyValue("--font-mono") || "").trim() || "monospace";
      g.save();
      g.lineCap = "round";
      // far arms first, so a near axis is not buried under a far one
      arms.slice().sort((a, b) => a.z - b.z).forEach(a => {
        const col = (cssv.getPropertyValue(a.tint) || "").trim() || "#888";
        const on = view.hotAxis === a.name;
        g.strokeStyle = col; g.fillStyle = col;
        g.globalAlpha = on ? 1 : (a.z < 0 ? 0.45 : 1);
        g.lineWidth = on ? 3 : 2;
        g.beginPath(); g.moveTo(a.ox, a.oy); g.lineTo(a.x, a.y); g.stroke();
        if (on) {                      // the arm under the cursor says it is a target
          g.beginPath(); g.arc(a.lx, a.ly, GIZMO.lab + 2, 0, Math.PI * 2);
          g.globalAlpha = 0.18; g.fill(); g.globalAlpha = 1;
        }
        g.font = (on ? "bold " : "") + "10px " + mono;
        g.textAlign = "center"; g.textBaseline = "middle";
        g.fillText(a.name, a.lx, a.ly);
      });
      g.restore();
    }

    return { pts: pts, sx: sx, sy: sy, rad: rad, bonds: bs, W: W, H: H };
  }

  /** Draw a still — the stream cells' thumbnails.
   *  @param {HTMLCanvasElement} canvas @param {any[]} atoms
   *  @param {{yaw?: number, pitch?: number}} [view] */
  MOL.draw = function (canvas, atoms, view) {
    // returns the projection like the interactive path does — a caller that
    // drew a still has the same right to know where things landed
    return render(canvas, atoms, {
      yaw: view && view.yaw != null ? view.yaw : 0.62,
      pitch: view && view.pitch != null ? view.pitch : -0.42,
    });
  };

  /* The three axis views, and the marker in the corner that IS the control for
     them. It used to be a button cycling "View down x -> y -> z", which asks the
     reader to remember which way they are facing and to press up to three times
     to get anywhere. The marker already states the orientation, so clicking the
     arm you want is the whole gesture — and it is one press, not three. */
  const AXES = [
    { name: "x", yaw: Math.PI / 2, pitch: 0, v: [1, 0, 0], tint: "--crit-maxs" },
    { name: "y", yaw: 0, pitch: Math.PI / 2, v: [0, 1, 0], tint: "--crit-rmsg" },
    { name: "z", yaw: 0, pitch: 0, v: [0, 0, 1], tint: "--crit-de" },
  ];
  const GIZMO = { ox: 40, arm: 24, lab: 8, hit: 14 };

  /** Where each axis arm's tip lands, in canvas pixels, for a given view. One
   *  function so the arm that is drawn and the arm that is clicked cannot
   *  disagree — the same reason the cube name has one speller.
   *  @param {number} H canvas css height @param {number} yaw @param {number} pitch
   *  @param {number} [bottom] px from the bottom edge — a stage with a caption
   *  down there says so, or the marker would be drawn over the writing */
  function gizmoArms(H, yaw, pitch, bottom) {
    const ca = Math.cos(yaw), sa = Math.sin(yaw);
    const cb = Math.cos(pitch), sb = Math.sin(pitch);
    const oy = H - (bottom || GIZMO.ox);
    return AXES.map((ax, i) => {
      const [x0, y0, z0] = ax.v;
      const x1 = x0 * ca + z0 * sa;
      const z1 = -x0 * sa + z0 * ca;
      const y1 = y0 * cb - z1 * sb;
      const z2 = y0 * sb + z1 * cb;
      return { i: i, name: ax.name, tint: ax.tint, z: z2,
               ox: GIZMO.ox, oy: oy,
               x: GIZMO.ox + x1 * GIZMO.arm, y: oy - y1 * GIZMO.arm,
               lx: GIZMO.ox + x1 * (GIZMO.arm + GIZMO.lab),
               ly: oy - y1 * (GIZMO.arm + GIZMO.lab) };
    });
  }

  /** The axis arm under a point, or null. The tip and its label are the target,
   *  not the line: two arms nearly edge-on cross near the origin, and a click
   *  there cannot mean one of them. @param {number} H @param {any} view
   *  @param {number} X @param {number} Y */
  function armAt(H, view, X, Y) {
    let best = null, bd = GIZMO.hit;
    gizmoArms(H, view.yaw, view.pitch, view.gizmoBottom).forEach(a => {
      const d = Math.hypot(a.lx - X, a.ly - Y);
      // a near arm wins a tie: it is the one the reader sees on top
      if (d < bd || (d === bd && best && a.z > best.z)) { bd = d; best = a; }
    });
    return best;
  }

  /** An interactive stage on `canvas`.
   *
   *  Drag turns it, the arrow keys turn it too (the drag is an addition, never
   *  the only way), a click picks an atom or a bond, and a right-drag from one
   *  atom to another measures between them.
   *
   *  @param {HTMLCanvasElement} canvas
   *  @param {{onPick?: (p: any) => void, yaw?: number, pitch?: number,
   *           gizmoBottom?: number}} [opts] gizmoBottom lifts the axis marker
   *  clear of whatever the stage draws in its bottom-left corner */
  MOL.mount = function (canvas, opts) {
    opts = opts || {};
    let atoms = [];
    let view = { yaw: opts.yaw != null ? opts.yaw : 0.62,
                 pitch: opts.pitch != null ? opts.pitch : -0.42 };
    /** @type {any} last projection, for hit-testing */
    let proj = null;
    /** @type {number[]} the picked atom(s) */
    let mark = [];
    /** The measurement in flight or pinned: {from, to, cur, pinned}.
     *  @type {any} */
    let measure = null;
    let drag = null;

    /** The axis arm the cursor is over, so the marker can say it is a target
     *  before it is pressed. @type {string} */
    let hotAxis = "";

    function paint() {
      proj = render(canvas, atoms, {
        yaw: view.yaw, pitch: view.pitch, mark: mark, measure: measure,
        gizmo: true, hotAxis: hotAxis, gizmoBottom: opts.gizmoBottom });
    }

    /** Turn the structure down one axis. @param {string} name x, y or z */
    function showAxis(name) {
      const a = AXES.filter(x => x.name === name)[0];
      if (!a) return;
      view = { yaw: a.yaw, pitch: a.pitch };
      paint();
    }

    /** The arm under a canvas point, or null. @param {{x: number, y: number}} p */
    function armUnder(p) {
      const box = canvas.getBoundingClientRect();
      return armAt(Math.round(box.height),
                   { yaw: view.yaw, pitch: view.pitch, gizmoBottom: opts.gizmoBottom },
                   p.x, p.y);
    }

    /** @param {PointerEvent} e @returns {{x: number, y: number}} */
    function local(e) {
      const r = canvas.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    /** The atom under a point, nearest to the viewer where they overlap.
     *  @param {number} X @param {number} Y @returns {number|null} */
    function atomAt(X, Y) {
      if (!proj) return null;
      let best = null, bz = -Infinity;
      proj.pts.forEach((p, i) => {
        const d = Math.hypot(proj.sx(p) - X, proj.sy(p) - Y);
        if (d <= proj.rad(p.el) + 3 && p.z > bz) { best = i; bz = p.z; }
      });
      return best;
    }

    /** An atom, else the bond nearest the point.
     *  @param {number} X @param {number} Y */
    function hitAt(X, Y) {
      if (!proj) return null;
      const a = atomAt(X, Y);
      if (a !== null) return { kind: "atom", i: a };
      let best = null, bestD = Infinity;
      proj.bonds.forEach(b => {
        const A = proj.pts[b[0]], B = proj.pts[b[1]];
        const ax = proj.sx(A), ay = proj.sy(A), bx = proj.sx(B), by = proj.sy(B);
        const L2 = (bx - ax) ** 2 + (by - ay) ** 2;
        if (!L2) return;
        const t = Math.max(0, Math.min(1, ((X - ax) * (bx - ax) + (Y - ay) * (by - ay)) / L2));
        const d = Math.hypot(ax + t * (bx - ax) - X, ay + t * (by - ay) - Y);
        if (d < 6 && d < bestD) { bestD = d; best = b; }
      });
      return best ? { kind: "bond", i: best[0], j: best[1] } : null;
    }

    canvas.style.cursor = "grab";
    canvas.addEventListener("contextmenu", e => e.preventDefault());

    canvas.addEventListener("pointerdown", e => {
      // preventScroll: focusing a tabbable element scrolls it into view, and
      // the stage is usually well down a long report — so a click on an atom
      // yanked the page, and then landed on a box that had moved
      canvas.focus({ preventScroll: true });
      canvas.setPointerCapture(e.pointerId);
      const p = local(e);
      if (e.button === 2) {                       // right: start measuring
        const from = atomAt(p.x, p.y);
        measure = from == null ? null : { from: from, to: null, cur: p };
        paint();
        return;
      }
      // the marker is a control, not part of the scene: pressing an arm must
      // not also start turning the structure under it
      if (armUnder(p)) return;
      drag = { x: e.clientX, y: e.clientY, yaw: view.yaw, pitch: view.pitch, moved: 0 };
      canvas.style.cursor = "grabbing";
    });

    canvas.addEventListener("pointermove", e => {
      // While the right button is down the line follows the cursor: that IS
      // the feedback that a measurement is being taken.
      if (measure && measure.pinned !== true && (e.buttons & 2)) {
        const p = local(e);
        measure.cur = p;
        const over = atomAt(p.x, p.y);
        measure.to = (over != null && over !== measure.from) ? over : null;
        paint();
        return;
      }
      if (!drag) {
        // over an arm the marker is a button, and says so
        const over = armUnder(local(e));
        const name = over ? over.name : "";
        if (name !== hotAxis) {
          hotAxis = name;
          canvas.style.cursor = name ? "pointer" : "grab";
          canvas.title = name ? "View down " + name : "";
          paint();
        }
        return;
      }
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.moved = Math.max(drag.moved, Math.hypot(dx, dy));
      view.yaw = drag.yaw + dx * 0.01;
      view.pitch = Math.max(-1.5, Math.min(1.5, drag.pitch - dy * 0.01));
      paint();
    });

    canvas.addEventListener("pointerup", e => {
      if (e.button === 2 || (measure && !drag)) {
        if (measure) {
          if (measure.to == null) measure = null;   // released on nothing
          else {
            measure.pinned = true;                 // keep it on screen
            if (opts.onPick) {
              opts.onPick({ kind: "distance", i: measure.from, j: measure.to,
                            value: MOL.distance(atoms[measure.from], atoms[measure.to]) });
            }
          }
          paint();
        }
        return;
      }
      const up = local(e);
      const arm = armUnder(up);
      if (arm) {                                   // the marker was pressed
        drag = null;
        showAxis(arm.name);
        return;
      }
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      canvas.style.cursor = "grab";
      if (moved > 4) return;                       // a turn, not a click
      const p = up;
      const h = hitAt(p.x, p.y);
      if (!h) { mark = []; paint(); if (opts.onPick) opts.onPick(null); return; }
      // clicking what is already selected lets it go
      const next = h.kind === "atom" ? [h.i] : [h.i, h.j];
      const same = next.length === mark.length && next.every((v, k) => v === mark[k]);
      mark = same ? [] : next;
      paint();
      if (opts.onPick) opts.onPick(same ? null : Object.assign({ atoms: atoms }, h));
    });
    canvas.addEventListener("pointercancel", () => {
      drag = null; canvas.style.cursor = "grab";
    });
    canvas.addEventListener("pointerleave", () => {
      if (!hotAxis) return;
      hotAxis = ""; canvas.style.cursor = "grab"; canvas.title = ""; paint();
    });

    if (!canvas.hasAttribute("tabindex")) canvas.setAttribute("tabindex", "0");
    canvas.addEventListener("keydown", e => {
      const step = 0.16;
      if (e.key === "ArrowLeft") view.yaw -= step;
      else if (e.key === "ArrowRight") view.yaw += step;
      else if (e.key === "ArrowUp") view.pitch = Math.max(-1.5, view.pitch - step);
      else if (e.key === "ArrowDown") view.pitch = Math.min(1.5, view.pitch + step);
      else if (e.key === "x" || e.key === "y" || e.key === "z") {
        // the marker is drawn, so it cannot be tabbed to: these keys are how it
        // is reached without a pointer, and the stage already takes focus
        showAxis(e.key);
        e.preventDefault();
        return;
      } else if (e.key === "Escape") {
        measure = null; mark = [];
        e.preventDefault(); paint();
        if (opts.onPick) opts.onPick(null);
        return;
      } else return;
      e.preventDefault(); paint();
    });

    let ro = null;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(() => paint());
      // the STAGE, not the canvas: paint() resizes the canvas, so observing the
      // canvas is observing the thing this callback is about to change -- the
      // other half of the loop the absolute canvas closes (app.css, "the stage
      // owns its height"). The stage is sized by the layout and by nothing here.
      ro.observe(canvas.parentElement || canvas);
    }
    // Everything above is bound to the CANVAS, not the window: setPointerCapture
    // is what keeps a drag that leaves the canvas attached to it, which is the
    // only reason those listeners were ever on the window. A stage whose canvas
    // is detached now answers nothing, so a re-rendered report cannot leave a
    // stale one clearing the pick the live one just made.
    return {
      /** @param {any[]} a */
      setAtoms(a) { atoms = a || []; mark = []; measure = null; paint(); },
      /** Turn the structure down one axis — what pressing that arm of the
       *  marker does, for a caller that wants it without a click.
       *  @param {string} name x, y or z */
      showAxis: showAxis,
      clearPick() { mark = []; measure = null; paint(); },
      repaint: paint,
      /** What the measurement is doing, for anything that needs to know — and
       *  for the headless check that the drag is visible while it happens. */
      measureState() {
        return measure ? { from: measure.from, to: measure.to,
                           cur: measure.cur, pinned: !!measure.pinned } : null;
      },
      /** the last projection — screen positions and radii, which is what a
       *  caller needs to reason about what is where */
      projection() {
        if (!proj) return null;
        return { atoms: atoms.length,
                 screen: proj.pts.map(p => [Math.round(proj.sx(p)), Math.round(proj.sy(p)),
                                            Math.round(proj.rad(p.el))]) };
      },
      destroy() { if (ro) ro.disconnect(); },
    };
  };
})();
