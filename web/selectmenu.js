// @ts-check
// Styled replacement for the native <select> popup — split out like combo.js.
// Plain global script (no modules); loaded before app.js.
//
// WHY THIS EXISTS
// QtWebEngine does not draw a <select>'s dropdown with the page's CSS. It hands
// the list to a NATIVE Qt widget, which is painted from the Qt palette — and the
// palette is the system one, not the app's theme. On a dark theme that is a
// white slab of a popup covering whatever is under it (reported on the MLIP
// "Base Python" picker, but every <select> in the app has it). There is no CSS
// that reaches inside a native popup, so the only fix is to not open one.
//
// WHY IT IS AN ENHANCEMENT, NOT A REWRITE
// The <select> elements stay in the DOM and stay the source of truth. Roughly
// thirty of them are read, written and populated from app.js, results_render.js
// and inline onchange= attributes (fillSelect, fillGroupedSelect,
// `sel.innerHTML = "<option ...>"`, `sel.value = prev`, `[...sel.options]`).
// Rewriting those call sites to a widget API would have touched every one of
// them and every payload that flows through them. So instead the native control
// is hidden and a styled trigger + listbox is drawn beside it, and everything
// the rest of the app does keeps working untouched:
//
//   * reads   — `sel.value` / `sel.options` are the real element's, unchanged.
//   * writes  — `sel.value = x` is intercepted per element (the prototype's own
//               setter still does the work) so the visible label follows a
//               programmatic set, which fires no event of its own.
//   * refills — a MutationObserver on the option list re-renders after
//               innerHTML replacement or appendChild.
//   * onchange— choosing a row dispatches a bubbling `change`, so inline
//               handlers (onKindChange, showSelectedResult, ...) still fire.
//
// This is also why the trigger is not given the id: the id belongs to the
// <select>, because getElementById("cfg-calc").value must keep meaning what it
// has always meant.

