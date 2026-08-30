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
// The freq/TD-DFT/CREST trackers live in progress_panels.js, which extends the
// SCFGraph namespace in place (same object, require() caches it) — so this side
// effect is what makes SCFGraph.FreqTracker & co. available here.
require(path.join(__dirname, "..", "..", "web", "progress_panels.js"));

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

test("derivative-integral BATCH lines turn the nuclei count into N/total", function () {
  // real ORCA 6.1.1 shape (0-based atom indices, one atom per batch here)
  const f = new SCFGraph.FreqTracker();
  f.push("GEOMETRIC PERTURBATIONS (144 nuclei)");
  assert.strictEqual(f._curSub(), "144 nuclei");   // stage just entered, nothing done
  f.push("BATCH   0: Atoms    0 -    0 (  3 perturbations)");
  assert.strictEqual(f._curSub(), "144 nuclei");   // batch 0 only STARTED
  f.push("   => RI-J derivative integrals                    ... done (    18.9 sec)");
  f.push("BATCH   1: Atoms    1 -    1 (  3 perturbations)");
  assert.strictEqual(f._curSub(), "1/144 nuclei");
  f.push("BATCH   2: Atoms    2 -    2 (  3 perturbations)");
  assert.strictEqual(f._curSub(), "2/144 nuclei");
  // a re-seen batch line never moves the count backwards
  f.push("BATCH   1: Atoms    1 -    1 (  3 perturbations)");
  assert.strictEqual(f._curSub(), "2/144 nuclei");
  // leaving the stage completes it: the frozen row reads the plain total
  f.push("                      ORCA SCF RESPONSE CALCULATION");
  assert.strictEqual(f.aStage, "cpscf");
  assert.strictEqual(f.subs[0], "144 nuclei");
});

test("a multi-atom batch credits its whole atom range", function () {
  const f = new SCFGraph.FreqTracker();
  f.push("GEOMETRIC PERTURBATIONS (12 nuclei)");
  f.push("BATCH   0: Atoms    0 -    3 ( 12 perturbations)");
  f.push("BATCH   1: Atoms    4 -    7 ( 12 perturbations)");
  assert.strictEqual(f._curSub(), "4/12 nuclei");
  f.push("BATCH   2: Atoms    8 -   11 ( 12 perturbations)");
  assert.strictEqual(f._curSub(), "8/12 nuclei");
});

test("BATCH lines outside the integrals stage never touch the count", function () {
  const f = new SCFGraph.FreqTracker();
  f.push("GEOMETRIC PERTURBATIONS (6 nuclei)");
  f.push("                      ORCA SCF RESPONSE CALCULATION");
  f.push("BATCH   3: Atoms    3 -    3 (  3 perturbations)");
  assert.strictEqual(f.aStage, "cpscf");
  assert.strictEqual(f.atomsDone, 6);   // frozen complete when the stage was left
  assert.strictEqual(f.batchIdx, -1);
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
  const html = SCFGraph.renderGeoProgress(g);
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
    const html = SCFGraph.renderGeoProgress(geoWithStubbedEta(c[0]));
    assert.ok(
      html.includes("roughly " + c[1] + " left"),
      c[0] + " ms should render bucket '" + c[1] + "'"
    );
  });
});

test("ETA line omits the time bucket when etaMs is unknown but still shows remaining steps", function () {
  const html = SCFGraph.renderGeoProgress(geoWithStubbedEta(null));
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
  const html = SCFGraph.renderSCFProgress(t, "TightSCF");
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
  const html = SCFGraph.renderSCFProgress(t, "TightSCF");
  assert.ok(html.includes("Geometry step 3"));
});

