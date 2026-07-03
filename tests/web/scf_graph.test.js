// @ts-check
/* ============================================================
   tests/web/scf_graph.test.js — plain-Node unit tests for
   web/scf_graph.js (no npm dependencies, no DOM).

   The module is an IIFE that exposes module.exports by design
   (see the header of web/scf_graph.js), so it loads with a
   plain require(). Run:

       node tests/web/scf_graph.test.js

   Exit code 0 == all tests passed.
   ============================================================ */
"use strict";

const assert = require("assert");
const path = require("path");

const SCFGraph = require(path.join(__dirname, "..", "..", "web", "scf_graph.js"));

// ---------- tiny test runner ----------
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
    const msg = String(err && err.stack ? err.stack : err);
    console.log("      " + msg.split("\n").join("\n      "));
  }
}

// ---------- helpers: synthetic ORCA log lines ----------
function feed(tracker, lines) {
  lines.forEach(function (l) { tracker.push(l); });
}

/**
 * One row of the "|Geometry convergence|" table.
 * @param {string} name  criterion name (e.g. "MAX gradient")
 * @param {string} val   value column (plain decimal string)
 * @param {string} tol   tolerance column
 * @param {boolean} yes  converged flag
 */
function critRow(name, val, tol, yes) {
  return { name: name, val: val, tol: tol, flag: yes ? "YES" : "NO" };
}

/**
 * The standard five-criteria row set. Only MAX gradient varies per test;
 * the other criteria default to small, non-dominant values.
 * @param {string} maxGradVal
 * @param {{deYes?:boolean, rmsgYes?:boolean, maxgYes?:boolean, rmssYes?:boolean, maxsYes?:boolean, deVal?:string}} [opts]
 */
function fiveRows(maxGradVal, opts) {
  opts = opts || {};
  return [
    critRow("Energy change", opts.deVal || "0.0000001000", "0.0000050000", !!opts.deYes),
    critRow("RMS gradient", "0.0000100000", "0.0001000000", !!opts.rmsgYes),
    critRow("MAX gradient", maxGradVal, "0.0003000000", !!opts.maxgYes),
    critRow("RMS step", "0.0001000000", "0.0020000000", !!opts.rmssYes),
    critRow("MAX step", "0.0002000000", "0.0040000000", !!opts.maxsYes),
  ];
}

/** All five criteria converged (values below their tolerances). */
function allYesRows() {
  return [
    critRow("Energy change", "0.0000010000", "0.0000050000", true),
    critRow("RMS gradient", "0.0000100000", "0.0001000000", true),
    critRow("MAX gradient", "0.0000500000", "0.0003000000", true),
    critRow("RMS step", "0.0001000000", "0.0020000000", true),
    critRow("MAX step", "0.0002000000", "0.0040000000", true),
  ];
}

/**
 * A full "GEOMETRY OPTIMIZATION CYCLE N" block: the cycle banner, the
 * convergence table (header, rows, terminating dotted line), and optionally
 * ORCA's real per-cycle wall time line.
 * @param {number} cycle
 * @param {{name:string,val:string,tol:string,flag:string}[]} rows
 * @param {number|null} [iterSec]
 */
function geoCycleBlock(cycle, rows, iterSec) {
  const lines = [
    "        *                GEOMETRY OPTIMIZATION CYCLE   " + cycle + "            *",
  ];
  lines.push.apply(lines, geoTableOnly(rows));
  if (iterSec != null) {
    lines.push("Time for complete geometry iter:     " + iterSec.toFixed(3) + " sec");
  }
  return lines;
}

/** Just the convergence table (no cycle banner) — used to replay a table. */
function geoTableOnly(rows) {
  const lines = [
    "          ----------------------|Geometry convergence|-------------------------",
    "          Item                value                   Tolerance       Converged",
    "          ---------------------------------------------------------------------",
  ];
  rows.forEach(function (r) {
    lines.push("          " + r.name + "       " + r.val + "            " + r.tol + "      " + r.flag);
  });
  lines.push("          .....................................................................");
  return lines;
}