(function () {
  "use strict";

  const OPEN_CLASS = "selectmenu-open";
  // Every attached instance, so a theme change or a relayout can re-sync them.
  const _menus = new WeakMap();
  // The instance whose list is currently open (only ever one).
  let _openMenu = null;

  // The prototype descriptor, captured once. Per-element overrides delegate to
  // it rather than reimplementing selection, so `value = "x"` keeps the native
  // semantics (unknown value => selectedIndex -1 => "").
  const VALUE_DESC = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype, "value");

  /** Text a row/trigger shows for an <option>: its label, falling back to value. */
  function optionLabel(opt) {
    return (opt.textContent || "").trim() || opt.value;
  }

  /**
   * Attach a styled menu to one <select>. Idempotent — attaching twice is a
   * no-op, which is what lets the observer rescan freely.
   */
  function attach(sel) {
    if (!(sel instanceof HTMLSelectElement)) return;
    if (sel.multiple || sel.size > 1) return;   // not a dropdown; leave it alone
    if (_menus.has(sel)) return;
    // Belt and braces for the same hazard the observer guards against: a
    // select already sitting in a wrapper has a menu, whatever the registry
    // says, and wrapping it twice is how the runaway above started.
    if (sel.parentElement && sel.parentElement.classList.contains("selectmenu")) return;

    const wrap = document.createElement("span");
    wrap.className = "selectmenu";
    const trigger = document.createElement("button");
    trigger.type = "button";                    // never submit anything
    trigger.className = "selectmenu-trigger";
    // Inline width lives on a few selects (`style="width:auto"` on the results
    // and free-energy pickers). The trigger takes the select's place in the
    // layout, so it has to take its inline style with it.
    const inlineStyle = sel.getAttribute("style");
    if (inlineStyle) trigger.setAttribute("style", inlineStyle);

    const label = document.createElement("span");
    label.className = "selectmenu-label";
    const caret = document.createElement("span");
    caret.className = "selectmenu-caret";
    caret.setAttribute("aria-hidden", "true");
    caret.textContent = "▾";               // ▾
    trigger.append(label, caret);

    // The list is appended to <body>, not next to the trigger: several of these
    // sit inside panels with their own overflow, and a popup that is a child of
    // a clipping ancestor gets cut off by it. Body + fixed positioning is the
    // only placement that cannot be clipped.
    const list = document.createElement("div");
    list.className = "selectmenu-list";
    list.setAttribute("role", "listbox");
    list.hidden = true;

    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);
    wrap.appendChild(trigger);
    document.body.appendChild(list);

    let rows = [];        // {value, el} for each selectable row, in view order
    let activeIdx = -1;   // highlighted row (keyboard), index into `rows`
    let typeahead = "";
    let typeaheadAt = 0;

    const inst = { sel, wrap, trigger, list, sync, close, destroy };
    _menus.set(sel, inst);

    // ---- rendering -------------------------------------------------------

    /** Mirror the <select>'s current state onto the trigger (label + disabled). */
    function sync() {
      const opt = sel.selectedOptions && sel.selectedOptions[0];
      label.textContent = opt ? optionLabel(opt) : "";
      // An empty label would collapse the trigger's height; keep it occupied.
      if (!label.textContent) label.innerHTML = "&nbsp;";
      trigger.disabled = sel.disabled;
      trigger.classList.toggle("is-placeholder", !opt || opt.value === "");
      if (!list.hidden) renderList();
    }

    /** Build the popup rows from the live <option>/<optgroup> tree. */
    function renderList() {
      list.innerHTML = "";
      rows = [];
      const current = sel.value;
      for (const node of sel.children) {
        if (node.tagName === "OPTGROUP") {
          const h = document.createElement("div");
          h.className = "selectmenu-group";
          h.textContent = node.label;
          list.appendChild(h);
          for (const o of node.children) addRow(o, current);
        } else if (node.tagName === "OPTION") {
          addRow(node, current);
        }
      }
      if (!rows.length) {
        const none = document.createElement("div");
        none.className = "selectmenu-none";
        none.textContent = "nothing to choose";
        list.appendChild(none);
      }
    }

    function addRow(o, current) {
      const row = document.createElement("div");
      row.className = "selectmenu-item";
      row.setAttribute("role", "option");
      row.textContent = optionLabel(o);
      if (o.disabled) row.classList.add("is-disabled");
      if (o.value === current) {
        row.classList.add("is-selected");
        row.setAttribute("aria-selected", "true");
      }
      const idx = rows.length;
      if (!o.disabled) {
        // mousedown, not click: it lands before the trigger's blur, so the list
        // is still open and the pick is not lost to a close-on-blur race.
        row.addEventListener("mousedown", (ev) => {
          ev.preventDefault();
          choose(o.value);
        });
        row.addEventListener("mouseenter", () => setActive(idx));
      }
      list.appendChild(row);
      rows.push({ value: o.value, el: row, disabled: !!o.disabled });
    }

    function setActive(i) {
      if (activeIdx >= 0 && rows[activeIdx]) rows[activeIdx].el.classList.remove("is-active");
      activeIdx = i;
      const r = rows[i];
      if (!r) return;
      r.el.classList.add("is-active");
      const rt = r.el.offsetTop, rb = rt + r.el.offsetHeight;
      if (rt < list.scrollTop) list.scrollTop = rt;
      else if (rb > list.scrollTop + list.clientHeight) list.scrollTop = rb - list.clientHeight;
    }

    /** Commit a value the way a user pick does: set it, then announce it. */
    function choose(value) {
      const before = sel.value;
      VALUE_DESC.set.call(sel, value);
      sync();
      close();
      trigger.focus();
      if (sel.value !== before) {
        // `input` then `change`, bubbling, exactly as the native control emits
        // them — the app's inline onchange= handlers listen for the second.
        sel.dispatchEvent(new Event("input", { bubbles: true }));
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    // ---- open / close / placement ---------------------------------------

    function place() {
      const r = trigger.getBoundingClientRect();
      const margin = 4;
      list.style.minWidth = r.width + "px";
      list.style.left = r.left + "px";
      // Measure first, then decide which side has room. Opening upward matters
      // for the pickers near the bottom of the Settings panel.
      list.style.top = "0px";
      list.style.maxHeight = "";
      const h = list.offsetHeight;
      const below = window.innerHeight - r.bottom - margin;
      const above = r.top - margin;
      if (h <= below || below >= above) {
        list.style.top = (r.bottom + margin) + "px";
        list.style.maxHeight = Math.max(96, below) + "px";
      } else {
        list.style.top = Math.max(margin, r.top - margin - Math.min(h, above)) + "px";
        list.style.maxHeight = Math.max(96, above) + "px";
      }
    }

    function open() {
      if (sel.disabled || !list.hidden) return;
      if (_openMenu && _openMenu !== inst) _openMenu.close();
      _openMenu = inst;
      renderList();
      list.hidden = false;
      wrap.classList.add(OPEN_CLASS);
      place();
      const i = rows.findIndex((r) => r.value === sel.value && !r.disabled);
      setActive(i >= 0 ? i : rows.findIndex((r) => !r.disabled));
    }

    function close() {
      if (list.hidden) return;
      list.hidden = true;
      wrap.classList.remove(OPEN_CLASS);
      activeIdx = -1;
      if (_openMenu === inst) _openMenu = null;
    }

    function destroy() {
      close();
      list.remove();
      _menus.delete(sel);
    }

    // ---- input -----------------------------------------------------------

    trigger.addEventListener("click", () => (list.hidden ? open() : close()));
    trigger.addEventListener("blur", () => { setTimeout(close, 120); });

    trigger.addEventListener("keydown", (ev) => {
      const k = ev.key;
      if (k === "Escape") { if (!list.hidden) { ev.preventDefault(); close(); } return; }
      if (list.hidden) {
        if (k === "Enter" || k === " " || k === "ArrowDown" || k === "ArrowUp") {
          ev.preventDefault(); open(); return;
        }
      } else {
        if (k === "Enter" || k === " ") {
          ev.preventDefault();
          if (rows[activeIdx]) choose(rows[activeIdx].value);
          return;
        }
        if (k === "ArrowDown" || k === "ArrowUp") {
          ev.preventDefault();
          const step = k === "ArrowDown" ? 1 : -1;
          let i = activeIdx;
          for (let n = 0; n < rows.length; n++) {
            i = (i + step + rows.length) % rows.length;
            if (!rows[i].disabled) break;
          }
          setActive(i);
          return;
        }
        if (k === "Home" || k === "End") {
          ev.preventDefault();
          setActive(k === "Home"
            ? rows.findIndex((r) => !r.disabled)
            : rows.length - 1 - [...rows].reverse().findIndex((r) => !r.disabled));
          return;
        }
      }
      // Type-ahead, the one native behaviour people miss most: typing jumps to
      // the first option starting with what was typed, and consecutive letters
      // within a second extend the search rather than restarting it.
      if (k.length === 1 && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
        const now = Date.now();
        typeahead = (now - typeaheadAt < 1000) ? typeahead + k : k;
        typeaheadAt = now;
        if (list.hidden) renderList();
        const q = typeahead.toLowerCase();
        const i = rows.findIndex(
          (r) => !r.disabled && (r.el.textContent || "").toLowerCase().startsWith(q));
        if (i >= 0) { if (list.hidden) choose(rows[i].value); else setActive(i); }
        ev.preventDefault();
      }
    });

    // ---- keeping in step with the rest of the app ------------------------

    // A programmatic `sel.value = x` fires no event, and the app does it in
    // several places (restoring a remembered result, re-applying a default
    // after a refill). Intercept it per element so the label follows.
    Object.defineProperty(sel, "value", {
      configurable: true,
      enumerable: true,
      get() { return VALUE_DESC.get.call(this); },
      set(v) { VALUE_DESC.set.call(this, v); sync(); },
    });

    // Refills replace the option list wholesale (innerHTML = "" + appendChild).
    // `disabled` is toggled around long-running actions.
    new MutationObserver(() => sync()).observe(sel, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ["disabled", "value", "selected", "label"],
    });

    sync();
  }

  /** Attach to every <select> under `root` that does not have a menu yet. */
  function scan(root) {
    const scope = root || document;
    if (scope instanceof HTMLSelectElement) { attach(scope); return; }
    if (!scope.querySelectorAll) return;
    scope.querySelectorAll("select").forEach(attach);
  }

  // Panels are built with innerHTML at runtime (the Build tab rebuilds its
  // whole config row per calculation kind), so new selects keep arriving.
  function observeDocument() {
    new MutationObserver((records) => {
      for (const rec of records) {
        for (const node of rec.addedNodes) {
          if (node.nodeType === 1) scan(/** @type {Element} */(node));
        }
        for (const node of rec.removedNodes) {
          if (node.nodeType !== 1) continue;
          const el = /** @type {Element} */(node);
          const gone = el.tagName === "SELECT" ? [el] : [...el.querySelectorAll("select")];
          for (const s of gone) {
            // A MOVE and a removal produce the same record, and attach() moves
            // the <select> into its wrapper -- so tearing down here on sight
            // destroys the menu that was just built, the re-scan of the newly
            // added wrapper finds an unattached select, builds it again, and
            // the two chase each other until the renderer stops responding.
            // Only a select that has actually left the document is gone; by the
            // time this callback runs the DOM has settled, so contains() is the
            // question to ask.
            if (document.contains(s)) continue;
            // The list lives on <body>, so a panel that is thrown away would
            // otherwise leave its popup behind for good.
            const m = _menus.get(s);
            if (m) m.destroy();
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }

  // An open list is positioned against the viewport, so anything that moves the
  // trigger has to move it too. Capture phase: scrolling happens inside panels,
  // and scroll does not bubble.
  function trackViewport() {
    const reposition = () => { if (_openMenu) _openMenu.close(); };
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    document.addEventListener("mousedown", (ev) => {
      if (!_openMenu) return;
      const t = /** @type {Node} */ (ev.target);
      if (!_openMenu.list.contains(t) && !_openMenu.wrap.contains(t)) _openMenu.close();
    }, true);
  }

  function init() {
    scan(document);
    observeDocument();
    trackViewport();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Exposed for the same reason setupCombo is: so a panel that builds markup
  // and wants it live immediately can say so instead of waiting for the
  // observer's microtask.
  window.selectmenuScan = scan;
})();