test("renderGeoProgress never contains '100%' before convergence, and shows it once done", function () {
  const g = new SCFGraph.GeoTracker();
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  feed(g, geoCycleBlock(2, fiveRows("0.0050000000")));
  const html = SCFGraph.renderGeoProgress(g);
  assert.strictEqual(typeof html, "string");
  assert.ok(html.includes("Optimization"));
  assert.ok(html.includes("criteria met"));
  assert.ok(html.includes("step 2"), "label shows the real ORCA cycle number");
  assert.ok(!html.includes("100%"), "must not claim 100% before all criteria are met");

  g.push("                    ***  OPTIMIZATION RUN DONE  ***");
  const doneHtml = SCFGraph.renderGeoProgress(g);
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

// ---------- CrestTracker (CREST conformer-search progress) ----------
// Line formats are taken verbatim from a real CREST 3.0.2 iMTD-GC run.
const CREST_LINES = [
  " -----------------------------",
  " Initial Geometry Optimization",
  " Geometry successfully optimized.",
  " Σ(t(MTD)) / ps :    70.0 (14 MTDs)",
  " ------------------------------",
  " Meta-Dynamics Iteration 1",
  "*MTD   1 completed successfully ...        0 min,  2.289 sec",
  "*MTD   2 completed successfully ...        0 min,  2.368 sec",
  "CREGEN> E lowest :   -12.40772",
  " 2 structures remain within    12.00 kcal/mol window",
  " Meta-Dynamics Iteration 2",
  "*MTD   1 completed successfully ...        0 min,  2.533 sec",
  "CREGEN> E lowest :   -12.40773",
  " 1 structures remain within     6.00 kcal/mol window",
  " Additional regular MDs on lowest 1 conformer(s)",
  "     |        Structure Crossing (GC)       |",
  "   ================================================",
  "   |           Final Geometry Optimization        |",
  " Final Ensemble Information",
  " number of unique conformers for further calc            1",
  " CREST terminated normally.",
];

test("CrestTracker tracks the phase chain, MTD count, energies and conformer count", function () {
  const c = new SCFGraph.CrestTracker();
  feed(c, CREST_LINES);
  assert.ok(c.hasData());
  assert.ok(c.finished, "CREST terminated normally -> finished");
  assert.strictEqual(c.aStage, "cregen", "advanced through init/sample/md/gc/final to the CREGEN sort");
  assert.strictEqual(c.mtdTotal, 14, "parsed '(14 MTDs)'");
  assert.strictEqual(c.iter, 2, "latest Meta-Dynamics Iteration");
  assert.strictEqual(c.nConf, 1, "unique conformers for further calc");
  assert.strictEqual(c.windowCount, 1, "latest N-structures-in-window count");
  assert.deepStrictEqual(c.energies, [-12.40772, -12.40773], "one E-lowest per CREGEN pass");
});

test("CrestTracker resets the per-iteration MTD count on a new Meta-Dynamics Iteration", function () {
  const c = new SCFGraph.CrestTracker();
  feed(c, [
    " Meta-Dynamics Iteration 1",
    "*MTD   1 completed successfully ...        0 min,  1.0 sec",
    "*MTD   2 completed successfully ...        0 min,  1.0 sec",
  ]);
  assert.strictEqual(c.mtdDone, 2);
  c.push(" Meta-Dynamics Iteration 2");
  assert.strictEqual(c.mtdDone, 0, "new iteration restarts the MTD counter");
  assert.strictEqual(c.iter, 2);
});

test("CrestTracker flags a topology-change safety termination as an error", function () {
  const c = new SCFGraph.CrestTracker();
  c.push(" Initial Geometry Optimization");
  c.push(" *WARNING* Change in topology detected!");
  c.push(" ERROR STOP safety termination of CREST");
  assert.ok(c.error, "topology change / safety termination -> error");
  assert.ok(!c.finished);
  const html = SCFGraph.renderCrestProgress(c);
  assert.ok(html.includes("STOPPED"), "the HUD status shows the run stopped");
});

test("renderCrestProgress renders the vertical stage stepper", function () {
  const c = new SCFGraph.CrestTracker();
  feed(c, CREST_LINES);
  const prog = SCFGraph.renderCrestProgress(c);
  assert.strictEqual(typeof prog, "string");
  assert.ok(prog.includes("vstep-rows"), "the stepper container");
  assert.ok(prog.includes("CREST conformer search"), "panel title");
  assert.ok(prog.includes("Metadynamics sampling"), "a stage label");
  assert.ok(prog.includes("vstep-pill ok") && prog.includes("DONE"), "finished pill");
  assert.ok(prog.includes("1 conformer"), "the headline result on the final stage (nConf singular)");
  assert.ok(!prog.includes("freq-jump") && !prog.includes("phase-track"), "old markup is gone");
  // an in-progress search: RUNNING pill, an emphasized current row, live key result
  const mid = new SCFGraph.CrestTracker();
  feed(mid, [" Initial Geometry Optimization", " Σ(t(MTD)) / ps : 70.0 (14 MTDs)",
             " Meta-Dynamics Iteration 1", "*MTD   1 completed successfully ... 1s"]);
  const midHtml = SCFGraph.renderCrestProgress(mid);
  assert.ok(midHtml.includes("RUNNING"), "running pill");
  assert.ok(midHtml.includes("vstep-row cur"), "a current-stage row");
  assert.ok(midHtml.includes("vstep-row done"), "a completed row before it");
  assert.ok(midHtml.includes("iteration 1 · 1/14 MTDs"), "live current-stage key result");
  // a stopped search past the first stage: STOPPED pill + a red current row
  const err = new SCFGraph.CrestTracker();
  err.push(" Initial Geometry Optimization");
  err.push(" Meta-Dynamics Iteration 1");
  err.push(" ERROR STOP safety termination of CREST");
  const errHtml = SCFGraph.renderCrestProgress(err);
  assert.ok(errHtml.includes("vstep-pill err") && errHtml.includes("STOPPED"), "stopped pill");
  assert.ok(errHtml.includes("vstep-row cur err"), "red current row");
  assert.ok(errHtml.includes("stopped — check the log"), "the stop hint as the current key result");
});

test("the stepper freezes each stage's key result and shows per-stage wall times", function () {
  const c = new SCFGraph.CrestTracker();
  feed(c, CREST_LINES);
  assert.ok(/iteration 2/.test(c.subs[1]), "the sampling stage's key result was frozen on leaving it");
  // deterministic clock: overwrite the wall-clock stamps (init/sample/md/gc/final/cregen)
  c.stageT = [1000, 39000, 200000, 210000, 253000, 300000];
  c.t0 = 1000; c.tEnd = 316000;
  const html = SCFGraph.renderCrestProgress(c);
  assert.ok(html.includes("0:38"), "init duration (entry-to-next-entry)");
  assert.ok(html.includes("2:41"), "sampling duration");
  assert.ok(html.includes("0:16"), "final CREGEN duration (entry to tEnd)");
  assert.ok(html.includes("elapsed <span>5:15"), "total elapsed, static once finished");
  // disk-rebuilt trackers suppress all wall times (the stamps are replay time)
  c.noTimes = true;
  const noT = SCFGraph.renderCrestProgress(c);
  assert.ok(!noT.includes("0:38") && !noT.includes("elapsed"), "no times on a seeded tracker");
});

test("the stepper marks a conditionally-skipped stage (CREST GC when nothing to cross)", function () {
  // a small-molecule run (like the real t2): reaches Final opt + CREGEN but the
  // pipeline never runs Genetic crossing (GC) because there's nothing to cross
  const c = new SCFGraph.CrestTracker();
  feed(c, [
    " Initial Geometry Optimization",
    " CREST iMTD-GC SAMPLING",
    " Meta-Dynamics Iteration 1",
    " Additional regular MDs on lowest 1 conformer(s)",
    "   |           Final Geometry Optimization        |",
    " Final Ensemble Information",
    " number of unique conformers for further calc            1",
    " CREST terminated normally.",
  ]);
  assert.strictEqual(c.aStage, "cregen", "advanced to CREGEN without ever entering GC");
  const html = SCFGraph.renderCrestProgress(c, { height: 600 });
  assert.ok(html.includes("vstep-row skipped"), "a skipped row is rendered");
  const gcRow = html.split("vstep-row").find(function (chunk) { return chunk.includes("Genetic crossing (GC)"); });
  assert.ok(gcRow && gcRow.trimStart().startsWith("skipped"), "GC is the skipped stage");
  assert.ok(gcRow.includes(">skipped<"), "the skipped row reads 'skipped'");
  // a run that DID cross has no skipped rows
  const full = new SCFGraph.CrestTracker();
  feed(full, CREST_LINES);
  assert.ok(!SCFGraph.renderCrestProgress(full, { height: 600 }).includes("vstep-row skipped"),
            "a full run (GC ran) has no skipped rows");
});

test("the stepper picks up job meta echoed in the output", function () {
  const c = new SCFGraph.CrestTracker();
  c.push(" $ /home/u/.local/bin/crest input.xyz --gfn2 --ewin 100 -T 4 --chrg 0 --uhf 0");
  assert.strictEqual(c.method, "GFN2-xTB");
  assert.strictEqual(c.cores, 4);
  const f = new SCFGraph.FreqTracker();
  f.push("|  1> ! wB97X-D4 def2-SVP VeryTightSCF RIJCOSX Freq def2/J");
  f.push("|  3> %pal nprocs 6 end");
  assert.strictEqual(f.method, "wB97X-D4 def2-SVP");
  assert.strictEqual(f.cores, 6);
  f.push("                       GEOMETRIC PERTURBATIONS (4 nuclei)");
  const html = SCFGraph.renderFreqProgress(f);
  assert.ok(html.includes("wB97X-D4 def2-SVP · 6 cores"), "meta line carries method + cores");
});

test("the stepper falls back to the compact strip when the height cannot fit the rows", function () {
  const mid = new SCFGraph.CrestTracker();
  feed(mid, [" Initial Geometry Optimization", " Σ(t(MTD)) / ps : 70.0 (14 MTDs)",
             " Meta-Dynamics Iteration 1", "*MTD   1 completed successfully ... 1s"]);
  const tall = SCFGraph.renderCrestProgress(mid, { height: 600 });
  assert.ok(tall.includes("vstep") && tall.includes('style="height:'), "tall window: full stepper sized to it");
  const short = SCFGraph.renderCrestProgress(mid, { height: 140 });
  assert.ok(short.includes("stepc") && !short.includes("vstep-rows"), "short window: compact strip");
  assert.ok(short.includes("STEP 2<span class=\"stepc-of\">/6</span>"), "compact step indicator");
  assert.ok(short.includes("Metadynamics sampling"), "compact carries the current stage label");
  assert.ok(short.includes("iteration 1 · 1/14 MTDs"), "compact carries the live key result");
  assert.ok(!short.includes("stripes"), "no hazard stripes on the compact strip");
});

test("CrestTracker counts an EARLY-terminated MTD toward the batch", function () {
  const c = new SCFGraph.CrestTracker();
  feed(c, [
    " Meta-Dynamics Iteration 2",
    "*MTD   1 completed successfully ...        4 min, 42.323 sec",
    "*MTD   3 terminated EARLY  ...        2 min, 25.625 sec",
  ]);
  assert.strictEqual(c.mtdDone, 2, "EARLY still delivered a trajectory — counter must not stall");
});

test("CrestTracker tracks the multilevel ensemble-optimization sub-phase", function () {
  // lines verbatim from a real CREST 3.0.2 run (Asp conformer search)
  const c = new SCFGraph.CrestTracker();
  feed(c, [
    " Initial Geometry Optimization",
    " t(MTD) / ps    :    17.0",
    " Σ(t(MTD)) / ps :   102.0 (6 MTDs)",
    " Meta-Dynamics Iteration 1",
    "*MTD   1 completed successfully ...        3 min, 55.219 sec",
  ]);
  assert.strictEqual(c.mtdLen, 17, "MTD length parsed");
  assert.ok(SCFGraph.renderCrestProgress(c).includes("iteration 1 · 1/6 MTDs · 17 ps each"));
  for (let k = 2; k <= 6; k++) c.push(`*MTD   ${k} completed successfully ...        3 min, 57.411 sec`);
  c.push(' Optimizing all 1014 structures from file "crest_dynamics.trj" ...');
  c.push(" crude pre-optimization");
  assert.ok(SCFGraph.renderCrestProgress(c).includes("optimizing 1014 structures (crude)"),
            "the multi-minute optimization block is visible, not a stalled MTD count");
  c.push("CREGEN> E lowest :   -74.74126");
  c.push(" 472 structures remain within    12.00 kcal/mol window");
  c.push(" optimization with tight thresholds");
  assert.ok(SCFGraph.renderCrestProgress(c).includes("optimizing 472 structures (tight)"),
            "the tight sub-block adopts the crude CREGEN's in-window count");
  c.push("CREGEN> E lowest :   -74.74664");
  c.push(" 93 structures remain within     6.00 kcal/mol window");
  assert.ok(SCFGraph.renderCrestProgress(c).includes("iteration 1 · E lowest -74.74664 Eh · 93 in 6 kcal window"),
            "after the iteration's tight CREGEN the sub shows the ensemble state");
});

test("the live CREGEN ensemble state rides the sampling row, next to the MTD count", function () {
  const c = new SCFGraph.CrestTracker();
  feed(c, [
    " Σ(t(MTD)) / ps :   102.0 (6 MTDs)",
    " Meta-Dynamics Iteration 1",
    "CREGEN> E lowest :   -74.74664",
    " 93 structures remain within     6.00 kcal/mol window",
    " A new lower conformer was found!",
    " Improved by    0.00020 Eh or    0.12361kcal/mol",
    " Meta-Dynamics Iteration 2",
    "*MTD   1 completed successfully ...        4 min, 42.323 sec",
  ]);
  const html = SCFGraph.renderCrestProgress(c, { height: 600 });
  assert.ok(html.includes("iteration 2 · 1/6 MTDs · E lowest -74.74664 Eh · 93 in 6 kcal window · new lowest −0.12 kcal/mol"),
            "MTD count and the CREGEN state share the sampling row's key-result line");
  assert.ok(!html.includes("vstep-foot"), "no footer strip while the search runs");
  // best-so-far: a later (higher) crude-pass E must not displace the minimum
  c.push("CREGEN> E lowest :   -74.73693");
  assert.ok(SCFGraph.renderCrestProgress(c, { height: 600 }).includes("E lowest -74.74664 Eh"));
});

test("a finished CREST stepper's footer shows ensemble statistics + runtime split", function () {
  const c = new SCFGraph.CrestTracker();
  feed(c, CREST_LINES.slice(0, -1).concat([
    " population of lowest in %             :   15.755",
    " ensemble free energy (kcal/mol)       :   -2.031",
    " Metadynamics (MTD)         ...       13 min, 47.202 sec ( 42.236%)",
    " Geometry optimization      ...       18 min, 45.221 sec ( 57.453%)",
    " CREST terminated normally.",
  ]));
  const html = SCFGraph.renderCrestProgress(c, { height: 600 });
  assert.ok(html.includes("lowest populated 15.8%"));
  assert.ok(html.includes("ensemble ΔG -2.03 kcal/mol"));
  assert.ok(html.includes("runtime: MTD 42% / opt 57%"));
  assert.ok(!html.includes("CREGEN · E lowest"), "the live CREGEN strip is replaced once done");
});

test("the CREST meta line carries the echoed solvent and NCI mode", function () {
  const c = new SCFGraph.CrestTracker();
  c.push(" $ /home/u/.local/bin/crest input.xyz --gfn2 --alpb acetone --ewin 6 -T 12 --nci --chrg 0 --uhf 0");
  c.push(" Initial Geometry Optimization");
  const html = SCFGraph.renderCrestProgress(c);
  assert.ok(html.includes("GFN2-xTB · ALPB(acetone) · NCI mode · 12 cores"),
            "method · solvent · NCI · cores on the meta line");
});

// ---------- summary ----------
console.log("");
console.log(passed + " passed, " + failed + " failed");
if (failed > 0) process.exitCode = 1;

test("geo tracker: post-opt stage reads complete after ORCA TERMINATED NORMALLY (re-seeded DONE calc)", function () {
  const g = new SCFGraph.GeoTracker();
  feed(g, geoCycleBlock(1, fiveRows("0.0140882096")));
  g.push("                    ***  OPTIMIZATION RUN DONE  ***");
  g.push("-----------------------");
  g.push("VIBRATIONAL FREQUENCIES");
  assert.strictEqual(g.postStage, "frequencies / properties");
  assert.strictEqual(g.postDone, false, "still running until the termination banner");
  g.push("                             ****ORCA TERMINATED NORMALLY****");
  assert.strictEqual(g.postDone, true, "terminated -> the post-opt stage is complete");
});
