// @ts-check
/* ============================================================
   tests/web/draw2d.test.js — plain-Node unit tests for the 2D drawing's
   pure core (web/next/draw2d.js: NB.draw2d.core). No npm dependencies, no
   DOM: the file only defines functions at load time, so it runs in a vm
   context that holds nothing but an empty NB.

       node tests/web/draw2d.test.js

   Exit code 0 == all tests passed.
   ============================================================ */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(
  path.join(__dirname, "..", "..", "web", "next", "draw2d.js"), "utf8");
const ctx = { NB: {} };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const D = ctx.NB.draw2d.core;

let passed = 0;
let failed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log("PASS  " + name);
  } catch (err) {
    failed++;
    console.log("FAIL  " + name);
    console.log("      " + String(err && err.stack ? err.stack : err).split("\n").join("\n      "));
  }
}
const plain = (x) => JSON.parse(JSON.stringify(x));
const close = (a, b, tol) => Math.abs(a - b) <= (tol || 1e-9);
const I = [1, 0, 0, 0, 1, 0, 0, 0, 1];
/** mol.js's own rotations, copied so the test is about the SAME matrices */
const rotY = (a) => { const c = Math.cos(a), s = Math.sin(a); return [c, 0, s, 0, 1, 0, -s, 0, c]; };
const rotX = (a) => { const c = Math.cos(a), s = Math.sin(a); return [1, 0, 0, 0, c, -s, 0, s, c]; };

// ---------------------------------------------------------------- projection

test("identity view: x right, y up, z toward the viewer, about the centre", () => {
  const v = D.viewCoords([{ x: 2, y: 3, z: 4 }], I, [1, 1, 1]);
  assert.deepStrictEqual(plain(v[0]), [1, 2, 3]);
});

test("a known rotation lands where mol.js would draw it", () => {
  // 90° about y: mol.js's rotY(π/2) sends +z to +x (toward the viewer → right)
  const R = rotY(Math.PI / 2);
  const v = D.viewCoords([{ x: 0, y: 0, z: 1 }, { x: 1, y: 0, z: 0 }], R, [0, 0, 0]);
  assert.ok(close(v[0][0], 1) && close(v[0][1], 0) && close(v[0][2], 0));
  assert.ok(close(v[1][0], 0) && close(v[1][2], -1));   // +x went away from the viewer
  // and 90° about x sends +y toward the viewer
  const w = D.viewCoords([{ x: 0, y: 1, z: 0 }], rotX(Math.PI / 2), [0, 0, 0]);
  assert.ok(close(w[0][1], 0) && close(w[0][2], 1));
});

