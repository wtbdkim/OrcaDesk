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
  // structure screening (Build tab; both over core/structure.py — read-only)
  check_structure(xyz: string, charge: number, multiplicity: number): Promise<string>;  // StructureCheck JSON
  compare_structures(reactantXyz: string, productXyz: string): Promise<string>;         // AtomOrder JSON
  // parsing
  // the Results tab's one file button: picks a result file and says how to read
  // it — {ok, path, route:"parse"|"structure"} | {ok:false, cancelled:true}
  pick_result_file(): Promise<string>;                        // PickedResultPayload JSON
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
  // 3D structure viewer (Results › Visual). The .xyz sets sitting with a result
  // — the CREST ensemble, the conformers/ export, a trajectory — discovered
  // rather than picked from a folder dialog. source is "calc:<name>"|"file:<path>"
  list_structure_sets(source: string): Promise<string>;       // StructureSetsResult JSON
  // frames for one of those sets: a folder of .xyz, or one (multi-frame) .xyz.
  // FramesResult JSON; "folder" is the favorites/export destination
  get_structure_frames(path: string): Promise<string>;
  // every result on disk under the workspace root, queued or not (newest first)
  list_workspace_results(): Promise<string>;                  // WorkspaceResultsResult JSON
  // viewer favorites (starred structures)
  get_favorites(source: string): Promise<string>;            // FavoritesResult JSON: {ok, labels[]}
  toggle_favorite(source: string, label: string, on: boolean): Promise<string>;  // FavoritesResult JSON: {ok, labels[]}
  export_frames(destKind: string, dest: string, framesJson: string): Promise<string>;  // ExportResult JSON: {ok, count, folder}
  // orbital / density cubes (orca_plot on a finished calc's .gbw). generate_cube
  // runs on a background thread: it returns the status, the UI polls
  // get_cube_status(), then fetches the cube once with get_cube_data() — the
  // ~3 MB payload must never ride the poll.
  // source is "calc:<name>" (a queued calc) or "file:<path>" (a result on disk)
  get_plot_options(source: string): Promise<string>;         // PlotOptionsResult JSON
  generate_cube(payloadJson: string): Promise<string>;       // CubeJob JSON; {source,kind,index,operator,grid}
  get_cube_status(): Promise<string>;                        // CubeJob JSON
  get_cube_data(): Promise<string>;                          // CubeDataResult JSON
  // Hand a generated file to the user's own programs instead of drawing it
  // in-app (P5). Refuses anything that is not a data format; failure is data.
  open_path_external(path: string): Promise<string>;          // OkResult JSON
  show_path_in_folder(path: string): Promise<string>;         // OkResult JSON
  // Write a parsed geometry into the run folder as <base>_structure.xyz, so it
  // can be handed to a program that reads files rather than payloads.
  save_structure_xyz(source: string, xyz: string): Promise<string>;  // SavedFileResult JSON
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
