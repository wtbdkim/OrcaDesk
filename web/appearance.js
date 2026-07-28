// @ts-check
// Theme / wallpaper / Liquid Glass appearance layer (applyTheme, applyThemeVariant,
// renderWallpaper, the _lg* globals and the compositor self-heal pulse) — split out
// of app.js. Plain global script, loaded before app.js.

// ---- theme (light / dark) ----
function applyTheme(theme) {
  const t = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t);
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.textContent = t === "light" ? "☀" : "☽";   // ☀ / ☽
    btn.title = t === "light" ? "Dark theme" : "Light theme";
  }
}
async function toggleTheme() {
  const next = (settings.theme === "light") ? "dark" : "light";
  applyTheme(next);   // flip the UI instantly, then persist
  const res = /** @type {SaveSettingsResult} */ (JSON.parse(await bridge.save_settings(JSON.stringify({ theme: next }))));
  // bad input comes back as {error} — don't clobber the settings mirror with it
  if ("error" in res) { failNotify("Could not save theme: " + res.error); return; }
  settings = res;
}

// ---- theme variant: shadcn (flat) / Liquid Glass ----
// The whole Liquid-Glass CSS layer is gated on html[data-theme-variant] +
// html[data-glass]; this code only flips those attributes, paints the wallpaper
// canvas, and mirrors the Settings → Appearance controls. Orthogonal to the
// light/dark toggle (applyTheme), which stays on the top-bar ☽ button.
const LG_LEVELS = ["restrained", "moderate", "bold", "vivid", "maximal"];
/** @typedef {{ base:string, prev:string, pools:Array<[string,number,number,number,number]> }} LgWallpaper */
/** Procedural wallpaper presets — `base` fill + additive radial `pools`
 * ([color, cx, cy, radius, alpha] as viewport fractions); `prev` is the swatch
 * gradient. Mirrors the design-preview renderer. @type {Object<string,LgWallpaper>} */
const LG_WALLPAPERS = {
  aurora:   { base:"#0a1622", prev:"linear-gradient(135deg,#2fd6a0,#2a7bff 58%,#7a5cff)", pools:[["#2fd6a0",.2,.28,.55,.8],["#2a7bff",.62,.5,.6,.85],["#3ad16b",.35,.82,.5,.7],["#7a5cff",.85,.2,.5,.7]] },
  aqua:     { base:"#071a2e", prev:"linear-gradient(135deg,#22c3ff,#2a6bff 70%,#0a3a7a)", pools:[["#22c3ff",.25,.3,.55,.8],["#2a6bff",.6,.55,.6,.85],["#1fd6c0",.8,.8,.45,.6],["#0a3a7a",.4,.9,.6,.7]] },
  sunset:   { base:"#2a0e1e", prev:"linear-gradient(135deg,#ffc24d,#ff4d8d 55%,#c04bff)", pools:[["#ffc24d",.2,.8,.5,.8],["#ff6a3d",.5,.6,.55,.8],["#ff4d8d",.7,.35,.55,.85],["#c04bff",.85,.12,.5,.7]] },
  grape:    { base:"#150a2a", prev:"linear-gradient(135deg,#5b4bff,#a44bff 55%,#ff5ca8)", pools:[["#5b4bff",.28,.3,.6,.8],["#a44bff",.62,.5,.55,.85],["#ff5ca8",.8,.8,.5,.7],["#3a7bff",.2,.85,.5,.6]] },
  graphite: { base:"#0c0e14", prev:"linear-gradient(135deg,#2b3550,#35507a)", pools:[["#35507a",.3,.35,.6,.55],["#2b3550",.7,.6,.6,.6],["#3a6a9a",.85,.85,.4,.4]] },
  ocean:    { base:"#04121f", prev:"linear-gradient(135deg,#0a6cff,#00c2c7 60%,#0a3a7a)", pools:[["#0a6cff",.28,.35,.6,.8],["#00c2c7",.7,.6,.55,.7],["#1f8cff",.85,.85,.45,.5],["#062a52",.2,.9,.5,.6]] },
};
const LG_WALL_ORDER = ["aurora", "aqua", "sunset", "grape", "graphite", "ocean"];
// data-URI ceiling for a custom wallpaper — mirrors bridge.py _WALLPAPER_MAX
// (24 MB ≈ an 18 MB image). Checked here so an oversize upload is rejected with
// feedback instead of showing this session then silently vanishing on restart.
const LG_WALL_MAX = 24 * 1024 * 1024;
/** @type {HTMLImageElement|null} */
let _lgCustomImg = null;        // loaded custom wallpaper image (if any)
let _lgCustomData = "";         // its data URI (upload-swatch thumbnail + re-persist)
let _wallpaperInited = false;   // custom image fetched from the backend once

