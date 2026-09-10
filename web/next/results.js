// @ts-check
"use strict";
/* ============================================================
   ORCAdesk notebook front-end — the report.

   A finished calculation read as a document, not as a tab: the summary first,
   then a grid of what the parser found, then the input this run read beside
   the output it produced, and the free-energy profile across the queue last.

   The parsing is all the backend's (parse_calc_output / parse_out_path); this
   file decides only what a section looks like. Charts are drawn at the box's
   real pixel width — a fixed viewBox scaled to fit would scale its strokes and
   type with it.
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
  let _rawOpen = true;

  const HA_KCAL = 627.5094740631;
  const HA_KJ = 2625.499639;

  // ---------------------------------------------------------------- drawing

  /** A bar/stick chart in SVG at a real pixel size.
   *  @param {{x: number, y: number, label?: string, cls?: string}[]} pts
   *  @param {number} w @param {number} h
   *  @param {{xlab: string, ylab: string, stick?: boolean, invertX?: boolean}} o */
  function chart(pts, w, h, o) {
    if (!pts.length) return `<div class="hint">Nothing to plot.</div>`;
    const L = 64, R = 16, T = 12, B = 40;   // gutters: the outermost tick and the rotated axis label both have to fit INSIDE the viewBox
    const iw = Math.max(w - L - R, 40), ih = Math.max(h - T - B, 40);
    let xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    let x0 = Math.min(...xs), x1 = Math.max(...xs);
    let y0 = Math.min(0, Math.min(...ys)), y1 = Math.max(...ys);
    if (x1 === x0) { x0 -= 1; x1 += 1; }
    if (y1 === y0) { y1 = y0 + 1; }
    const pad = (y1 - y0) * 0.08; y1 += pad;
    const px = (v) => {
      const t = (v - x0) / (x1 - x0);
      return L + (o.invertX ? 1 - t : t) * iw;
    };
    const py = (v) => T + ih - ((v - y0) / (y1 - y0)) * ih;

    let g = "";
    for (let i = 0; i <= 4; i++) {
      const y = T + (ih * i) / 4;
      const v = y1 - ((y1 - y0) * i) / 4;
      g += `<line x1="${L}" y1="${y}" x2="${L + iw}" y2="${y}" stroke="var(--border)" stroke-width="1"/>`;
      g += `<text x="${L - 8}" y="${y + 3.5}" text-anchor="end" font-size="10"
              fill="var(--muted-foreground)" font-family="var(--font-mono)">${fmt(v)}</text>`;
    }
    let body = "";
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
    // The outermost ticks anchor to their own edge, not to their centre: a
    // centred label at the last tick hangs half its width past the plot — which
    // is exactly what an exponential like 3.3e+3 does to the right gutter.
    const xanchor = ["start", "middle", "end"];
    const xticks = [x0, (x0 + x1) / 2, x1].map((v, i) =>
      `<text x="${px(v).toFixed(1)}" y="${T + ih + 16}" text-anchor="${xanchor[i]}" font-size="10"
         fill="var(--muted-foreground)" font-family="var(--font-mono)">${fmt(v)}</text>`).join("");

    return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img"
              aria-label="${NB.esc(o.ylab)} against ${NB.esc(o.xlab)}">
      ${g}
      <line x1="${L}" y1="${T}" x2="${L}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      <line x1="${L}" y1="${T + ih}" x2="${L + iw}" y2="${T + ih}" stroke="var(--border)" stroke-width="1"/>
      ${body}
      ${xticks}
      <text x="${L + iw / 2}" y="${h - 6}" text-anchor="middle" font-size="10.5"
        fill="var(--muted-foreground)">${NB.esc(o.xlab)}</text>
      <text x="17" y="${T + ih / 2}" text-anchor="middle" font-size="10.5"
        fill="var(--muted-foreground)" transform="rotate(-90 17 ${T + ih / 2})">${NB.esc(o.ylab)}</text>
    </svg>`;
  }
  /** @param {number} v */
  function fmt(v) {
    const a = Math.abs(v);
    if (a === 0) return "0";
    if (a >= 1000 || a < 0.01) return v.toExponential(1);
    return String(Math.round(v * 100) / 100);
  }

  /** Section names, in the order the report reads. The outline above the
   *  report is built from the ones a result actually has. @type {string[]} */
  let _present = [];

  /** @param {string} title @param {string} html @param {string} [span]
   *  "" (half) | "wide" | "third" | "twothirds" */
  function sec(title, html, span) {
    _present.push(title);
    const id = "sec-" + title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    return `<section class="secblock ${span || ""}" data-sec="${id}" id="${id}">
      <div class="sech"><h2>${NB.esc(title)}</h2></div>${html}</section>`;
  }
  /** @param {string[]} head @param {string[][]} rows @param {boolean} [scroll] */
  function table(head, rows, scroll) {
    const t = `<table><thead><tr>${head.map(h => `<th>${NB.esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    return scroll === false ? t : `<div class="scroll">${t}</div>`;
  }

  // ---------------------------------------------------------------- sections

  /** @param {any} d @param {number} px width available to a full-width section */
  function sections(d, px) {
    const out = [];
    const geom = _showAll || d.is_optimization;
    const elec = _showAll || d.show_elec;

    _present = [];
    if (d.summary && d.summary.length) {
      const rows = d.summary.filter(r => _showAll || (r[2] || "") !== "elec" || elec);
      out.push(sec("Summary", `<div class="kv">${rows.map(r => {
        const v = String(r[1]);
        const cls = /ABNORMAL|NOT converged/i.test(v) ? "err"
                  : (/imaginary/i.test(r[0]) && !/^0$/.test(v)) ? "warn"
                  : /converged|Normal/i.test(v) ? "ok" : "";
        return `<div class="k">${NB.esc(r[0])}</div><div class="v ${cls}">${NB.esc(v)}</div>`;
      }).join("")}</div>`, true));
    }

    if (d.frequencies && d.frequencies.length) {
      const pts = d.frequencies.map(f => ({
        x: f, y: 1, cls: f < 0 ? "var(--err)" : "var(--crit-de)",
      }));
      const imag = d.n_imaginary || d.frequencies.filter(f => f < 0).length;
      out.push(sec("Vibrational frequencies",
        `<div class="hint" style="margin-bottom:9px">${d.frequencies.length} modes${
          imag ? ` · <span style="color:var(--err)">${imag} imaginary</span>` : " · no imaginary modes"}</div>`
        + chart(pts, px, Math.round(px * 0.30), { xlab: "wavenumber (cm⁻¹)", ylab: "modes", stick: true })
        + table(["#", "cm⁻¹"], d.frequencies.map((f, i) =>
            [String(i + 1), `<span style="color:${f < 0 ? "var(--err)" : "inherit"}">${f.toFixed(2)}</span>`])),
        "wide"));
    }

    if (d.transitions && d.transitions.length) {
      const pts = d.transitions.map(t => ({ x: t.nm, y: t.fosc }));
      out.push(sec("UV/Vis absorption",
        chart(pts, px, Math.round(px * 0.30), { xlab: "wavelength (nm)", ylab: "f (osc.)", stick: true })
        + table(["State", "eV", "nm", "f"], d.transitions.map(t =>
            [String(t.state), t.ev.toFixed(3), t.nm.toFixed(1), t.fosc.toFixed(4)])),
        "wide"));
    }

    if (d.tddft_states && d.tddft_states.length) {
      out.push(sec("Excited states", table(["State", "eV", "Main contributions"],
        d.tddft_states.map(s => [
          String(s.state), s.ev.toFixed(3),
          (s.contributions || []).slice(0, 4)
            .map(c => `${NB.esc(c[0])}→${NB.esc(c[1])} ${(c[2] * 100).toFixed(0)}%`).join(", "),
        ])), "wide"));
    }

    if (d.neb_path && d.neb_path.length) {
      const pts = d.neb_path.map((p, i) => ({
        x: i, y: p.de_kcal, cls: p.is_ts ? "var(--warn)" : "var(--crit-de)",
      }));
      out.push(sec(d.neb_path_kind === "irc" ? "IRC profile" : "NEB path",
        chart(pts, px, Math.round(px * 0.30), { xlab: "image", ylab: "ΔE (kcal/mol)" })
        + table(["Image", "ΔE (kcal/mol)", ""], d.neb_path.map(p =>
            [NB.esc(p.label), p.de_kcal.toFixed(2), p.is_ts ? '<span class="badge running">TS</span>' : ""])),
        "wide"));
    }

    if (d.is_conformer_search && d.conformers && d.conformers.length) {
      out.push(sec("Conformers", table(["Rank", "ΔE (kcal/mol)", "Energy (Eh)", "Atoms"],
        d.conformers.map(c => [String(c.index), c.rel_kcal.toFixed(3),
                               c.energy_eh.toFixed(6), String(c.n_atoms)])), "wide"));
    }

    if (elec && d.orbitals && d.orbitals.length) {
      const near = frontierWindow(d.orbitals);
      out.push(sec("Orbital energies", table(["#", "Occ", "eV", ""],
        near.map(o => [String(o.idx), o.occ.toFixed(3), o.ev.toFixed(3),
          o.frontier ? `<span class="badge tag">${o.frontier.toUpperCase()}</span>` : ""]))));
    }
    if (elec && d.mulliken && d.mulliken.length) {
      out.push(sec("Mulliken charges", table(["Atom", "Charge (e)"],
        d.mulliken.map((m, i) => [`${i + 1} ${NB.esc(m[0])}`, m[1].toFixed(6)]))));
    }
    if (elec && d.loewdin && d.loewdin.length) {
      out.push(sec("Löwdin charges", table(["Atom", "Charge (e)"],
        d.loewdin.map((m, i) => [`${i + 1} ${NB.esc(m[0])}`, m[1].toFixed(6)]))));
    }
    if (elec && d.mayer_bonds && d.mayer_bonds.length) {
      out.push(sec("Mayer bond orders", table(["Bond", "Order"],
        d.mayer_bonds.map(b => [`${NB.esc(b[0])} – ${NB.esc(b[1])}`, b[2].toFixed(4)]))));
    }
    if (elec && d.mayer_valences && d.mayer_valences.length) {
      out.push(sec("Mayer valences", table(["Atom", "Valence"],
        d.mayer_valences.map(v => [`${v[0]} ${NB.esc(v[1])}`, v[2].toFixed(4)]))));
    }
    if (d.nmr && d.nmr.length) {
      out.push(sec("NMR shielding", table(["Atom", "Isotropic", "Anisotropy"],
        d.nmr.map(n => [`${n.idx} ${NB.esc(n.el)}`, n.iso.toFixed(3), n.aniso.toFixed(3)]))));
    }
    if (geom && d.geometry && d.geometry.length) {
      out.push(sec("Final geometry", table(["Atom", "x (Å)", "y (Å)", "z (Å)"],
        d.geometry.map((a, i) => [`${i + 1} ${NB.esc(a.el)}`,
          a.x.toFixed(6), a.y.toFixed(6), a.z.toFixed(6)]))));
    }
    return out.join("");
  }

  /** Orbitals near the frontier. A full manifold is thousands of rows nobody
   *  reads; the ones that matter are around HOMO/LUMO, and "Show all" opens the
   *  rest. @param {any[]} orbs */
  function frontierWindow(orbs) {
    if (_showAll) return orbs;
    const h = orbs.findIndex(o => o.frontier === "homo");
    if (h < 0) return orbs.slice(0, 40);
    return orbs.slice(Math.max(0, h - 12), h + 13);
  }

  // ---------------------------------------------------------------- raw pair

  /** The name the backend resolves a run folder by. A result opened from disk
   *  is not in the queue, but ORCAdesk's run folder is {workspace}/{name}/ and
   *  the artifact inside it is {name}.out — so the folder name IS the name the
   *  .inp/.out slots take, and a file opened by path stays as readable as a
   *  queued one. @param {string} path */
  function nameFromPath(path) {
    const parts = String(path || "").split(/[\/]/).filter(Boolean);
    const file = parts.length ? parts[parts.length - 1] : "";
    return file.replace(/\.(out|mlip\.json|log)$/i, "");
  }

  /** @param {string} name @param {any} d the payload, for its input echo */
  async function rawPair(name, d) {
    const host = NB.$("reswin");
    const inEl = host.querySelector('[data-r="in"]');
    const outEl = host.querySelector('[data-r="out"]');
    const oMeta = host.querySelector('[data-r="ometa"]');
    const iMeta = host.querySelector('[data-r="imeta"]');
    if (!inEl || !outEl) return;
    if (!name) { inEl.textContent = "—"; outEl.textContent = "—"; return; }
    if (iMeta) iMeta.textContent = name + ".inp";

    // the .inp ORCA actually read, off disk — not a preview regenerated from the
    // form, which can differ from what ran
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
    // Last resort: the parser echoes the input it read out of the .out itself,
    // which is the only source for a result whose .inp is gone.
    if (!inp && d && (d.input_keywords || d.input_block)) {
      inp = [d.input_keywords || "", d.input_block || ""].filter(Boolean).join("\n");
    }
    inEl.textContent = inp || "No input file for this result.";

    const r = await NB.call("get_output_tail", name, 6000);
    if (r && r.ok && r.lines && r.lines.length) {
      outEl.textContent = r.lines.join("\n");
      oMeta.textContent = (r.file || name + ".out")
        + (r.truncated ? " · last " + r.lines.length + " lines" : " · complete");
    } else {
      outEl.textContent = "No output on disk.";
      oMeta.textContent = "";
    }
  }

  // ---------------------------------------------------------------- profile

  let _fepUnit = "kcal";
  let _fepRef = "";

  async function profile(px) {
    const host = NB.$("fep");
    if (!host) return;
    const r = await NB.call("get_free_energy_profile");
    const pts = (r && r.ok && r.points) ? r.points : [];
    if (pts.length < 2) {
      host.innerHTML = sec("Free energy profile",
        `<div class="hint">Two or more finished frequency calculations build the profile —
          Gibbs energies only come from a run with frequencies.</div>`, true);
      return;
    }
    if (!_fepRef || !pts.some(p => p.name === _fepRef)) _fepRef = pts[0].name;
    const ref = pts.find(p => p.name === _fepRef);
    const k = _fepUnit === "kj" ? HA_KJ : HA_KCAL;
    const rows = pts.map((p, i) => ({ x: i, y: (p.gibbs_eh - ref.gibbs_eh) * k, name: p.name }));
    const unit = _fepUnit === "kj" ? "kJ/mol" : "kcal/mol";

    host.innerHTML = sec("Free energy profile",
      `<div class="frow" style="margin-bottom:10px">
         <div class="field mid"><label>Units</label><select data-f="unit">
           <option value="kcal"${_fepUnit === "kcal" ? " selected" : ""}>kcal/mol</option>
           <option value="kj"${_fepUnit === "kj" ? " selected" : ""}>kJ/mol</option>
         </select></div>
         <div class="field mid"><label>Reference (= 0)</label><select data-f="ref">${
           pts.map(p => `<option value="${NB.esc(p.name)}"${p.name === _fepRef ? " selected" : ""}>${NB.esc(p.name)}</option>`).join("")
         }</select></div>
       </div>`
      + chart(rows, px, Math.round(px * 0.32), { xlab: "queue order", ylab: `ΔG (${unit})` })
      + table(["Calculation", `ΔG (${unit})`, "G (Eh)"],
          rows.map((p, i) => [NB.esc(p.name), p.y.toFixed(2), pts[i].gibbs_eh.toFixed(6)]))
      , true);

    host.querySelector('[data-f="unit"]').addEventListener("change", e => {
      _fepUnit = e.target.value; profile(px);
    });
    host.querySelector('[data-f="ref"]').addEventListener("change", e => {
      _fepRef = e.target.value; profile(px);
    });
  }

  // ---------------------------------------------------------------- shell

  function head() {
    const opts = [];
    NB.queue.forEach(c => {
      if (c.state === "done" || c.state === "failed") opts.push({ v: "calc:" + c.name, l: c.name });
    });
    _ws.forEach(w => {
      if (opts.some(o => o.l === w.name)) return;
      opts.push({ v: "file:" + w.path, l: w.name + "  (on disk)" });
    });
    const cur = _curName ? "calc:" + _curName : (_curPath ? "file:" + _curPath : "");
    const title = _curName || (_curPath ? _curPath.split(/[\\/]/).pop() : "Results");
    const calc = _curName ? NB.queue.find(c => c.name === _curName) : null;
    return `<div>
      <div class="restool">
        <span class="restitle">${NB.esc(title)}</span>
        ${calc ? `<span class="badge ${calc.state}">${calc.state}</span>` : ""}
        <label class="lb" for="res-pick">Result</label>
        <select id="res-pick" data-r="pick">
          <option value="">Choose a result…</option>
          ${opts.map(o => `<option value="${NB.esc(o.v)}"${o.v === cur ? " selected" : ""}>${NB.esc(o.l)}</option>`).join("")}
        </select>
        <span class="sp"></span>
        <button class="btn btn-sm btn-ghost" type="button" data-r="showall"
          aria-pressed="${_showAll}" title="Every parsed value, ignoring per-kind filtering">Show all</button>
        <button class="btn btn-sm btn-ghost" type="button" data-r="rawtoggle"
          aria-pressed="${_rawOpen}">Input &amp; output</button>
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
    const px = Math.max(host.clientWidth - 40, 360);
    const body = _cur
      ? sections(_cur, px)
        + (_rawOpen ? `<section class="secblock third" data-sec="sec-input" id="sec-input">
             <div class="sech"><h2>Input</h2><span class="sp"></span>
               <span class="meta" data-r="imeta"></span></div>
             <div class="logbox" data-r="in"></div></section>
           <section class="secblock twothirds" data-sec="sec-output" id="sec-output">
             <div class="sech"><h2>Output</h2><span class="sp"></span>
               <span class="meta" data-r="ometa"></span></div>
             <div class="logbox" data-r="out"></div></section>` : "")
        + `<div class="secblock wide" id="fep"></div>`
      : `<section class="secblock wide"><p class="hint">No result open. Pick a finished
           calculation above, or open any ORCA output from disk — a result stays readable
           after it leaves the queue, because the run folder is still there.</p></section>
         <div class="secblock wide" id="fep"></div>`;
    host.innerHTML = head()
      + `<div class="resmain"><div class="rescontent">
           <nav class="secchips" aria-label="Result sections">${
             _present.map(t => `<button type="button" data-jump="sec-${
               t.toLowerCase().replace(/[^a-z0-9]+/g, "-")}">${NB.esc(t)}</button>`).join("")}</nav>
           <div class="resinner">${body}</div>
         </div></div>`;
    // the outline is built while the sections render, so it is written after
    host.querySelector(".secchips").innerHTML = _present.map(t =>
      `<button type="button" data-jump="sec-${t.toLowerCase().replace(/[^a-z0-9]+/g, "-")}">${NB.esc(t)}</button>`
    ).join("");
    host.querySelectorAll("[data-jump]").forEach(b => b.addEventListener("click", () => {
      const t = document.getElementById(/** @type {HTMLElement} */ (b).dataset.jump);
      if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
    }));

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
    q('[data-r="rawtoggle"]').addEventListener("click", () => { _rawOpen = !_rawOpen; paint(); });

    if (_cur && _rawOpen) rawPair(_curName || nameFromPath(_curPath), _cur);
    profile(px);
    if (NB.rail) NB.rail.render();
  }

  // ---------------------------------------------------------------- opening

  /** Put a parsed result on screen. The one way in — both open paths and any
   *  future producer go through it, so what "showing a result" means is
   *  defined once. @param {any} d @param {string} name @param {string} path */
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
    const p = _curPath;
    if (!p) { NB.toast("Open a result first."); return; }
    await NB.call("show_path_in_folder", p);
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
