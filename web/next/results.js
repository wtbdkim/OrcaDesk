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
   *  @param {{xlab: string, ylab: string, stick?: boolean, hover?: boolean}} o */
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
      o.hover ? ' data-hover="1" tabindex="0"' : ""}
      aria-label="${NB.esc(o.ylab)} against ${NB.esc(o.xlab)}${
        o.hover ? ". Hover a band for its value, or use the arrow keys" : ""}">
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

  // ---------------------------------------------------------------- summary

  /** What kind of run this was, in a sentence. The design gives every section a
   *  line saying what it is; the summary's says what was run. @param {any} d */
  function whatItWas(d) {
    const bits = [];
    if (d.is_optimization) bits.push("a geometry optimization");
    if (d.frequencies && d.frequencies.length) bits.push("a frequency calculation");
    if (d.transitions && d.transitions.length) bits.push("a TD-DFT excited-state calculation");
    if (d.nmr && d.nmr.length) bits.push("an NMR shielding calculation");
    if (d.neb_path && d.neb_path.length)
      bits.push(d.neb_path_kind === "irc" ? "an IRC path" : "a NEB-TS search");
    if (d.is_conformer_search) bits.push("a conformer search");
    if (!bits.length) bits.push("a single-point calculation");
    const what = bits.length === 1 ? bits[0]
      : bits.slice(0, -1).join(", ") + " followed by " + bits[bits.length - 1];
    return "This is " + what + ". Everything below is read out of its output file.";
  }

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
        : p[0] === "Run time" ? "took" : p[0].toLowerCase();
      return `<span>${NB.esc(short)} <b${t ? ` class="${t}"` : ""}>${NB.esc(p[1])}</b></span>`;
    }).join("");

    // the reference's own summary: not in a card — a .sech carrying the name,
    // the line saying what was run, then the facts
    _present.push({ id: "sec-summary", title: "Summary" });
    let out = `<section class="secblock wide" data-sec="sec-summary" id="sec-summary">
      <div class="sech">
        <div class="summaryhead">
          <span class="nm">${NB.esc(_curName || nameFromPath(_curPath) || "Result")}</span>
          ${calc ? `<span class="badge ${calc.state}">${calc.state}</span>` : ""}
        </div>
      </div>
      <p class="secdesc">${NB.esc(whatItWas(d))}</p>
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

  /** @param {any} d @param {number} px width of a full-width section
   *  @param {string} name */
  function sections(d, px, name) {
    const out = [summary(d)];
    const cw = px - 36;                       // inside a .plate (18px each side)
    const half = Math.round(px / 2) - 36;     // ...of a half-width section

    // the input beside the output, first: every other section is read out of it
    out.push(rawSections(name));

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
        + " Point at a band for its wavenumber; the arrow keys step through them.",
        plate(chart(pts, cw, Math.round(cw * 0.26),
                    { xlab: "wavenumber (cm⁻¹)", ylab: "modes",
                      stick: true, hover: true })),
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

    _present.push({ id: "sec-nbo", title: "Natural orbitals" });
    out.push(NB.viewer.nboSection());

    // Per-atom and per-bond numbers belong to the Structure viewer — click an
    // atom or a bond and they are right there. As TABLES they are for reading
    // without a mouse, so they live under Show all with everything else.
    if (_showAll) out.push(tables(d));
    return out.join("");
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

  /** @param {string} name */
  function rawSections(name) {
    _present.push({ id: "sec-input", title: "Input" });
    _present.push({ id: "sec-output", title: "Output" });
    return `<section class="secblock third" data-sec="sec-input" id="sec-input">
        <div class="sech"><h2>Input</h2><span class="sp"></span>
          <span class="meta">${NB.esc(name)}.inp</span></div>
        <p class="secdesc">The file handed to ORCA, exactly as it ran.</p>
        <div class="plate" style="padding:0;overflow:hidden">
          <div class="rawin" data-r="in"></div></div>
      </section>
      <section class="secblock twothirds" data-sec="sec-output" id="sec-output">
        <div class="sech"><h2>Output</h2><span class="sp"></span>
          <span class="meta" data-r="ometa"></span></div>
        <p class="secdesc">Everything ORCA wrote. Every other section here is read out of it.</p>
        <div class="plate" style="padding:0;overflow:hidden">
          <div class="outbar"><span class="sp"></span>
            <button class="btn btn-sm btn-ghost" type="button" data-r="tail">↓ End</button>
          </div>
          <div class="rawout" data-r="out"></div>
        </div>
      </section>`;
  }

  /** @param {string} name @param {any} d the payload, for its input echo */
  async function fillRaw(name, d) {
    const host = NB.$("reswin");
    const inEl = host.querySelector('[data-r="in"]');
    const outEl = host.querySelector('[data-r="out"]');
    const meta = host.querySelector('[data-r="ometa"]');
    if (!inEl || !outEl) return;
    if (!name) { inEl.textContent = "—"; outEl.textContent = "—"; return; }

    // the .inp ORCA actually read, off disk — not a preview regenerated from
    // the form, which can differ from what ran
    let inp = "";
    const disk = await NB.call("get_inp", name);
    if (disk && disk.ok && disk.text) inp = disk.text;
    if (!inp) {
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

    const r = await NB.call("get_output_tail", name, 6000);
    if (r && r.ok && r.lines && r.lines.length) {
      outEl.innerHTML = r.lines.map(l => `<div>${NB.esc(l)}</div>`).join("");
      if (meta) {
        meta.textContent = (r.file || name + ".out")
          + (r.truncated ? " · last " + r.lines.length + " lines" : " · complete");
      }
      outEl.scrollTop = outEl.scrollHeight;
    } else {
      outEl.textContent = "No output on disk.";
      if (meta) meta.textContent = "";
    }
  }

  // ---------------------------------------------------------------- profile

  let _fepUnit = "kcal";
  /** Which energy the stages are summed on: "g" (Gibbs free energy) or "e"
   *  (electronic energy).
   *
   *  It is a choice and not a fallback because the two cannot be mixed: adding
   *  a G to an E is neither. A monatomic ion is why the choice exists at all --
   *  Cl(-) has no vibrations to compute, so an sp of it has no G, and it is
   *  still a species in CoCl4(2-) + 6 H2O -> Co(H2O)6(2+) + 4 Cl(-). On G the
   *  profile says which species is missing one; on E every finished run can
   *  stand in it, at the price of leaving out the thermal terms. */
  let _fepBasis = "g";
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

  /** The energy a species supplies on the current basis, or null.
   *  @param {any} p @returns {number|null} */
  function speciesE(p) {
    if (!p) return null;
    const v = _fepBasis === "g" ? p.gibbs_eh : p.final_energy_eh;
    return (typeof v === "number" && isFinite(v)) ? v : null;
  }

  /** A stage's energy in Hartree, or null when it is not fully described.
   *
   *  Null rather than a partial sum: a stage with an empty row -- or one whose
   *  species has no energy on the chosen basis -- is a reaction nobody has
   *  finished writing down, and quietly summing what is there would print a
   *  number for it. @param {any} st @param {any} by */
  function stageG(st, by) {
    if (!st.terms.length) return null;
    let g = 0;
    for (const t of st.terms) {
      const e = speciesE(by[t.name]);
      if (e == null || !isFinite(t.coeff)) return null;
      g += t.coeff * e;
    }
    return g;
  }

  /** The species this profile uses that have no energy on the chosen basis --
   *  the reason a stage reads as a dash. @param {any} by @returns {string[]} */
  function fepMissing(by) {
    const out = [];
    (_fepStages || []).forEach(st => st.terms.forEach(t => {
      if (t.name && speciesE(by[t.name]) == null && out.indexOf(t.name) < 0) out.push(t.name);
    }));
    return out;
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
   *  @param {number} w @param {number} h @param {string} unit */
  function fepChart(lv, w, h, unit) {
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
        role="img" aria-label="Relative Gibbs free energy per stage, in ${NB.esc(unit)}">
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
      holder.innerHTML = `<div class="sech"><h2>Free energy profile</h2></div>
        <p class="secdesc">A profile is built from Gibbs free energies, and a Gibbs
          energy only comes from a run with frequencies.</p>
        ${plate('<p class="hint" style="margin:0">No finished frequency calculation yet.</p>')}`;
      return;
    }
    if (!_fepStages) _fepStages = fepDefault();
    if (_fepRef >= _fepStages.length) _fepRef = 0;

    const k = _fepUnit === "kj" ? HA_KJ : HA_KCAL;
    const unit = _fepUnit === "kj" ? "kJ/mol" : "kcal/mol";
    // the symbol says which energy this is: a profile on electronic energies is
    // not a free-energy profile, and calling it one would be the lie
    const sym = _fepBasis === "g" ? "ΔG" : "ΔE";
    const abs = _fepBasis === "g" ? "G" : "E";
    const gs = _fepStages.map(st => stageG(st, by));
    const refG = gs[_fepRef];
    const levels = _fepStages.map((st, i) => ({
      label: st.label,
      y: (gs[i] == null || refG == null) ? null : (gs[i] - refG) * k,
    }));

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

    // every finished species is listed, and the ones that cannot supply the
    // chosen energy say so in the row rather than being missing from it: a
    // picker that silently omits Cl(-) looks broken, which is exactly how the
    // frequencies-only filter read
    // A species with no energy on this basis is why a stage reads as a dash. Say
    // which one, and what the two ways out are -- for a single atom a frequency
    // run is still the right answer, since ORCA gives it translational and
    // electronic thermochemistry even though it has no vibrations to compute.
    const missing = fepMissing(by);
    if (missing.length) {
      warnings.unshift(`<div class="finding warn"><span>⚠</span><span>${
        missing.map(n => NB.esc(n)).join(", ")} ${missing.length === 1 ? "has" : "have"}
        no ${_fepBasis === "g" ? "Gibbs free energy" : "electronic energy"}, so every
        stage holding ${missing.length === 1 ? "it" : "them"} is left blank rather than
        summed on a different quantity. ${_fepBasis === "g"
          ? `Run a frequency calculation for ${missing.length === 1 ? "it" : "them"} —
             a single atom gets its translational and electronic thermochemistry even
             with nothing to vibrate — or switch the energy above to electronic.`
          : "Re-run it, or pick another calculation."}</span></div>`);
    }

    const speciesOptions = (sel) => `<option value="">pick a calculation…</option>`
      + pts.map(p => `<option value="${NB.esc(p.name)}"${
          p.name === sel ? " selected" : ""}>${NB.esc(p.name)}${
          speciesE(p) == null ? " — no " + abs : ""}</option>`).join("");

    const stageHtml = _fepStages.map((st, i) => `
      <div class="field-row" style="margin-bottom:8px">
        <div class="field mid"><label>Stage ${i + 1}</label>
          <input data-fep="label" data-i="${i}" value="${NB.esc(st.label)}"
                 placeholder="Reactants"></div>
        <div class="field narrow"><label>${sym} (${NB.esc(unit)})</label>
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
          <div class="field narrow"><label>Coefficient</label>
            <input class="mono" type="number" step="any" data-fep="coeff"
                   data-i="${i}" data-j="${j}" value="${t.coeff}"></div>
          <div class="field"><label>Calculation</label>
            <select data-fep="term" data-i="${i}" data-j="${j}"
              >${speciesOptions(t.name)}</select></div>
          <button class="rm" type="button" data-fep="delterm" data-i="${i}" data-j="${j}"
            aria-label="Remove this species">×</button>
        </div>`).join("")}
      ${i < _fepStages.length - 1 ? '<div class="divider"></div>' : ""}`).join("");

    holder.innerHTML = `<div class="sech"><h2>Free energy profile</h2><span class="sp"></span>
        <span class="meta">${pts.length} ${
          pts.length === 1 ? "species available" : "species available"}</span></div>
      <p class="secdesc">Build the reaction: each stage is a point on the coordinate and
        holds the species standing there, with the stoichiometric coefficient each one
        is taken with. Its energy is the weighted sum, and the profile is every stage
        against the reference. Every finished calculation can stand in it; which
        energy they are summed on is a choice, because a Gibbs energy and an
        electronic one cannot be added together — a species with no thermochemistry
        says so in the picker. This section spans the whole workspace, not the
        selected result.</p>`
      + plate(`<div class="field-row" style="margin-bottom:14px">
            <div class="field mid"><label>Energy</label><select data-fep="basis">
              <option value="g"${_fepBasis === "g" ? " selected" : ""}>Gibbs free energy (G)</option>
              <option value="e"${_fepBasis === "e" ? " selected" : ""}>Electronic energy (E)</option>
            </select></div>
            <div class="field mid"><label>Units</label><select data-fep="unit">
              <option value="kcal"${_fepUnit === "kcal" ? " selected" : ""}>kcal/mol</option>
              <option value="kj"${_fepUnit === "kj" ? " selected" : ""}>kJ/mol</option>
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
      + plate(fepChart(levels, cw, Math.round(cw * 0.34), unit))
      + tw(["Stage", "Species", `${sym} (${unit})`, `${abs} (Eh)`],
           _fepStages.map((st, i) => [
             NB.esc(st.label || "stage " + (i + 1)),
             st.terms.filter(t => t.name)
               .map(t => `${t.coeff === 1 ? "" : t.coeff + " × "}${NB.esc(t.name)}`)
               .join(" + ") || "—",
             levels[i].y == null ? "—" : fmt(levels[i].y),
             gs[i] == null ? "—" : gs[i].toFixed(6),
           ]), [false, false, true, true]);

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
        if (what === "basis") _fepBasis = b.value;
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
    const name = _curName || nameFromPath(_curPath);

    _present = [];
    const body = _cur
      ? sections(_cur, px, name) + `<section class="secblock wide" id="fep"></section>`
      : `<section class="secblock wide">${plate(
          `<p class="hint" style="margin:0">No result open. Pick a finished calculation
           in the queue, or open any ORCA output from disk — a result stays readable
           after it leaves the queue, because the run folder is still there.</p>`)}</section>
         <section class="secblock wide" id="fep"></section>`;
    _present.push({ id: "fep", title: "Free energy profile" });

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
    const tail = q('[data-r="tail"]');
    if (tail) tail.addEventListener("click", () => {
      const o = q('[data-r="out"]');
      if (o) o.scrollTop = o.scrollHeight;
    });
    host.querySelectorAll("[data-jump]").forEach(b => b.addEventListener("click",
      () => jump(/** @type {HTMLElement} */ (b).dataset.jump)));
    spy(host);
    bindCharts(host);

    if (_cur) {
      fillRaw(name, _cur);
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
        else return;
        e.preventDefault();
        show(i);
      });
    });
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
      : d.is_optimization ? "Optimization"
      : "Single point";
    const n = (d.geometry || []).length;
    return n ? `${kind} · ${n} atom${n === 1 ? "" : "s"}` : kind;
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
