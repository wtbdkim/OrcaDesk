// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — structure thumbnail.

   A small ball-and-stick render of a calculation's geometry, drawn on a 2D
   canvas: the first column of every cell. Deliberately not WebGL and not
   3Dmol.js — this is a still, one per cell, several on screen at once, and a
   GL context per cell would cost more than the picture is worth.

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
    H: "#ffffff", He: "#d9ffff", Li: "#cc80ff", Be: "#c2ff00", B: "#ffb5b5",
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

  /** @param {string} el */
  function color(el) { return CPK[el] || CPK[cap(el)] || "#b0b0b0"; }
  /** @param {string} el */
  function rcov(el) { return RCOV[el] || RCOV[cap(el)] || DEFAULT_R; }
  /** @param {string} s */
  function cap(s) {
    s = String(s || "");
    return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  }

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
   *  algorithm for the tens-of-atoms this draws.
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

  /** Draw `atoms` into `canvas`, filling its box.
   *
   *  The canvas bitmap is sized to the box × devicePixelRatio every time: a
   *  stale bitmap on a resized box is what turns a molecule into ellipses.
   *  @param {HTMLCanvasElement} canvas
   *  @param {any[]} atoms
   *  @param {{yaw?: number, pitch?: number}} [view] */
  MOL.draw = function (canvas, atoms, view) {
    if (!canvas || !atoms || !atoms.length) return;
    const box = canvas.getBoundingClientRect();
    if (box.width < 2 || box.height < 2) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = Math.round(box.width), H = Math.round(box.height);
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
      canvas.width = W * dpr; canvas.height = H * dpr;
    }
    const g = canvas.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    // centre, then a fixed three-quarter view — the angle a structure is
    // conventionally shown at, and the same one for every cell so two
    // thumbnails are comparable
    const yaw = view && view.yaw != null ? view.yaw : 0.62;
    const pitch = view && view.pitch != null ? view.pitch : -0.42;
    let cx = 0, cy = 0, cz = 0;
    atoms.forEach(a => { cx += a.x; cy += a.y; cz += a.z; });
    cx /= atoms.length; cy /= atoms.length; cz /= atoms.length;

    const cosA = Math.cos(yaw), sinA = Math.sin(yaw);
    const cosB = Math.cos(pitch), sinB = Math.sin(pitch);
    const pts = atoms.map(a => {
      const x0 = a.x - cx, y0 = a.y - cy, z0 = a.z - cz;
      const x1 = x0 * cosA + z0 * sinA;
      const z1 = -x0 * sinA + z0 * cosA;
      const y1 = y0 * cosB - z1 * sinB;
      const z2 = y0 * sinB + z1 * cosB;
      return { x: x1, y: y1, z: z2, el: a.el };
    });

    let span = 1;
    pts.forEach(p => {
      span = Math.max(span, Math.abs(p.x), Math.abs(p.y));
    });
    const pad = 16;
    const s = (Math.min(W, H) / 2 - pad) / (span + 0.9);
    const px = (p) => W / 2 + p.x * s;
    const py = (p) => H / 2 - p.y * s;
    const rad = (el) => Math.max(2.5, rcov(el) * s * 0.32);

    // painter's algorithm: everything sorted by depth, drawn back to front, so
    // a near atom covers the bond behind it without a z-buffer
    const items = [];
    pts.forEach((p, i) => items.push({ z: p.z, kind: "atom", i: i }));
    bonds(atoms).forEach(b => {
      items.push({ z: (pts[b[0]].z + pts[b[1]].z) / 2, kind: "bond", a: b[0], b: b[1] });
    });
    items.sort((u, v) => u.z - v.z);

    const line = Math.max(1.6, s * 0.13);
    items.forEach(it => {
      if (it.kind === "bond") {
        const A = pts[it.a], B = pts[it.b];
        const mx = (px(A) + px(B)) / 2, my = (py(A) + py(B)) / 2;
        g.lineCap = "round";
        g.lineWidth = line;
        g.strokeStyle = color(A.el);
        g.beginPath(); g.moveTo(px(A), py(A)); g.lineTo(mx, my); g.stroke();
        g.strokeStyle = color(B.el);
        g.beginPath(); g.moveTo(mx, my); g.lineTo(px(B), py(B)); g.stroke();
      } else {
        const p = pts[it.i];
        const r = rad(p.el);
        const X = px(p), Y = py(p);
        // a single off-centre highlight reads as a sphere without a shader
        const grad = g.createRadialGradient(X - r * 0.35, Y - r * 0.35, r * 0.1, X, Y, r);
        grad.addColorStop(0, "#ffffff");
        grad.addColorStop(0.35, color(p.el));
        grad.addColorStop(1, shade(color(p.el), 0.62));
        g.fillStyle = grad;
        g.beginPath(); g.arc(X, Y, r, 0, Math.PI * 2); g.fill();
      }
    });
  };

  /** @param {string} hex @param {number} k */
  function shade(hex, k) {
    const n = parseInt(hex.slice(1), 16);
    const r = Math.round(((n >> 16) & 255) * k);
    const g2 = Math.round(((n >> 8) & 255) * k);
    const b = Math.round((n & 255) * k);
    return `rgb(${r},${g2},${b})`;
  }
})();