function _clampVariant(v) { return v === "liquidglass" ? "liquidglass" : "shadcn"; }
function _clampLevel(l) { return LG_LEVELS.indexOf(l) >= 0 ? l : "moderate"; }

/** Flip the DOM attributes + Settings UI state for a variant/level. Paints the
 * wallpaper when liquidglass is active. Does NOT persist (callers do). */
function applyThemeVariant(variant, level) {
  const v = _clampVariant(variant), lv = _clampLevel(level);
  const root = document.documentElement;
  root.setAttribute("data-theme-variant", v);
  root.setAttribute("data-glass", lv);
  const bs = document.getElementById("variant-shadcn");
  const bl = document.getElementById("variant-liquidglass");
  if (bs) bs.classList.toggle("on", v === "shadcn");
  if (bl) bl.classList.toggle("on", v === "liquidglass");
  const opts = document.getElementById("lg-options");
  if (opts) opts.style.display = v === "liquidglass" ? "" : "none";
  document.querySelectorAll("#lg-level-row button").forEach(b =>
    b.classList.toggle("on", b.getAttribute("data-level") === lv));
  if (v === "liquidglass") { renderWallpaper(); _lgPulseStart(); }
  else {
    _lgPulseStop();
    // The hidden canvas keeps its full viewport×DPR backing store (~tens of MB)
    // unless shrunk — the CSS display:none alone releases nothing. renderWallpaper
    // re-sizes it on the way back to liquidglass.
    const cv = /** @type {HTMLCanvasElement|null} */ (/** @type {unknown} */ (document.getElementById("lgWall")));
    if (cv && cv.width > 1) { cv.width = 1; cv.height = 1; }
  }
}

/** Persist a settings patch; refresh the mirror. Returns false on backend error. */
async function _persistAppearance(patch) {
  const res = /** @type {SaveSettingsResult} */ (JSON.parse(await bridge.save_settings(JSON.stringify(patch))));
  if ("error" in res) { failNotify("Could not save appearance: " + res.error); return false; }
  settings = res;
  return true;
}

async function setThemeVariant(variant) {
  const v = _clampVariant(variant);
  settings.theme_variant = v;
  applyThemeVariant(v, settings.glass_level);
  if (v === "liquidglass") await initWallpaper();
  await _persistAppearance({ theme_variant: v });
}

async function setGlassLevel(level) {
  const lv = _clampLevel(level);
  settings.glass_level = lv;
  applyThemeVariant(settings.theme_variant, lv);
  await _persistAppearance({ glass_level: lv });
}

async function setWallpaper(key) {
  settings.wallpaper = key;
  renderWallpaper();
  _markWallpaperSel();
  await _persistAppearance({ wallpaper: key });
}

/** Build the wallpaper swatch grid once: presets + a custom-image swatch
 * (hidden until an image exists) + the upload tile. The custom image gets its
 * OWN swatch so the ＋ tile stays visible after an upload — turning the ＋
 * tile itself into the thumbnail removed the only affordance for picking a
 * different image (the "＋ disappears" bug). */
function buildWallpaperSwatches() {
  const grid = document.getElementById("lg-wall-grid");
  if (!grid || grid.dataset.built) return;
  grid.dataset.built = "1";
  LG_WALL_ORDER.forEach(k => {
    const b = document.createElement("button");
    b.className = "lg-wall-sw"; b.dataset.k = k; b.title = k;
    b.style.background = LG_WALLPAPERS[k].prev;
    const lab = document.createElement("span");
    lab.className = "lg-wall-label"; lab.textContent = k;
    b.appendChild(lab);
    b.onclick = () => setWallpaper(k);
    grid.appendChild(b);
  });
  const cust = document.createElement("button");
  cust.className = "lg-wall-sw custom"; cust.dataset.k = "custom";
  cust.title = "Custom image"; cust.style.display = "none";
  const clab = document.createElement("span");
  clab.className = "lg-wall-label"; clab.textContent = "custom";
  cust.appendChild(clab);
  cust.onclick = () => setWallpaper("custom");
  grid.appendChild(cust);
  const up = document.createElement("button");
  up.className = "lg-wall-sw up";   // no data-k: it's an action, never "selected"
  up.title = "Upload image"; up.textContent = "＋";
  up.onclick = () => onWallpaperUpload();
  grid.appendChild(up);
}