test("handedness: the drawing is the screen, not its mirror image", () => {
  // mol.js draws screen X = ox + x·s and Y = oy − y·s (y up), so an atom to
  // the right and one above on screen must be right and above in the drawing,
  // and the three view axes must stay right-handed (x × y = z): a mirrored
  // drawing would turn every R centre into S.
  const R = [0.36, 0.48, -0.8, -0.8, 0.6, 0, 0.48, 0.64, 0.6];   // a proper rotation
  const det = R[0] * (R[4] * R[8] - R[5] * R[7]) - R[1] * (R[3] * R[8] - R[5] * R[6])
            + R[2] * (R[3] * R[7] - R[4] * R[6]);
  assert.ok(close(det, 1, 1e-9));
  const pts = [{ x: 1, y: 0, z: 0 }, { x: 0, y: 1, z: 0 }, { x: 0, y: 0, z: 1 }];
  const v = D.viewCoords(pts, R, [0, 0, 0]);
  const cross = [v[0][1] * v[1][2] - v[0][2] * v[1][1],
                 v[0][2] * v[1][0] - v[0][0] * v[1][2],
                 v[0][0] * v[1][1] - v[0][1] * v[1][0]];
  cross.forEach((c, k) => assert.ok(close(c, v[2][k], 1e-9)));
  // and the SVG flips y once: an atom above (y > 0) is drawn with a SMALLER svg y
  const lay = D.layout([{ el: "O", x: 0, y: 0, z: 0 }, { el: "N", x: 0, y: 1.4, z: 0 }],
                       { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  const svg = D.toSvg(lay, { frame: "tight" });
  const ys = [...svg.matchAll(/<text x="[-\d.]+" y="([-\d.]+)">(\w)/g)]
    .map(m => [m[2], Number(m[1])]);
  const yO = ys.filter(t => t[0] === "O")[0][1], yN = ys.filter(t => t[0] === "N")[0][1];
  assert.ok(yN < yO, "N (above) must be drawn above O");
});

// ---------------------------------------------------------------- the angle rule

/** a C–C bond tilted θ degrees out of the screen plane, far end toward (+1)
 *  or away from (−1) the viewer */
function tilted(deg, sign) {
  const t = deg * Math.PI / 180;
  return [{ el: "C", x: 0, y: 0, z: 0 },
          { el: "C", x: 1.5 * Math.cos(t), y: 0, z: sign * 1.5 * Math.sin(t) }];
}

test("θ = asin(|Δz| / L)", () => {
  assert.ok(close(D.outOfPlane([0, 0, 0], [1, 0, 1], Math.SQRT2), 45, 1e-9));
  assert.strictEqual(D.outOfPlane([0, 0, 0], [1, 0, 0], 1), 0);
});

test("threshold edge: at the threshold plain, just above it a wedge", () => {
  const at = tilted(25, 1);
  const L = 1.5;
  const v = D.viewCoords(at, I, [0, 0, 0]);
  assert.strictEqual(D.stereoFlag(v[0], v[1], L, 25.0001), 0);
  assert.strictEqual(D.stereoFlag(v[0], v[1], L, 24.999), 1);
  const lay = D.layout(tilted(24, 1), { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(lay.bonds[0].stereo, 0);
  const lay2 = D.layout(tilted(26, 1), { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(lay2.bonds[0].stereo, 1);
});

test("wedge when the far end comes toward the viewer, hash when it goes away", () => {
  const up = D.layout(tilted(50, 1), { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  const down = D.layout(tilted(50, -1), { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(up.bonds[0].stereo, 1);
  assert.strictEqual(down.bonds[0].stereo, 6);
  // turning the molecule over (180° about y) swaps them -- the drawing follows the view
  const flipped = D.layout(tilted(50, 1), { bonds: [[0, 1, 1]] },
                           { R: rotY(Math.PI), c: [0, 0, 0] });
  assert.strictEqual(flipped.bonds[0].stereo, 6);
});

test("the threshold option is honoured", () => {
  const lay = D.layout(tilted(40, 1), { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] },
                       { threshold: 45 });
  assert.strictEqual(lay.bonds[0].stereo, 0);
});

test("anchor ordering: the narrow end sits on the stereocentre", () => {
  // C0 (stereocentre) — C1 tilted toward the viewer; bond given as [1, 0]
  const atoms = tilted(60, 1);
  const lay = D.layout(atoms, { bonds: [[1, 0, 1]], stereocentres: [0] },
                       { R: I, c: [0, 0, 0] });
  const b = lay.bonds[0];
  assert.strictEqual(lay.atoms[b.a].src, 0, "anchor first");
  assert.strictEqual(b.stereo, 1);
  // same geometry, the stereocentre now the far atom: anchor on it, and the
  // far end (C0) is now the one going AWAY -> a hash
  const lay2 = D.layout(atoms, { bonds: [[0, 1, 1]], stereocentres: [1] },
                        { R: I, c: [0, 0, 0] });
  assert.strictEqual(lay2.atoms[lay2.bonds[0].a].src, 1);
  assert.strictEqual(lay2.bonds[0].stereo, 6);
});

test("anchor ordering: heavy atom over hydrogen, then more heavy neighbours", () => {
  const els = ["C", "H", "C", "C", "O"];
  const nb = D.neighbours(5, [[0, 1, 1], [0, 2, 1], [2, 3, 1], [2, 4, 1]]);
  assert.deepStrictEqual(plain(D.anchorFirst(1, 0, els, nb, new Set())), [0, 1]);
  // C2 has three heavy neighbours, C0 one
  assert.deepStrictEqual(plain(D.anchorFirst(0, 2, els, nb, new Set())), [2, 0]);
  // ties keep the lower index
  assert.deepStrictEqual(plain(D.anchorFirst(4, 3, els, nb, new Set())), [3, 4]);
});

test("a double bond is never wedged", () => {
  const lay = D.layout(tilted(70, 1), { bonds: [[0, 1, 2]] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(lay.bonds[0].stereo, 0);
  assert.strictEqual(lay.bonds[0].order, 2);
});

// ---------------------------------------------------------------- hydrogens and labels

test("H filtering: hidden on carbon, shown on heteroatoms and stereocentres", () => {
  // methanol-like: C0 (H1, H2) — O3 (H4); and C5 a stereocentre with H6
  const atoms = [
    { el: "C", x: 0, y: 0, z: 0 }, { el: "H", x: -0.6, y: 0.9, z: 0 },
    { el: "H", x: -0.6, y: -0.9, z: 0 }, { el: "O", x: 1.4, y: 0, z: 0 },
    { el: "H", x: 1.8, y: 0.9, z: 0 }, { el: "C", x: -1.5, y: 0, z: 0 },
    { el: "H", x: -1.9, y: 0, z: 1 }];
  const per = { bonds: [[0, 1, 1], [0, 2, 1], [0, 3, 1], [3, 4, 1], [0, 5, 1], [5, 6, 1]],
                stereocentres: [5] };
  const lay = D.layout(atoms, per, { R: I, c: [0, 0, 0] });
  const shown = lay.atoms.map(a => a.src).sort((a, b) => a - b);
  assert.deepStrictEqual(plain(shown), [0, 3, 4, 5, 6]);
  const all = D.layout(atoms, per, { R: I, c: [0, 0, 0] }, { allH: true });
  assert.strictEqual(all.atoms.length, 7);
  // bonds index into the SHOWN list
  lay.bonds.forEach(b => assert.ok(b.a < lay.atoms.length && b.b < lay.atoms.length));
});

test("labels: carbon implicit, heteroatoms and charges shown, a lone carbon counts its H", () => {
  const atoms = [{ el: "C", x: 0, y: 0, z: 0 }, { el: "N", x: 1.5, y: 0, z: 0 },
                 { el: "H", x: 2, y: 1, z: 0 }];
  const lay = D.layout(atoms, { bonds: [[0, 1, 1], [1, 2, 1]], charges: [0, 1, 0] },
                       { R: I, c: [0, 0, 0] });
  const byEl = Object.fromEntries(lay.atoms.map(a => [a.el, a]));
  assert.strictEqual(byEl.C.label, "");
  assert.strictEqual(byEl.N.label, "N");
  assert.strictEqual(byEl.N.charge, 1);
  assert.ok(D.toSvg(lay).indexOf(">+</tspan>") > 0);
  assert.strictEqual(D.chargeText(-2), "2−");
  // methane with its hydrogens hidden is a labelled CH4, not an empty drawing
  const ch4 = D.layout([{ el: "C", x: 0, y: 0, z: 0 }, { el: "H", x: 1, y: 0, z: 0 },
                        { el: "H", x: -1, y: 0, z: 0 }, { el: "H", x: 0, y: 1, z: 0 },
                        { el: "H", x: 0, y: -1, z: 0 }],
                       { bonds: [[0, 1, 1], [0, 2, 1], [0, 3, 1], [0, 4, 1]] },
                       { R: I, c: [0, 0, 0] });
  assert.strictEqual(ch4.atoms.length, 1);
  assert.strictEqual(ch4.atoms[0].label, "CH4");
});

// ---------------------------------------------------------------- outputs

test("molInput carries the drawn coordinates and flags, anchor-first", () => {
  const lay = D.layout(tilted(60, -1), { bonds: [[1, 0, 1]], stereocentres: [0] },
                       { R: I, c: [0, 0, 0] });
  const m = D.molInput(lay, "x");
  assert.strictEqual(m.atoms.length, 2);
  assert.deepStrictEqual(plain(m.bonds), [{ a: 0, b: 1, order: 1, stereo: 6 }]);
  assert.ok(close(m.atoms[1].x, lay.atoms[1].x, 1e-4));
});

test("the preview frame does not change as the structure turns", () => {
  const atoms = [{ el: "C", x: 0, y: 0, z: 0 }, { el: "O", x: 1.2, y: 0.3, z: 0.8 }];
  const per = { bonds: [[0, 1, 2]] };
  const box = (R) => /viewBox="([^"]+)"/.exec(
    D.toSvg(D.layout(atoms, per, { R: R, c: [0.6, 0.15, 0.4] }), { frame: "sphere" }))[1];
  assert.strictEqual(box(I), box(rotY(0.7)));
  assert.strictEqual(box(I), box(rotX(2.1)));
});

test("SVG is well-formed enough: one root, wedges as polygons, hashes as rungs", () => {
  const lay = D.layout(tilted(60, 1).concat([{ el: "C", x: -1.5, y: 0, z: -1 }]),
                       { bonds: [[0, 1, 1], [0, 2, 1]] }, { R: I, c: [0, 0, 0] });
  const svg = D.toSvg(lay, { frame: "tight", background: "#ffffff" });
  assert.ok(svg.startsWith("<svg ") && svg.endsWith("</svg>"));
  assert.strictEqual((svg.match(/<polygon/g) || []).length, 1);
  assert.ok((svg.match(/<line/g) || []).length >= 3);
  assert.ok(svg.indexOf('fill="#ffffff"') > 0);
});

test("a bond at a stereocentre pointing at the viewer is flagged end-on", () => {
  const at = tilted(80, 1);
  const flagged = D.layout(at, { bonds: [[0, 1, 1]], stereocentres: [0] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(flagged.endOn, 1);
  const plain = D.layout(at, { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(plain.endOn, 0);       // no stereocentre: nothing to misread
  const fine = D.layout(tilted(60, 1), { bonds: [[0, 1, 1]], stereocentres: [0] },
                        { R: I, c: [0, 0, 0] });
  assert.strictEqual(fine.endOn, 0);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