/** SCF iteration row: "<iter> <energy> <sci delta-E> ..." */
function scfIterLine(iter, energy, dE) {
  return "  " + iter + "   " + energy + "   " + dE + "  4.97e-03  2.50e-02";
}

/**
 * A GeoTracker with one committed step and a stubbed ETA estimator, so the
 * renderGeoProgress() output exercises the (non-exported) ETA bucket mapping
 * with an exactly controlled etaMs.
 * @param {number|null} etaMs
 */
function geoWithStubbedEta(etaMs) {
  const g = new SCFGraph.GeoTracker();
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  g.estimateETA = function () {
    return {
      remainingSteps: 5,
      etaMs: etaMs,
      etaLowMs: etaMs == null ? null : etaMs * 0.5,
      etaHighMs: etaMs == null ? null : etaMs * 2.0,
      conf: "med",
    };
  };
  return g;
}

// ============================================================
// SCFTracker
// ============================================================

test("scf tracker accumulates the delta-E series from iteration rows (first nonzero dE is the start anchor)", function () {
  const t = new SCFGraph.SCFTracker();
  assert.strictEqual(t.hasData(), false);
  feed(t, [
    scfIterLine(0, "-232.0328599741", "0.000000e+00"),
    scfIterLine(1, "-232.1151660682", "-8.23e-02"),
    scfIterLine(2, "-232.1200000000", "-4.84e-03"),
    scfIterLine(3, "-232.1205000000", "-5.00e-04"),
  ]);
  assert.strictEqual(t.points.length, 4);
  // dE is stored as |Delta-E|
  assert.strictEqual(t.points[0].dE, 0);
  assert.strictEqual(t.points[1].dE, 8.23e-2);
  assert.strictEqual(t.points[2].dE, 4.84e-3);
  assert.strictEqual(t.points[3].dE, 5.0e-4);
  // the iter-0 row (dE = 0) must NOT be the start anchor
  assert.strictEqual(t.startDE, 8.23e-2);
  assert.strictEqual(t.current(), 5.0e-4);
  assert.strictEqual(t.hasData(), true);
});

test("scf progress is between 0 and 1 mid-convergence and increases as dE shrinks", function () {
  const t = new SCFGraph.SCFTracker();
  feed(t, [
    scfIterLine(1, "-100.0000000000", "-1.00e-02"),
    scfIterLine(2, "-100.0100000000", "-1.00e-05"),
  ]);
  const target = SCFGraph.targetFor("TightSCF");
  assert.strictEqual(target, 1e-8);
  const p1 = t.progress(target);
  // log-scale: (log 1e-2 - log 1e-5) / (log 1e-2 - log 1e-8) = 3/6 = 0.5
  assert.ok(Math.abs(p1 - 0.5) < 1e-9, "expected 0.5, got " + p1);
  t.push(scfIterLine(3, "-100.0101000000", "-1.00e-06"));
  const p2 = t.progress(target);
  assert.ok(p2 > p1, "progress must increase as dE shrinks");
  assert.ok(p2 < 1, "still above the target -> below 1");
  // reaching the target -> exactly 1
  t.push(scfIterLine(4, "-100.0101010000", "-1.00e-09"));
  assert.strictEqual(t.progress(target), 1);
});

test("scf iteration counter restarting begins a new SCF block (curve reset)", function () {
  const t = new SCFGraph.SCFTracker();
  feed(t, [
    scfIterLine(1, "-100.0000000000", "-1.00e-02"),
    scfIterLine(2, "-100.0100000000", "-1.00e-03"),
    scfIterLine(3, "-100.0110000000", "-1.00e-04"),
  ]);
  assert.strictEqual(t.points.length, 3);
  // an iter number <= the last one restarts the block
  t.push(scfIterLine(1, "-100.0111000000", "-2.00e-02"));
  assert.strictEqual(t.points.length, 1);
  assert.strictEqual(t.startDE, 2.0e-2);
  assert.strictEqual(t.lastIter, 1);
});