function _markWallpaperSel() {
  const grid = document.getElementById("lg-wall-grid");
  if (!grid) return;
  const cur = settings.wallpaper || "aurora";
  grid.querySelectorAll(".lg-wall-sw").forEach(b =>
    b.classList.toggle("on", b.getAttribute("data-k") === cur));
}

/** Reflect a loaded custom image onto its own swatch (thumbnail) and show it. */
function _applyCustomSwatch() {
  const grid = document.getElementById("lg-wall-grid");
  if (!grid) return;
  const cust = /** @type {HTMLElement|null} */ (grid.querySelector(".lg-wall-sw.custom"));
  if (cust && _lgCustomData) {
    cust.style.display = "";
    cust.style.background = "center/cover url(" + _lgCustomData + ")";
  }
}

function onWallpaperUpload() {
  // always opens the OS picker — selecting the existing custom image is the
  // custom swatch's job (the old re-select-first behavior made the ＋ tile
  // need two clicks to actually replace the image)
  let fi = /** @type {HTMLInputElement|null} */ (/** @type {unknown} */ (document.getElementById("lg-wall-file")));
  if (!fi) {
    fi = document.createElement("input");
    fi.type = "file"; fi.accept = "image/*"; fi.id = "lg-wall-file"; fi.style.display = "none";
    fi.addEventListener("change", _onWallpaperFile);
    document.body.appendChild(fi);
  }
  fi.value = "";
  fi.click();
}

