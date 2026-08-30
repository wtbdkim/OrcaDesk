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
  set_wallpaper_image(dataUri: string): Promise<string>;   // WallpaperResult JSON: {ok, stored}; stored=false when ""/invalid/oversize (cleared)
  get_wallpaper_image(): Promise<string>;                   // bare data-URI string ("" = none)
  autodetect_orca(): Promise<string>;          // AutodetectResult JSON (mutates settings on success)
  // MLIP environments (separate from ORCA; one env per MLIP)
  pick_mlip_python(): Promise<string>;         // raw path string
  add_mlip_env(payloadJson: string): Promise<string>;  // MlipStatusPayload | {error}
  remove_mlip_env(id: string): Promise<string>;        // MlipStatusPayload JSON
  check_mlip(id: string): Promise<string>;             // MlipStatusPayload JSON ("" = all)
  get_mlip_status(): Promise<string>;          // MlipStatusPayload JSON
  // one-click MLIP environment creation (venv + torch for a device + a backend)
  get_mlip_install_options(): Promise<string>; // MlipInstallOptionsPayload JSON
  get_mlip_install_status(): Promise<string>;  // MlipInstallPayload JSON
  create_mlip_env(payloadJson: string): Promise<string>;  // MlipInstallPayload | {error}
  cancel_mlip_install(): Promise<string>;      // MlipInstallPayload JSON
  // CREST (runs in WSL; separate from ORCA)
  get_crest_status(): Promise<string>;         // CrestStatusPayload JSON
  check_crest(): Promise<string>;              // CrestStatusPayload JSON (re-probe)
  install_crest(distro: string): Promise<string>;      // CrestStatusPayload JSON ("" = first distro)
  set_crest_distro(distro: string): Promise<string>;   // CrestStatusPayload JSON
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
  parse_calc_output(name: string): Promise<string>;
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
  get_output_tail(name: string, max_lines: number): Promise<string>;
  export_conformers(name: string): Promise<string>;
  // 3D structure viewer (Results tab); both return FramesResult JSON:
  // {ok,title,frames:[{label,xyz,energy}]} | {ok:false,...}
  get_conformer_frames(name: string): Promise<string>;
  // FramesResult JSON; also returns "folder" (the picked path) on success, and
  // has a cancel branch: {ok:false, cancelled:true} when the picker is closed
  // (a deliberate dismissal, not an error)
  browse_xyz_folder(): Promise<string>;
  // viewer favorites (starred structures)
  get_favorites(source: string): Promise<string>;            // FavoritesResult JSON: {ok, labels[]}
  toggle_favorite(source: string, label: string, on: boolean): Promise<string>;  // FavoritesResult JSON: {ok, labels[]}
  export_frames(destKind: string, dest: string, framesJson: string): Promise<string>;  // ExportResult JSON: {ok, count, folder}
  // run / results
  get_free_energy_profile(): Promise<string>;
  check_overwrite_conflicts(): Promise<string>;
  has_existing_output(name: string): Promise<string>;
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

/* ---------- window.SCFGraph: scf_graph.js + progress_panels.js ----------
   One namespace, two files. scf_graph.js creates it (SCF/geo convergence
   graph); progress_panels.js loads next and extends it in place with the
   freq/TD-DFT/CREST step panels. */

interface SCFGraphAPI {
  SCFTracker: new () => any;
  GeoTracker: new () => any;
  /* the four below come from progress_panels.js */
  FreqTracker: new () => any;
  TddftTracker: new () => any;
  CrestTracker: new () => any;
  isScfIter(line: string): boolean;
  fmtClock(sec: number): string;
  targetFor(scfConvergence: string): number;
  renderSCFProgress(...args: any[]): string;
  renderSCFGraph(...args: any[]): string;
  renderGeoProgress(...args: any[]): string;
  renderGeoGraph(...args: any[]): string;
  renderFreqProgress(...args: any[]): string;
  renderTddftProgress(...args: any[]): string;
  renderCrestProgress(...args: any[]): string;
  setEtaMode(mode: string): void;
  setGeoMode(mode: string): void;
}

declare var SCFGraph: SCFGraphAPI;

/* both files also export for node-based testing */
declare var module: { exports: any };
declare function require(id: string): any;

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