test("scf tracker resets its curve and records the step on a GEOMETRY OPTIMIZATION CYCLE line", function () {
  const t = new SCFGraph.SCFTracker();
  feed(t, [
    scfIterLine(1, "-100.0000000000", "-1.00e-02"),
    scfIterLine(2, "-100.0100000000", "-1.00e-03"),
  ]);
  t.push("        *                GEOMETRY OPTIMIZATION CYCLE   2            *");
  assert.strictEqual(t.step, 2);
  assert.strictEqual(t.points.length, 0);
  assert.strictEqual(t.hasData(), false);
});

test("isScfIter matches SCF iteration rows only", function () {
  assert.strictEqual(SCFGraph.isScfIter("  2    -232.1151660682   -8.23e-02  4.97e-03"), true);
  assert.strictEqual(SCFGraph.isScfIter("TOTAL SCF ENERGY"), false);
  assert.strictEqual(SCFGraph.isScfIter("  12  some text without numbers"), false);
  assert.strictEqual(SCFGraph.isScfIter(""), false);
});

// ============================================================
// GeoTracker
// ============================================================

test("geo tracker counts exactly one step per optimization-cycle table", function () {
  const g = new SCFGraph.GeoTracker();
  assert.strictEqual(g.hasData(), false);
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  assert.strictEqual(g.steps.length, 1);
  assert.strictEqual(g.steps[0].step, 1);
  feed(g, geoCycleBlock(2, fiveRows("0.0100000000")));
  assert.strictEqual(g.steps.length, 2);
  assert.strictEqual(g.steps[1].step, 2);
  assert.strictEqual(g.hasData(), true);
  // MAX gradient tolerance was read live from the table
  assert.strictEqual(g.tol, 0.0003);
});

test("geo tracker replaying the same cycle's table is idempotent (keyed by cycle number, overwrites values)", function () {
  const g = new SCFGraph.GeoTracker();
  const block = geoCycleBlock(1, fiveRows("0.0140882096"));
  feed(g, block);
  assert.strictEqual(g.steps.length, 1);
  assert.strictEqual(g.worst.length, 1);
  // full replay (cycle banner + table), e.g. a log tail re-read
  feed(g, block);
  assert.strictEqual(g.steps.length, 1, "replaying the same cycle must not inflate the step count");
  assert.strictEqual(g.worst.length, 1, "replaying the same cycle must not inflate the worst-ratio series");
  // a re-emitted table for the SAME cycle overwrites the stored values
  feed(g, geoTableOnly(fiveRows("0.0120000000")));
  assert.strictEqual(g.steps.length, 1);
  assert.strictEqual(g.steps[0].maxGrad, 0.012);
});

test("geo progress caps at 0.99 before all criteria are met, even if MAX gradient already reached tolerance", function () {
  const g = new SCFGraph.GeoTracker();
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  // step 1 IS the start anchor, so progress is exactly 0 there
  const early = g.progress();
  assert.strictEqual(early, 0);
  // MAX gradient below tolerance (YES) but Energy change still NO -> 4/5 met
  feed(g, geoCycleBlock(2, fiveRows("0.0000500000", {
    maxgYes: true, rmsgYes: true, rmssYes: true, maxsYes: true,
  })));
  assert.strictEqual(g.allConverged(), false);
  const cs = g.criteriaSummary();
  assert.strictEqual(cs.met, 4);
  assert.strictEqual(cs.total, 5);
  const p = g.progress();
  assert.ok(p <= 0.99, "progress must be capped at 0.99 before all criteria are met, got " + p);
  assert.ok(p > 0.9, "MAX gradient at tolerance should sit at the cap, got " + p);
});