function _onWallpaperFile(ev) {
  const inp = /** @type {HTMLInputElement} */ (ev.target);
  const f = inp.files && inp.files[0];
  if (!f || !/^image\//.test(f.type || "")) return;
  const rd = new FileReader();
  rd.onload = () => {
    const data = /** @type {string} */ (rd.result);
    // reject oversize BEFORE committing, so we never switch to a custom wallpaper
    // the backend can't persist (which would fall back to a preset on restart
    // while settings still said "custom" — a silent loss + state desync)
    if (data.length > LG_WALL_MAX) {
      failNotify("That image is too large (max ~18 MB). Pick a smaller one.");
      return;
    }
    const img = new Image();
    img.onload = async () => {
      _lgCustomImg = img; _lgCustomData = data;
      _applyCustomSwatch();
      settings.wallpaper = "custom";
      renderWallpaper();
      _markWallpaperSel();
      // persist the image blob (a file in user_data_root) then the key choice.
      // Backstop: if the backend still couldn't store it (write error / cap),
      // warn — the image shows this session but won't survive a restart.
      const r = /** @type {WallpaperResult} */ (
        JSON.parse(await bridge.set_wallpaper_image(data)));
      if (r.error || r.stored === false) {
        failNotify("That wallpaper could not be saved; it will not persist after a restart.");
      }
      await _persistAppearance({ wallpaper: "custom" });
    };
    img.onerror = () => failNotify("That image could not be loaded.");
    img.src = data;
  };
  rd.readAsDataURL(f);
}

/** First-time liquidglass setup: build swatches, pull the stored custom image
 * from the backend (so it survives restarts), then paint. */
async function initWallpaper() {
  buildWallpaperSwatches();
  if (!_wallpaperInited) {
    _wallpaperInited = true;
    try {
      const data = await bridge.get_wallpaper_image();
      if (data) {
        await new Promise(resolve => {
          const img = new Image();
          img.onload = () => { _lgCustomImg = img; _lgCustomData = data; _applyCustomSwatch(); resolve(null); };
          img.onerror = () => resolve(null);
          img.src = data;
        });
      }
    } catch (e) { /* no custom image stored */ }
  }
  renderWallpaper();
  _markWallpaperSel();
}

// ---- wallpaper canvas renderer (procedural presets or a custom image) ----
function _a2(a) { const h = Math.round(a * 255).toString(16); return h.length < 2 ? "0" + h : h; }
/** @param {CanvasRenderingContext2D} ctx @param {LgWallpaper} wp */
function _wallPools(ctx, wp, W, H) {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = wp.base; ctx.fillRect(0, 0, W, H);
  ctx.globalCompositeOperation = "lighter";
  wp.pools.forEach(p => {
    const cx = p[1] * W, cy = p[2] * H, r = p[3] * Math.max(W, H);
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    g.addColorStop(0, p[0] + _a2(p[4]));
    g.addColorStop(1, p[0] + "00");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.fill();
  });
  ctx.globalCompositeOperation = "source-over";
}
/** @param {CanvasRenderingContext2D} ctx */
function _wallVignette(ctx, W, H, s) {
  const g = ctx.createRadialGradient(W / 2, H * .42, 0, W / 2, H * .42, Math.max(W, H) * .78);
  g.addColorStop(0, "transparent");
  g.addColorStop(1, "rgba(0,0,0," + s + ")");
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
}
/** @param {CanvasRenderingContext2D} ctx @param {HTMLImageElement} img */
function _wallImage(ctx, img, W, H) {
  const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
  if (!iw || !ih) return;
  ctx.clearRect(0, 0, W, H);
  const s = Math.max(W / iw, H / ih), dw = iw * s, dh = ih * s;
  ctx.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
}
/** Paint the wallpaper canvas for the current settings.wallpaper. No-op if the
 * canvas is missing (should never happen — it's in index.html). */
function renderWallpaper() {
  const cv = /** @type {HTMLCanvasElement|null} */ (/** @type {unknown} */ (document.getElementById("lgWall")));
  if (!cv || !cv.getContext) return;
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  const W = window.innerWidth, H = window.innerHeight;
  cv.width = Math.max(1, W * DPR);
  cv.height = Math.max(1, H * DPR);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  const key = settings.wallpaper || "aurora";
  if (key === "custom" && _lgCustomImg) {
    _wallImage(ctx, _lgCustomImg, W, H);
    _wallVignette(ctx, W, H, .2);
    return;
  }
  const wp = LG_WALLPAPERS[key] || LG_WALLPAPERS.aurora;
  _wallPools(ctx, wp, W, H);
  _wallVignette(ctx, W, H, .34);
}
// repaint on resize (viewport-sized canvas), only while liquidglass is active
window.addEventListener("resize", () => {
  if (settings.theme_variant === "liquidglass") renderWallpaper();
});

// ---- compositor self-heal heartbeat (DESIGN.md §16.5 rule 4) ----
// The §16.5 layer rules make compositor drops unlikely but can't prevent the
// ones caused by EXTERNAL GPU events (sleep/resume, driver reset, GPU memory
// pressure) — and a static chrome bar is never re-invalidated, so a dropped
// layer would stay gone until restart. While Liquid Glass is active we flip
// --lg-pulse between 0 and 0.004 every LG_PULSE_MS; style.css routes that
// imperceptible delta through all three composited pieces of both bars
// (tint alpha, saturate() in the backdrop chain, an inset outline painted
// with the labels), so any dropped piece is re-rastered — healed — within
// one period. The pulse must stay PAINT-ONLY: a will-change/transform nudge
// would make the bar its own render surface and break what its
// backdrop-filter samples.
const LG_PULSE_MS = 250;  // heal-latency upper bound; cost per tick is re-rastering two thin bars
let _lgPulseTimer = 0;
let _lgPulseOn = false;

function _lgPulseStart() {
  if (_lgPulseTimer) return;
  _lgPulseTimer = window.setInterval(() => {
    _lgPulseOn = !_lgPulseOn;
    document.documentElement.style.setProperty("--lg-pulse", _lgPulseOn ? "0.004" : "0");
  }, LG_PULSE_MS);
}

function _lgPulseStop() {
  if (_lgPulseTimer) { clearInterval(_lgPulseTimer); _lgPulseTimer = 0; }
  _lgPulseOn = false;
  document.documentElement.style.removeProperty("--lg-pulse");
}
