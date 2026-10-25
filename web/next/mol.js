// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — structure rendering.

   Every structure the preview draws: the thumbnail in every stream cell, the
   interactive stage in the report's Structure and Visual sections, and the
   pop-out viewer. Deliberately not WebGL and not 3Dmol.js — the thumbnails are
   several on screen at once, and a GL context each would cost more than the
   picture is worth; the stage needs picking and measuring, which is easier
   against a projection this file already computes than against a library's
   scene graph.

   The look follows Avogadro2, the molecular editor most ORCA users already
   read structures in (DESIGN.md §17.4k): lit spheres and shaded cylinders,
   ball-and-stick at 0.3 x the van der Waals radius on 0.1 Å sticks, multiple
   bonds as parallel sticks in the plane of the neighbours, and the same four
   display types — ball and stick, sticks, space-filling, wireframe. Jmol CPK
   colours, which is what Avogadro, Jmol and 3Dmol all start from, so a
   structure here is recognisable next to one anywhere else.

   A 2D canvas has no depth buffer, so the picture has to SAY which atom is in
   front, and three cues do it together: a mild perspective (a near atom is
   drawn larger and further from the centre), depth fog (a far atom fades
   toward the stage's own colour), and a dark rim on every sphere and stick so
   a near one is cut out against whatever it covers. Painter's algorithm — sort
   by depth, draw back to front — does the occlusion itself.
   ============================================================ */

/** @type {any} */
var MOL = {};

(function () {

  // Jmol CPK, H through Pu. H is drawn a shade off white so it still has an
  // edge on the light stage; everything else is the published table.
  const CPK = {
    H: "#dcdcdc", He: "#d9ffff",
    Li: "#cc80ff", Be: "#c2ff00", B: "#ffb5b5", C: "#909090", N: "#3050f8",
    O: "#ff0d0d", F: "#90e050", Ne: "#b3e3f5",
    Na: "#ab5cf2", Mg: "#8aff00", Al: "#bfa6a6", Si: "#f0c8a0", P: "#ff8000",
    S: "#ffff30", Cl: "#1ff01f", Ar: "#80d1e3",
    K: "#8f40d4", Ca: "#3dff00", Sc: "#e6e6e6", Ti: "#bfc2c7", V: "#a6a6ab",
    Cr: "#8a99c7", Mn: "#9c7ac7", Fe: "#e06633", Co: "#f090a0", Ni: "#50d050",
    Cu: "#c88033", Zn: "#7d80b0", Ga: "#c28f8f", Ge: "#668f8f", As: "#bd80e3",
    Se: "#ffa100", Br: "#a62929", Kr: "#5cb8d1",
    Rb: "#702eb0", Sr: "#00ff00", Y: "#94ffff", Zr: "#94e0e0", Nb: "#73c2c9",
    Mo: "#54b5b5", Tc: "#3b9e9e", Ru: "#248f8f", Rh: "#0a7d8c", Pd: "#006985",
    Ag: "#c0c0c0", Cd: "#ffd98f", In: "#a67573", Sn: "#668080", Sb: "#9e63b5",
    Te: "#d47a00", I: "#940094", Xe: "#429eb0",
    Cs: "#57178f", Ba: "#00c900", La: "#70d4ff", Ce: "#ffffc7", Pr: "#d9ffc7",
    Nd: "#c7ffc7", Pm: "#a3ffc7", Sm: "#8fffc7", Eu: "#61ffc7", Gd: "#45ffc7",
    Tb: "#30ffc7", Dy: "#1fffc7", Ho: "#00ff9c", Er: "#00e675", Tm: "#00d452",
    Yb: "#00bf38", Lu: "#00ab24", Hf: "#4dc2ff", Ta: "#4da6ff", W: "#2194d6",
    Re: "#267dab", Os: "#266696", Ir: "#175487", Pt: "#d0d0e0", Au: "#ffd123",
    Hg: "#b8b8d0", Tl: "#a6544d", Pb: "#575961", Bi: "#9e4fb5", Po: "#ab5c00",
    At: "#754f45", Rn: "#428296",
    Fr: "#420066", Ra: "#007d00", Ac: "#70abfa", Th: "#00baff", Pa: "#00a1ff",
    U: "#008fff", Np: "#0080ff", Pu: "#006bff",
  };
  // Covalent radii in Å — Cordero et al., Dalton Trans. 2008, 2832, the table
  // every chemistry viewer uses, H through Pu. This decides BONDING only: what
  // is bonded to what is the sum of two of these, so an unknown element guesses
  // GENEROUSLY — a missed bond is a molecule drawn as dust.
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
  // Van der Waals radii in Å — the size an atom is DRAWN at, which is how
  // Avogadro, Jmol and 3Dmol all size a ball (ball-and-stick at 0.3 x this,
  // space-filling at 1 x). Main group: Bondi, J. Phys. Chem. 1964, 68, 441,
  // with Mantina et al., J. Phys. Chem. A 2009, 113, 5806 for the elements
  // Bondi left out. The d and f blocks have no van der Waals radius anyone
  // agrees on — Bondi's few metal values and Alvarez's 2013 set differ by 0.7 Å
  // — so for those the drawing size is a stated CONVENTION rather than a
  // looked-up number: the covalent radius plus 0.4 Å, which lands Bondi's own
  // Ni/Pd/Pt within 0.05 Å and keeps a metal centre at least as large as the
  // carbons around it. An element in neither looks like carbon, which is what
  // "I don't know" should look like.
  const VDW = {
    H: 1.20, He: 1.40,
    Li: 1.82, Be: 1.53, B: 1.92, C: 1.70, N: 1.55, O: 1.52, F: 1.47, Ne: 1.54,
    Na: 2.27, Mg: 1.73, Al: 1.84, Si: 2.10, P: 1.80, S: 1.80, Cl: 1.75, Ar: 1.88,
    K: 2.75, Ca: 2.31,
    Ga: 1.87, Ge: 2.11, As: 1.85, Se: 1.90, Br: 1.85, Kr: 2.02,
    Rb: 3.03, Sr: 2.49,
    In: 1.93, Sn: 2.17, Sb: 2.06, Te: 2.06, I: 1.98, Xe: 2.16,
    Cs: 3.43, Ba: 2.68,
    Tl: 1.96, Pb: 2.02, Bi: 2.07, Po: 1.97, At: 2.02, Rn: 2.20,
    Fr: 3.48, Ra: 2.83,
  };
  const BOND_FALLBACK = 1.2;   // generous: rather a bond too many than a bond missed
  const DRAW_FALLBACK = 1.70;  // ordinary: carbon, which is what "I don't know" looks like
  const METAL_VDW_PAD = 0.4;   // the d/f-block convention above

  /** @param {string} s */
  function cap(s) {
    s = String(s || "");
    return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  }
  /** @param {string} el */
  function color(el) { return CPK[el] || CPK[cap(el)] || "#b0b0b0"; }
  /** The radius that decides BONDING. @param {string} el */
  function rcov(el) { return RCOV[el] || RCOV[cap(el)] || BOND_FALLBACK; }
  /** The radius an atom is DRAWN from, before the display type scales it.
   *  H (1.20) comes out at 71% of C (1.70): small enough to read as hydrogen,
   *  large enough to be a ball rather than a knob on a stick. @param {string} el */
  function vdw(el) {
    const v = VDW[el] || VDW[cap(el)];
    if (v) return v;
    const r = RCOV[el] || RCOV[cap(el)];
    return r ? r + METAL_VDW_PAD : DRAW_FALLBACK;
  }
  MOL.color = color;
  MOL.radius = vdw;

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
   *  algorithm for the tens of atoms this draws — and it runs once per
   *  structure now, not once per frame (see `scene`).
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
  /** The angle a–b–c at b, degrees. @param {any} a @param {any} b @param {any} c */
  MOL.angle = function (a, b, c) {
    const u = [a.x - b.x, a.y - b.y, a.z - b.z], v = [c.x - b.x, c.y - b.y, c.z - b.z];
    const d = (Math.hypot(u[0], u[1], u[2]) * Math.hypot(v[0], v[1], v[2])) || 1;
    const cs = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / d;
    return Math.acos(Math.max(-1, Math.min(1, cs))) * 180 / Math.PI;
  };
  /** The dihedral a–b–c–d, degrees, IUPAC sign — the sign ORCA reports and
   *  targets (a { D } constraint at +120 measured +120 this way on 6.1.1).
   *  @param {any} a @param {any} b @param {any} c @param {any} d */
  MOL.dihedral = function (a, b, c, d) {
    const b1 = [b.x - a.x, b.y - a.y, b.z - a.z];
    const b2 = [c.x - b.x, c.y - b.y, c.z - b.z];
    const b3 = [d.x - c.x, d.y - c.y, d.z - c.z];
    /** @param {number[]} p @param {number[]} q */
    const cr = (p, q) => [p[1] * q[2] - p[2] * q[1], p[2] * q[0] - p[0] * q[2], p[0] * q[1] - p[1] * q[0]];
    /** @param {number[]} p @param {number[]} q */
    const dt = (p, q) => p[0] * q[0] + p[1] * q[1] + p[2] * q[2];
    const n23 = cr(b2, b3);
    return Math.atan2(Math.hypot(b2[0], b2[1], b2[2]) * dt(b1, n23), dt(cr(b1, b2), n23)) * 180 / Math.PI;
  };
  /** What a chain of 2–4 atoms measures — the bond, angle or dihedral it
   *  names, which is also the ORCA constraint it would be.
   *  @param {any[]} atoms @param {number[]} idx
   *  @returns {{value: number, unit: string}|null} */
  MOL.measureChain = function (atoms, idx) {
    const p = idx.map(i => atoms[i]);
    if (p.some(x => !x)) return null;
    if (p.length === 2) return { value: MOL.distance(p[0], p[1]), unit: "Å" };
    if (p.length === 3) return { value: MOL.angle(p[0], p[1], p[2]), unit: "°" };
    if (p.length === 4) return { value: MOL.dihedral(p[0], p[1], p[2], p[3]), unit: "°" };
    return null;
  };

  // ---------------------------------------------------------------- display

  /* The four display types Avogadro offers, in Å. `atom` is the drawn radius,
     `bond` the stick radius, `multi` whether a double bond is drawn as two
     sticks. Space-filling has no sticks; wireframe has no spheres. */
  const STYLES = {
    ball:  { label: "Ball & stick", key: "1", atom: (el) => vdw(el) * 0.3, bond: 0.1, multi: true },
    stick: { label: "Sticks", key: "2", atom: () => 0.15, bond: 0.15, multi: false },
    fill:  { label: "Spheres", key: "3", atom: (el) => vdw(el), bond: 0, multi: false },
    wire:  { label: "Wire", key: "4", atom: () => 0, bond: 0, multi: true },
  };
  const STYLE_ORDER = ["ball", "stick", "fill", "wire"];
  const LABELS = [["off", "Off"], ["element", "Element"], ["index", "Index"]];

  /* One display for the whole app, the way Avogadro's display types belong to
     the scene and not to a window: a structure switched to sticks in the
     pop-out is sticks in the report beside it. Kept in localStorage as a
     per-viewer convenience — it must work, and does, when that is refused. */
  const STORE_KEY = "orcadesk.next.display";
  const DISPLAY = { style: "ball", hydrogens: true, labels: "off", orders: true };
  /** @param {any} p */
  function patchDisplay(p) {
    if (!p || typeof p !== "object") return;
    if (STYLES[p.style]) DISPLAY.style = p.style;
    if (typeof p.hydrogens === "boolean") DISPLAY.hydrogens = p.hydrogens;
    if (LABELS.some(l => l[0] === p.labels)) DISPLAY.labels = p.labels;
    if (typeof p.orders === "boolean") DISPLAY.orders = p.orders;
  }
  try { patchDisplay(JSON.parse(window.localStorage.getItem(STORE_KEY) || "null")); }
  catch (e) { /* private window, blocked storage: the defaults stand */ }

  /** Every stage that is on screen, so a display change repaints all of them.
   *  A stage re-registers itself on every paint and is dropped once its canvas
   *  has left the document, so a re-rendered report does not leak its old ones.
   *  @type {Set<{canvas: HTMLCanvasElement, paint: () => void}>} */
  const _live = new Set();
  /** open display menus, which re-draw their own state @type {Set<() => void>} */
  const _watch = new Set();

  /** @param {any} patch */
  function setDisplay(patch) {
    patchDisplay(patch);
    try { window.localStorage.setItem(STORE_KEY, JSON.stringify(DISPLAY)); }
    catch (e) { /* the change still applies for this session */ }
    _live.forEach(st => {
      if (!st.canvas.isConnected) { _live.delete(st); return; }
      st.paint();
    });
    _watch.forEach(f => f());
  }
  MOL.display = () => Object.assign({}, DISPLAY);
  MOL.setDisplay = setDisplay;

  // ---------------------------------------------------------------- colour

  /** @param {string} s a CSS colour as the tokens write them @returns {number[]} */
  function rgbOf(s) {
    s = String(s || "").trim();
    let m = /^#([0-9a-f]{3})$/i.exec(s);
    if (m) return m[1].split("").map(c => parseInt(c + c, 16));
    m = /^#([0-9a-f]{6})/i.exec(s);
    if (m) { const n = parseInt(m[1], 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
    m = /rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(s);
    if (m) return [+m[1], +m[2], +m[3]];
    return [14, 14, 16];
  }
  /** @param {number[]} a @param {number[]} b @param {number} t */
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }
  /** @param {number[]} a @param {number} k */
  function dim(a, k) { return [a[0] * k, a[1] * k, a[2] * k]; }
  /** @param {number[]} a */
  function css(a) { return `rgb(${Math.round(a[0])},${Math.round(a[1])},${Math.round(a[2])})`; }
  const WHITE = [255, 255, 255];
  const HOT = "#ffd166";

  // ---------------------------------------------------------------- rotation

  /* The orientation is a rotation MATRIX, not a yaw and a pitch. Two Euler
     angles with the pitch clamped at ±1.5 could never turn a molecule over,
     and a drag rotated about the molecule's axes rather than the screen's, so
     the same gesture did different things depending on where you had got to.
     Each drag step now rotates about the SCREEN's axes, which is the trackball
     Avogadro's navigate tool is: the near side follows the cursor. */
  /** @param {number[]} A @param {number[]} B */
  function mul(A, B) {
    const C = new Array(9);
    for (let r = 0; r < 3; r++) {
      for (let c = 0; c < 3; c++) {
        C[r * 3 + c] = A[r * 3] * B[c] + A[r * 3 + 1] * B[3 + c] + A[r * 3 + 2] * B[6 + c];
      }
    }
    return C;
  }
  /** @param {number} a */
  function rotY(a) { const c = Math.cos(a), s = Math.sin(a); return [c, 0, s, 0, 1, 0, -s, 0, c]; }
  /** @param {number} a */
  function rotX(a) { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, c, -s, 0, s, c]; }
  /** The orientation the old yaw/pitch pair named, so a caller's angles still
   *  mean what they meant. @param {number} yaw @param {number} pitch */
  function fromYawPitch(yaw, pitch) { return mul(rotX(pitch), rotY(yaw)); }
  /** Re-orthonormalize: a few hundred small rotations multiplied together drift,
   *  and a drifted matrix shears the molecule. @param {number[]} R */
  function ortho(R) {
    const a = [R[0], R[1], R[2]];
    let n = Math.hypot(a[0], a[1], a[2]) || 1;
    a[0] /= n; a[1] /= n; a[2] /= n;
    const b = [R[3], R[4], R[5]];
    const d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    b[0] -= d * a[0]; b[1] -= d * a[1]; b[2] -= d * a[2];
    n = Math.hypot(b[0], b[1], b[2]) || 1;
    b[0] /= n; b[1] /= n; b[2] /= n;
    const c = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    return [a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2]];
  }

  // ---------------------------------------------------------------- scene

  /** A Mayer bond order, as a picture: single, partial (one solid stick and one
   *  dashed — delocalized, an aromatic ring's 1.3–1.5), double, triple. The
   *  bands are wide on purpose: a Mayer order is not an integer, and the
   *  picture should not pretend it is one more precisely than it is.
   *  @param {number} o */
  function classify(o) {
    if (!(o >= 1.25)) return 1;
    if (o < 1.75) return 1.5;
    if (o < 2.4) return 2;
    return 3;
  }

  /** The direction a double bond's two sticks are spread along: in the plane
   *  of a neighbour, so a carbonyl's two lines lie in the molecule's plane and
   *  close up when it is seen edge-on, as Avogadro draws them. The neighbour
   *  with the most bonds of its own wins — inside a ring that is the next ring
   *  atom rather than a hydrogen, so a partial bond's dashed half falls inside
   *  the ring. @param {any[]} atoms @param {number[][]} nb
   *  @param {number} i @param {number} j @returns {number[]} */
  function planeDir(atoms, nb, i, j) {
    const A = atoms[i], B = atoms[j];
    let ux = B.x - A.x, uy = B.y - A.y, uz = B.z - A.z;
    const L = Math.hypot(ux, uy, uz) || 1;
    ux /= L; uy /= L; uz /= L;
    const cands = [];
    nb[i].forEach(c => { if (c !== j) cands.push([c, A]); });
    nb[j].forEach(c => { if (c !== i) cands.push([c, B]); });
    cands.sort((p, q) => nb[q[0]].length - nb[p[0]].length);
    for (const [c, from] of cands) {
      const C = atoms[c];
      let vx = C.x - from.x, vy = C.y - from.y, vz = C.z - from.z;
      const d = vx * ux + vy * uy + vz * uz;
      vx -= d * ux; vy -= d * uy; vz -= d * uz;
      const n = Math.hypot(vx, vy, vz);
      if (n > 0.1) return [vx / n, vy / n, vz / n];
    }
    // a lone diatomic: any perpendicular will do — pick the axis least along u
    const ax = Math.abs(ux) < 0.6 ? [1, 0, 0] : (Math.abs(uy) < 0.6 ? [0, 1, 0] : [0, 0, 1]);
    const px = uy * ax[2] - uz * ax[1], py = uz * ax[0] - ux * ax[2], pz = ux * ax[1] - uy * ax[0];
    const n = Math.hypot(px, py, pz) || 1;
    return [px / n, py / n, pz / n];
  }

  /** Everything about a structure that does not change when it is turned:
   *  bonds, the centre, the bounding sphere, and how each bond is drawn.
   *  Cached per atom list, so a frame of a drag is projection and painting
   *  only. @type {WeakMap<any[], any>} */
  const _scenes = new WeakMap();
  /** @param {any[]} atoms @param {any[]|null} [orders] [i, j, order] triples */
  function scene(atoms, orders) {
    if (!orders) { const hit = _scenes.get(atoms); if (hit) return hit; }
    const bs = bonds(atoms);
    let cx = 0, cy = 0, cz = 0;
    atoms.forEach(a => { cx += a.x; cy += a.y; cz += a.z; });
    const n = atoms.length || 1;
    cx /= n; cy /= n; cz /= n;
    let span = 1;
    atoms.forEach(a => { span = Math.max(span, Math.hypot(a.x - cx, a.y - cy, a.z - cz)); });
    const nb = atoms.map(() => []);
    bs.forEach(([i, j]) => { nb[i].push(j); nb[j].push(i); });
    const ord = new Map();
    (orders || []).forEach(o => {
      const i = o[0], j = o[1];
      if (i >= 0 && j >= 0) ord.set(Math.min(i, j) + "-" + Math.max(i, j), o[2]);
    });
    const info = bs.map(([i, j]) => {
      const o = ord.get(i + "-" + j);
      const order = o == null ? 1 : classify(o);
      return { i: i, j: j, n: order, off: order > 1 ? planeDir(atoms, nb, i, j) : null };
    });
    const sc = { bonds: info, cx: cx, cy: cy, cz: cz, span: span, hasOrders: ord.size > 0 };
    if (!orders) _scenes.set(atoms, sc);
    return sc;
  }

  // ---------------------------------------------------------------- painting

  /* The camera sits PERSPECTIVE bounding radii from the centre. Close enough
     that the near side of a molecule is visibly larger than the far side —
     4 radii puts the nearest atom at 1.33x and the farthest at 0.8x — and far
     enough that nothing distorts. The fit below allows for it exactly, so the
     scale is still a property of the molecule and not of the orientation. */
  const PERSPECTIVE = 4;
  /** How much a far atom fades toward the stage colour: the farthest one keeps
   *  40% of its own colour, the nearest all of it. */
  const FOG = 0.6;

  /** Project, sort and paint. Returns the screen positions and radii, which is
   *  what picking and measuring are done against — the projection is computed
   *  once per frame either way.
   *  @param {HTMLCanvasElement} canvas @param {any[]} atoms @param {any} sc scene()
   *  @param {{R: number[], zoom?: number, panX?: number, panY?: number,
   *           mark?: number[],
   *           measure?: {from: number|null, to?: number|null,
   *                      cur?: {x: number, y: number}|null}|null,
   *           labels?: boolean, gizmo?: boolean, hotAxis?: string,
   *           gizmoBottom?: number,
   *           chain?: {atoms: number[], cur?: {x: number, y: number}|null}|null,
   *           hilite?: number[]}} view */
  function render(canvas, atoms, sc, view) {
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

    const cssv = getComputedStyle(document.documentElement);
    const ground = rgbOf((cssv.getPropertyValue("--stage") || "").trim() || "#0e0e10");
    const mono = (cssv.getPropertyValue("--font-mono") || "").trim() || "monospace";

    const st = STYLES[DISPLAY.style] || STYLES.ball;
    const wire = DISPLAY.style === "wire", fill = DISPLAY.style === "fill";
    const R = view.R;
    const vis = atoms.map(a => DISPLAY.hydrogens || a.el !== "H");

    // The scale is fitted to the molecule's BOUNDING SPHERE, not to how wide it
    // happens to look from here (DESIGN.md §17.4e). A rotation preserves
    // distance from the centre, so this number does not change while you turn
    // the structure. `ball` is the room the outermost sphere needs, and `kfit`
    // the most the perspective can push a point on that sphere outward —
    // max r·sinθ·D/(D − r·cosθ) is D/√(D² − r²), exact rather than generous.
    const span = sc.span;
    const D = span * PERSPECTIVE;
    let ball = st.bond;
    atoms.forEach(a => { ball = Math.max(ball, st.atom(a.el)); });
    const kfit = D / Math.sqrt(D * D - span * span);
    const s = ((Math.min(W, H) / 2 - 18) / ((span + ball) * kfit)) * (view.zoom || 1);
    const ox = W / 2 + (view.panX || 0), oy = H / 2 + (view.panY || 0);

    /** a model-space point to the screen: {X, Y, z, k}, k the perspective
     *  magnification at that depth @param {number} x @param {number} y @param {number} z */
    function project(x, y, z) {
      const x0 = x - sc.cx, y0 = y - sc.cy, z0 = z - sc.cz;
      const rx = R[0] * x0 + R[1] * y0 + R[2] * z0;
      const ry = R[3] * x0 + R[4] * y0 + R[5] * z0;
      const rz = R[6] * x0 + R[7] * y0 + R[8] * z0;
      const k = D / (D - Math.min(rz, D * 0.9));
      return { X: ox + rx * s * k, Y: oy - ry * s * k, z: rz, k: k };
    }
    const pts = atoms.map(a => Object.assign(project(a.x, a.y, a.z), { el: a.el }));
    /** 0 at the near edge of the bounding sphere, FOG at the far one @param {number} z */
    const fogAt = (z) => FOG * Math.min(1, Math.max(0, (span - z) / (2 * span)));
    /** @param {number[]} c @param {number} f */
    const fog = (c, f) => mix(c, ground, f);

    // A ball is never thinner than the stick it sits on — below that it reads
    // as a kink in the line, not an atom (DESIGN.md §17.4e). That is the only
    // floor, stated against the stick rather than as a pixel count.
    const stickPx = wire ? 1.6 : Math.max(1.6, 2 * st.bond * s);
    /** drawn radius in px at the atom's own depth @param {any} p */
    const radOf = (p) => {
      if (wire) return 3;
      if (fill) return Math.max(1.5, st.atom(p.el) * s * p.k);
      return Math.max(stickPx * 0.62 * p.k, st.atom(p.el) * s * p.k);
    };
    const rad = (el, p) => radOf(p || { el: el, k: 1 });

    const items = [];
    if (!wire) pts.forEach((p, i) => { if (vis[i]) items.push({ z: p.z, kind: 0, i: i }); });
    /** @type {[number, number][]} the bonds actually on screen, for hit-testing */
    const shown = [];
    const bonded = new Uint8Array(atoms.length);
    if (!fill) {
      sc.bonds.forEach((b, k) => {
        if (!vis[b.i] || !vis[b.j]) return;
        shown.push([b.i, b.j]);
        bonded[b.i] = 1; bonded[b.j] = 1;
        // each HALF is its own item, at its own depth: a bond running from
        // the front of the molecule to the back is in front of one atom and
        // behind the other, and one depth for the whole stick gets one of
        // those wrong
        items.push({ z: (3 * pts[b.i].z + pts[b.j].z) / 4, kind: 1, k: k, from: b.i, to: b.j });
        items.push({ z: (pts[b.i].z + 3 * pts[b.j].z) / 4, kind: 1, k: k, from: b.j, to: b.i });
      });
    }
    // an atom with nothing bonded to it would vanish from a wireframe
    if (wire) pts.forEach((p, i) => { if (vis[i] && !bonded[i]) items.push({ z: p.z, kind: 2, i: i }); });
    items.sort((u, v) => u.z - v.z);

    const mark = view.mark || [];
    const labels = view.labels !== false && DISPLAY.labels !== "off";

    /** A shaded stick from (x1,y1) to (x2,y2): a dark rim one pixel outside it,
     *  then a gradient ACROSS it — lit edge, highlight, body, shadowed edge —
     *  which is what makes a flat stroke read as a cylinder. The light is from
     *  the upper left, as on the spheres.
     *  @param {number} x1 @param {number} y1 @param {number} x2 @param {number} y2
     *  @param {number} w @param {number[]} c @param {number} f @param {boolean} dash */
    function stick(x1, y1, x2, y2, w, c, f, dash) {
      g.lineCap = dash ? "butt" : "round";
      g.setLineDash(dash ? [Math.max(2, w * 1.1), Math.max(2, w * 0.9)] : []);
      if (wire) {
        g.strokeStyle = css(fog(c, f)); g.lineWidth = w;
        g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
        return;
      }
      const dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy);
      let nx = -0.7071, ny = -0.7071;
      if (L > 1e-3) { nx = -dy / L; ny = dx / L; if (nx + ny > 0) { nx = -nx; ny = -ny; } }
      const h = w / 2;
      g.strokeStyle = css(fog(dim(c, 0.22), f));
      g.lineWidth = w + Math.max(1, w * 0.18);
      g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
      const gr = g.createLinearGradient(x1 + nx * h, y1 + ny * h, x1 - nx * h, y1 - ny * h);
      gr.addColorStop(0, css(fog(dim(c, 0.78), f)));
      gr.addColorStop(0.28, css(fog(mix(c, WHITE, 0.42), f)));
      gr.addColorStop(0.55, css(fog(c, f)));
      gr.addColorStop(1, css(fog(dim(c, 0.4), f)));
      g.strokeStyle = gr; g.lineWidth = w;
      g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
    }

    /** @param {any} it */
    function halfBond(it) {
      const b = sc.bonds[it.k];
      const A = atoms[it.from], B = atoms[it.to];
      const ux = B.x - A.x, uy = B.y - A.y, uz = B.z - A.z;
      const L = Math.hypot(ux, uy, uz) || 1;
      // Start at the sphere's surface, not its centre: a stick coming toward
      // the viewer is drawn over its own ball, and from the centre it would
      // poke through the middle of it.
      let t0 = 0;
      if (DISPLAY.style === "ball") {
        const ra = st.atom(A.el), rb = st.bond;
        t0 = Math.min(0.45, Math.sqrt(Math.max(0, ra * ra - rb * rb)) / L);
      }
      const sx0 = A.x + ux * t0, sy0 = A.y + uy * t0, sz0 = A.z + uz * t0;
      const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2, mz = (A.z + B.z) / 2;
      const hot = mark.length === 2 && mark.indexOf(it.from) >= 0 && mark.indexOf(it.to) >= 0;
      const c = rgbOf(hot ? HOT : color(A.el));
      const f = hot ? 0 : fogAt(it.z);
      const k = (pts[it.from].k * 3 + pts[it.to].k) / 4;
      const w0 = (wire ? (hot ? 3 : 1.8) : stickPx * (hot ? 1.25 : 1)) * k;

      // the sticks that make up this bond: [offset in Å, width in px, dashed]
      let parts = [[0, w0, false]];
      if (st.multi && DISPLAY.orders && b.n > 1 && b.off) {
        const w1 = Math.max(1.2, w0 * (b.n === 3 ? 0.5 : 0.62));
        // centre-to-centre spacing: whichever is wider, the Å spacing or
        // enough pixels that two sticks do not merge into one fat one on a
        // thumbnail
        const gap = Math.max(b.n === 3 ? 0.13 : 0.18, (w1 * 1.4) / (s * k));
        parts = b.n === 3 ? [[-gap, w1, false], [0, w1, false], [gap, w1, false]]
              : [[-gap / 2, w1, false], [gap / 2, w1, b.n === 1.5]];
      }
      parts.forEach(([o, w, dash]) => {
        const ofx = b.off ? b.off[0] * /** @type {number} */ (o) : 0;
        const ofy = b.off ? b.off[1] * /** @type {number} */ (o) : 0;
        const ofz = b.off ? b.off[2] * /** @type {number} */ (o) : 0;
        const P = project(sx0 + ofx, sy0 + ofy, sz0 + ofz);
        const Q = project(mx + ofx, my + ofy, mz + ofz);
        stick(P.X, P.Y, Q.X, Q.Y, /** @type {number} */ (w), c, f, /** @type {boolean} */ (dash));
      });
      g.setLineDash([]);
    }

    /** A lit sphere: a highlight up and to the left, the element's colour, and
     *  a rim dark enough to cut a near atom out against a far one behind it.
     *  @param {number} i */
    function sphere(i) {
      const p = pts[i], r = radOf(p), X = p.X, Y = p.Y;
      const c = rgbOf(color(p.el)), f = fogAt(p.z);
      const gr = g.createRadialGradient(X - r * 0.38, Y - r * 0.42, r * 0.04, X, Y, r);
      gr.addColorStop(0, css(fog(mix(c, WHITE, 0.72), f)));
      gr.addColorStop(0.28, css(fog(mix(c, WHITE, 0.16), f)));
      gr.addColorStop(0.68, css(fog(c, f)));
      gr.addColorStop(1, css(fog(dim(c, 0.38), f)));
      g.fillStyle = gr;
      g.beginPath(); g.arc(X, Y, r, 0, Math.PI * 2); g.fill();
      g.strokeStyle = css(fog(dim(c, 0.2), f));
      g.lineWidth = Math.min(1.6, Math.max(0.8, r * 0.07));
      g.stroke();
    }

    /** @param {number} i */
    function label(i) {
      const p = pts[i];
      const txt = DISPLAY.labels === "index" ? p.el + (i + 1) : p.el;
      const r = radOf(p);
      const fs = Math.round(Math.max(9, Math.min(13, wire ? 11 : r * 0.95)));
      g.font = `600 ${fs}px ${mono}`;
      g.textAlign = "center"; g.textBaseline = "middle";
      g.lineJoin = "round"; g.lineWidth = 3;
      g.strokeStyle = "rgba(0,0,0,.72)";
      g.strokeText(txt, p.X, p.Y);
      g.fillStyle = "#fff";
      g.fillText(txt, p.X, p.Y);
      g.textAlign = "left"; g.textBaseline = "alphabetic";
    }

    /** the selection ring, drawn with its atom so a nearer atom still covers it
     *  @param {number} i */
    function ring(i) {
      const p = pts[i];
      g.strokeStyle = HOT; g.lineWidth = 2;
      g.beginPath(); g.arc(p.X, p.Y, radOf(p) + 3, 0, Math.PI * 2); g.stroke();
    }

    items.forEach(it => {
      if (it.kind === 1) halfBond(it);
      else if (it.kind === 0) {
        sphere(it.i);
        if (mark.indexOf(it.i) >= 0) ring(it.i);
        if (labels) label(it.i);
      } else {                                     // a wireframe's lone atom
        const p = pts[it.i];
        g.fillStyle = css(fog(rgbOf(color(p.el)), fogAt(p.z)));
        g.beginPath(); g.arc(p.X, p.Y, 2.5 * p.k, 0, Math.PI * 2); g.fill();
      }
    });
    if (wire) {
      // a wireframe has no spheres to carry a ring or a label, so both go on
      // top once the lines are down
      mark.forEach(i => { if (pts[i] && mark.length === 1) ring(i); });
      if (labels) pts.forEach((p, i) => { if (vis[i]) label(i); });
    }

    // The measurement, drawn over everything - INCLUDING while the button is
    // still down. `to` is the atom under the cursor (null while over nothing),
    // `cur` is the cursor itself, so the drag is visible from the first pixel
    // and the distance appears only once there is one to state.
    const m = view.measure;
    if (m && m.from != null && pts[m.from]) {
      const A = pts[m.from];
      const ax = A.X, ay = A.Y;
      let tx, ty, text = null;
      if (m.to != null && pts[m.to]) {
        tx = pts[m.to].X; ty = pts[m.to].Y;
        text = MOL.distance(atoms[m.from], atoms[m.to]).toFixed(3) + " Å";
      } else if (m.cur) { tx = m.cur.x; ty = m.cur.y; }
      if (tx !== undefined) {
        const mc = (cssv.getPropertyValue("--crit-de") || "").trim() || "#4cc9f0";
        g.save();
        g.setLineDash([5, 4]); g.strokeStyle = mc; g.lineWidth = 1.6;
        g.beginPath(); g.moveTo(ax, ay); g.lineTo(tx, ty); g.stroke();
        g.restore();
        g.strokeStyle = mc; g.lineWidth = 2;
        g.beginPath(); g.arc(ax, ay, radOf(A) + 4, 0, Math.PI * 2); g.stroke();
        if (m.to != null && pts[m.to]) {
          g.beginPath(); g.arc(tx, ty, radOf(pts[m.to]) + 4, 0, Math.PI * 2); g.stroke();
        }
        if (text) {
          g.font = "11px " + mono;
          const lx = (ax + tx) / 2, ly = (ay + ty) / 2 - 7;
          const tw = g.measureText(text).width;
          g.fillStyle = css(ground);
          g.fillRect(lx - tw / 2 - 4, ly - 11, tw + 8, 15);
          g.fillStyle = mc; g.textAlign = "center"; g.textBaseline = "middle";
          g.fillText(text, lx, ly - 3);
          g.textAlign = "left"; g.textBaseline = "alphabetic";
        }
      }
    }

    // A chain of atoms (opts.onChain): the one being dragged out, through the
    // atoms it has collected to the cursor, and the one a caller highlights
    // (setHighlight) — a constraint's atoms, joined in their order, since an
    // angle's vertex is the MIDDLE atom and a ring alone would not say which.
    // Both carry the value they name, so the drag says "bond, 1.43 Å" before it
    // is released.
    /** @param {number[]} idx @param {{x: number, y: number}|null|undefined} cur
     *  @param {string} col @param {boolean} dashed */
    function chainPath(idx, cur, col, dashed) {
      const ps = idx.map(i => pts[i]).filter(Boolean);
      if (!ps.length) return;
      g.save();
      g.strokeStyle = col; g.lineWidth = 2; g.lineJoin = "round";
      g.setLineDash(dashed ? [5, 4] : []);
      g.beginPath(); g.moveTo(ps[0].X, ps[0].Y);
      ps.slice(1).forEach(p => g.lineTo(p.X, p.Y));
      if (cur) g.lineTo(cur.x, cur.y);
      if (ps.length > 1 || cur) g.stroke();
      g.setLineDash([]);
      ps.forEach(p => { g.beginPath(); g.arc(p.X, p.Y, radOf(p) + 4, 0, Math.PI * 2); g.stroke(); });
      const mv = idx.length >= 2 ? MOL.measureChain(atoms, idx) : null;
      if (mv) {
        const last = ps[ps.length - 1];
        const text = mv.value.toFixed(mv.unit === "Å" ? 3 : 1) + " " + mv.unit;
        g.font = "11px " + mono;
        const tw = g.measureText(text).width;
        const lx = Math.min(W - tw / 2 - 6, Math.max(tw / 2 + 6, last.X)),
              ly = Math.max(14, last.Y - radOf(last) - 14);
        g.fillStyle = css(ground);
        g.fillRect(lx - tw / 2 - 4, ly - 8, tw + 8, 15);
        g.fillStyle = col; g.textAlign = "center"; g.textBaseline = "middle";
        g.fillText(text, lx, ly);
        g.textAlign = "left"; g.textBaseline = "alphabetic";
      }
      g.restore();
    }
    if (view.hilite && view.hilite.length) chainPath(view.hilite, null, HOT, false);
    if (view.chain && view.chain.atoms.length) {
      chainPath(view.chain.atoms, view.chain.cur,
                (cssv.getPropertyValue("--crit-de") || "").trim() || "#4cc9f0", true);
    }

    // The axis marker, last so nothing is drawn over it.
    if (view.gizmo) {
      const arms = gizmoArms(H, R, view.gizmoBottom);
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

    return { pts: pts, sx: (p) => p.X, sy: (p) => p.Y, rad: rad, radOf: radOf,
             bonds: shown, vis: vis, W: W, H: H };
  }

  const DEFAULT_YAW = 0.62, DEFAULT_PITCH = -0.42;

  /** Draw a still.
   *  @param {HTMLCanvasElement} canvas @param {any[]} atoms
   *  @param {{yaw?: number, pitch?: number}} [view] */
  MOL.draw = function (canvas, atoms, view) {
    return render(canvas, atoms, scene(atoms || []), {
      R: fromYawPitch(view && view.yaw != null ? view.yaw : DEFAULT_YAW,
                      view && view.pitch != null ? view.pitch : DEFAULT_PITCH),
      labels: false,
    });
  };

  /* The three axis views, and the marker in the corner that IS the control for
     them (DESIGN.md §17.4f). */
  const AXES = [
    { name: "x", yaw: Math.PI / 2, pitch: 0, v: [1, 0, 0], tint: "--crit-maxs" },
    { name: "y", yaw: 0, pitch: Math.PI / 2, v: [0, 1, 0], tint: "--crit-rmsg" },
    { name: "z", yaw: 0, pitch: 0, v: [0, 0, 1], tint: "--crit-de" },
  ];
  const GIZMO = { ox: 40, arm: 24, lab: 8, hit: 14 };

  /** Where each axis arm's tip lands, in canvas pixels, for a given rotation.
   *  One function so the arm that is drawn and the arm that is clicked cannot
   *  disagree — the same reason the cube name has one speller.
   *  @param {number} H canvas css height @param {number[]} R
   *  @param {number} [bottom] px from the bottom edge — a stage with a caption
   *  down there says so, or the marker would be drawn over the writing */
  function gizmoArms(H, R, bottom) {
    const oy = H - (bottom || GIZMO.ox);
    return AXES.map((ax, i) => {
      const [x0, y0, z0] = ax.v;
      const x1 = R[0] * x0 + R[1] * y0 + R[2] * z0;
      const y1 = R[3] * x0 + R[4] * y0 + R[5] * z0;
      const z2 = R[6] * x0 + R[7] * y0 + R[8] * z0;
      return { i: i, name: ax.name, tint: ax.tint, z: z2,
               ox: GIZMO.ox, oy: oy,
               x: GIZMO.ox + x1 * GIZMO.arm, y: oy - y1 * GIZMO.arm,
               lx: GIZMO.ox + x1 * (GIZMO.arm + GIZMO.lab),
               ly: oy - y1 * (GIZMO.arm + GIZMO.lab) };
    });
  }

  /** The axis arm under a point, or null. The tip and its label are the target,
   *  not the line: two arms nearly edge-on cross near the origin, and a click
   *  there cannot mean one of them. @param {number} H @param {number[]} R
   *  @param {number|undefined} bottom @param {number} X @param {number} Y */
  function armAt(H, R, bottom, X, Y) {
    let best = null, bd = GIZMO.hit;
    gizmoArms(H, R, bottom).forEach(a => {
      const d = Math.hypot(a.lx - X, a.ly - Y);
      // a near arm wins a tie: it is the one the reader sees on top
      if (d < bd || (d === bd && best && a.z > best.z)) { bd = d; best = a; }
    });
    return best;
  }

  const ZOOM_MIN = 0.25, ZOOM_MAX = 25;

  /** An interactive stage on `canvas`.
   *
   *  Drag turns it (about the screen's axes — the near side follows the
   *  cursor), Shift+drag or a middle-drag moves it, the wheel zooms about the
   *  cursor, a click picks an atom or a bond, and a right-drag from one atom to
   *  another measures between them. Every one of those has a key too (the drag
   *  is an addition, never the only way): arrows turn, + and − zoom, 0 puts the
   *  view back, x/y/z look down an axis, 1–4 pick the display type, H shows or
   *  hides hydrogens and L cycles the labels.
   *
   *  @param {HTMLCanvasElement} canvas
   *  @param {{onPick?: (p: any) => void, yaw?: number, pitch?: number,
   *           gizmoBottom?: number, wheel?: string, labels?: boolean,
   *           onChain?: (atoms: number[]) => void}} [opts]
   *    onChain (opt-in; the builder's constraint picking) changes what a left
   *    drag that STARTS ON AN ATOM does: it collects the atoms it passes over,
   *    up to four, and hands them over on release when there are at least two
   *    (dragging back onto the previous atom takes the last one off). A drag
   *    from empty space still turns the structure, and a press-and-release on
   *    one atom is still a click. Without it, nothing here changes.
   *    gizmoBottom lifts the axis marker clear of whatever the stage draws in
   *    its bottom-left corner. wheel "plain" zooms on a bare wheel — for a stage
   *    that owns its window; anywhere else the wheel scrolls the page, as it
   *    must in a report, and Ctrl+wheel (a touchpad pinch) zooms. labels false
   *    keeps atom labels off a stage too small for them, the cell thumbnail. */
  MOL.mount = function (canvas, opts) {
    opts = opts || {};
    const home = fromYawPitch(opts.yaw != null ? opts.yaw : DEFAULT_YAW,
                              opts.pitch != null ? opts.pitch : DEFAULT_PITCH);
    /** @type {any[]} */
    let atoms = [];
    let sc = scene(atoms);
    let R = home.slice(), zoom = 1, panX = 0, panY = 0;
    /** @type {any} last projection, for hit-testing */
    let proj = null;
    /** @type {number[]} the picked atom(s) */
    let mark = [];
    /** The measurement in flight or pinned: {from, to, cur, pinned}.
     *  @type {any} */
    let measure = null;
    /** @type {any} */
    let drag = null;
    /** The chain being dragged out (opts.onChain): {atoms, cur, x, y, moved}.
     *  @type {any} */
    let chain = null;
    /** atoms a caller asked to be shown joined (setHighlight) @type {number[]} */
    let hilite = [];

    /** The axis arm the cursor is over, so the marker can say it is a target
     *  before it is pressed. @type {string} */
    let hotAxis = "";

    const handle = { canvas: canvas, paint: paint };
    function paint() {
      _live.add(handle);
      proj = render(canvas, atoms, sc, {
        R: R, zoom: zoom, panX: panX, panY: panY, mark: mark, measure: measure,
        gizmo: true, hotAxis: hotAxis, gizmoBottom: opts.gizmoBottom,
        labels: opts.labels !== false, chain: chain, hilite: hilite });
    }

    /** Turn the structure down one axis. @param {string} name x, y or z */
    function showAxis(name) {
      const a = AXES.filter(x => x.name === name)[0];
      if (!a) return;
      R = fromYawPitch(a.yaw, a.pitch);
      paint();
    }

    /** Put the camera back where the stage opened: orientation, zoom and pan. */
    function resetView() {
      R = home.slice(); zoom = 1; panX = 0; panY = 0;
      paint();
    }

    /** Zoom by `f` about a canvas point (the centre when none is given), so the
     *  thing under the cursor stays under it. @param {number} f
     *  @param {number} [qx] @param {number} [qy] */
    function zoomBy(f, qx, qy) {
      const box = canvas.getBoundingClientRect();
      const W = box.width, H = box.height;
      const nz = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoom * f));
      const k = nz / zoom;
      if (qx == null) { qx = W / 2; qy = H / 2; }
      panX = qx - W / 2 - (qx - W / 2 - panX) * k;
      panY = qy - H / 2 - (qy - H / 2 - panY) * k;
      zoom = nz;
      paint();
    }

    /** The arm under a canvas point, or null. @param {{x: number, y: number}} p */
    function armUnder(p) {
      const box = canvas.getBoundingClientRect();
      return armAt(Math.round(box.height), R, opts.gizmoBottom, p.x, p.y);
    }

    /** @param {MouseEvent} e @returns {{x: number, y: number}} */
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
        if (!proj.vis[i]) return;
        const d = Math.hypot(p.X - X, p.Y - Y);
        if (d <= proj.radOf(p) + 3 && p.z > bz) { best = i; bz = p.z; }
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
        const ax = A.X, ay = A.Y, bx = B.X, by = B.Y;
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
    // a middle press would otherwise start Chromium's autoscroll
    canvas.addEventListener("mousedown", e => { if (e.button === 1) e.preventDefault(); });

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
      const pan = e.button === 1 || (e.button === 0 && e.shiftKey);
      // the marker is a control, not part of the scene: pressing an arm must
      // not also start turning the structure under it
      if (!pan && armUnder(p)) return;
      if (!pan && opts.onChain && e.button === 0) {
        const at = atomAt(p.x, p.y);
        if (at != null) {        // on an atom: this drag names atoms, not a turn
          chain = { atoms: [at], cur: null, x: e.clientX, y: e.clientY, moved: 0 };
          return;
        }
      }
      drag = { x: e.clientX, y: e.clientY, lx: e.clientX, ly: e.clientY,
               moved: 0, pan: pan };
      canvas.style.cursor = pan ? "move" : "grabbing";
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
      if (chain) {
        const p = local(e);
        chain.moved = Math.max(chain.moved, Math.hypot(e.clientX - chain.x, e.clientY - chain.y));
        if (chain.moved <= 4) return;              // still a click
        canvas.style.cursor = "crosshair";
        chain.cur = p;
        const over = atomAt(p.x, p.y), n = chain.atoms.length;
        if (over != null) {
          // back onto the atom before the last takes the last one off, so a
          // wrong turn is undone without starting again
          if (n >= 2 && over === chain.atoms[n - 2]) chain.atoms.pop();
          else if (n < 4 && chain.atoms.indexOf(over) < 0) chain.atoms.push(over);
        }
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
      const dx = e.clientX - drag.lx, dy = e.clientY - drag.ly;
      drag.lx = e.clientX; drag.ly = e.clientY;
      drag.moved = Math.max(drag.moved, Math.hypot(e.clientX - drag.x, e.clientY - drag.y));
      if (drag.pan) { panX += dx; panY += dy; }
      else R = ortho(mul(mul(rotX(dy * 0.01), rotY(dx * 0.01)), R));
      paint();
    });

    canvas.addEventListener("pointerup", e => {
      if (chain && e.button === 0) {
        const c = chain;
        chain = null;
        canvas.style.cursor = "grab";
        if (c.atoms.length >= 2) { paint(); if (opts.onChain) opts.onChain(c.atoms.slice()); }
        else if (c.moved > 4) paint();             // dragged off into nothing
        else clickAt(local(e));                    // a click on the atom
        return;
      }
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
      if (!drag) {
        const arm = armUnder(up);
        if (arm) showAxis(arm.name);               // the marker was pressed
        return;
      }
      const moved = drag.moved, panned = drag.pan;
      drag = null;
      canvas.style.cursor = "grab";
      if (moved > 4 || panned) return;             // a turn or a move, not a click
      clickAt(up);
    });
    /** A click: pick what is under it. @param {{x: number, y: number}} up */
    function clickAt(up) {
      const h = hitAt(up.x, up.y);
      if (!h) { mark = []; paint(); if (opts.onPick) opts.onPick(null); return; }
      // clicking what is already selected lets it go
      const next = h.kind === "atom" ? [h.i] : [h.i, h.j];
      const same = next.length === mark.length && next.every((v, k) => v === mark[k]);
      mark = same ? [] : next;
      paint();
      if (opts.onPick) opts.onPick(same ? null : Object.assign({ atoms: atoms }, h));
    }
    canvas.addEventListener("pointercancel", () => {
      drag = null; chain = null; canvas.style.cursor = "grab";
    });
    canvas.addEventListener("pointerleave", () => {
      if (!hotAxis) return;
      hotAxis = ""; canvas.style.cursor = "grab"; canvas.title = ""; paint();
    });

    canvas.addEventListener("wheel", e => {
      // in a report the wheel is the page's: a stage that took it would trap
      // the reader halfway down. Ctrl+wheel is also what a touchpad pinch sends.
      if (opts.wheel !== "plain" && !e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      const p = local(e);
      zoomBy(Math.exp(-dy * 0.0015), p.x, p.y);
    }, { passive: false });

    if (!canvas.hasAttribute("tabindex")) canvas.setAttribute("tabindex", "0");
    canvas.addEventListener("keydown", e => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;   // never take a shortcut
      const step = 0.16;
      const k = e.key;
      if (k === "ArrowLeft") R = ortho(mul(rotY(-step), R));
      else if (k === "ArrowRight") R = ortho(mul(rotY(step), R));
      else if (k === "ArrowUp") R = ortho(mul(rotX(-step), R));
      else if (k === "ArrowDown") R = ortho(mul(rotX(step), R));
      else if (k === "+" || k === "=") { zoomBy(1.25); e.preventDefault(); return; }
      else if (k === "-" || k === "_") { zoomBy(0.8); e.preventDefault(); return; }
      else if (k === "0" || k === "Home") { resetView(); e.preventDefault(); return; }
      else if (k === "x" || k === "y" || k === "z") {
        // the marker is drawn, so it cannot be tabbed to: these keys are how it
        // is reached without a pointer, and the stage already takes focus
        showAxis(k);
        e.preventDefault();
        return;
      } else if (k >= "1" && k <= "4") {
        setDisplay({ style: STYLE_ORDER[Number(k) - 1] });
        e.preventDefault();
        return;
      } else if (k === "h" || k === "H") {
        setDisplay({ hydrogens: !DISPLAY.hydrogens });
        e.preventDefault();
        return;
      } else if (k === "l" || k === "L") {
        const at = LABELS.findIndex(l => l[0] === DISPLAY.labels);
        setDisplay({ labels: LABELS[(at + 1) % LABELS.length][0] });
        e.preventDefault();
        return;
      } else if (k === "Escape") {
        measure = null; mark = []; chain = null;
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
      /** @param {any[]} a
       *  @param {{orders?: any[]}} [extra] orders: [i, j, Mayer order] for the
       *    bonds the run itself analysed, drawn as multiple bonds. Only a
       *    structure the orders were computed FOR may carry them — a trajectory
       *    frame is a different geometry. */
      setAtoms(a, extra) {
        atoms = a || [];
        const o = extra && extra.orders && extra.orders.length ? extra.orders : null;
        sc = scene(atoms, o);
        mark = []; measure = null; chain = null; hilite = []; paint();
      },
      /** Show these atoms joined, in this order — a constraint's atoms; [] to
       *  clear. Dropped by setAtoms, whose structure the indices may not fit.
       *  @param {number[]} idx */
      setHighlight(idx) {
        hilite = (idx || []).filter(i => Number.isInteger(i) && i >= 0 && i < atoms.length);
        paint();
      },
      /** the chain in flight, for anything that needs to know */
      chainState() { return chain ? { atoms: chain.atoms.slice(), moved: chain.moved } : null; },
      /** Turn the structure down one axis — what pressing that arm of the
       *  marker does, for a caller that wants it without a click.
       *  @param {string} name x, y or z */
      showAxis: showAxis,
      resetView: resetView,
      /** @param {number} f */
      zoomBy(f) { zoomBy(f); },
      /** whether this structure came with bond orders to draw */
      hasOrders: () => !!sc.hasOrders,
      wheel: () => opts.wheel === "plain" ? "plain" : "ctrl",
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
                 screen: proj.pts.map(p => [Math.round(p.X), Math.round(p.Y),
                                            Math.round(proj.radOf(p))]) };
      },
      destroy() { if (ro) ro.disconnect(); _live.delete(handle); },
    };
  };

  // ---------------------------------------------------------------- the menu

  let _menuSeq = 0;

  /** The Display menu: the display type, hydrogens, multiple bonds, labels and
   *  the way back to the starting view — Avogadro's Display Types panel, in a
   *  popover because a stage in a reading column has no room for a panel.
   *
   *  `btn` opens it; it is appended to `host` (a stage's .stageacts, where it
   *  falls in under the button, or a stage itself, where it floats); `stage` is
   *  the MOL.mount handle whose structure it describes. The settings are the
   *  app's, not the stage's, so every other stage follows.
   *  @param {HTMLElement} btn @param {HTMLElement} host @param {any} stage
   *  @returns {{close: () => void, setStage: (s: any) => void}} */
  MOL.displayMenu = function (btn, host, stage) {
    const menu = document.createElement("div");
    menu.className = "dispmenu";
    menu.hidden = true;
    menu.id = "dispmenu-" + (++_menuSeq);
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Display");
    btn.setAttribute("aria-controls", menu.id);
    btn.setAttribute("aria-expanded", "false");
    host.appendChild(menu);

    function fill() {
      // keep the focus on the control that was just pressed: the menu redraws
      // itself on every change, and a keyboard user would otherwise be thrown
      // back to the document
      const act = /** @type {HTMLElement} */ (document.activeElement);
      const focusKey = act && menu.contains(act)
        ? ["data-style", "data-lab", "data-opt", "data-reset"]
            .map(a => act.hasAttribute(a) ? `[${a}="${act.getAttribute(a)}"]` : "")
            .filter(Boolean)[0] : "";
      const d = DISPLAY;
      const st = STYLES[d.style];
      const orders = !!(stage && stage.hasOrders());
      const multiNote = !orders
        ? "No bond orders for this structure — every bond is drawn single."
        : !st.multi ? STYLES[d.style].label + " draws every bond single."
        : "From the run's Mayer bond orders; a dashed half is a partial bond.";
      const zoomNote = (stage && stage.wheel() === "plain" ? "Scroll" : "Ctrl+scroll")
        + " zooms · Shift+drag or middle-drag moves it";
      menu.innerHTML = `
        <div class="dmh">Style</div>
        <div class="seg" role="group" aria-label="Display type">${STYLE_ORDER.map(k =>
          `<button type="button" data-style="${k}" aria-pressed="${d.style === k}"
             title="${STYLES[k].label} (${STYLES[k].key})">${STYLES[k].label.replace("&", "&amp;")}</button>`
        ).join("")}</div>
        <label class="dmrow"><input type="checkbox" data-opt="hydrogens"${
          d.hydrogens ? " checked" : ""}> Hydrogens <kbd>H</kbd></label>
        <label class="dmrow"${orders && st.multi ? "" : ' aria-disabled="true"'}><input
          type="checkbox" data-opt="orders"${d.orders ? " checked" : ""}${
          orders && st.multi ? "" : " disabled"}> Multiple bonds</label>
        <p class="dmnote">${multiNote}</p>
        <div class="dmh">Labels <kbd>L</kbd></div>
        <div class="seg" role="group" aria-label="Atom labels">${LABELS.map(l =>
          `<button type="button" data-lab="${l[0]}" aria-pressed="${d.labels === l[0]}">${l[1]}</button>`
        ).join("")}</div>
        <div class="dmfoot"><button class="btn btn-sm btn-ghost" type="button"
          data-reset="1">Reset view <kbd>0</kbd></button></div>
        <p class="dmnote">${zoomNote}</p>`;
      if (focusKey) {
        const again = /** @type {HTMLElement|null} */ (menu.querySelector(focusKey));
        if (again) again.focus();
      }
    }

    function refresh() {
      if (!menu.isConnected) { _watch.delete(refresh); return; }
      if (!menu.hidden) fill();
    }
    _watch.add(refresh);

    /** @param {PointerEvent} e */
    function outside(e) {
      const t = /** @type {Node} */ (e.target);
      if (!menu.contains(t) && !btn.contains(t)) close();
    }
    function open() {
      fill();
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      document.addEventListener("pointerdown", outside, true);
      const first = /** @type {HTMLElement|null} */ (menu.querySelector('[aria-pressed="true"]'));
      if (first) first.focus();
    }
    function close() {
      if (menu.hidden) return;
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", outside, true);
    }

    btn.addEventListener("click", () => { if (menu.hidden) open(); else close(); });
    menu.addEventListener("keydown", e => {
      if (e.key !== "Escape") return;
      // the menu's Escape is the menu's: the pop-out window closes on Escape
      // too, and one press should not do both
      e.stopPropagation(); e.preventDefault();
      close(); btn.focus();
    });
    menu.addEventListener("click", e => {
      const b = /** @type {HTMLElement|null} */ (
        /** @type {HTMLElement} */ (e.target).closest("[data-style],[data-lab],[data-reset]"));
      if (!b || !menu.contains(b)) return;
      if (b.dataset.style) setDisplay({ style: b.dataset.style });
      else if (b.dataset.lab) setDisplay({ labels: b.dataset.lab });
      else if (b.dataset.reset && stage) stage.resetView();
    });
    menu.addEventListener("change", e => {
      const i = /** @type {HTMLInputElement} */ (e.target);
      if (i.dataset && i.dataset.opt) setDisplay({ [i.dataset.opt]: i.checked });
    });

    return {
      close: close,
      /** the pop-out's stage is created after its button exists @param {any} s */
      setStage(s) { stage = s; refresh(); },
    };
  };
})();