test("geo progress reaches 1 when every criterion of the latest step is YES", function () {
  const g = new SCFGraph.GeoTracker();
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  feed(g, geoCycleBlock(2, allYesRows()));
  assert.strictEqual(g.allConverged(), true);
  assert.strictEqual(g.progress(), 1);
});

test("geo progress jumps to 1 on the OPTIMIZATION RUN DONE / CONVERGED marker", function () {
  const g = new SCFGraph.GeoTracker();
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  assert.ok(g.progress() < 1);
  g.push("                    ***  OPTIMIZATION RUN DONE  ***");
  assert.strictEqual(g.done, true);
  assert.strictEqual(g.progress(), 1);

  const g2 = new SCFGraph.GeoTracker();
  feed(g2, geoCycleBlock(1, fiveRows("0.0140882096")));
  g2.push("       ***        THE OPTIMIZATION HAS CONVERGED     ***");
  assert.strictEqual(g2.done, true);
  assert.strictEqual(g2.progress(), 1);
});

// ============================================================
// FreqTracker (numerical displacements)
// ============================================================

test("freq tracker advances the displacement counter monotonically", function () {
  const f = new SCFGraph.FreqTracker();
  assert.strictEqual(f.hasData(), false);
  f.push("                       * ORCA NUMERICAL FREQUENCIES *");
  assert.strictEqual(f.active, true);
  assert.strictEqual(f.mode, "numerical");
  f.push("Number of displacements            ...        12");
  assert.strictEqual(f.total, 12);
  f.push("The calculation will be done for displacement    3 /   12");
  assert.strictEqual(f.cur, 3);
  assert.strictEqual(f.progress(), 3 / 12);
  f.push("The calculation will be done for displacement    6 /   12");
  assert.strictEqual(f.cur, 6);
  // a re-seen earlier displacement (log replay) must never move the counter back
  f.push("The calculation will be done for displacement    3 /   12");
  assert.strictEqual(f.cur, 6);
  assert.strictEqual(f.progress(), 6 / 12);
});

test("freq progress caps at 0.999 while the last displacement runs, then 1 after VIBRATIONAL FREQUENCIES", function () {
  const f = new SCFGraph.FreqTracker();
  f.push("Number of displacements            ...        10");
  f.push("The calculation will be done for displacement   10 /   10");
  assert.strictEqual(f.cur, 10);
  assert.strictEqual(f.total, 10);
  // cur/total == 1 but progress() must stay strictly below 1 (0.999 cap):
  // this is the contract the display layer's floor() relies on
  assert.strictEqual(f.progress(), 0.999);
  assert.ok(f.progress() < 1);
  f.push("                           VIBRATIONAL FREQUENCIES");
  assert.strictEqual(f.dispDone, true);
  assert.strictEqual(f.progress(), 1);
});

// ============================================================
// Marker case-sensitivity (real false-positive strings)
// ============================================================

test("lowercase look-alike markers never activate the freq chain", function () {
  const f = new SCFGraph.FreqTracker();
  // real strings present in every ORCA output, opt-only runs included
  // (quoted in the scf_graph.js comments as the known false positives)
  feed(f, [
    "Your calculation utilizes the pre 5.0 version of the SCF Hessian",
    "Properties with geometric perturbations:",
    "   SCF Hessian                     ...          NO",
    "   Vibrational frequencies         ...          NO",
  ]);
  assert.strictEqual(f.active, false);
  assert.strictEqual(f.hasData(), false);
  assert.strictEqual(f.mode, "");
  assert.strictEqual(f.aIdx, -1);
});

