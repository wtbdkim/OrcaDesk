"""Measure a rendered notebook UI and dump a spec the two sides can be diffed on.

    python tools/uispec.py mock spec_mock.json    # design/notebook-reference.html
    python tools/uispec.py real spec_real.json    # web/next/, in the real window
    python tools/uidiff.py spec_mock.json spec_real.json

The notebook front-end (DESIGN.md §17) is meant to BE the approved design, not
an interpretation of it, and "looks about right" is not a check anyone can
repeat. So both sides are rendered and measured through the same landmark list
and the same computed properties, and the difference is a number.

The real side is seeded so the two pages hold the same SHAPE of data — finished
rows and queued ones, a raw calculation, a cell with both a chart and an output
pane — because otherwise the diff measures the sample, not the layout.
Requires PyQt6 (it boots the real MainWindow against a throwaway %APPDATA%).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

MODE = sys.argv[1] if len(sys.argv) > 1 else "mock"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else f"spec_{MODE}.json").resolve()  # before any chdir
ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT / "design" / "notebook-reference.html"

# role -> (mockup selector, web/next selector)
# The preview adopted the design's class vocabulary, so both sides are measured
# through the SAME selector — any difference left is a real one.
SEL = [
    "topbar", ".topbar",
    "brand", ".brand",
    "brand_name", ".brand h1",
    "ver_badge", ".ver-badge",
    "studypick", ".studypick",
    "tools", ".topbar .tools",
    "view_seg", ".seg.viewseg",
    "view_seg_btn", ".seg.viewseg button",
    "view_seg_on", '.seg.viewseg button[aria-pressed="true"]',
    "iconbtn", ".topbar .iconbtn",
    "tools_btn", ".topbar .tools .btn",
    "status_pill", ".status-pill",

    "rail", ".rail",
    "rail_scroll", ".railscroll",
    "rail_head", ".rail-h",
    "rail_foot", ".railfoot",
    "runrow", ".railfoot .runrow",
    "runrow_btn", ".railfoot .runrow .btn",
    "q_group", ".qgroup",
    "q_group_head", ".qgroup .qgh",
    "q_list", ".qgroup .qlist",
    "q_row", '.qrow[data-state="queued"]',
    "q_main", '.qrow[data-state="queued"] .qmain',
    "q_dot", '.qrow[data-state="queued"] .qmain .d.raw',
    "q_name", '.qrow[data-state="queued"] .qn',
    "q_tag", '.qrow[data-state="queued"] .qtag',
    "q_meta", '.qrow[data-state="queued"] .qm',
    "q_acts", '.qrow[data-state="queued"] .qacts',
    "q_badge", '.qrow[data-state="queued"] .qacts .badge',
    "q_exp", '.qrow[data-state="queued"] .qexp',
    "grip", '.qrow[data-state="queued"] .grip',

    "stream", ".stream",
    "streaminner", ".streaminner",
    "cell", ".cell:not(.live):not(.dead):not(.stub)",
    "cell_head", ".cell:not(.stub) .cellhead",
    "cell_idx", ".cell:not(.stub) .cellhead .idx",
    "cell_name", ".cell:not(.stub) .cellhead .nm",
    "cell_acts", ".cell:not(.stub) .cellhead .acts",
    "cell_body", ".cellbody",
    "cell_grid", ".celgrid",
    "thumb", ".thumb",
    "pane", ".pane:not([hidden])",
    "pane_head", ".pane:not([hidden]) .panehead",
    "pane_title", ".pane:not([hidden]) .panehead .t",
    "logbox", ".logbox",
    "facts", ".factline",
]
LANDMARKS = {SEL[i]: (SEL[i + 1], SEL[i + 1]) for i in range(0, len(SEL), 2)}

# The report, measured with a result open. Its own pass, because these elements
# do not exist until the Results view is up — and because leaving them out of
# the first list is exactly how a report of unstyled markup passed a 0-difference
# check once already.
RSEL = [
    "res_tool", ".restool",
    "res_title", ".restitle",
    "res_content", ".rescontent",
    "res_inner", ".resinner",
    "secchips", ".secchips",
    "secchip_btn", '.secchips button[aria-current="true"]',
    "secblock", ".secblock",
    "secblock_wide", ".secblock.wide",
    "secblock_third", ".secblock.third",
    "secblock_two", ".secblock.twothirds",
    "sech", ".resinner .sech",
    "sech_h2", ".resinner .sech h2",
    "sech_meta", ".resinner .sech .meta",
    "secdesc", ".resinner .secdesc",
    "plate", '.resinner .plate:not([style])',
    "tablewrap", ".resinner .tw",
    "table_th", ".resinner .tw th",
    # a plain data cell on both sides: td.lab is a different thing (sans, wraps,
    # 13px) and which column carries it depends on the table, not the design
    "table_td", ".resinner .tw td:not(.lab)",
    "table_td_lab", ".resinner .tw td.lab",
    "rep_factline", ".resinner .factline",
    "rawin", ".rawin",
    "rawout", ".rawout",
    "secblock_viewer", ".secblock.viewer",
    "secblock_vizhalf", ".secblock.vizhalf",
    "viewstage", ".viewstage",
    "viewlegend", ".viewstage .viewlegend",
    "stage_act", ".viewstage .act",
    "vizpanel", ".vizpanel",
    "vizlist", ".vizlist",
    "vizstage", ".vizstage",
    "viztypes", ".viztypes",
    "viztype", ".viztype",
    "vizcap", ".vizcap",
]
REPORT = {RSEL[i]: (RSEL[i + 1], RSEL[i + 1]) for i in range(0, len(RSEL), 2)}

PROPS = ["display", "gridTemplateColumns", "gridTemplateRows", "gap",
         "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
         "marginTop", "marginBottom",
         "borderRadius", "borderTopWidth", "borderColor",
         "fontSize", "fontWeight", "lineHeight", "letterSpacing", "fontFamily",
         "color", "backgroundColor", "boxShadow", "textTransform", "minHeight"]

JS = """
(function (map, props) {
  var out = {};
  var root = getComputedStyle(document.documentElement);
  out.__tokens = {};
  ["--radius","--radius-sm","--radius-lg","--pill","--radius-pill","--rail",
   "--background","--foreground","--card","--border","--accent","--muted-foreground",
   "--font-sans","--font-mono"].forEach(function (t) {
    var v = root.getPropertyValue(t).trim();
    if (v) out.__tokens[t] = v;
  });
  out.__window = {w: window.innerWidth, h: window.innerHeight,
                  bodyW: document.documentElement.scrollWidth};
  Object.keys(map).forEach(function (role) {
    var el = document.querySelector(map[role]);
    if (!el) { out[role] = null; return; }
    var cs = getComputedStyle(el);
    var r = el.getBoundingClientRect();
    var o = {box: [Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10]};
    props.forEach(function (p) { o[p] = cs[p]; });
    out[role] = o;
  });
  return out;
})(%s, %s)
"""


def measure(page, side, on_done, table=None):
    m = table if table is not None else LANDMARKS
    js = JS % (json.dumps({k: v[0 if side == "mock" else 1] for k, v in m.items()}),
               json.dumps(PROPS))
    page.runJavaScript(js, on_done)


def merge(a, b):
    """Fold the report pass into the jobs pass. __tokens/__window come from the
    first, which is the one measured with the window in its normal state."""
    for k, v in b.items():
        if not k.startswith("__"):
            a[k] = v
    return a


# --------------------------------------------------------------------------
def run_mock():
    from PyQt6.QtCore import QTimer, QUrl
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1680, 1000)
    view.show()
    # the window chrome the real app has (title bar) is not in the mockup, so
    # both sides are measured at the same INNER size
    view.load(QUrl.fromLocalFile(str(MOCK)))
    # the mockup opens light; both sides are measured in the same theme
    FORCE_DARK = "document.documentElement.setAttribute('data-theme','dark')"

    state = {}

    def jobs_done(res):
        state["jobs"] = res
        # the reference's Results view is a data-view on the shell
        view.page().runJavaScript(
            'document.querySelector(".app").setAttribute("data-view","results")',
            lambda _r: QTimer.singleShot(900,
                lambda: measure(view.page(), "mock", report_done, REPORT)))

    def report_done(res):
        out = merge(state["jobs"], res)
        OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print("wrote", OUT, "landmarks:", len([k for k in out if not k.startswith("__")]))
        missing = [k for k in out if not k.startswith("__") and out[k] is None]
        if missing:
            print("  NOT FOUND in mockup:", missing)
        app.quit()

    view.loadFinished.connect(
        lambda ok: QTimer.singleShot(1200, lambda: view.page().runJavaScript(
            FORCE_DARK, lambda _r: QTimer.singleShot(900,
                lambda: measure(view.page(), "mock", jobs_done)))))
    QTimer.singleShot(60000, app.quit)
    app.exec()


def run_real():
    tmp = Path(tempfile.mkdtemp(prefix="orcadesk-spec-"))
    os.environ["APPDATA"] = str(tmp)
    (tmp / "ORCAdesk").mkdir(parents=True, exist_ok=True)
    ws = tmp / "ws"; ws.mkdir(exist_ok=True)
    fake = tmp / "orca.exe"; fake.write_bytes(b"")
    (tmp / "ORCAdesk" / "settings.json").write_text(json.dumps({
        "workspace_root": str(ws), "ui_variant": "notebook",
        "orca_path": str(fake)}), encoding="utf-8")
    import shutil
    (ws / "h2o").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "tests" / "nbo" / "fixtures" / "h2o.out", ws / "h2o" / "h2o.out")
    # something for the Visual panel to find, so both sides have one to measure
    NL = chr(10)
    (ws / "h2o" / "h2o_trj.xyz").write_text(
        "3" + NL + "frame 1  E = -76.4000" + NL
        + "O 0.0 0.0 0.117" + NL + "H 0.0 0.757 -0.469" + NL + "H 0.0 -0.757 -0.469" + NL
        + "3" + NL + "frame 2  E = -76.4210" + NL
        + "O 0.0 0.0 0.120" + NL + "H 0.0 0.760 -0.470" + NL + "H 0.0 -0.760 -0.470" + NL,
        encoding="utf-8")

    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    from orcamgr.gui.window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow()
    page = win.view.page()
    console = []

    def hook(self, level, msg, line, source):
        console.append(f"[{level}] {str(source).split('/')[-1]}:{line} {msg}")
    type(page).javaScriptConsoleMessage = hook

    win.resize(1680, 1000)
    win.show()

    # Seed a queue that exercises the same shapes the mockup draws: a row, and a
    # cell with BOTH panes (chart + output). "h2o" already has an .out in the
    # workspace, so NB.seed replays its convergence history into the trackers.
    SEED = """
      (async function () {
        var wait = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
        var NL = String.fromCharCode(10);
        var xyz = "O 0.0 0.0 0.1173" + NL + "H 0.0 0.7572 -0.4692" + NL + "H 0.0 -0.7572 -0.4692";
        function calc(name, raw) {
          return JSON.stringify({
            name: name, kind: "opt", charge: 0, multiplicity: 1,
            geometry_source: "direct", xyz: xyz, ref_name: "",
            is_raw: !!raw,
            raw_text: raw ? "! B3LYP def2-SVP Opt" + NL + "* xyz 0 1" + NL + xyz + NL + "*" + NL : "",
            config: {kind: "opt", functional: "wB97X-D4", basis_set: "def2-TZVP",
                     ri_approximation: "RIJCOSX", scf_convergence: "TightSCF",
                     calculation_type: "TightOpt", options: "", maxcore_mb: 2400,
                     nprocs: 6, max_iter: 0, solvation: {model: "", solvent: ""},
                     basis_assignments: []},
            state: "pending", message: ""});
        }
        // four that will finish (against the throwaway orca.exe they fail at
        // once, which is still a Done group), then three left queued
        // NOT named after the fixture folder: running the queue would write into
        // ws/h2o/ and clobber the .out the report pass reads
        for (var i = 0; i < 4; i++) await NB.call("add_calc", calc("run-" + i, false));
        await NB.poll();
        await NB.call("run_queue", "[]");
        // wait for the run to actually stop before queueing more, or the engine
        // starts them too and there is no Queued group left to measure
        for (var t = 0; t < 40; t++) {
          await wait(400);
          await NB.poll();
          if (!NB.running) break;
        }
        for (var j = 0; j < 3; j++) await NB.call("add_calc", calc("queued-" + j, j === 1));
        await NB.poll();
        await NB.seed("h2o");
        NB.stream.render();
        await wait(700);
        return [].map.call(document.querySelectorAll(".qrow"), function (r) {
          return r.dataset.state; }).join(",") + " | groups=" +
          document.querySelectorAll(".qgroup").length + " running=" + NB.running;
      })()
    """

    state = {}
    json_out = json.dumps(str(ws / "h2o" / "h2o.out"))

    def seeded(n):
        QTimer.singleShot(900, lambda: measure(page, "real", jobs_done))

    def jobs_done(res):
        state["jobs"] = res
        # h2o has a real .out in the throwaway workspace, so the report has
        # something to render
        page.runJavaScript(
            'NB.results.openPath(' + json_out + ').then(function(){'
            '  document.querySelector(\"[data-r=\\"showall\\"]\").click(); })',
            lambda _r: QTimer.singleShot(2600,
                lambda: measure(page, "real", report_done, REPORT)))

    def report_done(res):
        done(merge(state["jobs"], res))

    def done(res):
        res["__console"] = console
        res["__panes"] = None
        OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print("wrote", OUT, "landmarks:", len([k for k in res if not k.startswith("__")]))
        missing = [k for k in res if not k.startswith("__") and res[k] is None]
        if missing:
            print("  NOT FOUND in web/next:", missing)
        if console:
            print("  CONSOLE:", console)
        app.quit()

    def go(ok):
        if ok:
            QTimer.singleShot(2200, lambda: page.runJavaScript(SEED, seeded))
    win.view.loadFinished.connect(go)
    QTimer.singleShot(90000, app.quit)
    app.exec()


run_mock() if MODE == "mock" else run_real()
