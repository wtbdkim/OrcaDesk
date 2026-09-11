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
  // covalent radii (Å), for deciding what is bonded to what
  const RCOV = {
    H: 0.31, C: 0.76, N: 0.71, O: 0.66, F: 0.57, P: 1.07, S: 1.05, Cl: 1.02,
    Br: 1.20, I: 1.39, Si: 1.11, B: 0.84, Na: 1.66, K: 2.03, Mg: 1.41,
    Ca: 1.76, Fe: 1.32, Cu: 1.32, Zn: 1.22, Ru: 1.46, Pd: 1.39, Ag: 1.45,
    Pt: 1.36, Au: 1.36,
  };
  const DEFAULT_R = 1.2;

  /** @param {string} s */
  function cap(s) {
    s = String(s || "");
    return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  }
  /** @param {string} el */
  function color(el) { return CPK[el] || CPK[cap(el)] || "#b0b0b0"; }
  /** @param {string} el */
  function rcov(el) { return RCOV[el] || RCOV[cap(el)] || DEFAULT_R; }
  MOL.color = color;

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
   *  @param {{yaw: number, pitch: number, mark?: number[], measure?: number[],
   *           labels?: boolean}} view */
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

    let span = 1;
    pts.forEach(p => { span = Math.max(span, Math.abs(p.x), Math.abs(p.y)); });
    const s = (Math.min(W, H) / 2 - 18) / (span + 0.9);
    const sx = (p) => W / 2 + p.x * s;
    const sy = (p) => H / 2 - p.y * s;
    const rad = (el) => Math.max(2.5, rcov(el) * s * 0.32);

    const bs = bonds(atoms);
    const items = [];
    pts.forEach((p, i) => items.push({ z: p.z, kind: "atom", i: i }));
    bs.forEach((b, k) => items.push({
      z: (pts[b[0]].z + pts[b[1]].z) / 2, kind: "bond", a: b[0], b: b[1], k: k }));
    items.sort((u, v) => u.z - v.z);

    const line = Math.max(1.6, s * 0.13);
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

    return { pts: pts, sx: sx, sy: sy, rad: rad, bonds: bs, W: W, H: H };
  }

  /** Draw a still — the stream cells' thumbnails.
   *  @param {HTMLCanvasElement} canvas @param {any[]} atoms
   *  @param {{yaw?: number, pitch?: number}} [view] */
  MOL.draw = function (canvas, atoms, view) {
    render(canvas, atoms, {
      yaw: view && view.yaw != null ? view.yaw : 0.62,
      pitch: view && view.pitch != null ? view.pitch : -0.42,
    });
  };

  /* The three axis views the axis button walks, in the order it walks them. */
  const AXES = [
    { name: "x", yaw: Math.PI / 2, pitch: 0 },
    { name: "y", yaw: 0, pitch: Math.PI / 2 },
    { name: "z", yaw: 0, pitch: 0 },
  ];

  /** An interactive stage on `canvas`.
   *
   *  Drag turns it, the arrow keys turn it too (the drag is an addition, never
   *  the only way), a click picks an atom or a bond, and a right-drag from one
   *  atom to another measures between them.
   *
   *  @param {HTMLCanvasElement} canvas
   *  @param {{onPick?: (p: any) => void, yaw?: number, pitch?: number}} [opts] */
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
    let axis = -1;
    let drag = null;

    function paint() {
      proj = render(canvas, atoms, {
        yaw: view.yaw, pitch: view.pitch, mark: mark, measure: measure });
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
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.moved = Math.max(drag.moved, Math.hypot(dx, dy));
      view.yaw = drag.yaw + dx * 0.01;
      view.pitch = Math.max(-1.5, Math.min(1.5, drag.pitch - dy * 0.01));
      axis = -1;
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
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      canvas.style.cursor = "grab";
      if (moved > 4) return;                       // a turn, not a click
      const p = local(e);
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

    if (!canvas.hasAttribute("tabindex")) canvas.setAttribute("tabindex", "0");
    canvas.addEventListener("keydown", e => {
      const step = 0.16;
      if (e.key === "ArrowLeft") view.yaw -= step;
      else if (e.key === "ArrowRight") view.yaw += step;
      else if (e.key === "ArrowUp") view.pitch = Math.max(-1.5, view.pitch - step);
      else if (e.key === "ArrowDown") view.pitch = Math.min(1.5, view.pitch + step);
      else if (e.key === "Escape") {
        measure = null; mark = [];
        e.preventDefault(); paint();
        if (opts.onPick) opts.onPick(null);
        return;
      } else return;
      e.preventDefault(); axis = -1; paint();
    });

    let ro = null;
    if (typeof ResizeObserver === "function") {
      ro = new ResizeObserver(() => paint());
      ro.observe(canvas);
    }
    // Turning and releasing happen on the WINDOW, because a drag that leaves
    // the canvas still has to keep turning it. That means the listeners outlive
    // the canvas unless they are taken back: a re-rendered report would
    // otherwise leave a stage bound to a detached canvas answering the same
    // events, and the stale one clearing the pick the live one just made.
    

    return {
      /** @param {any[]} a */
      setAtoms(a) { atoms = a || []; mark = []; measure = null; paint(); },
      /** Walk x, then y, then z. @returns {string} the axis now shown */
      nextAxis() {
        axis = (axis + 1) % AXES.length;
        view = { yaw: AXES[axis].yaw, pitch: AXES[axis].pitch };
        paint();
        return AXES[axis].name;
      },
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
