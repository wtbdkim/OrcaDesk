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

    // a right-drag measurement, drawn over everything
    const m = view.measure;
    if (m && m.length === 2) {
      const A = pts[m[0]], B = pts[m[1]];
      g.setLineDash([4, 4]); g.strokeStyle = "#ffd166"; g.lineWidth = 1.4;
      g.beginPath(); g.moveTo(sx(A), sy(A)); g.lineTo(sx(B), sy(B)); g.stroke();
      g.setLineDash([]);
      const d = MOL.distance(atoms[m[0]], atoms[m[1]]);
      const tx = (sx(A) + sx(B)) / 2, ty = (sy(A) + sy(B)) / 2;
      const label = d.toFixed(3) + " Å";
      g.font = "11px ui-monospace, monospace";
      const w = g.measureText(label).width + 10;
      g.fillStyle = "rgba(0,0,0,.72)";
      g.beginPath(); g.roundRect(tx - w / 2, ty - 9, w, 18, 9); g.fill();
      g.fillStyle = "#ffd166"; g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(label, tx, ty);
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
    /** @type {number[]|null} */
    let measure = null;
    let axis = -1;
    let drag = null, rdrag = null;

    function paint() {
      proj = render(canvas, atoms, {
        yaw: view.yaw, pitch: view.pitch, mark: mark, measure: measure });
    }

    /** @param {MouseEvent} e @returns {{kind: string, i: number, j?: number}|null} */
    function hit(e) {
      if (!proj) return null;
      const r = canvas.getBoundingClientRect();
      const X = e.clientX - r.left, Y = e.clientY - r.top;
      // atoms first: a bond behind an atom is not what was aimed at
      let best = null, bestD = Infinity;
      proj.pts.forEach((p, i) => {
        const d = Math.hypot(proj.sx(p) - X, proj.sy(p) - Y);
        if (d <= proj.rad(p.el) + 2 && (best === null || p.z > proj.pts[best].z)) {
          best = i;
        }
      });
      if (best !== null) return { kind: "atom", i: best };
      proj.bonds.forEach(b => {
        const A = proj.pts[b[0]], B = proj.pts[b[1]];
        const ax = proj.sx(A), ay = proj.sy(A), bx = proj.sx(B), by = proj.sy(B);
        const L2 = (bx - ax) ** 2 + (by - ay) ** 2;
        if (!L2) return;
        let t = ((X - ax) * (bx - ax) + (Y - ay) * (by - ay)) / L2;
        t = Math.max(0, Math.min(1, t));
        const d = Math.hypot(ax + t * (bx - ax) - X, ay + t * (by - ay) - Y);
        if (d < 6 && d < bestD) { bestD = d; best = b; }
      });
      if (best && Array.isArray(best)) return { kind: "bond", i: best[0], j: best[1] };
      return null;
    }

    canvas.addEventListener("mousedown", e => {
      // preventScroll: focusing a tabbable element scrolls it into view, and
      // the stage is usually well down a long report — so clicking an atom
      // yanked the page, and the click then landed on a box that had moved
      canvas.focus({ preventScroll: true });
      if (e.button === 2) {
        const h = hit(e);
        if (h && h.kind === "atom") { rdrag = h.i; measure = null; paint(); }
        e.preventDefault();
        return;
      }
      drag = { x: e.clientX, y: e.clientY, yaw: view.yaw, pitch: view.pitch, moved: false };
    });
    const gone = new AbortController();
    const on = gone.signal;
    window.addEventListener("mousemove", e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      view.yaw = drag.yaw + dx * 0.01;
      view.pitch = Math.max(-1.5, Math.min(1.5, drag.pitch + dy * 0.01));
      axis = -1;
      paint();
    }, { signal: on });
    window.addEventListener("mouseup", e => {
      if (rdrag != null) {
        const h = hit(e);
        if (h && h.kind === "atom" && h.i !== rdrag) {
          measure = [rdrag, h.i];
          if (opts.onPick) {
            opts.onPick({ kind: "distance", i: measure[0], j: measure[1],
                          value: MOL.distance(atoms[measure[0]], atoms[measure[1]]) });
          }
        }
        rdrag = null; paint(); return;
      }
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      if (moved) return;
      const h = hit(e);
      if (!h) { mark = []; measure = null; paint(); if (opts.onPick) opts.onPick(null); return; }
      // clicking the same atom again lets it go
      const next = h.kind === "atom" ? [h.i] : [h.i, h.j];
      const same = next.length === mark.length && next.every((v, k) => v === mark[k]);
      mark = same ? [] : next;
      measure = null;
      paint();
      if (opts.onPick) opts.onPick(same ? null : Object.assign({ atoms: atoms }, h));
    }, { signal: on });
    canvas.addEventListener("contextmenu", e => e.preventDefault());
    canvas.addEventListener("keydown", e => {
      const step = 0.12;
      if (e.key === "ArrowLeft") view.yaw -= step;
      else if (e.key === "ArrowRight") view.yaw += step;
      else if (e.key === "ArrowUp") view.pitch = Math.max(-1.5, view.pitch - step);
      else if (e.key === "ArrowDown") view.pitch = Math.min(1.5, view.pitch + step);
      else return;
      e.preventDefault(); axis = -1; paint();
    });
    if (!canvas.hasAttribute("tabindex")) canvas.setAttribute("tabindex", "0");

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
      /** the last projection — screen positions and radii, which is what a
       *  caller needs to reason about what is where */
      projection() {
        if (!proj) return null;
        return { atoms: atoms.length,
                 screen: proj.pts.map(p => [Math.round(proj.sx(p)), Math.round(proj.sy(p)),
                                            Math.round(proj.rad(p.el))]) };
      },
      destroy() { if (ro) ro.disconnect(); gone.abort(); },
    };
  };
})();
