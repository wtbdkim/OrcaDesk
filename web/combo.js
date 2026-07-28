// @ts-check
// Searchable combobox widget (setupCombo / comboValue) — split out of app.js.
// Plain global script (no modules), like scf_graph.js; loaded before app.js.

// ---- custom searchable combobox (search + group headers + scroll + free text) ----
// Registry of combo instances by container id, so editCalc can set values.
const _combos = {};
// Build a combobox inside container `#combo-<key>`. `groups` is the level-ordered
// {groupKey: [items]} dict. The input keeps any typed value (out-of-list allowed).
function setupCombo(containerId, groups, def) {
  const root = document.getElementById(containerId);
  if (!root) return;
  const input = root.querySelector(".combo-input");
  const list = root.querySelector(".combo-list");
  // flatten with group tags, preserving order
  const entries = [];   // {value, group}
  for (const [key, items] of Object.entries(groups || {})) {
    if (!items || !items.length) continue;
    const label = prettyGroup(key);
    for (const it of items) entries.push({ value: it, group: label });
  }
  let activeIdx = -1;    // highlighted row index (into the currently rendered list)
  let rendered = [];     // current filtered entries (flat, excluding headers)

  function render(filter) {
    const q = (filter || "").trim().toLowerCase();
    list.innerHTML = "";
    rendered = [];
    let lastGroup = null;
    let count = 0;
    for (const e of entries) {
      if (q && !e.value.toLowerCase().includes(q)) continue;
      if (e.group !== lastGroup) {
        const h = document.createElement("div");
        h.className = "combo-group";
        h.textContent = e.group;
        list.appendChild(h);
        lastGroup = e.group;
      }
      const row = document.createElement("div");
      row.className = "combo-item";
      row.textContent = e.value;
      const idx = rendered.length;
      row.addEventListener("mousedown", (ev) => {
        // mousedown (not click) so it fires before input blur
        ev.preventDefault();
        choose(e.value);
      });
      row.addEventListener("mouseenter", () => setActive(idx));
      list.appendChild(row);
      rendered.push({ value: e.value, el: row });
      count++;
    }
    if (count === 0) {
      const none = document.createElement("div");
      none.className = "combo-none";
      none.textContent = q ? `No match — "${filter}" will be used as-is` : "No options";
      list.appendChild(none);
    }
    activeIdx = -1;
  }
  function open() {
    // show the full list on focus (don't pre-filter by the current value, so
    // the user can browse freely); highlight the current value if it's present
    render("");
    list.style.display = "block";
    const cur = input.value;
    const i = rendered.findIndex((r) => r.value === cur);
    if (i >= 0) setActive(i);
  }
  function close() { list.style.display = "none"; activeIdx = -1; }
  function isOpen() { return list.style.display !== "none"; }
  function choose(val) { input.value = val; close(); input.dispatchEvent(new Event("change", { bubbles: true })); }
  function setActive(i) {
    if (activeIdx >= 0 && rendered[activeIdx]) rendered[activeIdx].el.classList.remove("active");
    activeIdx = i;
    if (activeIdx >= 0 && rendered[activeIdx]) {
      rendered[activeIdx].el.classList.add("active");
      rendered[activeIdx].el.scrollIntoView({ block: "nearest" });
    }
  }

  input.addEventListener("focus", open);
  input.addEventListener("input", () => {
    render(input.value);
    list.style.display = "block";
    // auto-highlight the first match so Enter picks it right away
    if (rendered.length) setActive(0);
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      if (!isOpen()) { open(); if (rendered.length) setActive(0); return; }
      setActive(Math.min(activeIdx + 1, rendered.length - 1));
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      if (!isOpen()) return;
      setActive(Math.max(activeIdx - 1, 0));
    } else if (ev.key === "Enter") {
      // pick the highlighted row, or the first match if none highlighted
      if (isOpen() && rendered.length) {
        ev.preventDefault();
        const pick = activeIdx >= 0 ? activeIdx : 0;
        choose(rendered[pick].value);
      }
    } else if (ev.key === "Escape") {
      close();
    } else if (ev.key === "Tab") {
      close();
    }
  });
  // close when focus leaves the combo
  input.addEventListener("blur", () => { setTimeout(close, 120); });

  if (def != null) input.value = def;
  _combos[containerId] = {
    get: () => input.value,
    set: (v) => { input.value = v == null ? "" : v; },
  };
  // initial (hidden) render so the list is ready
  render("");
  close();
}
function comboValue(containerId) {
  const c = _combos[containerId];
  return c ? c.get() : "";
}