test("lowercase look-alikes do not set the geo post-opt stage; the uppercase banner does", function () {
  const g = new SCFGraph.GeoTracker();
  g.push("                    ***  OPTIMIZATION RUN DONE  ***");
  assert.strictEqual(g.done, true);
  feed(g, [
    "Your calculation utilizes the pre 5.0 version of the SCF Hessian",
    "Properties with geometric perturbations:",
    "   SCF Hessian                     ...          NO",
  ]);
  assert.strictEqual(g.postStage, "", "mixed-case mentions must not flip the post-opt stage");
  g.push("                           VIBRATIONAL FREQUENCIES");
  assert.strictEqual(g.postStage, "frequencies / properties");
});

test("shared CP-SCF banner never bootstraps the analytical chain (NMR false positive)", function () {
  // GIAO NMR runs print "ORCA SCF RESPONSE CALCULATION" for their own CP-SCF
  // solve; without an analytical boot banner first it must not activate.
  const f = new SCFGraph.FreqTracker();
  f.push("                      ORCA SCF RESPONSE CALCULATION");
  assert.strictEqual(f.active, false);
  assert.strictEqual(f.mode, "");
});

test("uppercase boot banner activates the analytical chain and parses the nuclei count", function () {
  const f = new SCFGraph.FreqTracker();
  f.push("        GEOMETRIC PERTURBATIONS (5 nuclei)");
  assert.strictEqual(f.active, true);
  assert.strictEqual(f.mode, "analytical");
  assert.strictEqual(f.nuclei, 5);
  assert.strictEqual(f.perturbTotal, 15); // 3N
  // now the shared banner may advance the already-active chain
  f.push("                      ORCA SCF RESPONSE CALCULATION");
  assert.strictEqual(f.aStage, "cpscf");
});

// ============================================================
// ETA: real estimator + bucket boundaries
// ============================================================

test("geo ETA becomes available (non-stale) on a clean monotone worst-ratio series with real cycle times", function () {
  SCFGraph.setEtaMode("conservative");
  const g = new SCFGraph.GeoTracker();
  for (let c = 1; c <= 12; c++) {
    const w = 1.3 - 0.1 * c; // worst log-ratio: 1.2 down to 0.1
    const maxg = (0.0003 * Math.pow(10, w)).toFixed(10);
    feed(g, geoCycleBlock(c, fiveRows(maxg), 10.0));
  }
  assert.strictEqual(g.steps.length, 12);
  assert.strictEqual(g.worst.length, 12);
  // per-step time comes from ORCA's own wall-time lines, not Date.now gaps
  assert.strictEqual(g.perStepMs(), 10000);
  const eta = g.estimateETA();
  assert.ok(eta, "expected a non-null ETA after 12 clean decreasing cycles");
  assert.ok(["high", "med", "low"].indexOf(eta.conf) >= 0, "conf should be a fresh estimate, got " + eta.conf);
  assert.ok(eta.remainingSteps > 0 && eta.remainingSteps < 5, "remaining ~1 step, got " + eta.remainingSteps);
  assert.ok(eta.etaMs != null && eta.etaMs > 0 && eta.etaMs < 45000, "etaMs ~10s, got " + eta.etaMs);
  const html = SCFGraph.renderGeoProgress(g, "");
  assert.ok(html.includes("more step"), "ETA line should render the remaining-steps text");
  assert.ok(html.includes("roughly under a minute left"), "a ~10s ETA falls in the first bucket");
});

test("ETA bucket boundaries: 45s / 8m / 50m / 5h / 24h map to the documented coarse phrases", function () {
  const cases = [
    [44 * 1000, "under a minute"],
    [45 * 1000, "a few minutes"],
    [(8 * 60 - 1) * 1000, "a few minutes"],
    [8 * 60 * 1000, "tens of minutes"],
    [(50 * 60 - 1) * 1000, "tens of minutes"],
    [50 * 60 * 1000, "a few hours"],
    [(5 * 3600 - 1) * 1000, "a few hours"],
    [5 * 3600 * 1000, "many hours"],
    [(24 * 3600 - 1) * 1000, "many hours"],
    [24 * 3600 * 1000, "a day or more"],
  ];
  cases.forEach(function (c) {
    const html = SCFGraph.renderGeoProgress(geoWithStubbedEta(c[0]), "");
    assert.ok(
      html.includes("roughly " + c[1] + " left"),
      c[0] + " ms should render bucket '" + c[1] + "'"
    );
  });
});

