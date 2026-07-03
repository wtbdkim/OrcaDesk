/* ============================================================
   Ambient declarations for the environment web/app.js runs in:
   Qt WebEngine's QWebChannel, the Python Bridge object, and the
   globals shared between app.js and scf_graph.js.

   Slot signatures mirror the @pyqtSlot declarations in
   orcamgr/gui/bridge.py (all slots take/return JSON *strings*;
   the decoded payload shapes live in web/types.js).
   ============================================================ */

/** The Python Bridge proxied over the QWebChannel. Every call resolves to
 *  the slot's string result (JSON for most slots — see web/types.js). */
interface OrcaBridge {
  // about / settings
  get_about(): Promise<string>;
  get_settings(): Promise<string>;
  save_settings(payloadJson: string): Promise<string>;
  autodetect_orca(): Promise<string>;          // AutodetectResult JSON (mutates settings on success)
  // MLIP environments (separate from ORCA; one env per MLIP)
  pick_mlip_python(): Promise<string>;         // raw path string
  add_mlip_env(payloadJson: string): Promise<string>;  // MlipStatusPayload | {error}
  remove_mlip_env(id: string): Promise<string>;        // MlipStatusPayload JSON
  check_mlip(id: string): Promise<string>;             // MlipStatusPayload JSON ("" = all)
  get_mlip_status(): Promise<string>;          // MlipStatusPayload JSON
  // file pickers / loaders
  pick_orca_executable(): Promise<string>;     // raw path string
  pick_workspace(): Promise<string>;           // raw path string
  // all four loaders share one LoadResult JSON envelope (cancel vs failure)
  load_xyz_file(): Promise<string>;            // LoadResult JSON
  load_xyz_path(path: string): Promise<string>; // LoadResult JSON
  load_inp_file(): Promise<string>;            // LoadResult JSON
  load_inp_path(path: string): Promise<string>; // LoadResult JSON
  load_choices(name: string): Promise<string>;
  // parsing
  parse_out_file(): Promise<string>;
  parse_out_path(path: string): Promise<string>;
  build_inp_preview(calcJson: string): Promise<string>;
  // queue
  add_calc(calcJson: string): Promise<string>;
  remove_calc(name: string): Promise<string>;
  clear_queue(): Promise<string>;
  reorder_calc(fromIdx: number, toIdx: number): Promise<string>;
  update_calc(oldName: string, calcJson: string): Promise<string>;
  get_queue(): Promise<string>;
  get_calc(name: string): Promise<string>;
  get_log(since: number): Promise<string>;
  get_inp(name: string): Promise<string>;
  get_graph_lines(name: string): Promise<string>;
  // run / results
  get_free_energy_profile(): Promise<string>;
  check_overwrite_conflicts(): Promise<string>;
  run_queue(skipNamesJson: string): Promise<string>;
  cancel_queue(): Promise<string>;
  stop_after_current(): Promise<string>;
  // phone-sync server
  get_server_status(): Promise<string>;
  get_connect_qr(): Promise<string>;
  start_server(): Promise<string>;
  stop_server(): Promise<string>;
}

/** Injected by Qt WebEngine when a QWebChannel is registered on the page. */
declare var qt: { webChannelTransport: unknown };

/** From qrc:///qtwebchannel/qwebchannel.js (loaded in index.html). */
declare class QWebChannel {
  constructor(
    transport: unknown,
    initCallback: (channel: { objects: { bridge: OrcaBridge } }) => void,
  );
}

/* ---------- scf_graph.js public surface (window.SCFGraph) ---------- */

interface SCFGraphAPI {
  SCFTracker: new () => any;
  GeoTracker: new () => any;
  FreqTracker: new () => any;
  TddftTracker: new () => any;
  isScfIter(line: string): boolean;
  targetFor(scfConvergence: string): number;
  renderSCFProgress(...args: any[]): string;
  renderSCFGraph(...args: any[]): string;
  renderGeoProgress(...args: any[]): string;
  renderGeoGraph(...args: any[]): string;
  renderFreqProgress(...args: any[]): string;
  renderTddftProgress(...args: any[]): string;
  setEtaMode(mode: string): void;
  setGeoMode(mode: string): void;
  SCF_TARGETS: Record<string, number>;
}

declare var SCFGraph: SCFGraphAPI;

/* scf_graph.js also exports for node-based testing */
declare var module: { exports: any };

/* ---------- window entry points called from Python (window.py) ---------- */

interface Window {
  SCFGraph: SCFGraphAPI;
  onInpDropped?: (path: string) => void;
  onXyzDropped?: (path: string) => void;
  onOutDropped?: (path: string) => void;
}

/* ---------- pragmatic DOM loosening ----------
   The UI reads .value/.checked/... directly off getElementById results in
   ~100 places. Under checkJs those are TS2339 on HTMLElement. Rather than
   sprinkle casts on every line (pure noise), widen the lookup return type
   to an HTMLElement that also carries the common form-control members.
   (Interface merging puts these overloads first, so they win.) */

interface ORCAFormElement extends HTMLElement {
  value: string;
  checked: boolean;
  disabled: boolean;
  selectedIndex: number;
  options: HTMLOptionsCollection;
  selectionStart: number | null;
  selectionEnd: number | null;
  setSelectionRange(start: number, end: number): void;
}

interface Document {
  getElementById(elementId: string): ORCAFormElement | null;
  querySelector(selectors: string): ORCAFormElement | null;
  querySelectorAll(selectors: string): NodeListOf<ORCAFormElement>;
}

interface HTMLElement {
  querySelector(selectors: string): ORCAFormElement | null;
  querySelectorAll(selectors: string): NodeListOf<ORCAFormElement>;
}
