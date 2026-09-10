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
  let _ws = [];
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
  /** @param {string} v @param {string} label */
  function tone(v, label) {
    if (/ABNORMAL|NOT converged/i.test(v)) return "err";
    if (/imaginary/i.test(label) && !/^0$/.test(v)) return "warn";
    if (/converged|Normal/i.test(v)) return "ok";
    return "";
  }

  // ---------------------------------------------------------------- drawing

  /** A line or stick chart in SVG at a real pixel size.
   *  @param {{x: number, y: number, cls?: string}[]} pts
   *  @param {number} w @param {number} h
   *  @param {{xlab: string, ylab: string, stick?: boolean}} o */
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
        `<line x1="${px(p.x).toFixed(1)}" y1="${py(0).toFixed(1)}"
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

    return `<div class="chart"><svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img"
      aria-label="${NB.esc(o.ylab)} against ${NB.esc(o.xlab)}">
      ${grid}
      <line x1="${L}" y1="${T}" x2="${L}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      <line x1="${L}" y1="${T + ih}" x2="${L + iw}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      ${body}${ticks}
      <text x="${L + iw / 2}" y="${h - 6}" text-anchor="middle" font-size="10.5"
        fill="var(--muted-foreground)">${NB.esc(o.xlab)}</text>
      <text x="17" y="${T + ih / 2}" text-anchor="middle" font-size="10.5"
        fill="var(--muted-foreground)" transform="rotate(-90 17 ${T + ih / 2})">${NB.esc(o.ylab)}</text>
    </svg></div>`;
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
      const pts = d.frequencies.map(f => ({
        x: f, y: 1, cls: f < 0 ? "var(--err)" : "var(--crit-de)" }));
      out.push(sec("sec-freq", "Vibrational modes",
        d.frequencies.length + " modes · " + imag + " imaginary",
        imag ? "One imaginary mode is what makes a structure a transition state; more "
             + "than one means it is not a stationary point of either kind."
             : "No imaginary modes — this geometry is a minimum on the surface.",
        plate(chart(pts, cw, Math.round(cw * 0.26),
                    { xlab: "wavenumber (cm⁻¹)", ylab: "modes", stick: true }))
        + tw(["Mode", "cm⁻¹"], d.frequencies.map((f, i) => [
            String(i + 1),
            `<span${f < 0 ? ' class="err"' : ""}>${f.toFixed(2)}</span>`])),
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
            [`${n.idx} ${NB.esc(n.el)}`, n.iso.toFixed(3), n.aniso.toFixed(3)]))));
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
          [`${i + 1} ${NB.esc(m[0])}`, m[1].toFixed(6)]))));
    }
    if (d.loewdin && d.loewdin.length) {
      out.push(sec("sec-loew", "Löwdin charges", d.loewdin.length + " atoms",
        "The same partition after symmetric orthogonalization; usually the steadier of "
        + "the two across basis sets.",
        tw(["Atom", "Charge (e)"], d.loewdin.map((m, i) =>
          [`${i + 1} ${NB.esc(m[0])}`, m[1].toFixed(6)]))));
    }
    if (d.mayer_bonds && d.mayer_bonds.length) {
      out.push(sec("sec-mbo", "Mayer bond orders", d.mayer_bonds.length + " bonds",
        "Also on any bond you click in <b>Structure</b>. Near 1, 2 and 3 for single, "
        + "double and triple bonds.",
        tw(["Bond", "Order"], d.mayer_bonds.map(b =>
          [`${NB.esc(b[0])} – ${NB.esc(b[1])}`, b[2].toFixed(4)]))));
    }
    if (d.mayer_valences && d.mayer_valences.length) {
      out.push(sec("sec-mval", "Mayer valences", d.mayer_valences.length + " atoms",
        "Total bonding capacity used per atom; a value well below the usual one points "
        + "at a bond the geometry has stretched.",
        tw(["Atom", "Valence"], d.mayer_valences.map(v =>
          [`${v[0]} ${NB.esc(v[1])}`, v[2].toFixed(4)]))));
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
    // which is the only source for a result whose .inp is gone
    if (!inp && d && (d.input_keywords || d.input_block)) {
      inp = [d.input_keywords || "", d.input_block || ""].filter(Boolean).join("\n");
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
  let _fepRef = "";

  /** @param {number} px */
  async function profile(px) {
    const holder = NB.$("fep");
    if (!holder) return;
    const cw = px - 36;
    const r = await NB.call("get_free_energy_profile");
    const pts = (r && r.ok && r.points) ? r.points : [];

    if (pts.length < 2) {
      holder.innerHTML = `<div class="sech"><h2>Free energy profile</h2></div>
        <p class="secdesc">Two or more finished frequency calculations build the profile —
          a Gibbs energy only comes from a run with frequencies.</p>
        ${plate('<p class="hint" style="margin:0">Nothing to compare yet.</p>')}`;
      return;
    }
    if (!_fepRef || !pts.some(p => p.name === _fepRef)) _fepRef = pts[0].name;
    const ref = pts.filter(p => p.name === _fepRef)[0];
    const k = _fepUnit === "kj" ? HA_KJ : HA_KCAL;
    const unit = _fepUnit === "kj" ? "kJ/mol" : "kcal/mol";
    const rows = pts.map((p, i) => ({ x: i, y: (p.gibbs_eh - ref.gibbs_eh) * k, name: p.name }));

    holder.innerHTML = `<div class="sech"><h2>Free energy profile</h2><span class="sp"></span>
        <span class="meta">${pts.length} frequency calculations</span></div>
      <p class="secdesc">Relative Gibbs free energy across the finished frequency
        calculations, in queue order. This one spans the whole workspace, not the
        selected result.</p>`
      + plate(`<div class="field-row" style="margin-bottom:14px">
            <div class="field mid"><label>Units</label><select data-f="unit">
              <option value="kcal"${_fepUnit === "kcal" ? " selected" : ""}>kcal/mol</option>
              <option value="kj"${_fepUnit === "kj" ? " selected" : ""}>kJ/mol</option>
            </select></div>
            <div class="field mid"><label>Reference (= 0)</label><select data-f="ref">${
              pts.map(p => `<option value="${NB.esc(p.name)}"${
                p.name === _fepRef ? " selected" : ""}>${NB.esc(p.name)}</option>`).join("")
            }</select></div>
          </div>`
        + chart(rows, cw, Math.round(cw * 0.28), { xlab: "queue order", ylab: `ΔG (${unit})` }))
      + tw(["Calculation", `ΔG (${unit})`, "G (Eh)"], rows.map((p, i) =>
          [NB.esc(p.name), p.y.toFixed(2), pts[i].gibbs_eh.toFixed(6)]));

    holder.querySelector('[data-f="unit"]').addEventListener("change", e => {
      _fepUnit = e.target.value; profile(px);
    });
    holder.querySelector('[data-f="ref"]').addEventListener("change", e => {
      _fepRef = e.target.value; profile(px);
    });
  }

  // ---------------------------------------------------------------- shell

  function toolbar() {
    const opts = [];
    NB.queue.forEach(c => {
      if (c.state === "done" || c.state === "failed") opts.push({ v: "calc:" + c.name, l: c.name });
    });
    _ws.forEach(w => {
      if (opts.some(o => o.l === w.name)) return;
      opts.push({ v: "file:" + w.path, l: w.name + "  (on disk)" });
    });
    const cur = _curName ? "calc:" + _curName : (_curPath ? "file:" + _curPath : "");
    const title = _curName || (_curPath ? nameFromPath(_curPath) : "Results");
    const calc = _curName ? NB.queue.find(c => c.name === _curName) : null;
    return `<div>
      <div class="restool">
        <span class="restitle">${NB.esc(title)}</span>
        ${calc ? `<span class="badge ${calc.state}">${calc.state}</span>` : ""}
        <label class="lb" for="res-pick">Result</label>
        <select id="res-pick" data-r="pick">
          <option value="">Choose a result…</option>
          ${opts.map(o => `<option value="${NB.esc(o.v)}"${
            o.v === cur ? " selected" : ""}>${NB.esc(o.l)}</option>`).join("")}
        </select>
        <span class="sp"></span>
        <button class="btn btn-sm btn-ghost" type="button" data-r="showall"
          aria-pressed="${_showAll}"
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
    // .rescontent has 24px side padding; a wide section is what is left of it
    const px = Math.max(host.clientWidth - 48, 360);
    const name = _curName || nameFromPath(_curPath);

    _present = [];
    const body = _cur
      ? sections(_cur, px, name) + `<section class="secblock wide" id="fep"></section>`
      : `<section class="secblock wide">${plate(
          `<p class="hint" style="margin:0">No result open. Pick a finished calculation
           above, or open any ORCA output from disk — a result stays readable after it
           leaves the queue, because the run folder is still there.</p>`)}</section>
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
    q('[data-r="pick"]').addEventListener("change", e => {
      const v = e.target.value;
      if (!v) return;
      if (v.startsWith("calc:")) open(v.slice(5));
      else openPath(v.slice(5));
    });
    q('[data-r="openfile"]').addEventListener("click", openFileDialog);
    q('[data-r="openfolder"]').addEventListener("click", openFolderDialog);
    q('[data-r="showall"]').addEventListener("click", () => { _showAll = !_showAll; paint(); });
    q('[data-r="infolder"]').addEventListener("click", showInFolder);
    const tail = q('[data-r="tail"]');
    if (tail) tail.addEventListener("click", () => {
      const o = q('[data-r="out"]');
      if (o) o.scrollTop = o.scrollHeight;
    });
    host.querySelectorAll("[data-jump]").forEach(b => b.addEventListener("click", () => {
      const t = document.getElementById(/** @type {HTMLElement} */ (b).dataset.jump);
      if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
    spy(host);

    if (_cur) {
      fillRaw(name, _cur);
      // how the BACKEND addresses this result. A queued calculation is parsed
      // by kind, a file on disk by its folder — the prefix keeps the two routes
      // apart, and an MLIP or CREST run read the wrong way is a bogus result.
      const source = _curName ? "calc:" + _curName : "file:" + _curPath;
      NB.viewer.mountStructure(_cur, source);
      NB.viewer.mountVisual(source);
      NB.viewer.mountNbo(source);
    }
    profile(px);
    if (NB.rail) NB.rail.render();
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
  /** @param {string} path */
  async function openPath(path) {
    NB.go("results");
    const d = await NB.call("parse_out_path", path);
    if (!d || !d.summary) { NB.fail((d && d.error) || "Could not read that output file."); return; }
    show(d, "", d.path || path);
  }
  async function openFileDialog() {
    const r = await NB.call("pick_result_file");
    if (!r || r.cancelled || !r.ok) return;
    if (r.route === "structure") {
      NB.toast("Structure files open in the 3D viewer, which is not in this preview yet.");
      return;
    }
    openPath(r.path);
  }
  async function openFolderDialog() {
    const r = await NB.call("pick_result_folder");
    if (!r || r.cancelled || !r.ok) return;
    openPath(r.path);
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

  /** On entry: rescan the workspace, because results outlive the queue and a
   *  folder written since the last look should be offerable. */
  async function enter() {
    const r = await NB.call("list_workspace_results");
    _ws = (r && r.ok && r.results) ? r.results : [];
    paint();
  }

  NB.results = { enter: enter, open: open, openPath: openPath, show: show,
                 showInp: showInp, paint: paint,
                 currentName: () => _curName };
})();
