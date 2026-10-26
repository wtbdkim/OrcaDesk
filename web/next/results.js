// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the report.

   A finished calculation read as a document, not as a dump. Every section is
   built out of the design's own components — a heading with a `.meta`, one line
   of `.secdesc` saying what the section is, then a `.plate` or a `.tw` table —
   so app.css styles it and the spacing rhythm comes from the sheet rather than
   from ad-hoc margins here.

   The summary is CURATED. ORCA's parse carries ~40 scalars and printing them in
   source order is a data dump, not a result: the questions a person asks first
   (did it finish, did it converge, how long) go in a `.factline`, what the
   calculation actually produced goes in a plate of `.kvline` pairs, and the SCF
   decomposition and provenance sit behind "Show all". Anything the parser adds
   that this file has not heard of lands in the VISIBLE plate, never behind the
   toggle — a new value must not be able to hide itself.

   The parsing is all the backend's (parse_calc_output / parse_out_path); this
   file decides only what a section looks like. Charts are drawn at the box's
   real pixel width — a fixed viewBox scaled to fit would scale its strokes and
   its type with it.
   ============================================================ */

(function () {

  /** @type {any} the ParsePayload on screen */
  let _cur = null;
  /** what identifies it: a queued calculation's name, or a path on disk */
  let _curName = "";
  let _curPath = "";
  /** @type {any[]} */
  /** Which of the two the Output/Visual pair last jumped to. It is a cursor on
   *  one scrolling report, not a mode: both halves are always rendered. */
  let _visual = false;
  let _showAll = false;
  /** @type {{id: string, title: string}[]} rebuilt as the sections render */
  let _present = [];

  const HA_KCAL = 627.5094740631;
  const HA_KJ = 2625.499639;

  /* The ones the eye goes to first: did it finish, did it converge, what did it
     cost. They read as one line, not as rows. */
  const HEADLINE = ["Termination", "Error", "Optimization", "Run time",
                    "Charge / Mult", "Atoms", "Imaginary modes"];
  /* Provenance and the SCF energy decomposition: real, occasionally needed,
     never the answer to why the calculation was run. Behind "Show all". */
  const DEMOTE = ["File", "ORCA version", "Electrons", "Nuclear repulsion",
                  "Electronic energy", "One-electron energy", "Two-electron energy",
                  "Kinetic energy", "Potential energy", "Virial ratio",
                  "Rotational const."];

  // ---------------------------------------------------------------- pieces

  /** @param {string} id @param {string} title @param {string} meta
   *  @param {string} desc @param {string} body @param {string} [span] */
  function sec(id, title, meta, desc, body, span) {
    _present.push({ id: id, title: title });
    return `<section class="secblock ${span || ""}" data-sec="${id}" id="${id}">
      <div class="sech"><h2>${NB.esc(title)}</h2><span class="sp"></span>${
        meta ? `<span class="meta">${NB.esc(meta)}</span>` : ""}</div>
      ${desc ? `<p class="secdesc">${desc}</p>` : ""}
      ${body}</section>`;
  }
  /** @param {string} html @param {string} [style] */
  function plate(html, style) {
    return `<div class="plate"${style ? ` style="${style}"` : ""}>${html}</div>`;
  }
  /** A table in its own scroll box. `num` marks the numeric columns, which line
   *  up on the right so digits are comparable down the column.
   *  @param {string[]} head @param {string[][]} rows @param {boolean[]} [num] */
  function tw(head, rows, num) {
    const isNum = (i) => (num ? !!num[i] : i > 0);
    return `<div class="tw"><table>
      <thead><tr>${head.map((h, i) =>
        `<th${isNum(i) ? ' style="text-align:right"' : ""}>${NB.esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr>${r.map((c, i) =>
        `<td class="${isNum(i) ? "n" : "lab"}">${c}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>`;
  }
  /** @param {[string, string][]} pairs */
  function kvlines(pairs) {
    return pairs.map(p =>
      `<div class="kvline"><span>${NB.esc(p[0])}</span><span class="v">${p[1]}</span></div>`).join("");
  }
  /** One atom, numbered the way every atom on this screen is numbered.
   *
   *  ORCA indexes its Mayer and NMR tables from ZERO, and the parse keeps that
   *  index verbatim; the geometry, Mulliken and Löwdin tables here are numbered
   *  by position from ONE, and so is the label the 3D picker puts on an atom
   *  (`O1`). Passing ORCA's number through meant one molecule wearing two
   *  numberings on one screen — the Mayer section even says "also on any bond
   *  you click in Structure", which then answered `O1 – H2` for the row called
   *  `0-O – 1-H`. Everything user-facing counts from one; this is the one place
   *  that knows ORCA does not. @param {number} i @param {string} el */
  function atomLabel(i, el) { return (i + 1) + " " + NB.esc(el); }

  /** The same, for a Mayer bond end, which the parser keeps as ORCA printed it
   *  ("0-O"). @param {string} label */
  function mayerAtom(label) {
    const m = /^\s*(\d+)\s*-\s*(.*)$/.exec(String(label));
    return m ? atomLabel(Number(m[1]), m[2].trim()) : NB.esc(String(label));
  }

  /** @param {string} v @param {string} label */
  function tone(v, label) {
    if (/ABNORMAL|NOT converged/i.test(v)) return "err";
    if (/imaginary/i.test(label) && !/^0$/.test(v)) return "warn";
    if (/converged|Normal/i.test(v)) return "ok";
    return "";
  }

  // ---------------------------------------------------------------- drawing

  /** A line or stick chart in SVG at a real pixel size. With `hover` the sticks
   *  become readable: each carries its `lab` and bindCharts wires the cursor.
   *  @param {{x: number, y: number, cls?: string, lab?: string}[]} pts
   *  @param {number} w @param {number} h
   *  @param {{xlab: string, ylab: string, stick?: boolean, hover?: boolean,
   *           pick?: string}} o pick names what a click on a band does
   *    ("mode": shows that vibration moving on the Structure stage) */
  function chart(pts, w, h, o) {
    if (!pts.length) return `<p class="hint" style="margin:0">Nothing to plot.</p>`;
    const L = 64, R = 16, T = 12, B = 40;
    const iw = Math.max(w - L - R, 40), ih = Math.max(h - T - B, 40);
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    let y0 = Math.min(0, Math.min(...ys)), y1 = Math.max(...ys);
    if (x1 === x0) { x0 -= 1; x1 += 1; }
    if (y1 === y0) { y1 = y0 + 1; }
    y1 += (y1 - y0) * 0.08;
    const px = (v) => L + ((v - x0) / (x1 - x0)) * iw;
    const py = (v) => T + ih - ((v - y0) / (y1 - y0)) * ih;

    let grid = "";
    for (let i = 0; i <= 4; i++) {
      const y = T + (ih * i) / 4;
      const v = y1 - ((y1 - y0) * i) / 4;
      grid += `<line x1="${L}" y1="${y}" x2="${L + iw}" y2="${y}" stroke="var(--border)" stroke-width="1"/>`
            + `<text x="${L - 8}" y="${y + 3.5}" text-anchor="end" font-size="10"
                 fill="var(--muted-foreground)">${fmt(v)}</text>`;
    }
    let body;
    if (o.stick) {
      body = pts.map(p =>
        `<line class="stk" data-px="${px(p.x).toFixed(1)}"${
           o.hover ? ` data-lab="${NB.esc(p.lab || fmt(p.x))}"` : ""}
               x1="${px(p.x).toFixed(1)}" y1="${py(0).toFixed(1)}"
               x2="${px(p.x).toFixed(1)}" y2="${py(p.y).toFixed(1)}"
               stroke="${p.cls || "var(--crit-de)"}" stroke-width="1.6"/>`).join("");
    } else {
      const d = pts.map((p, i) => `${i ? "L" : "M"}${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`).join(" ");
      body = `<path d="${d}" fill="none" stroke="var(--crit-de)" stroke-width="1.8"
                stroke-linejoin="round" stroke-linecap="round"/>`
           + pts.map(p => `<circle cx="${px(p.x).toFixed(1)}" cy="${py(p.y).toFixed(1)}" r="3"
                fill="${p.cls || "var(--crit-de)"}"/>`).join("");
    }
    // the outermost ticks anchor to their own edge, or a wide label hangs past
    // the plot — an exponential is easily 40px
    const anchor = ["start", "middle", "end"];
    const ticks = [x0, (x0 + x1) / 2, x1].map((v, i) =>
      `<text x="${px(v).toFixed(1)}" y="${T + ih + 16}" text-anchor="${anchor[i]}"
         font-size="10" fill="var(--muted-foreground)">${fmt(v)}</text>`).join("");

    // The readout furniture, only when asked for: a cursor line the width of the
    // plot (a band is 1.6px, so highlighting the band alone is not enough to see
    // where the pointer is) and the value plate bindCharts fills in.
    const hov = o.hover
      ? `<line class="chi" x1="${L}" y1="${T}" x2="${L}" y2="${T + ih}"/>` : "";
    return `<div class="chart"><svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img"${
      o.hover ? ' data-hover="1" tabindex="0"' : ""}${
      o.pick ? ` data-pick="${o.pick}"` : ""}
      aria-label="${NB.esc(o.ylab)} against ${NB.esc(o.xlab)}${
        o.hover ? ". Hover a band for its value, or use the arrow keys" : ""}${
        o.pick === "mode" ? "; click a band, or press Enter on it, to see it move" : ""}">
      ${grid}
      <line x1="${L}" y1="${T}" x2="${L}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      <line x1="${L}" y1="${T + ih}" x2="${L + iw}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      ${hov}${body}${ticks}
      <text x="${L + iw / 2}" y="${h - 6}" text-anchor="middle" font-size="10.5"
        fill="var(--muted-foreground)">${NB.esc(o.xlab)}</text>
      <text x="17" y="${T + ih / 2}" text-anchor="middle" font-size="10.5"
        fill="var(--muted-foreground)" transform="rotate(-90 17 ${T + ih / 2})">${NB.esc(o.ylab)}</text>
    </svg>${o.hover ? `<div class="chtip" role="status" hidden></div>` : ""}</div>`;
  }
  /** @param {number} v */
  function fmt(v) {
    const a = Math.abs(v);
    if (a === 0) return "0";
    if (a >= 1000 || a < 0.01) return v.toExponential(1);
    return String(Math.round(v * 100) / 100);
  }

  /** The imaginary modes, one button each, under the spectrum. They are the
   *  modes a reader comes to a frequency result to look at -- is this the
   *  reaction coordinate, or a methyl rotor? -- and a negative band sits on
   *  the left edge of a crowded axis, so they get a way in that needs no aim.
   *  @param {number[]} freqs */
  function modeJump(freqs) {
    const im = freqs.map((f, i) => ({ f: f, n: i + 1 })).filter(m => m.f < 0);
    if (!im.length) return "";
    return `<div class="modejump" role="group" aria-label="Imaginary modes">
      <span class="hint">Imaginary:</span>${im.map(m =>
        `<button class="btn btn-sm btn-ghost" type="button" data-mode="${m.n}"
           aria-pressed="false">Mode ${m.n} · ${m.f.toFixed(2)} cm⁻¹</button>`).join("")}
    </div>`;
  }

  // ---------------------------------------------------------------- summary

  /** @param {any} d */
  function summary(d) {
    const rows = (d.summary || []).filter(r =>
      _showAll || (r[2] || "") !== "elec" || d.show_elec);
    const head = [], key = [], rest = [];
    rows.forEach(r => {
      const label = r[0], value = String(r[1]);
      if (HEADLINE.indexOf(label) >= 0) head.push([label, value]);
      else if (DEMOTE.indexOf(label) >= 0) rest.push([label, value]);
      else key.push([label, value]);
    });

    const calc = _curName ? NB.queue.find(c => c.name === _curName) : null;
    const facts = head.map(p => {
      const t = tone(p[1], p[0]);
      const short = p[0] === "Charge / Mult" ? "charge / mult"
        : p[0] === "Run time" ? "time" : p[0].toLowerCase();
      return `<span>${NB.esc(short)} <b${t ? ` class="${t}"` : ""}>${NB.esc(p[1])}</b></span>`;
    }).join("");

    // the reference's own summary: not in a card — a .sech carrying the name,
    // then the facts
    _present.push({ id: "sec-summary", title: "Summary" });
    let out = `<section class="secblock wide" data-sec="sec-summary" id="sec-summary">
      <div class="sech">
        <div class="summaryhead">
          <span class="nm">${NB.esc(_curName || nameFromPath(_curPath) || "Result")}</span>
          ${calc ? `<span class="badge ${calc.state}">${calc.state}</span>` : ""}
        </div>
      </div>
      ${facts ? `<div class="factline" style="margin:0 0 18px">${facts}</div>` : ""}
      ${key.length ? plate(kvlines(key.map(p => [p[0], NB.esc(p[1])]))) : ""}
    </section>`;
    if (_showAll && rest.length) {
      out += sec("sec-all", "Everything else the parser read", rest.length + " values",
        "Provenance and the SCF energy decomposition — real, occasionally needed, "
        + "and not what the calculation was run for.",
        tw(["Quantity", "Value"], rest.map(p => [NB.esc(p[0]), NB.esc(p[1])])), "wide");
    }
    return out;
  }

  // ---------------------------------------------------------------- sections

  /** @param {any} d @param {number} px width of a full-width section */
  function sections(d, px) {
    const out = [summary(d)];
    const cw = px - 36;                       // inside a .plate (18px each side)
    const half = Math.round(px / 2) - 36;     // ...of a half-width section

    // the input beside the output, first: every other section is read out of it.
    // An MLIP or CREST run was handed no .inp, and has no wavefunction for the
    // natural-orbital analysis to read -- neither section is drawn for one
    // (the reference's MLIP and CREST results carry neither, DESIGN.md §17.4s)
    const orca = resultBackend() === "DFT";
    out.push(rawSections(orca));

    if (d.frequencies && d.frequencies.length) {
      const imag = d.n_imaginary || d.frequencies.filter(f => f < 0).length;
      // No table under the spectrum: a hundred-odd rows of one number each is
      // the chart transcribed, and it buried everything below it. The number is
      // on the band instead, where the reader is already looking.
      const pts = d.frequencies.map((f, i) => ({
        x: f, y: 1, cls: f < 0 ? "var(--err)" : "var(--crit-de)",
        lab: `Mode ${i + 1} · ${f.toFixed(2)} cm⁻¹${f < 0 ? " · imaginary" : ""}` }));
      out.push(sec("sec-freq", "Vibrational modes",
        d.frequencies.length + " modes · " + imag + " imaginary",
        (imag ? "One imaginary mode is what makes a structure a transition state; more "
              + "than one means it is not a stationary point of either kind."
              : "No imaginary modes — this geometry is a minimum on the surface.")
        + " Point at a band for its wavenumber; the arrow keys step through them. Click"
        + " one, or press Enter on it, to see that mode move on the structure.",
        plate(chart(pts, cw, Math.round(cw * 0.26),
                    { xlab: "wavenumber (cm⁻¹)", ylab: "modes",
                      stick: true, hover: true, pick: "mode" })
              + modeJump(d.frequencies)),
        "wide"));
    }

    // the structure, and everything else in its folder that can be drawn
    const struct = NB.viewer.structureSection(d);
    if (struct) { _present.push({ id: "sec-structure", title: "Structure" }); out.push(struct); }
    _present.push({ id: "sec-visual", title: "Visual" });
    out.push(NB.viewer.visualSection());

    if (d.transitions && d.transitions.length) {
      const bright = d.transitions.reduce((a, b) => (b.fosc > a.fosc ? b : a));
      const byState = {};
      (d.tddft_states || []).forEach(st => { byState[st.state] = st; });
      out.push(sec("sec-tddft", "Excited states", d.transitions.length + " transitions",
        `The brightest is at ${bright.nm.toFixed(1)} nm (f = ${bright.fosc.toFixed(4)}). `
        + "Oscillator strength is what a measured spectrum would actually show.",
        plate(chart(d.transitions.map(t => ({ x: t.nm, y: t.fosc })),
                    cw, Math.round(cw * 0.26),
                    { xlab: "wavelength (nm)", ylab: "f (osc.)", stick: true }))
        + tw(["State", "eV", "nm", "f", "Character"], d.transitions.map(t => {
            const st = byState[t.state];
            const chr = st && st.contributions
              ? st.contributions.slice(0, 3)
                  .map(c => `${NB.esc(c[0])}→${NB.esc(c[1])} ${(c[2] * 100).toFixed(0)}%`).join(", ")
              : "";
            return [String(t.state), t.ev.toFixed(3), t.nm.toFixed(1), t.fosc.toFixed(4), chr];
          }), [false, true, true, true, false]),
        "wide"));
    } else if (d.tddft_states && d.tddft_states.length) {
      out.push(sec("sec-tddft", "Excited states", d.tddft_states.length + " states",
        "Which orbital pairs each state is made of, largest contribution first.",
        tw(["State", "eV", "Main contributions"], d.tddft_states.map(st => [
          String(st.state), st.ev.toFixed(3),
          (st.contributions || []).slice(0, 4)
            .map(c => `${NB.esc(c[0])}→${NB.esc(c[1])} ${(c[2] * 100).toFixed(0)}%`).join(", "),
        ]), [false, true, false]), "wide"));
    }

    if (d.is_conformer_search && d.conformers && d.conformers.length) {
      out.push(sec("sec-conf", "Conformer ensemble", d.conformers.length + " conformers",
        "Ranked by energy; the first is the one a later calculation referencing this "
        + "search receives.",
        plate(chart(d.conformers.map(c => ({ x: c.index, y: c.rel_kcal })),
                    half, Math.round(half * 0.5),
                    { xlab: "rank", ylab: "ΔE (kcal/mol)" }))
        + tw(["#", "ΔE (kcal/mol)", "Energy (Eh)", "Atoms"], d.conformers.map(c =>
            [String(c.index), c.rel_kcal.toFixed(3), c.energy_eh.toFixed(6), String(c.n_atoms)]))));
    }

    if (d.nmr && d.nmr.length) {
      out.push(sec("sec-nmr", "NMR shieldings", d.nmr.length + " nuclei",
        "Absolute isotropic shielding. A chemical shift is this subtracted from a "
        + "reference computed at the same level.",
        plate(chart(d.nmr.map(n => ({ x: n.idx, y: n.iso })), half, Math.round(half * 0.5),
                    { xlab: "nucleus", ylab: "σ iso (ppm)", stick: true }))
        + tw(["Nucleus", "σ iso (ppm)", "Anisotropy (ppm)"], d.nmr.map(n =>
            [atomLabel(n.idx, n.el), n.iso.toFixed(3), n.aniso.toFixed(3)]))));
    }

    if (d.neb_path && d.neb_path.length) {
      const irc = d.neb_path_kind === "irc";
      const ts = d.neb_path.filter(p => p.is_ts)[0];
      out.push(sec("sec-path", "Reaction path",
        (irc ? "IRC" : "NEB-TS") + " · " + d.neb_path.length + " images",
        irc ? "The intrinsic reaction coordinate, run downhill from the transition state "
            + "in both directions."
            : "Energy along the band. The marked image is the transition state."
              + (ts ? ` Its barrier is ${ts.de_kcal.toFixed(2)} kcal/mol.` : ""),
        plate(chart(d.neb_path.map((p, i) => ({
                      x: i, y: p.de_kcal,
                      cls: p.is_ts ? "var(--warn)" : "var(--crit-de)" })),
                    cw, Math.round(cw * 0.26),
                    { xlab: "image", ylab: "ΔE (kcal/mol)" }))
        + tw(["Image", "ΔE (kcal/mol)", ""], d.neb_path.map(p => [
            NB.esc(p.label), p.de_kcal.toFixed(2),
            p.is_ts ? '<span class="badge running">TS</span>' : ""]),
            [false, true, false]),
        "wide"));
    }

    if (d.scan_points && d.scan_points.length && d.scan_coords && d.scan_coords.length) {
      out.push(scanSection(d, cw));
    }

    if (orca) {
      _present.push({ id: "sec-nbo", title: "Natural orbitals" });
      out.push(NB.viewer.nboSection());
    }

    // Per-atom and per-bond numbers belong to the Structure viewer — click an
    // atom or a bond and they are right there. As TABLES they are for reading
    // without a mouse, so they live under Show all with everything else.
    if (_showAll) out.push(tables(d));
    return out.join("");
  }

  /** A relaxed surface scan: the energy along the coordinate it walked
   *  (DESIGN.md §17.4r). Each point is a whole optimization with that
   *  coordinate held, so a point is a STATE of the curve, not a sample along
   *  a path — the line joins them only to be read. ΔE is against the lowest
   *  point, as ORCA's own table has no reference of its own; the highest one
   *  is marked, because a scan is most often run to find where that is. Its
   *  structures are the "Scan points" set in Visual, step for step.
   *  @param {any} d @param {number} cw */
  function scanSection(d, cw) {
    const coords = d.scan_coords, pts = d.scan_points;
    const geo = d.geometry || [];
    /** "Angle H2–O1–H3": ORCA's own order, numbered from 1 like every atom
     *  on this screen (§17.4d) @param {any} c */
    const name = (c) => ({ B: "Bond", A: "Angle", D: "Dihedral" }[c.kind] || "Coordinate")
      + " " + c.atoms.map(i => (geo[i] ? geo[i].el : "") + (i + 1)).join("–");
    /** @param {any} c */
    const unit = (c) => c.kind === "B" ? "Å" : "°";
    /** @param {any} c @param {number} v */
    const val = (c, v) => v.toFixed(c.kind === "B" ? 3 : 1);
    const planned = coords.reduce((n, c) => n * (c.steps || 1), 1);
    const meta = (pts.length < planned ? `${pts.length} of ${planned}` : String(pts.length))
      + " points";
    const stopped = pts.length < planned
      ? ` The run stopped before the other ${planned - pts.length}; these are the points it finished.`
      : "";
    const top = pts.reduce((a, b) => (b.de_kcal > a.de_kcal ? b : a));
    const rows = pts.map((p, i) => [String(i + 1)]
      .concat(coords.map((c, k) => val(c, p.values[k])))
      .concat([p.de_kcal.toFixed(2), p.e_eh.toFixed(6)]));
    const head = ["Point"].concat(coords.map(c => `${name(c)} (${unit(c)})`))
      .concat(["ΔE (kcal/mol)", "Energy (Eh)"]);

    if (coords.length > 1) {
      // A grid is a surface, and one line through it would be a lie about
      // which neighbours are neighbours. Listed, not drawn.
      return sec("sec-scan", "Relaxed surface scan", meta,
        `A ${coords.length}-dimensional scan over ${coords.map(name).map(NB.esc).join(" and ")}; `
        + "each point is an optimization with those coordinates held. ΔE is against the "
        + "lowest point. A surface is listed here, not drawn." + stopped,
        tw(head, rows), "wide");
    }
    const c = coords[0];
    return sec("sec-scan", "Relaxed surface scan", meta,
      `${NB.esc(name(c))} from ${val(c, c.start)} to ${val(c, c.end)} ${unit(c)}; each point `
      + "is an optimization with it held there, the rest of the structure relaxed. ΔE is "
      + `against the lowest point; the highest, at ${val(c, top.values[0])} ${unit(c)}, `
      + `is ${top.de_kcal.toFixed(2)} kcal/mol above it. Its structures are the scan points `
      + "in <b>Visual</b>." + stopped,
      plate(chart(pts.map(p => ({ x: p.values[0], y: p.de_kcal,
                                  cls: p === top ? "var(--warn)" : undefined })),
                  cw, Math.round(cw * 0.26),
                  { xlab: `${name(c)} (${unit(c)})`, ylab: "ΔE (kcal/mol)" }))
      + tw(head, rows),
      "wide");
  }

  /** The per-atom and per-bond tables, under Show all. @param {any} d */
  function tables(d) {
    const out = [];
    if (d.orbitals && d.orbitals.length) {
      out.push(sec("sec-orb", "Orbital energies", d.orbitals.length + " orbitals",
        "The gap between the highest occupied and the lowest empty level is the one "
        + "number most of this table exists to give.",
        tw(["Orbital", "Occupation", "eV", ""], d.orbitals.map(o => [
          "#" + o.idx + (o.spin ? " " + NB.esc(o.spin) : ""),
          o.occ.toFixed(3), o.ev.toFixed(3),
          o.frontier ? `<span class="badge">${o.frontier.toUpperCase()}</span>` : ""]),
          [false, true, true, false])));
    }
    if (d.mulliken && d.mulliken.length) {
      out.push(sec("sec-mull", "Mulliken charges", d.mulliken.length + " atoms",
        "Also on any atom you click in <b>Structure</b>. Basis-set dependent, so read "
        + "the pattern rather than the digits.",
        tw(["Atom", "Charge (e)"], d.mulliken.map((m, i) =>
          [atomLabel(i, m[0]), m[1].toFixed(6)]))));
    }
    if (d.loewdin && d.loewdin.length) {
      out.push(sec("sec-loew", "Löwdin charges", d.loewdin.length + " atoms",
        "The same partition after symmetric orthogonalization; usually the steadier of "
        + "the two across basis sets.",
        tw(["Atom", "Charge (e)"], d.loewdin.map((m, i) =>
          [atomLabel(i, m[0]), m[1].toFixed(6)]))));
    }
    if (d.mayer_bonds && d.mayer_bonds.length) {
      out.push(sec("sec-mbo", "Mayer bond orders", d.mayer_bonds.length + " bonds",
        "Also on any bond you click in <b>Structure</b>. Near 1, 2 and 3 for single, "
        + "double and triple bonds.",
        tw(["Bond", "Order"], d.mayer_bonds.map(b =>
          [`${mayerAtom(b[0])} – ${mayerAtom(b[1])}`, b[2].toFixed(4)]))));
    }
    if (d.mayer_valences && d.mayer_valences.length) {
      out.push(sec("sec-mval", "Mayer valences", d.mayer_valences.length + " atoms",
        "Total bonding capacity used per atom; a value well below the usual one points "
        + "at a bond the geometry has stretched.",
        tw(["Atom", "Valence"], d.mayer_valences.map(v =>
          [atomLabel(v[0], v[1]), v[2].toFixed(4)]))));
    }
    if (d.geometry && d.geometry.length) {
      out.push(sec("sec-geom", "Final geometry", d.geometry.length + " atoms",
        "The coordinates the run ended on — what a later calculation referencing this "
        + "one starts from.",
        tw(["Atom", "x (Å)", "y (Å)", "z (Å)"], d.geometry.map((a, i) =>
          [`${i + 1} ${NB.esc(a.el)}`, a.x.toFixed(6), a.y.toFixed(6), a.z.toFixed(6)]))));
    }
    return out.join("");
  }

  /** Orbitals near the frontier. A full manifold is thousands of rows nobody
   *  reads; the ones that matter are around HOMO/LUMO. @param {any[]} orbs */
  function frontierWindow(orbs) {
    if (_showAll) return orbs;
    let h = -1;
    orbs.forEach((o, i) => { if (o.frontier === "homo") h = i; });
    if (h < 0) return orbs.slice(0, 40);
    return orbs.slice(Math.max(0, h - 12), h + 13);
  }

  // ---------------------------------------------------------------- raw pair

  /** The name the backend resolves a run folder by. A result opened from disk
   *  is not in the queue, but a run folder is `{workspace}/{name}/` and the
   *  artifact inside it is `{name}.out` — so the folder name IS the name the
   *  .inp/.out slots take. @param {string} path */
  function nameFromPath(path) {
    const parts = String(path || "").split(/[\\/]/).filter(Boolean);
    const file = parts.length ? parts[parts.length - 1] : "";
    return file.replace(/\.(out|mlip\.json|log)$/i, "");
  }

  /** @param {boolean} orca an ORCA run, the only kind that was handed an .inp */
  function rawSections(orca) {
    if (orca) _present.push({ id: "sec-input", title: "Input" });
    _present.push({ id: "sec-output", title: "Output" });
    return (orca ? `<section class="secblock third" data-sec="sec-input" id="sec-input">
        <div class="sech"><h2>Input</h2></div>
        <p class="secdesc">The file handed to ORCA, exactly as it ran.</p>
        <div class="plate" style="padding:0;overflow:hidden">
          <div class="rawin" data-r="in"></div></div>
      </section>` : "")
      + `<section class="secblock ${orca ? "twothirds" : "wide"}" data-sec="sec-output" id="sec-output">
        <div class="sech"><h2>Output</h2><span class="sp"></span>
          <span class="meta" data-r="ometa"></span></div>
        <p class="secdesc">${orca ? "Everything ORCA wrote. Every other section here is read out of it."
          : "Everything the run wrote to its log. The numbers above are read from its result files."}</p>
        <div class="plate" style="padding:0;overflow:hidden">
          <div class="outbar">
            <label class="lb" for="out-find">Find</label>
            <input id="out-find" type="search" placeholder="e.g. CONVERGED"
              spellcheck="false" autocomplete="off" data-r="find" disabled>
            <span class="lb" data-r="fcount" aria-live="polite"></span>
            <button class="btn btn-sm btn-ghost" type="button" data-r="fprev"
              aria-label="Previous match" disabled>Prev</button>
            <button class="btn btn-sm btn-ghost" type="button" data-r="fnext"
              aria-label="Next match" disabled>Next</button>
            <span class="sp"></span>
            <button class="btn btn-sm btn-ghost" type="button" data-r="top">↑ Top</button>
            <button class="btn btn-sm btn-ghost" type="button" data-r="tail">↓ End</button>
          </div>
          <div class="rawout" data-r="out" tabindex="0" role="region"
            aria-label="ORCA output"></div>
        </div>
      </section>`;
  }

  /** @param {string} name a calc name, or "file:<path>" for a result opened
   *  from disk -- its folder is the file's own, not {workspace}/{stem}
   *  @param {any} d the payload, for its input echo */
  async function fillRaw(name, d) {
    const host = NB.$("reswin");
    const inEl = host.querySelector('[data-r="in"]');
    const outEl = host.querySelector('[data-r="out"]');
    if (!outEl) return;
    if (!name) { if (inEl) inEl.textContent = "—"; outEl.textContent = "—"; return; }
    // first, not after the input's awaits: a report re-rendered meanwhile
    // must not have this (older) result's output mounted over its own
    mountOutput(host, name);
    if (!inEl) return;      // an MLIP or CREST run: there is no input file to show

    // the .inp ORCA actually read, off disk — not a preview regenerated from
    // the form, which can differ from what ran
    let inp = "";
    const disk = await NB.call("get_inp", name);
    if (disk && disk.ok && disk.text) inp = disk.text;
    if (!inp && !name.startsWith("file:")) {
      const g = await NB.call("get_calc", name);
      if (g && g.ok && g.calc) {
        if (g.calc.is_raw && g.calc.raw_text) inp = g.calc.raw_text;
        else {
          const pv = await NB.call("build_inp_preview", JSON.stringify(g.calc));
          if (pv && pv.ok) inp = pv.text;
        }
      }
    }
    // last resort: the parser echoes the input it read out of the .out itself,
    // which is the only source for a result whose .inp is gone. input_block is
    // the whole echo and already CONTAINS the "!" line input_keywords is the
    // summary of, so joining the two printed that line twice; the keywords are
    // the fallback for an echo the parser could not read, not an addition.
    if (!inp && d && (d.input_keywords || d.input_block)) {
      inp = d.input_block || ("! " + d.input_keywords);
    }
    inEl.textContent = inp || "No input file for this result.";
  }

  // ---------------------------------------------------------------- output
  /* The whole .out, from its first line. A real one runs to hundreds of MB, so
     the page never holds it: the backend indexes the line offsets once
     (orcamgr/outview.py) and this draws the lines on screen plus a margin,
     fetched by number in chunks, inside a spacer as tall as the whole file --
     so the scrollbar is the file's scrollbar. Find runs on the backend for the
     same reason and returns the line of every match; the matches on screen
     are marked with the CSS Custom Highlight API, which paints ranges over the
     text nodes without splitting them. */

  const OUT_CHUNK = 400;        // lines per fetch
  const OUT_OVERSCAN = 40;      // lines drawn above and below the screen
  const OUT_CACHE = 120;        // chunks kept (48k lines)
  // Chromium stops laying out a box somewhere past 33M px; above this the
  // spacer stays at this height and the scroll position maps onto the file
  // proportionally instead of 1:1 (a ~750k-line .out)
  const OUT_MAXPX = 15e6;

  /** @type {any} the live pane; a later mount replaces it, and a stale reply
   *  checks identity against it */
  let _out = null;

  /** ASCII-only case folding, because that is what the backend's search does
   *  (bytes.lower) -- folding more here would mark text it never counted.
   *  @param {string} s */
  const asciiLower = (s) => s.replace(/[A-Z]+/g, m => m.toLowerCase());

  /** @param {number} b */
  function fmtSize(b) {
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(b < 10240 ? 1 : 0) + " kB";
    return (b / 1048576).toFixed(b < 10485760 ? 1 : 0) + " MB";
  }

  /** @param {any} hl {all, cur} Highlights, or null to clear */
  function setMarks(hl) {
    const reg = /** @type {any} */ (CSS).highlights;
    if (!reg) return;
    if (!hl) { reg.delete("outfind"); reg.delete("outfind-cur"); return; }
    reg.set("outfind", hl.all);
    reg.set("outfind-cur", hl.cur);
  }

  /** @param {HTMLElement} host @param {string} src a calc name or "file:<path>" */
  async function mountOutput(host, src) {
    const q = (/** @type {string} */ k) =>
      /** @type {any} */ (host.querySelector(`[data-r="${k}"]`));
    const box = q("out"), meta = q("ometa");
    if (!box) return;
    if (_out && _out.ro) _out.ro.disconnect();
    const st = /** @type {any} */ ({ src, box, chunks: new Map(), pending: new Set(),
      total: 0, lh: 19.8, pad: 0, scaled: false, sizeH: 0, raf: 0, drawn: "",
      ver: 0, query: "", hits: [], hitTotal: 0, cur: -1, ro: null, win: null });
    _out = st;
    setMarks(null);
    const info = await NB.call("get_output_info", src);
    if (_out !== st) return;
    if (!info || !info.ok || !info.lines) {
      box.textContent = info && info.ok ? "The output file is empty." : "No output on disk.";
      if (meta) meta.textContent = "";
      return;
    }
    st.total = info.lines;
    if (meta) meta.textContent = `${fmtSize(info.bytes)} · `
      + `${info.lines.toLocaleString()} lines`;

    box.innerHTML = `<div class="rawsize"><div class="rawwin"><div>0</div></div></div>`;
    const size = box.firstElementChild;
    st.win = size.firstElementChild;
    st.lh = st.win.firstElementChild.getBoundingClientRect().height || 19.8;
    st.pad = parseFloat(getComputedStyle(box).paddingTop) || 0;
    const full = st.total * st.lh;
    st.scaled = full > OUT_MAXPX;
    st.sizeH = Math.min(full, OUT_MAXPX);
    size.style.height = st.sizeH + "px";
    // the longest line in BYTES, so a wide character over-reserves a little;
    // capped, because one runaway line must not make the bar useless
    size.style.width = `max(100%, ${Math.min(info.max_len || 0, 2000) + 1}ch)`;

    box.addEventListener("scroll", () => outSchedule(st));
    st.ro = new ResizeObserver(() => outSchedule(st));
    st.ro.observe(box);
    bindFind(host, st);
    q("top").addEventListener("click", () => { box.scrollTop = 0; });
    q("tail").addEventListener("click", () => { box.scrollTop = box.scrollHeight; });
    outRender(st);
  }

  /** @param {any} st */
  function outSchedule(st) {
    if (st.raf) return;
    st.raf = requestAnimationFrame(() => { st.raf = 0; if (_out === st) outRender(st); });
  }

  /** How many lines fit on screen. @param {any} st */
  const outVisible = (st) => Math.ceil(st.box.clientHeight / st.lh) + 1;

  /** The first line on screen and where its box starts inside the spacer.
   *  @param {any} st @returns {[number, number]} */
  function outFirst(st) {
    const box = st.box, vis = outVisible(st);
    if (!st.scaled) {
      const first = Math.floor(Math.max(0, box.scrollTop - st.pad) / st.lh);
      return [first, first * st.lh];
    }
    const max = Math.max(1, box.scrollHeight - box.clientHeight);
    const first = Math.round(Math.min(1, box.scrollTop / max) * Math.max(0, st.total - vis));
    const top = Math.min(Math.max(0, box.scrollTop - st.pad), st.sizeH - vis * st.lh);
    return [first, Math.max(0, top)];
  }

  /** Line k's text, or null while its chunk is being fetched.
   *  @param {any} st @param {number} k @returns {string|null} */
  function outLine(st, k) {
    const ci = Math.floor(k / OUT_CHUNK);
    const c = st.chunks.get(ci);
    if (c) return c[k - ci * OUT_CHUNK] ?? "";
    if (!st.pending.has(ci)) {
      st.pending.add(ci);
      NB.call("get_output_lines", st.src, ci * OUT_CHUNK, OUT_CHUNK).then(r => {
        if (_out !== st) return;
        st.pending.delete(ci);
        st.chunks.set(ci, r && r.ok ? r.lines : []);
        while (st.chunks.size > OUT_CACHE) st.chunks.delete(st.chunks.keys().next().value);
        st.ver++;
        outSchedule(st);
      });
    }
    return null;
  }

  /** @param {any} st */
  function outRender(st) {
    const [first, top] = outFirst(st);
    const a = Math.max(0, first - OUT_OVERSCAN);
    const b = Math.min(st.total, first + outVisible(st) + OUT_OVERSCAN);
    const key = `${a}:${b}:${top}:${st.ver}:${st.query}:${st.cur}`;
    if (key === st.drawn) return;
    st.drawn = key;
    // the chunks on screen become the most recently used, so eviction takes others
    for (let ci = Math.floor(a / OUT_CHUNK); ci <= Math.floor((b - 1) / OUT_CHUNK); ci++) {
      const c = st.chunks.get(ci);
      if (c) { st.chunks.delete(ci); st.chunks.set(ci, c); }
    }
    const rows = [];
    for (let k = a; k < b; k++) {
      const t = outLine(st, k);
      rows.push(t === null ? `<div></div>` : `<div>${NB.esc(t)}</div>`);
    }
    st.win.style.top = (top - (first - a) * st.lh) + "px";
    st.win.innerHTML = rows.join("");
    outMark(st, a);
  }

  /** Mark every match among the lines drawn, the current one apart.
   *  @param {any} st @param {number} a the first line drawn */
  function outMark(st, a) {
    const H = /** @type {any} */ (window).Highlight;
    if (!H || !st.query) { setMarks(null); return; }
    const all = new H(), cur = new H();
    const ql = asciiLower(st.query), n = ql.length;
    const curLine = st.cur >= 0 ? st.hits[st.cur] : -1;
    const curOcc = curLine >= 0 ? st.cur - lowerBound(st.hits, curLine) : -1;
    const kids = st.win.children;
    for (let i = 0; i < kids.length; i++) {
      const node = kids[i].firstChild;
      if (!node) continue;
      const text = asciiLower(node.nodeValue || "");
      let at = text.indexOf(ql), occ = 0;
      while (at !== -1) {
        const r = new Range();
        r.setStart(node, at);
        r.setEnd(node, at + n);
        (a + i === curLine && occ === curOcc ? cur : all).add(r);
        at = text.indexOf(ql, at + n);
        occ++;
      }
    }
    setMarks({ all, cur });
  }

  /** The first index in a sorted array whose value is >= v.
   *  @param {number[]} arr @param {number} v */
  function lowerBound(arr, v) {
    let lo = 0, hi = arr.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < v) lo = m + 1; else hi = m; }
    return lo;
  }

  /** Bring line k on screen, centred, unless it is already well inside it.
   *  @param {any} st @param {number} k */
  function outReveal(st, k) {
    const box = st.box, vis = outVisible(st);
    const [first] = outFirst(st);
    if (k > first && k < first + vis - 2) { outRender(st); return; }
    if (!st.scaled) {
      box.scrollTop = st.pad + k * st.lh - (box.clientHeight - st.lh) / 2;
    } else {
      const want = Math.max(0, k - Math.floor(vis / 2));
      box.scrollTop = want / Math.max(1, st.total - vis) * (box.scrollHeight - box.clientHeight);
    }
    outRender(st);
  }

  /** @param {HTMLElement} host @param {any} st */
  function bindFind(host, st) {
    const q = (/** @type {string} */ k) =>
      /** @type {any} */ (host.querySelector(`[data-r="${k}"]`));
    const input = q("find"), count = q("fcount"), prev = q("fprev"), next = q("fnext");
    if (!input) return;
    input.disabled = false;
    let timer = 0, asked = "";

    const show = () => {
      const n = st.hits.length;
      prev.disabled = next.disabled = n === 0;
      if (!st.query) count.textContent = "";
      else if (!st.hitTotal) count.textContent = "No matches";
      else count.textContent = `${(st.cur + 1).toLocaleString()} of ${st.hitTotal.toLocaleString()}`
        + (st.hitTotal > n ? ` · first ${n.toLocaleString()} reachable` : "");
    };
    /** @param {number} i */
    const go = (i) => {
      const n = st.hits.length;
      if (!n) return;
      st.cur = ((i % n) + n) % n;
      show();
      outReveal(st, st.hits[st.cur]);
    };
    const run = async () => {
      const want = input.value;
      asked = want;
      if (!want) {
        st.query = ""; st.hits = []; st.hitTotal = 0; st.cur = -1;
        show();
        outRender(st);
        return;
      }
      count.textContent = "Searching…";
      const r = await NB.call("find_in_output", st.src, want);
      if (_out !== st || asked !== want) return;
      st.query = want;
      st.hits = r && r.ok ? r.hits : [];
      st.hitTotal = r && r.ok ? (r.total || 0) : 0;
      st.cur = -1;
      if (!st.hits.length) { show(); outRender(st); return; }
      // from where the reader is, the way a browser's find goes on
      const i = lowerBound(st.hits, outFirst(st)[0]);
      go(i < st.hits.length ? i : 0);
    };

    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(run, 250);
    });
    input.addEventListener("keydown", (/** @type {KeyboardEvent} */ e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      if (input.value !== st.query || asked !== input.value) {
        clearTimeout(timer);
        run();
      } else go(st.cur + (e.shiftKey ? -1 : 1));
    });
    prev.addEventListener("click", () => go(st.cur - 1));
    next.addEventListener("click", () => go(st.cur + 1));
  }

  // Ctrl+F finds in the output: the embedded browser has no find bar of its
  // own, so without this the shortcut every reader tries did nothing
  document.addEventListener("keydown", e => {
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "f") return;
    const f = /** @type {any} */ (document.getElementById("out-find"));
    if (!f || f.disabled || !f.offsetParent) return;
    e.preventDefault();
    f.focus();
    f.select();
  });

  // ---------------------------------------------------------------- profile

  let _fepUnit = "kcal";
  /** The thermodynamic quantities a reaction profile can be read on, each as
   *  what one species supplies in Hartree (or Hartree/K for S) -- null when the
   *  run printed none.
   *
   *  A profile is not only ΔG. Whether a step is enthalpy- or entropy-driven is
   *  the question ΔG alone cannot answer, and ORCA prints every term it is
   *  built from, so all of them are here and each is its own column.
   *
   *  They are never mixed: a stage whose species cannot supply the chosen
   *  quantity has no value on it. A monatomic ion is why E is on the list at
   *  all -- Cl(-) has no vibrations, so an sp of it has no G, and it is still a
   *  species in CoCl4(2-) + 6 H2O -> Co(H2O)6(2+) + 4 Cl(-).
   *
   *  `name` is written to sit mid-sentence ("has no enthalpy").
   *
   *  S is derived as (T·S)/T per species rather than sent, because ORCA prints
   *  the entropy TERM; a species without a temperature has no S.
   *  @type {{key: string, sym: string, abs: string, name: string,
   *          entropy?: boolean, get: (p: any) => any}[]} */
  const RXN_QTY = [
    { key: "g", sym: "ΔG", abs: "G", name: "Gibbs free energy", get: p => p.gibbs_eh },
    { key: "h", sym: "ΔH", abs: "H", name: "enthalpy", get: p => p.enthalpy_eh },
    { key: "ts", sym: "TΔS", abs: "T·S", name: "entropy term", get: p => p.entropy_term_eh },
    { key: "s", sym: "ΔS", abs: "S", name: "entropy", entropy: true,
      get: p => (num(p.entropy_term_eh) && num(p.temperature_k) && p.temperature_k > 0)
        ? p.entropy_term_eh / p.temperature_k : null },
    { key: "u", sym: "ΔU", abs: "U", name: "internal energy", get: p => p.total_thermal_eh },
    { key: "e0", sym: "ΔE₀", abs: "E+ZPE", name: "E + zero-point energy",
      get: p => (num(p.final_energy_eh) && num(p.zpe_eh))
        ? p.final_energy_eh + p.zpe_eh : null },
    { key: "e", sym: "ΔE", abs: "E", name: "electronic energy", get: p => p.final_energy_eh },
  ];
  /** @param {any} v @returns {boolean} */
  function num(v) { return typeof v === "number" && isFinite(v); }
  /** @param {string} key */
  function qtyOf(key) { return RXN_QTY.find(q => q.key === key) || RXN_QTY[0]; }

  /** Which quantity the diagram is drawn on. The table shows them all. */
  let _fepQty = "g";
  /** What the table's Δ is taken against: "ref" (the stage at zero) or "prev"
   *  (the stage before it -- a step's own ΔH and TΔS, which is what says
   *  whether that step is enthalpy- or entropy-driven). */
  let _fepCmp = "ref";
  /** Which stage sits at zero. An index, not a name: stages are positions on a
   *  reaction coordinate and two of them may hold the same species. */
  let _fepRef = 0;
  /** The profile being built. One entry per point on the reaction coordinate,
   *  each a label and the species standing at it with their stoichiometric
   *  coefficients.
   *
   *  This replaced a chart of every finished frequency calculation in queue
   *  order, which is not a reaction profile: it put unrelated molecules on one
   *  axis and printed differences between them. A step in a real profile is
   *  A + 2B -> C, and both the SET and the COEFFICIENTS are the user's to give
   *  -- nothing in a run folder says which calculation is a reactant, or that
   *  two of it are needed.
   *  @type {{label: string, terms: {name: string, coeff: number}[]}[]} */
  let _fepStages = null;
  /** get_free_energy_profile's answer, fetched once per open rather than on
   *  every keystroke. @type {any[]} */
  let _fepPts = [];

  function fepDefault() {
    return [{ label: "Reactants", terms: [{ name: "", coeff: 1 }] },
            { label: "Products", terms: [{ name: "", coeff: 1 }] }];
  }

  /** What a species supplies on quantity q, or null.
   *  @param {any} p @param {any} q @returns {number|null} */
  function speciesE(p, q) {
    if (!p) return null;
    const v = q.get(p);
    return num(v) ? v : null;
  }

  /** A stage's value of quantity q, or null when it is not fully described.
   *
   *  Null rather than a partial sum: a stage with an empty row -- or one whose
   *  species cannot supply q -- is a reaction nobody has finished writing down,
   *  and quietly summing what is there would print a number for it.
   *  @param {any} st @param {any} by @param {any} q */
  function stageG(st, by, q) {
    if (!st.terms.length) return null;
    let g = 0;
    for (const t of st.terms) {
      const e = speciesE(by[t.name], q);
      if (e == null || !isFinite(t.coeff)) return null;
      g += t.coeff * e;
    }
    return g;
  }

  /** The species this profile uses that cannot supply quantity q -- the reason
   *  a stage reads as a dash. @param {any} by @param {any} q @returns {string[]} */
  function fepMissing(by, q) {
    const out = [];
    (_fepStages || []).forEach(st => st.terms.forEach(t => {
      if (t.name && speciesE(by[t.name], q) == null && out.indexOf(t.name) < 0) out.push(t.name);
    }));
    return out;
  }

  /** The temperatures the profile's thermochemistry was computed at, distinct.
   *  More than one means every thermal Δ subtracts terms taken at different
   *  temperatures. @param {any} by @returns {number[]} */
  function fepTemps(by) {
    const ts = [];
    (_fepStages || []).forEach(st => st.terms.forEach(t => {
      const p = by[t.name];
      if (!p || !num(p.temperature_k)) return;
      if (!ts.some(x => Math.abs(x - p.temperature_k) < 0.01)) ts.push(p.temperature_k);
    }));
    return ts;
  }

  /** The matter standing at a stage: coefficient-weighted element counts and
   *  charge, so two stages can be asked whether they are the same stuff.
   *  @param {any} st @param {any} by */
  function stageMatter(st, by) {
    /** @type {Record<string, number>} */
    const el = {};
    let charge = 0, known = true;
    st.terms.forEach(t => {
      const p = by[t.name];
      if (!p) { known = false; return; }
      charge += t.coeff * (p.charge || 0);
      Object.keys(p.atoms || {}).forEach(k => {
        el[k] = (el[k] || 0) + t.coeff * p.atoms[k];
      });
    });
    return { el: el, charge: charge, known: known };
  }

  /** How two stages differ in matter, in words, or "" when they do not.
   *  @param {any} a @param {any} b */
  function matterDiff(a, b) {
    const bits = [];
    const keys = Object.keys(Object.assign({}, a.el, b.el)).sort();
    keys.forEach(k => {
      const d = (b.el[k] || 0) - (a.el[k] || 0);
      if (Math.abs(d) > 1e-9) bits.push(`${d > 0 ? "+" : ""}${+d.toFixed(3)} ${k}`);
    });
    if (Math.abs(b.charge - a.charge) > 1e-9) {
      const d = b.charge - a.charge;
      bits.push(`${d > 0 ? "+" : ""}${+d.toFixed(3)} charge`);
    }
    return bits.join(", ");
  }

  /** A reaction-coordinate diagram: a level per stage, joined by a thin line.
   *
   *  Not a line through the points. A stage is a STATE, not a sample of
   *  something continuous, and the flat bar is what says so -- the line chart
   *  this replaced drew a slope between two molecules and invited the reading
   *  that something happens along it.
   *  @param {{label: string, y: number|null}[]} lv
   *  @param {number} w @param {number} h @param {string} unit @param {string} what */
  function fepChart(lv, w, h, unit, what) {
    const L = 64, R = 16, T = 16, B = 46;
    const iw = Math.max(w - L - R, 40), ih = Math.max(h - T - B, 40);
    const known = lv.filter(s => s.y != null);
    if (known.length < 2) {
      return `<p class="hint" style="margin:0">Give at least two stages a complete
        set of species and the profile appears here.</p>`;
    }
    const ys = known.map(s => /** @type {number} */ (s.y));
    let y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    if (y1 === y0) { y0 -= 1; y1 += 1; }
    const pad = (y1 - y0) * 0.16; y0 -= pad; y1 += pad;
    const slot = iw / lv.length;
    const bw = Math.min(slot * 0.56, 130);
    const cx = (i) => L + slot * (i + 0.5);
    const py = (v) => T + ih - ((v - y0) / (y1 - y0)) * ih;

    let grid = "";
    for (let i = 0; i <= 4; i++) {
      const y = T + (ih * i) / 4;
      const v = y1 - ((y1 - y0) * i) / 4;
      grid += `<line x1="${L}" y1="${y}" x2="${L + iw}" y2="${y}"
                 stroke="var(--grid)" stroke-width="1"/>`
        + `<text x="${L - 8}" y="${y + 3.5}" text-anchor="end" font-size="10"
             fill="var(--muted-foreground)">${fmt(v)}</text>`;
    }
    let body = "";
    for (let i = 0; i < lv.length; i++) {
      const s = lv[i];
      if (s.y == null) continue;
      const y = py(s.y);
      body += `<line x1="${(cx(i) - bw / 2).toFixed(1)}" y1="${y.toFixed(1)}"
                 x2="${(cx(i) + bw / 2).toFixed(1)}" y2="${y.toFixed(1)}"
                 stroke="var(--crit-de)" stroke-width="3" stroke-linecap="round"/>`
        + `<text x="${cx(i).toFixed(1)}" y="${(y - 8).toFixed(1)}" text-anchor="middle"
             font-size="10" font-family="var(--font-mono)"
             fill="var(--foreground)">${fmt(s.y)}</text>`;
      // the connector reaches the next stage that HAS a level, so one
      // incomplete stage in the middle does not break the diagram in two
      let j = i + 1;
      while (j < lv.length && lv[j].y == null) j++;
      if (j < lv.length) {
        body += `<line x1="${(cx(i) + bw / 2).toFixed(1)}" y1="${y.toFixed(1)}"
                   x2="${(cx(j) - bw / 2).toFixed(1)}" y2="${py(/** @type {number} */ (lv[j].y)).toFixed(1)}"
                   stroke="var(--muted-foreground)" stroke-width="1"
                   stroke-dasharray="4 3" opacity=".7"/>`;
      }
    }
    const labs = lv.map((s, i) =>
      `<text x="${cx(i).toFixed(1)}" y="${T + ih + 18}" text-anchor="middle" font-size="10"
         fill="var(--muted-foreground)">${NB.esc(s.label || "stage " + (i + 1))}</text>`).join("");

    return `<div class="chart"><svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"
        role="img" aria-label="Relative ${NB.esc(what)} per stage, in ${NB.esc(unit)}">
      ${grid}
      <line x1="${L}" y1="${T}" x2="${L}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      <line x1="${L}" y1="${T + ih}" x2="${L + iw}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      ${body}${labs}
      <text x="${(L + iw / 2).toFixed(1)}" y="${h - 4}" text-anchor="middle" font-size="10"
        fill="var(--muted-foreground)">reaction coordinate</text>
    </svg></div>`;
  }

  /** @param {number} px */
  async function profile(px) {
    const r = await NB.call("get_free_energy_profile");
    _fepPts = (r && r.ok && r.points) ? r.points : [];
    renderFep(px);
  }

  /** @param {number} px */
  function renderFep(px) {
    const holder = NB.$("fep");
    if (!holder) return;
    const cw = px - 36;
    const pts = _fepPts;
    /** @type {any} */
    const by = {};
    pts.forEach(p => { by[p.name] = p; });

    if (!pts.length) {
      holder.innerHTML = `<div class="sech"><h2>Reaction profile</h2></div>
        <p class="secdesc">A profile is built from finished calculations: each stage of
          the reaction holds the species standing there.</p>
        ${plate('<p class="hint" style="margin:0">No finished calculation yet.</p>')}`;
      return;
    }
    if (!_fepStages) _fepStages = fepDefault();
    if (_fepRef >= _fepStages.length) _fepRef = 0;

    const kj = _fepUnit === "kj";
    const unit = kj ? "kJ/mol" : "kcal/mol";
    // S is per kelvin and reads in cal or J, not kcal or kJ -- a reaction
    // entropy of -0.03 kcal/(mol·K) is a number nobody quotes
    const sUnit = kj ? "J/(mol·K)" : "cal/(mol·K)";
    /** @param {any} q */
    const scale = (q) => (kj ? HA_KJ : HA_KCAL) * (q.entropy ? 1000 : 1);
    /** @param {any} q */
    const unitOf = (q) => q.entropy ? sUnit : unit;

    const Q = qtyOf(_fepQty);
    // every quantity for every stage, in Hartree (Hartree/K for S)
    /** @type {Record<string, (number|null)[]>} */
    const abs = {};
    RXN_QTY.forEach(q => { abs[q.key] = _fepStages.map(st => stageG(st, by, q)); });
    /** A stage's Δ on q against the reference, or against the stage before it,
     *  in display units. @param {any} q @param {number} i @param {string} cmp */
    const delta = (q, i, cmp) => {
      const j = cmp === "prev" ? i - 1 : _fepRef;
      if (j < 0) return null;
      const a = abs[q.key][i], b = abs[q.key][j];
      return (a == null || b == null) ? null : (a - b) * scale(q);
    };
    const levels = _fepStages.map((st, i) => ({ label: st.label, y: delta(Q, i, "ref") }));

    // Does each stage hold the same matter as the reference? Reported, not
    // enforced: comparing a fragment against a whole is a thing people do on
    // purpose, and the app's job is to say what the number is made of (P2).
    const refM = stageMatter(_fepStages[_fepRef], by);
    const warnings = [];
    _fepStages.forEach((st, i) => {
      if (i === _fepRef || !st.terms.length) return;
      const m = stageMatter(st, by);
      if (!m.known || !refM.known) return;
      const d = matterDiff(refM, m);
      if (d) {
        warnings.push(`<div class="finding warn"><span>⚠</span><span>${
          NB.esc(st.label || "stage " + (i + 1))} does not hold the same matter as ${
          NB.esc(_fepStages[_fepRef].label || "the reference")}: ${NB.esc(d)}. The
          difference between them is not a reaction energy.</span></div>`);
      }
    });

    // Thermochemistry at two temperatures: every thermal Δ would subtract terms
    // taken under different conditions. Said, not refused -- a deliberate
    // temperature scan compares exactly that.
    const temps = fepTemps(by);
    if (temps.length > 1) {
      warnings.push(`<div class="finding warn"><span>⚠</span><span>The species'
        thermochemistry was computed at different temperatures (${
        temps.map(t => t.toFixed(2) + " K").join(", ")}), so ΔG, ΔH, TΔS and ΔS
        mix conditions. ΔE is unaffected.</span></div>`);
    }

    // A species that cannot supply the drawn quantity is why a stage reads as a
    // dash. Say which one, and what the ways out are -- for a single atom a
    // frequency run is still the right answer, since ORCA gives it
    // translational and electronic thermochemistry with nothing to vibrate.
    const missing = fepMissing(by, Q);
    if (missing.length) {
      const it = missing.length === 1 ? "it" : "them";
      warnings.unshift(`<div class="finding warn"><span>⚠</span><span>${
        missing.map(n => NB.esc(n)).join(", ")} ${missing.length === 1 ? "has" : "have"}
        no ${NB.esc(Q.name)} (${NB.esc(Q.abs)}), so every stage holding
        ${it} is left blank rather than summed on a different quantity. ${
        Q.key === "e"
          ? "Re-run it, or pick another calculation."
          : `Run a frequency calculation for ${it} — a single atom gets its
             translational and electronic thermochemistry even with nothing to
             vibrate — or draw the profile on the electronic energy.`}</span></div>`);
    }

    // every finished species is listed, and the ones that cannot supply the
    // drawn quantity say so in the row rather than being missing from it: a
    // picker that silently omits Cl(-) looks broken
    const speciesOptions = (sel) => `<option value="">pick a calculation…</option>`
      + pts.map(p => `<option value="${NB.esc(p.name)}"${
          p.name === sel ? " selected" : ""}>${NB.esc(p.name)}${
          speciesE(p, Q) == null ? " — no " + Q.abs : ""}</option>`).join("");

    const stageHtml = _fepStages.map((st, i) => `
      <div class="field-row" style="margin-bottom:8px">
        <div class="field mid"><label>Stage ${i + 1}</label>
          <input data-fep="label" data-i="${i}" value="${NB.esc(st.label)}"
                 placeholder="Reactants"></div>
        <div class="field narrow"><label>${Q.sym} (${NB.esc(unitOf(Q))})</label>
          <input class="mono" value="${levels[i].y == null ? "—" : fmt(levels[i].y)}"
                 readonly tabindex="-1"></div>
        <div class="field"><label>&nbsp;</label>
          <div class="snips" style="margin:0">
            <button class="btn btn-xs btn-ghost" type="button" data-fep="addterm" data-i="${i}"
              >+ species</button>
            ${_fepStages.length > 2
              ? `<button class="btn btn-xs btn-danger" type="button" data-fep="delstage"
                   data-i="${i}">remove stage</button>` : ""}
          </div></div>
      </div>
      ${st.terms.map((t, j) => `
        <div class="basis-row" style="margin-bottom:8px">
          <div class="field narrow"><label${j ? ' class="vh"' : ""}>Coefficient</label>
            <input class="mono" type="number" step="any" data-fep="coeff"
                   data-i="${i}" data-j="${j}" value="${t.coeff}"></div>
          <div class="field"><label${j ? ' class="vh"' : ""}>Calculation</label>
            <select data-fep="term" data-i="${i}" data-j="${j}"
              >${speciesOptions(t.name)}</select></div>
          <button class="rm" type="button" data-fep="delterm" data-i="${i}" data-j="${j}"
            aria-label="Remove this species">×</button>
        </div>`).join("")}
      ${i < _fepStages.length - 1 ? '<div class="divider"></div>' : ""}`).join("");

    // The table: every quantity, so ΔG can be read as ΔH − TΔS beside it. A
    // column is kept only when some stage has a value on it -- a profile built
    // entirely from single points has nothing to say about entropy.
    // in the order they build on each other, E up to G, whatever the diagram shows
    const cols = ["e", "e0", "u", "h", "ts", "s", "g"].map(qtyOf)
      .filter(q => abs[q.key].some(v => v != null));
    const rows = _fepStages.map((st, i) => [
      NB.esc(st.label || "stage " + (i + 1)),
      st.terms.filter(t => t.name)
        .map(t => `${t.coeff === 1 ? "" : t.coeff + " × "}${NB.esc(t.name)}`)
        .join(" + ") || "—",
    ].concat(cols.map(q => {
      const v = delta(q, i, _fepCmp);
      if (_fepCmp === "prev" && i === 0) return "";
      if (v == null) return "—";
      return q.key === Q.key ? `<b>${fmt(v)}</b>` : fmt(v);
    })));
    const T = temps.length === 1 ? temps[0] : null;

    holder.innerHTML = `<div class="sech"><h2>Reaction profile</h2><span class="sp"></span>
        <span class="meta">${pts.length} species available${
          T != null ? ` · ${T.toFixed(2)} K` : ""}</span></div>
      <p class="secdesc">Build the reaction: each stage is a point on the coordinate and
        holds the species standing there, with the stoichiometric coefficient each one
        is taken with. A stage's value is the weighted sum, and the profile is every
        stage against the reference. The diagram is drawn on one quantity and the table
        gives them all — ΔE, ΔE₀, ΔU, ΔH, TΔS, ΔS and ΔG — so a step can be read as
        enthalpy- or entropy-driven. Quantities are never mixed: a species with no
        thermochemistry says so in the picker. This section spans the whole workspace,
        not the selected result.</p>`
      + plate(`<div class="field-row" style="margin-bottom:14px">
            <div class="field mid"><label>Diagram</label><select data-fep="qty">${
              RXN_QTY.map(q => `<option value="${q.key}"${q.key === Q.key ? " selected" : ""}>${
                NB.esc(q.sym)} — ${NB.esc(q.name.charAt(0).toUpperCase() + q.name.slice(1))}</option>`).join("")
            }</select></div>
            <div class="field mid"><label>Units</label><select data-fep="unit">
              <option value="kcal"${!kj ? " selected" : ""}>kcal/mol</option>
              <option value="kj"${kj ? " selected" : ""}>kJ/mol</option>
            </select></div>
            <div class="field mid"><label>Reference (= 0)</label><select data-fep="ref">${
              _fepStages.map((st, i) => `<option value="${i}"${
                i === _fepRef ? " selected" : ""}>${
                NB.esc(st.label || "stage " + (i + 1))}</option>`).join("")
            }</select></div>
          </div>
          ${stageHtml}
          <div class="btn-group" style="margin-top:14px">
            <button class="btn btn-sm" type="button" data-fep="addstage">+ stage</button>
            <button class="btn btn-sm btn-ghost" type="button" data-fep="reset">Start over</button>
          </div>`)
      + warnings.join("")
      + plate(fepChart(levels, cw, Math.round(cw * 0.34), unitOf(Q), Q.name))
      + `<div class="field-row" style="margin:14px 0 8px">
          <div class="field mid"><label>Table Δ against</label><select data-fep="cmp">
            <option value="ref"${_fepCmp === "ref" ? " selected" : ""}>the reference stage</option>
            <option value="prev"${_fepCmp === "prev" ? " selected" : ""}>the stage before (per step)</option>
          </select></div></div>`
      + (cols.length
        ? tw(["Stage", "Species"].concat(cols.map(q => `${q.sym} (${unitOf(q)})`)),
             rows, [false, false].concat(cols.map(() => true)))
        : plate('<p class="hint" style="margin:0">No stage is complete yet.</p>'));

    // one handler for the whole section: the stage list is rebuilt on every
    // edit, and a listener per control would be re-attached on each rebuild
    const edit = (fn) => { fn(); renderFep(px); };
    holder.querySelectorAll("[data-fep]").forEach(elm => {
      const b = /** @type {any} */ (elm);
      const i = Number(b.dataset.i), j = Number(b.dataset.j);
      const what = b.dataset.fep;
      if (what === "addterm" || what === "delterm" || what === "delstage"
          || what === "addstage" || what === "reset") {
        b.addEventListener("click", () => edit(() => {
          if (what === "addterm") _fepStages[i].terms.push({ name: "", coeff: 1 });
          else if (what === "delterm") _fepStages[i].terms.splice(j, 1);
          else if (what === "delstage") {
            _fepStages.splice(i, 1);
            if (_fepRef >= _fepStages.length) _fepRef = 0;
          } else if (what === "addstage") {
            _fepStages.push({ label: "Stage " + (_fepStages.length + 1),
                              terms: [{ name: "", coeff: 1 }] });
          } else { _fepStages = fepDefault(); _fepRef = 0; }
        }));
        return;
      }
      // change, not input: the section re-renders on every edit, and a keystroke
      // handler would take the caret out of the field being typed in
      b.addEventListener("change", () => edit(() => {
        if (what === "qty") _fepQty = b.value;
        else if (what === "cmp") _fepCmp = b.value;
        else if (what === "unit") _fepUnit = b.value;
        else if (what === "ref") _fepRef = Number(b.value);
        else if (what === "label") _fepStages[i].label = b.value;
        else if (what === "term") _fepStages[i].terms[j].name = b.value;
        else if (what === "coeff") {
          const v = parseFloat(b.value);
          _fepStages[i].terms[j].coeff = isFinite(v) ? v : 1;
        }
      }));
    });
  }

  // ---------------------------------------------------------------- shell

  function toolbar() {
    const title = _curName || (_curPath ? nameFromPath(_curPath) : "Results");
    const calc = _curName ? NB.queue.find(c => c.name === _curName) : null;
    // No result picker here: the RAIL is the picker, in either view, and a
    // result that is not in it is opened with the two buttons on the right --
    // which puts it in the rail's Opened files group and keeps it there. A
    // select listing the workspace as well was a second picker for the same
    // job, and the one that could not say where a result came from.
    //
    // Output / Visual does not switch a mode: Visual IS one of the sections, so
    // the pair jumps to it, the way the outline chips below do. Show all is
    // Output's and goes away while Visual is the one being read.
    return `<div>
      <div class="restool">
        <span class="restitle">${NB.esc(title)}</span>
        ${calc ? `<span class="badge ${calc.state}">${calc.state}</span>` : ""}
        <div class="seg" role="group" aria-label="Result view">
          <button type="button" aria-pressed="${!_visual}" data-resmode="output">Output</button>
          <button type="button" aria-pressed="${_visual}" data-resmode="visual">Visual</button>
        </div>
        <span class="sp"></span>
        <button class="btn btn-sm btn-ghost" type="button" data-r="showall"
          aria-pressed="${_showAll}"${_visual ? " hidden" : ""}
          title="Every parsed value, including the SCF decomposition">Show all</button>
        <button class="btn btn-sm btn-ghost" type="button" data-r="openfile">Open file…</button>
        <button class="btn btn-sm btn-ghost" type="button" data-r="openfolder">Open folder…</button>
        <button class="btn btn-sm btn-ghost" type="button" data-r="infolder">Show in folder</button>
      </div>
      ${(!_curName && _curPath) ? `<div class="resprov">
        <span>Opened file — not a calculation in this workspace.</span>
        <span class="p">${NB.esc(_curPath)}</span></div>` : ""}
    </div>`;
  }

  function paint() {
    const host = NB.$("reswin");
    if (!host) return;
    // The Visual panel may be holding the app's only WebGL box inside a stage
    // this render is about to replace wholesale. Send it home first: losing it
    // with the innerHTML would lose the renderer, and every later surface with
    // it. (mvwin.js volumeView, "the BOX moves".)
    if (NB.mv && NB.mv.vol) NB.mv.vol.park();
    // .rescontent has 24px side padding; a wide section is what is left of it
    const px = Math.max(host.clientWidth - 48, 360);
    _present = [];
    const body = _cur
      ? sections(_cur, px) + `<section class="secblock wide" id="fep"></section>`
      : `<section class="secblock wide">${plate(
          `<p class="hint" style="margin:0">No result open.</p>`)}</section>
         <section class="secblock wide" id="fep"></section>`;
    _present.push({ id: "fep", title: "Reaction profile" });

    host.innerHTML = toolbar()
      + `<div class="resmain"><div class="rescontent">
           <nav class="secchips" aria-label="Result sections">${
             _present.map(s => `<button type="button" data-jump="${s.id}">${
               NB.esc(s.title)}</button>`).join("")}</nav>
           <div class="resinner">${body}</div>
         </div></div>`;

    const q = (s) => /** @type {any} */ (host.querySelector(s));
    host.querySelectorAll("[data-resmode]").forEach(b => b.addEventListener("click", () => {
      _visual = /** @type {HTMLElement} */ (b).dataset.resmode === "visual";
      host.querySelectorAll("[data-resmode]").forEach(o =>
        o.setAttribute("aria-pressed", String(o === b)));
      const sa = q('[data-r="showall"]');
      if (sa) sa.hidden = _visual;
      jump(_visual ? "sec-visual" : "sec-summary");
    }));
    q('[data-r="openfile"]').addEventListener("click", openFileDialog);
    q('[data-r="openfolder"]').addEventListener("click", openFolderDialog);
    q('[data-r="showall"]').addEventListener("click", () => { _showAll = !_showAll; paint(); });
    q('[data-r="infolder"]').addEventListener("click", showInFolder);
    host.querySelectorAll("[data-jump]").forEach(b => b.addEventListener("click",
      () => jump(/** @type {HTMLElement} */ (b).dataset.jump)));
    spy(host);
    bindCharts(host);
    host.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click",
      () => showMode(Number(/** @type {HTMLElement} */ (b).dataset.mode))));

    if (_cur) {
      fillRaw(_curName || (_curPath ? "file:" + _curPath : ""), _cur);
      // how the BACKEND addresses this result. A queued calculation is parsed
      // by kind, a file on disk by its folder — the prefix keeps the two routes
      // apart, and an MLIP or CREST run read the wrong way is a bogus result.
      const source = _curName ? "calc:" + _curName : "file:" + _curPath;
      NB.viewer.mountStructure(_cur, source);
      NB.viewer.mountVisual(source, _cur);
      NB.viewer.mountNbo(source);
    }
    profile(px);
    if (NB.rail) NB.rail.render();
  }

  /** Give every stick chart that asked for it a value readout.
   *
   *  The hit target is the WHOLE plot and the nearest band wins: a band is
   *  1.6px wide and there can be 150 of them overlapping, so a per-band hit
   *  area would be unhittable (the guideline's 24px minimum is unreachable for
   *  a spectrum — the answer is to widen the target, not the mark). The same
   *  cursor moves on ←/→, so the numbers are reachable without a pointer:
   *  removing the table must not make them hover-only.
   *  @param {HTMLElement} host */
  function bindCharts(host) {
    host.querySelectorAll(".chart svg[data-hover]").forEach((n) => {
      const svg = /** @type {any} */ (n);
      const sticks = /** @type {any[]} */ ([...svg.querySelectorAll(".stk")]);
      const box = svg.parentElement, tip = box && box.querySelector(".chtip");
      const cur = svg.querySelector(".chi");
      if (!sticks.length || !tip || !cur) return;
      const xs = sticks.map(s => parseFloat(s.getAttribute("data-px")));
      let at = -1;

      /** @param {number} i */
      const show = (i) => {
        if (i < 0 || i >= sticks.length) return;
        at = i;
        cur.setAttribute("x1", String(xs[i]));
        cur.setAttribute("x2", String(xs[i]));
        cur.style.opacity = "1";
        sticks.forEach((s, k) => s.classList.toggle("on", k === i));
        tip.textContent = sticks[i].getAttribute("data-lab");
        tip.hidden = false;
        // Placed from the band's own client rect, not from user units: the svg
        // scales to the pane (max-width:100%), so the two are not the same px.
        const r = sticks[i].getBoundingClientRect();
        const b = box.getBoundingClientRect();
        const w = tip.offsetWidth;
        tip.style.left = Math.max(0, Math.min(b.width - w,
                                              r.left - b.left + r.width / 2 - w / 2)) + "px";
        tip.style.top = Math.max(0, r.top - b.top - tip.offsetHeight - 6) + "px";
      };
      const hide = () => {
        at = -1;
        tip.hidden = true;
        cur.style.opacity = "0";
        sticks.forEach(s => s.classList.remove("on"));
      };
      /** @param {number} clientX */
      const nearest = (clientX) => {
        const b = svg.getBoundingClientRect();
        const ux = (clientX - b.left) * (svg.viewBox.baseVal.width / b.width);
        let best = 0;
        for (let i = 1; i < xs.length; i++) {
          if (Math.abs(xs[i] - ux) < Math.abs(xs[best] - ux)) best = i;
        }
        return best;
      };

      // a band that does something when picked (chart's `pick`): the
      // vibrational spectrum's shows that mode moving on the Structure stage
      const pick = svg.dataset.pick === "mode"
        ? (/** @type {number} */ i) => showMode(i + 1) : null;
      if (pick) svg.addEventListener("click", (/** @type {any} */ e) => pick(nearest(e.clientX)));

      svg.addEventListener("pointermove", (/** @type {any} */ e) => show(nearest(e.clientX)));
      svg.addEventListener("pointerleave", hide);
      svg.addEventListener("blur", hide);
      svg.addEventListener("keydown", (/** @type {any} */ e) => {
        let i = at;
        if (e.key === "ArrowRight") i = at < 0 ? 0 : Math.min(sticks.length - 1, at + 1);
        else if (e.key === "ArrowLeft") i = at < 0 ? sticks.length - 1 : Math.max(0, at - 1);
        else if (e.key === "Home") i = 0;
        else if (e.key === "End") i = sticks.length - 1;
        else if (e.key === "Escape") { hide(); return; }
        else if ((e.key === "Enter" || e.key === " ") && pick && at >= 0) {
          e.preventDefault(); pick(at); return;
        }
        else return;
        e.preventDefault();
        show(i);
      });
    });
  }

  /** Show report mode `n` moving on the Structure stage, and mark it here.
   *  @param {number} n */
  function showMode(n) {
    NB.viewer.playMode(n, markMode);
  }
  /** The band and the button of the mode on the Structure stage, marked so the
   *  spectrum says which one is moving; 0 clears them. @param {number} n */
  function markMode(n) {
    const host = NB.$("reswin");
    if (!host) return;
    host.querySelectorAll('.chart svg[data-pick="mode"]').forEach(svg =>
      svg.querySelectorAll(".stk").forEach((st, k) => st.classList.toggle("sel", k === n - 1)));
    host.querySelectorAll("[data-mode]").forEach(b => b.setAttribute("aria-pressed",
      String(Number(/** @type {HTMLElement} */ (b).dataset.mode) === n)));
  }

  /** Mark the chip for the section being read. An outline that does not say
   *  where you are is a list of links; the marking is the point of it.
   *  @param {HTMLElement} host */
  function spy(host) {
    const content = host.querySelector(".rescontent");
    const chips = [...host.querySelectorAll("[data-jump]")];
    if (!content || !chips.length) return;
    const mark = () => {
      // the section whose top is nearest below the sticky outline
      const line = content.getBoundingClientRect().top + 80;
      let cur = chips[0];
      chips.forEach(b => {
        const t = document.getElementById(/** @type {HTMLElement} */ (b).dataset.jump);
        if (t && t.getBoundingClientRect().top <= line) cur = b;
      });
      chips.forEach(b => {
        if (b === cur) b.setAttribute("aria-current", "true");
        else b.removeAttribute("aria-current");
      });
    };
    content.addEventListener("scroll", mark, { passive: true });
    mark();
  }

  // ---------------------------------------------------------------- opening

  /** Put a parsed result on screen. The one way in, so what "showing a result"
   *  means is defined once. @param {any} d @param {string} name @param {string} path */
  function show(d, name, path) {
    _cur = d; _curName = name || ""; _curPath = path || "";
    paint();
  }

  /** Open a queued calculation's result. Parsed BY NAME so the backend
   *  dispatches on its kind — a folder heuristic would read an MLIP or CREST
   *  run as an ORCA output. @param {string} name */
  async function open(name) {
    NB.go("results");
    const d = await NB.call("parse_calc_output", name);
    if (!d || !d.summary) { NB.fail((d && d.error) || `No readable output for "${name}" yet.`); return; }
    show(d, name, d.path || "");
  }
  /** @param {string} path @returns {Promise<any>} the payload, or null */
  async function openPath(path) {
    NB.go("results");
    const d = await NB.call("parse_out_path", path);
    if (!d || !d.summary) {
      NB.fail((d && d.error) || "Could not read that output file.");
      return null;
    }
    show(d, "", d.path || path);
    return d;
  }

  /** What a result opened from disk IS, in one line, for the rail's row. The
   *  parse payload carries no kind and no charge -- it is what the parser
   *  found, not what someone asked for -- so this says what is in the file
   *  rather than repeating the design's "Opt / 12 atoms / charge 0 / mult 1"
   *  with two invented numbers. @param {any} d @returns {string} */
  function describeResult(d) {
    const kind = d.is_conformer_search ? "Conformer search"
      : d.transitions && d.transitions.length ? "TD-DFT"
      : d.nmr && d.nmr.length ? "NMR"
      : d.neb_path && d.neb_path.length ? (d.neb_path_kind === "irc" ? "IRC" : "NEB")
      : d.frequencies && d.frequencies.length ? (d.is_optimization ? "Opt + Freq" : "Frequencies")
      : d.scan_points && d.scan_points.length ? "Relaxed scan"
      : d.is_optimization ? "Optimization"
      : "Single point";
    const n = (d.geometry || []).length;
    return n ? `${kind} · ${n} atom${n === 1 ? "" : "s"}` : kind;
  }

  /** The backend of the result on screen: a queued calculation's by its kind,
   *  a file's by what is in it. @returns {string} "DFT" | "MLIP" | "CREST" */
  function resultBackend() {
    const c = _curName ? NB.queue.filter(x => x.name === _curName)[0] : null;
    return c ? NB.rail.backendOf(c) : backendOfResult(_curPath, _cur);
  }

  /** The backend a file came out of, read from the file itself: an engine-
   *  written *.mlip.json is the MLIP worker's, a conformer list is CREST's.
   *  @param {string} path @param {any} d @returns {string} */
  function backendOfResult(path, d) {
    if (/[.]mlip[.]json$/i.test(path)) return "MLIP";
    if (d && d.is_conformer_search) return "CREST";
    return "DFT";
  }

  /** Hand a result opened from outside the workspace to the rail, which keeps
   *  the list. @param {string} path @param {any} d */
  function noteOpened(path, d) {
    // a Windows path is what the picker hands back; the basename split needs
    // one separator, and the row shows the path as the user knows it
    const norm = String(path).replace(/\\/g, "/");
    const cut = norm.lastIndexOf("/");
    NB.rail.noteOpened({
      path: path,
      name: cut >= 0 ? norm.slice(cut + 1) : norm,
      folder: cut >= 0 ? path.slice(0, cut) : "",
      backend: backendOfResult(path, d),
      meta: describeResult(d),
    });
  }

  async function openFileDialog() {
    const r = await NB.call("pick_result_file");
    if (!r || r.cancelled || !r.ok) return;
    if (r.route === "structure") {
      NB.toast("Structure files open in the 3D viewer, which is not in this preview yet.");
      return;
    }
    const d = await openPath(r.path);
    if (d) noteOpened(r.path, d);
  }
  async function openFolderDialog() {
    const r = await NB.call("pick_result_folder");
    if (!r || r.cancelled || !r.ok) return;
    const d = await openPath(r.path);
    if (d) noteOpened(r.path, d);
  }
  async function showInFolder() {
    if (!_curPath) { NB.toast("Open a result first."); return; }
    await NB.call("show_path_in_folder", _curPath);
  }

  /** @param {string} name */
  async function showInp(name) {
    let text = "";
    const disk = await NB.call("get_inp", name);
    if (disk && disk.ok && disk.text) text = disk.text;
    if (!text) {
      const g = await NB.call("get_calc", name);
      if (g && g.ok && g.calc) {
        if (g.calc.is_raw && g.calc.raw_text) text = g.calc.raw_text;
        else {
          const pv = await NB.call("build_inp_preview", JSON.stringify(g.calc));
          if (pv && pv.ok) text = pv.text;
        }
      }
    }
    if (text) NB.modalCode(name + ".inp", text);
    else NB.modalRaw(name + ".inp",
      "<p>No input file yet, and nothing to generate one from.</p>");
  }

  /** Scroll a section to the top of the report. @param {string} id */
  function jump(id) {
    const t = document.getElementById(id);
    if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** On entry there is nothing to fetch: the rail is the picker, and it is
   *  already drawn from the queue the app polls. The workspace scan that used
   *  to run here filled a second picker in the toolbar -- a disk walk on every
   *  entry to the tab, for a list the rail already holds, minus the provenance
   *  it could not show. A result from outside comes in through Open file... /
   *  Open folder..., which is also how it gets a row. */
  function enter() {
    paint();
  }

  NB.results = { enter: enter, open: open, openPath: openPath, show: show,
                 showInp: showInp, paint: paint, openFileDialog: openFileDialog,
                 currentName: () => _curName, currentPath: () => _curPath };
})();
