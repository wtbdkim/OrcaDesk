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

test("flip: a clicked atom reverses its own rule, and only its own", () => {
  // the methanol-like structure above: C0 (H1, H2) — O3 (H4); C5* — H6
  const atoms = [
    { el: "C", x: 0, y: 0, z: 0 }, { el: "H", x: -0.6, y: 0.9, z: 0 },
    { el: "H", x: -0.6, y: -0.9, z: 0 }, { el: "O", x: 1.4, y: 0, z: 0 },
    { el: "H", x: 1.8, y: 0.9, z: 0 }, { el: "C", x: -1.5, y: 0, z: 0 },
    { el: "H", x: -1.9, y: 0, z: 1 }];
  const per = { bonds: [[0, 1, 1], [0, 2, 1], [0, 3, 1], [3, 4, 1], [0, 5, 1], [5, 6, 1]],
                stereocentres: [5] };
  const view = { R: I, c: [0, 0, 0] };
  const src = (lay) => lay.atoms.map(a => a.src).sort((a, b) => a - b);
  const lab = (lay, i) => lay.atoms.filter(a => a.src === i)[0].label;
  // a hidden H shown, a shown H (on O) hidden: O then counts it -> "OH"
  const h = D.layout(atoms, per, view, { flip: new Set([1, 4]) });
  assert.deepStrictEqual(plain(src(h)), [0, 1, 3, 5, 6]);
  assert.strictEqual(lab(h, 3), "OH");
  assert.strictEqual(lab(h, 1), "H");
  // with All H a flipped H is the one left out; an array works as well as a Set
  const all = D.layout(atoms, per, view, { allH: true, flip: [2] });
  assert.deepStrictEqual(plain(src(all)), [0, 1, 3, 4, 5, 6]);
  // a carbon gets its symbol (with the H it hides), a heteroatom loses its
  const c = D.layout(atoms, per, view, { flip: [0, 3] });
  assert.strictEqual(lab(c, 0), "CH2");
  assert.strictEqual(lab(c, 3), "");
  assert.strictEqual(lab(c, 5), "");          // not clicked: unchanged
  // a charged atom and a lone atom keep their symbols
  const n = D.layout([{ el: "N", x: 0, y: 0, z: 0 }, { el: "C", x: 1.5, y: 0, z: 0 },
                      { el: "Cl", x: 5, y: 0, z: 0 }],
                     { bonds: [[0, 1, 1]], charges: [1, 0, 0] }, view, { flip: [0, 2] });
  assert.strictEqual(lab(n, 0), "N");
  assert.strictEqual(lab(n, 2), "Cl");
  // no flip is the drawing as before
  assert.deepStrictEqual(plain(D.layout(atoms, per, view, { flip: [] })),
                         plain(D.layout(atoms, per, view)));
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

// ---------------------------------------------------------------- orbital lobes

/** one atom's lobe payload: a pure p orbital along `p` */
const pLobe = (p, share) => ({ share: share == null ? 1 : share, s: 0, p: p,
                               s_share: 0, p_share: share == null ? 1 : share, d_share: 0 });
const ONE = [{ el: "C", x: 0, y: 0, z: 0 }];

test("a p vector along +x draws its positive lobe to the right", () => {
  const r = D.lobeShapes(ONE, [pLobe([1, 0, 0])], { R: I, c: [0, 0, 0] });
  assert.strictEqual(r.shapes.length, 2);
  const pos = r.shapes.filter(s => s.sign === 1)[0], neg = r.shapes.filter(s => s.sign === -1)[0];
  assert.ok(pos.x > 0 && close(pos.y, 0) && neg.x < 0);
  // edge-on, the two lobes meet at the atom: centre L/2 out, semi-axis L/2
  assert.ok(close(pos.x, D.LOBE_LEN / 2) && close(pos.rx, D.LOBE_LEN / 2));
  assert.ok(close(pos.ry, D.LOBE_WIDTH * D.LOBE_LEN / 2));
  // and in the SVG the + ellipse is on the right of the screen
  const lay = D.layout(ONE, { bonds: [] }, { R: I, c: [0, 0, 0] }, { lobes: [pLobe([1, 0, 0])] });
  const svg = D.toSvg(lay, { frame: "tight" });
  const m = /<ellipse cx="([-\d.]+)"[^>]*data-sign="\+"/.exec(svg);
  assert.ok(m && Number(m[1]) > 0, svg);
});

test("a p vector toward the viewer draws a near-circle, the positive lobe on top", () => {
  const r = D.lobeShapes(ONE, [pLobe([0, 0, 1])], { R: I, c: [0, 0, 0] });
  const top = r.shapes[r.shapes.length - 1];
  assert.strictEqual(top.sign, 1);
  assert.ok(close(top.rx, top.ry, 1e-9) && close(top.x, 0) && close(top.y, 0));
  const away = D.lobeShapes(ONE, [pLobe([0, 0, -1])], { R: I, c: [0, 0, 0] });
  assert.strictEqual(away.shapes[away.shapes.length - 1].sign, -1);
  // the SVG paints in that order: the last orbital shape is the + one
  const svg = D.toSvg(D.layout(ONE, { bonds: [] }, { R: I, c: [0, 0, 0] },
                               { lobes: [pLobe([0, 0, 1])] }), { frame: "tight" });
  const signs = (svg.match(/data-sign="([+-])"/g) || []).map(x => x.slice(-2, -1));
  assert.deepStrictEqual(signs, ["-", "+"]);
});

test("the lobes follow the view matrix", () => {
  // 90° about y sends +x away from the viewer: the + lobe goes to the back
  const r = D.lobeShapes(ONE, [pLobe([1, 0, 0])], { R: rotY(Math.PI / 2), c: [0, 0, 0] });
  assert.strictEqual(r.shapes[r.shapes.length - 1].sign, -1);
  assert.ok(close(r.shapes[0].rx, r.shapes[0].ry, 1e-9));
  // 90° about x sends +y toward the viewer
  const s = D.lobeShapes(ONE, [pLobe([0, 1, 0])], { R: rotX(Math.PI / 2), c: [0, 0, 0] });
  assert.strictEqual(s.shapes[s.shapes.length - 1].sign, 1);
  // a general view: the + lobe's centre is R·p̂ · L/2 from the atom, and the
  // ellipse lies along the projected axis
  const R = [0.36, 0.48, -0.8, -0.8, 0.6, 0, 0.48, 0.64, 0.6];
  const t = D.lobeShapes(ONE, [pLobe([0, 0, 2])], { R: R, c: [0, 0, 0] });
  const pos = t.shapes.filter(x => x.sign === 1)[0];
  const a = D.LOBE_LEN / 2;
  assert.ok(close(pos.x, -0.8 * a, 1e-9) && close(pos.y, 0) && close(pos.z, 0.6 * a, 1e-9));
  assert.ok(close(pos.angle, 180));
  const b = D.LOBE_WIDTH * a;
  assert.ok(close(pos.rx, Math.sqrt(a * a * 0.64 + b * b * 0.36), 1e-9));
});

test("sizes go as √share, small shares get nothing, s and d atoms get circles", () => {
  const four = [{ el: "C", x: 0, y: 0, z: 0 }, { el: "C", x: 3, y: 0, z: 0 },
                { el: "Fe", x: -3, y: 0, z: 0 }, { el: "H", x: 0, y: 3, z: 0 }];
  const lobes = [pLobe([1, 0, 0], 0.64), pLobe([1, 0, 0], 0.16),
                 { share: 0.25, s: 0.1, p: [0, 0, 0.1], s_share: 0.01, p_share: 0.01, d_share: 0.23 },
                 { share: 0.01, s: -0.1, p: [0, 0, 0], s_share: 0.01, p_share: 0, d_share: 0 }];
  const r = D.lobeShapes(four, lobes, { R: I, c: [0, 0, 0] });
  const big = r.shapes.filter(s => s.atom === 0 && s.sign === 1)[0];
  const small = r.shapes.filter(s => s.atom === 1 && s.sign === 1)[0];
  assert.ok(close(big.rx / small.rx, 2, 1e-9));
  assert.strictEqual(r.shapes.filter(s => s.atom === 3).length, 0);    // below 2 %
  const d = r.shapes.filter(s => s.atom === 2);
  assert.strictEqual(d.length, 1);
  assert.strictEqual(d[0].kind, "d");
  assert.ok(close(d[0].rx, D.S_RADIUS * Math.sqrt(0.25 / 0.64)));
  // the same H atom above a lower threshold: an s circle in its sign
  const s = D.lobeShapes(four, lobes, { R: I, c: [0, 0, 0] }, { min: 0.005 })
    .shapes.filter(x => x.atom === 3);
  assert.strictEqual(s.length, 1);
  assert.strictEqual(s[0].kind, "s");
  assert.strictEqual(s[0].sign, -1);
  // reach: the frame's radius grows to the farthest lobe tip
  assert.ok(close(r.reach, 3 + D.LOBE_LEN * 0.5, 1e-9));
  // the largest share gets LOBE_LEN whatever it is: sizes are per orbital
  assert.ok(close(big.rx, D.LOBE_LEN / 2, 1e-9));
  // a main-group atom whose small share is mostly d (polarisation, a
  // neighbour's tail) is drawn by its p, not ringed
  const c = D.lobeShapes([{ el: "C", x: 0, y: 0, z: 0 }],
    [{ share: 0.03, s: 0, p: [1, 0, 0], s_share: 0, p_share: 0.008, d_share: 0.022 }],
    { R: I, c: [0, 0, 0] }).shapes;
  assert.deepStrictEqual(plain(c.map(x => x.kind)), ["lobe", "lobe"]);
});

test("lobes are drawn under the bonds, inside the saved crop, and never in the MOL", () => {
  const at = [{ el: "C", x: 0, y: 0, z: 0 }, { el: "C", x: 1.34, y: 0, z: 0 }];
  const lobes = [pLobe([0, 1, 0], 0.5), pLobe([0, 1, 0], 0.5)];
  const lay = D.layout(at, { bonds: [[0, 1, 2]] }, { R: I, c: [0.67, 0, 0] }, { lobes: lobes });
  const svg = D.toSvg(lay, { frame: "tight", background: "#ffffff", pos: "#4f86f7", neg: "#f2555a" });
  assert.ok(svg.indexOf('data-layer="orbital"') < svg.indexOf('<g stroke="#111111"'));
  assert.ok(svg.indexOf('stroke="#4f86f7"') > 0 && svg.indexOf('stroke="#f2555a"') > 0);
  // the crop holds the lobe tips: LOBE_LEN above the bond
  const vb = /viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"/.exec(svg);
  assert.ok(vb && Number(vb[2]) < -D.LOBE_LEN * 24);
  const plainSvg = D.toSvg(D.layout(at, { bonds: [[0, 1, 2]] }, { R: I, c: [0.67, 0, 0] }),
                           { frame: "tight" });
  assert.ok(plainSvg.indexOf("data-layer") < 0);
  const mol = plain(D.molInput(lay, "x"));
  assert.deepStrictEqual(Object.keys(mol).sort(), ["atoms", "bonds", "title"]);
  // the live frame (bounding sphere) also makes room for them
  const sphere = D.toSvg(lay, { frame: "sphere" });
  const sb = /viewBox="([-\d.]+)/.exec(sphere);
  assert.ok(sb && Number(sb[1]) <= -(0.67 + D.LOBE_LEN) * 24);
});

test("sameStructure: same elements, order and positions within tolerance", () => {
  const at = [{ el: "O", x: 0, y: 0, z: 0 }, { el: "H", x: 0.96, y: 0, z: 0 }];
  assert.ok(D.sameStructure(at, ["O", "H"], [[0, 0, 0], [0.97, 0, 0]]));
  assert.ok(!D.sameStructure(at, ["O", "H"], [[0, 0, 0], [1.2, 0, 0]]));
  assert.ok(!D.sameStructure(at, ["H", "O"], [[0, 0, 0], [0.96, 0, 0]]));
  assert.ok(!D.sameStructure(at, ["O"], [[0, 0, 0]]));
});

test("tint mixes toward white and leaves non-hex colours alone", () => {
  assert.strictEqual(D.tint("#000000", 0.5), "#808080");
  assert.strictEqual(D.tint("#fff", 0.3), "#ffffff");
  assert.strictEqual(D.tint("rgb(1,2,3)", 0.5), "rgb(1,2,3)");
});

// ------------------------------------------------- fixed bond length, Face on

/** ethylene in the xy plane, C=C along x */
const ETHYLENE = [
  { el: "C", x: -0.665, y: 0, z: 0 }, { el: "C", x: 0.665, y: 0, z: 0 },
  { el: "H", x: -1.23, y: 0.92, z: 0 }, { el: "H", x: -1.23, y: -0.92, z: 0 },
  { el: "H", x: 1.23, y: 0.92, z: 0 }, { el: "H", x: 1.23, y: -0.92, z: 0 }];
const ETH_PER = { bonds: [[0, 1, 2], [0, 2, 1], [0, 3, 1], [1, 4, 1], [1, 5, 1]] };
const mulR = (A, B) => [0, 1, 2].flatMap(r => [0, 1, 2].map(c =>
  A[r * 3] * B[c] + A[r * 3 + 1] * B[3 + c] + A[r * 3 + 2] * B[6 + c]));
/** the drawn C=C length in a saved drawing, px */
function savedCC(R) {
  const lay = D.layout(ETHYLENE, ETH_PER, { R: R, c: [0, 0, 0] }, { allH: true });
  const svg = D.toSvg(lay, { frame: "tight", scale: D.exportScale(lay) });
  const ln = [...svg.matchAll(/<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)"/g)]
    .map(m => Math.hypot(m[3] - m[1], m[4] - m[2]));
  return { lay: lay, longest: Math.max(...ln) };
}

test("a saved drawing draws the median heavy-atom bond BOND_PX long, whatever the slant", () => {
  const flat = D.layout(ETHYLENE, ETH_PER, { R: I, c: [0, 0, 0] });
  assert.ok(close(D.exportScale(flat) * 1.33, D.BOND_PX, 1e-6));
  // seen at 60° the C=C is drawn half as long: the scale doubles to make up
  const slant = D.layout(ETHYLENE, ETH_PER, { R: rotY(Math.PI / 3), c: [0, 0, 0] });
  assert.ok(close(D.exportScale(slant) * 1.33 * 0.5, D.BOND_PX, 1e-6));
  // so the two saved drawings carry the same bond (the C=C's main line runs
  // the whole length when its ends carry no label)
  assert.ok(close(savedCC(I).longest, savedCC(rotY(Math.PI / 3)).longest, 0.2));
});

test("exportScale: H bonds only when there is nothing else, end-on bonds ignored, capped", () => {
  const ch4 = [{ el: "C", x: 0, y: 0, z: 0 }, { el: "H", x: 1.09, y: 0, z: 0 }];
  const lay = D.layout(ch4, { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] }, { allH: true });
  assert.ok(close(D.exportScale(lay) * 1.09, D.BOND_PX, 1e-6));
  // straight down the bond: nothing to measure, the plain scale
  const endOn = D.layout(tilted(90, 1), { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(D.exportScale(endOn), 24);
  // nearly end-on: capped at 4× rather than blown up
  const near = D.layout(tilted(85, 1), { bonds: [[0, 1, 1]] }, { R: I, c: [0, 0, 0] });
  assert.strictEqual(D.exportScale(near), 96);
  // the live preview keeps the plain scale (no scale given)
  const eth = D.layout(ETHYLENE, ETH_PER, { R: I, c: [0, 0, 0] });
  assert.strictEqual(D.toSvg(eth, { frame: "sphere" }), D.toSvg(eth, { frame: "sphere", scale: 24 }));
  assert.notStrictEqual(D.toSvg(eth, { frame: "tight" }),
                        D.toSvg(eth, { frame: "tight", scale: D.exportScale(eth) }));
});

test("eigSym3: A·v = λ·v, largest first", () => {
  const A = [4, 1, 0.5, 1, 3, 0.2, 0.5, 0.2, 1];
  const e = D.eigSym3(A);
  assert.ok(e.values[0] >= e.values[1] && e.values[1] >= e.values[2]);
  e.vectors.forEach((v, k) => {
    const Av = [0, 1, 2].map(r => A[r * 3] * v[0] + A[r * 3 + 1] * v[1] + A[r * 3 + 2] * v[2]);
    Av.forEach((x, i) => assert.ok(close(x, e.values[k] * v[i], 1e-9)));
    assert.ok(close(Math.hypot(v[0], v[1], v[2]), 1, 1e-9));
  });
});

test("Face on lays a planar molecule in the screen by the smallest turn", () => {
  const R0 = mulR(rotX(0.7), rotY(-1.1));
  const R = D.flatView(ETHYLENE, R0);
  // a proper rotation
  const det = R[0] * (R[4] * R[8] - R[5] * R[7]) - R[1] * (R[3] * R[8] - R[5] * R[6])
            + R[2] * (R[3] * R[7] - R[4] * R[6]);
  assert.ok(close(det, 1, 1e-9));
  // every atom in the screen plane
  D.viewCoords(ETHYLENE, R, [0, 0, 0]).forEach(p => assert.ok(close(p[2], 0, 1e-9)));
  // the side that faced the viewer still does (z kept on the same side)
  assert.ok(R0[8] * R[8] > 0);
  // pressing it again changes nothing
  const again = D.flatView(ETHYLENE, R);
  again.forEach((x, k) => assert.ok(close(x, R[k], 1e-9)));
  // already flat, turned only in the plane: kept as it is
  const inPlane = [Math.cos(0.4), -Math.sin(0.4), 0, Math.sin(0.4), Math.cos(0.4), 0, 0, 0, 1];
  D.flatView(ETHYLENE, inPlane).forEach((x, k) => assert.ok(close(x, inPlane[k], 1e-9)));
  // two atoms or a line: no plane, the view is left alone
  D.flatView(ETHYLENE.slice(0, 2), R0).forEach((x, k) => assert.strictEqual(x, R0[k]));
});

test("Face on keeps the drawing's handedness: a stereocentre reads the same", () => {
  // CHFClBr: the wedge pattern is a property of the molecule, not the view
  const at = [{ el: "C", x: 0, y: 0, z: 0 }, { el: "F", x: 1.0, y: 0.3, z: 0.6 },
              { el: "Cl", x: -0.9, y: 0.8, z: -0.5 }, { el: "Br", x: -0.3, y: -1.2, z: 0.7 },
              { el: "H", x: 0.2, y: 0.1, z: -1.0 }];
  const R0 = mulR(rotY(0.5), rotX(-0.3));
  const R = D.flatView(at, R0);
  const det = R[0] * (R[4] * R[8] - R[5] * R[7]) - R[1] * (R[3] * R[8] - R[5] * R[6])
            + R[2] * (R[3] * R[7] - R[4] * R[6]);
  assert.ok(close(det, 1, 1e-9), "a mirror would turn R into S");
});

// ------------------------------------- mol.js: the view on the clipboard
// (Ctrl+C / Ctrl+V on a 3D stage -- the way several structures are drawn in
// 2D from the same side)

const molCtx = {};
vm.createContext(molCtx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "web", "next", "mol.js"), "utf8")
                + ";this.MOL = MOL;", molCtx);
const MOL = molCtx.MOL;

test("a copied view reads back as the same matrix", () => {
  const R = mulR(rotX(0.7), rotY(-1.1));
  const back = MOL.parseView(MOL.viewText(R));
  back.forEach((x, k) => assert.ok(close(x, R[k], 2e-6)));
  // found inside other text, and with exponents
  assert.ok(MOL.parseView("see: ORCAdesk view 1 0 0 0 1e0 0 0 0 1 (copied)"));
});

test("a clipboard without a view, or with a sheared or mirrored one, is refused or mended", () => {
  assert.strictEqual(MOL.parseView("hello"), null);
  assert.strictEqual(MOL.parseView("ORCAdesk view 1 0 0 0 1 0 0 0"), null);       // 8 numbers
  assert.strictEqual(MOL.parseView("ORCAdesk view 2 0 0 0 1 0 0 0 1"), null);     // not unit
  assert.strictEqual(MOL.parseView("ORCAdesk view 1 0 0 0.5 1 0 0 0 1"), null);   // sheared
  // a mirror (third row flipped) comes back as the proper rotation: never an
  // R centre turned S
  const m = MOL.parseView("ORCAdesk view 1 0 0 0 1 0 0 0 -1");
  assert.deepStrictEqual(plain(m), [1, 0, 0, 0, 1, 0, 0, 0, 1]);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