test("ETA line omits the time bucket when etaMs is unknown but still shows remaining steps", function () {
  const html = SCFGraph.renderGeoProgress(geoWithStubbedEta(null), "");
  assert.ok(!html.includes("roughly"), "no bucket text without an etaMs");
  assert.ok(html.includes("~5 more steps"), "remaining-steps count still shown");
});

// ============================================================
// Renderers: HTML strings, no DOM, no premature "100%"
// ============================================================

test("renderSCFProgress returns an HTML string and never contains '100%' mid-convergence", function () {
  const t = new SCFGraph.SCFTracker();
  feed(t, [
    scfIterLine(1, "-100.0000000000", "-1.00e-02"),
    scfIterLine(2, "-100.0100000000", "-1.00e-05"),
  ]);
  const html = SCFGraph.renderSCFProgress(t, "TightSCF", "");
  assert.strictEqual(typeof html, "string");
  assert.ok(html.includes("SCF convergence 50%"));
  assert.ok(html.includes("width:50%"));
  assert.ok(html.includes("scf-prog-bar"));
  assert.ok(!html.includes("100%"), "must not claim 100% below the target");
});

test("renderSCFProgress prefixes the geometry step label during an optimization", function () {
  const t = new SCFGraph.SCFTracker();
  t.push("        *                GEOMETRY OPTIMIZATION CYCLE   3            *");
  t.push(scfIterLine(1, "-100.0000000000", "-1.00e-02"));
  const html = SCFGraph.renderSCFProgress(t, "TightSCF", "");
  assert.ok(html.includes("Geometry step 3"));
});

test("renderGeoProgress never contains '100%' before convergence, and shows it once done", function () {
  const g = new SCFGraph.GeoTracker();
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  feed(g, geoCycleBlock(2, fiveRows("0.0050000000")));
  const html = SCFGraph.renderGeoProgress(g, "");
  assert.strictEqual(typeof html, "string");
  assert.ok(html.includes("Optimization"));
  assert.ok(html.includes("criteria met"));
  assert.ok(html.includes("step 2"), "label shows the real ORCA cycle number");
  assert.ok(!html.includes("100%"), "must not claim 100% before all criteria are met");

  g.push("                    ***  OPTIMIZATION RUN DONE  ***");
  const doneHtml = SCFGraph.renderGeoProgress(g, "");
  assert.ok(doneHtml.includes("Optimization complete"));
  assert.ok(doneHtml.includes("100%"));
  assert.ok(doneHtml.includes("width:100%"));
});

test("renderFreqProgress floors the percentage so the 0.999 cap never displays as 100%", function () {
  const f = new SCFGraph.FreqTracker();
  f.push("Number of displacements            ...        10");
  f.push("The calculation will be done for displacement   10 /   10");
  const html = SCFGraph.renderFreqProgress(f);
  assert.strictEqual(typeof html, "string");
  assert.ok(html.includes("99%"), "floor(0.999 * 100) = 99");
  assert.ok(html.includes("displacement 10/10"));
  assert.ok(!html.includes("100%"), "must not display 100% while the last displacement runs");
  // after the frequency table appears the panel flips to the explicit 100% state
  f.push("                           VIBRATIONAL FREQUENCIES");
  const doneHtml = SCFGraph.renderFreqProgress(f);
  assert.ok(doneHtml.includes("displacements complete"));
  assert.ok(doneHtml.includes("100%"));
});

// ---------- summary ----------
console.log("");
console.log(passed + " passed, " + failed + " failed");
if (failed > 0) process.exitCode = 1;
