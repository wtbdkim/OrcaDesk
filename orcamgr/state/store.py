"""
QueueStore — the single shared queue state that the PyQt app, the HTTP server,
and (through the server) the phone all read and write.

Deliberately framework-independent: no FastAPI, no PyQt imports here, so it can
be unit-tested on its own. It wraps a list of Calculation objects with a
threading.RLock so concurrent access (Qt UI thread + server worker thread) is
safe.

This is the option-1 design we agreed on: one in-memory queue object, shared
via a lock, no SQLite.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Optional

from ..core.queue import (
    GATES, KNOWN_KINDS, REF_POINTS, Calculation, CalcState, GeometrySource,
    QueueEngine, QueueCallbacks, gate_waiting, ref_point_problem,
    result_from_output, template_hess_problem, validate_result,
)
from ..core.input_generator import (CONSTRAINED_KINDS, JOB_PLACEHOLDER,
                                    SCAN_KINDS, StepConfig,
                                    constraint_index_error,
                                    constraint_linear_warning, is_level_text,
                                    raw_geometry_mismatch, template_config,
                                    template_job_error,
                                    template_option_conflicts)
from ..core.structure import parse_block
from ..core.runner import OrcaRunError
from ..core.procutil import process_matches
from ..paths import data_dir, user_data_root
# Wire-payload shapes (plain dicts at runtime; see schemas.py for the mirror
# contract with web/types.js)
from .schemas import (CalcFull, CalcSummary, LogLine, LogPayload,
                      QueueSnapshot, RunResources, SystemInputSummary,
                      SystemSummary, XyzFileEntry)
from .inpsets import MLIP_KEYS, mlip_config_from_text

# States whose calculations the user may still edit / reorder.
# PENDING: never run yet. CANCELLED: a deliberate user stop, not a failure, so
# it can be fixed and retried. BLOCKED: must stay editable because after its
# failed parent is removed the user has to re-point the reference at another
# calculation — locking it would strand it forever. FAILED is deliberately NOT
# editable (P24 revision): a failure locks the calc at the moment it fails —
# no edit, no implicit re-run; the only interactions are removal (×) and the
# explicit way back below. DONE is excluded too (a completed result is frozen).
# The one explicit way back from either is the apply_ops op rerun_calc, a new
# set put on it behind an overwrite warning. RUNNING is in flight.
EDITABLE_STATES = {CalcState.PENDING, CalcState.CANCELLED, CalcState.BLOCKED}
#: the states rerun_calc puts a new set on (behind an overwrite warning)
RERUNNABLE_STATES = (CalcState.DONE, CalcState.FAILED)
#: the states a rerun row (Calculation.rerun) still has not started in
_UNSTARTED = (CalcState.PENDING, CalcState.BLOCKED)


def is_rerun(c: Calculation) -> bool:
    """A DONE or FAILED calc put back to PENDING by rerun_calc that has not
    started since: the old output in its folder is one the user chose to
    overwrite, so it is neither offered nor given keep-existing
    (bridge._overwrite_conflicts, make_engine_factory). A row that started (or
    was cancelled) is not one."""
    return bool(getattr(c, "rerun", False)) and c.state in _UNSTARTED


class QueueOpError(ValueError):
    """An apply_ops batch was refused. ``index`` is the 0-based position of the
    operation that failed, or None when the batch as a whole was (a final
    state with a dangling reference while the queue runs)."""

    def __init__(self, message: str, index: "int | None"):
        super().__init__(message)
        self.index = index


def _new_pin() -> str:
    """A fresh 6-digit access PIN (cryptographically random, zero-padded)."""
    return f"{secrets.randbelow(1_000_000):06d}"


# calc.name becomes an on-disk folder name (workspace_root / name), so it must be
# validated at this single shared serialization point — the only client-side
# guard lives in the desktop JS and is bypassed by the phone/HTTP path.
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')
# ...and it is also what ORCA is invoked with. ORCA 6 splits its own argument on
# whitespace before handing it to orca_startup, so a space or an '&' in the file
# name makes every run of that calculation die in Startup ("Cannot open input
# file <first word>" / "no input files") — a FAILED calc, and FAILED is locked
# (P24), so the name can never be run again. The FOLDER may contain spaces —
# OrcaRunner.launch passes the bare file name from inside the folder, which is
# what makes a workspace under "C:\Users\John Smith" work.
#
# ',', ';' and '=' were measured as harmless on 6.1.1 and used to be allowed
# here, but that measurement only covered the argument ORCA parses ITSELF. ORCA
# also shells out to helper binaries and collects their output through a
# redirection it does NOT quote:
#     otool_gcp "<name>.gcp.in.tmp" -level R2SCAN3C > <name>.gcp.out
# cmd.exe ends a redirection target at its token delimiters — space, tab, ',',
# ';', '=' — so under a name like "(S,S)mol" gCP's result lands in a file called
# "(S", ORCA never finds "<name>.gcp.out", and the run dies with "Error
# (ORCA_SCF/GCP/Energy): Calculation of the gCP correction failed!" after the
# SCF is already paid for. Only the gCP-carrying composite methods hit it
# (r2SCAN-3c, B97-3c, HF-3c, PBEh-3c, and an explicit ! GCP(...)), which is why
# a plain functional/basis run hid this for so long. '(', ')' and "'" survive
# that parse and stay legal.
_ORCA_HOSTILE_CHARS = re.compile(r"[\s&,;=]")
# Control characters and lone surrogates are not typeable in the desktop's
# single-line input but arrive freely through calc_from_dict (the phone/HTTP
# path, a hand-edited session.json). A control character reaches mkdir and dies
# with a localized WinError 123, which stamps the calc FAILED — locked (P24);
# a surrogate cannot be encoded to UTF-8 at all and used to poison session
# autosave for the rest of the app's life.
_UNUSABLE_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
# Windows caps a path component at 255 characters, and the workspace path plus
# "{name}/{name}.inp" is spent twice over on top of it. The cap is here rather
# than at mkdir so the refusal is a sentence instead of a WinError 3.
_MAX_NAME_LEN = 120
_RESERVED_NAMES = {"con", "prn", "aux", "nul",
                   *(f"com{i}" for i in range(1, 10)),
                   *(f"lpt{i}" for i in range(1, 10))}


def _validate_calc_name(name: str, kind: str = "", restoring: bool = False) -> None:
    """Reject names that could escape the workspace or break on Windows.
    Allows Unicode (e.g. Korean) — only path-dangerous patterns are blocked.

    ``kind`` selects the ORCA-only rule below. It defaults to "" (apply it),
    because a caller that does not know the kind is building an ORCA calc or
    checking a name in the abstract, and the stricter answer is the safe one.

    ``restoring`` marks the session-restore path, where the name is a fact on
    disk rather than a proposal. The ORCA rule is a "this run will fail" rule,
    not a "this path is dangerous" rule, so applying it retroactively would
    silently EVICT calcs that predate a tightening (the ',' / ';' / '=' one
    below) from the queue at the next launch — deleting the user's record of a
    finished or failed run to enforce something only a NEW run has to obey.
    Every path-safety rule still applies; only the ORCA rule is grandfathered.
    """
    if _BAD_NAME_CHARS.search(name):
        raise ValueError('Name contains characters not allowed in a folder name: \\ / : * ? " < > |')
    # ORCA's constraint, applied where ORCA runs. The MLIP and CREST pipelines
    # pass the name as a subprocess argv element / a shell-quoted variable, so a
    # space is genuinely fine for them — and refusing it for every kind would
    # DROP an existing MLIP/CREST calculation from the queue at the next launch
    # (load_session skips entries that fail validation).
    if (not restoring and not (kind or "").startswith(("mlip", "crest"))
            and _ORCA_HOSTILE_CHARS.search(name)):
        raise ValueError("Name must not contain a space or one of & , ; = — ORCA "
                         "cannot open an input file whose name has one, and its "
                         "gCP helper writes its result to the wrong file.")
    if _UNUSABLE_NAME_CHARS.search(name):
        raise ValueError("Name contains a character that cannot be used in a "
                         "folder name.")
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"Name is too long ({len(name)} characters); "
                         f"keep it under {_MAX_NAME_LEN}.")
    if ".." in name:
        raise ValueError("Name must not contain '..'.")
    if name.endswith("."):
        raise ValueError("Name must not end with a dot.")
    if name.split(".")[0].lower() in _RESERVED_NAMES:
        raise ValueError(f"'{name}' uses a name reserved by Windows.")


def _flatten_choices(value) -> list:
    """Flatten a choices JSON (dict of categories, or list) into a flat list,
    skipping metadata keys that start with '_'."""
    out = []
    if isinstance(value, list):
        out.extend(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
                out.extend(v)
            elif isinstance(v, dict):
                if "keywords" in v and isinstance(v["keywords"], list):
                    out.extend(v["keywords"])
                elif "aliases" in v and isinstance(v["aliases"], list):
                    out.extend(v["aliases"])
                else:
                    out.extend(_flatten_choices(v))
    seen = set()
    flat = []
    for item in out:
        if isinstance(item, str) and item not in seen:
            seen.add(item)
            flat.append(item)
    return flat


def load_all_choices() -> dict:
    """Read every data/*.json and return {name: [flat list of options]}."""
    names = ["functionals", "basis_sets", "scf_convergences",
             "ri_approximations", "solvents", "calculation_types", "options",
             "mace_models"]
    result = {}
    for name in names:
        path = data_dir() / f"{name}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result[name] = _flatten_choices(data)
        except (OSError, json.JSONDecodeError):
            result[name] = []
    return result


def load_choice_groups(name: str) -> dict:
    """Read one data/<name>.json and return it grouped: {category: [items]}.

    Used by the desktop bridge (which shows grouped dropdowns). Shares the same
    flatten rules as load_all_choices so PC and phone always see identical
    options. Metadata keys (leading '_') are skipped.
    """
    path = data_dir() / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    groups: dict = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if k.startswith("_"):
                continue
            items = _flatten_choices(v if isinstance(v, (list, dict)) else [])
            if items:
                groups[k] = items
    elif isinstance(data, list):
        flat = _flatten_choices(data)
        if flat:
            groups["all"] = flat
    return groups


def calc_to_dict(c: Calculation, waiting: bool = False) -> CalcSummary:
    """Serialize a Calculation to a plain dict for JSON responses.
    ``waiting`` is derived by the caller that sees the whole queue (snapshot:
    core.queue.gate_waiting); a lone calc cannot know it."""
    return CalcSummary(
        name=c.name,
        kind=c.kind,
        charge=c.charge,
        multiplicity=c.multiplicity,
        geometry_source=c.geometry_source.value
        if isinstance(c.geometry_source, GeometrySource) else str(c.geometry_source),
        ref_name=c.ref_name,
        is_raw=c.is_raw,
        state=c.state.value if isinstance(c.state, CalcState) else str(c.state),
        message=c.message,
        output_path=c.output_path,
        # SCF convergence setting (used by the live graph to place the target line)
        scf_convergence=getattr(c.config, "scf_convergence", "") if c.config else "",
        # a compact one-line summary for list rows
        meta=_meta_line(c),
        # backend-specific row detail as EXPLICIT fields (not just inside meta):
        # the desktop escapes user-facing strings before innerHTML, so it needs
        # the raw values, not the pre-joined line
        mlip_model=(getattr(c.config, "mlip_model", "") if c.config else "")
        if c.kind.startswith("mlip") else "",
        crest_method=(getattr(c.config, "crest_method", "") if c.config else "")
        if c.kind.startswith("crest") else "",
        # only while the process lives: once the calc leaves RUNNING its state
        # says everything, and the flag is not reset on the way out
        faulted=bool(c.faulted) and c.state == CalcState.RUNNING,
        # the queue editor's graph (core.queue.Calculation)
        system=c.system,
        node=c.node,
        col=c.col,
        row=c.row,
        after=c.after,
        gate=c.gate,
        auto_pass=c.auto_pass,
        approved=c.approved,
        ref_point=c.ref_point,
        set_name=c.set_name,
        waiting=bool(waiting),
    )


def _meta_line(c: Calculation) -> str:
    if c.geometry_source == GeometrySource.REFERENCE or c.geometry_source == "reference":
        src = f"ref {c.ref_name}"
    else:
        src = ".xyz"
    # MLIP calcs don't use charge/multiplicity; show the MACE model instead.
    if c.kind.startswith("mlip"):
        model = getattr(c.config, "mlip_model", "") if c.config else ""
        return f"{c.kind} · {src} · {model or 'MACE'}"
    # CREST calcs use charge/multiplicity too; also show the tight-binding method.
    if c.kind.startswith("crest"):
        method = getattr(c.config, "crest_method", "") if c.config else ""
        return f"{c.kind} · {src} · {method or 'gfn2'} · q{c.charge} m{c.multiplicity}"
    return f"{c.kind} · {src} · q{c.charge} m{c.multiplicity}"


def constraint_warning(calc) -> str:
    """What the queue says, without refusing, about a calculation's geometry
    constraints when it is added or edited: a valued angle ORCA will stop on
    as a linear bend (input_generator.constraint_linear_warning). The start
    angle is judged when the coordinates are in hand, the target alone for a
    referenced geometry. A level block's constraints (its options) are judged
    the same way; any other raw text is the user's own. "" when there is
    nothing to say."""
    cfg = calc.config
    if calc.is_raw and not is_level_text(calc.raw_text):
        return ""
    if not cfg.geom_constraints or cfg.kind not in CONSTRAINED_KINDS:
        return ""
    if calc.geometry_source == GeometrySource.DIRECT:
        atoms = parse_block(calc.xyz or "")
        if atoms:
            return constraint_linear_warning(cfg.geom_constraints, atoms)
    return constraint_linear_warning(cfg.geom_constraints)


def raw_geometry_warning(calc) -> str:
    """What the queue says, without refusing, when a hand-written input's own
    * xyz block is not the geometry stored with it — the coordinates that
    were given will not be the ones that run
    (input_generator.raw_geometry_mismatch). "" when there is nothing to say."""
    if not calc.is_raw or calc.geometry_source != GeometrySource.DIRECT:
        return ""
    return raw_geometry_mismatch(calc.raw_text or "", calc.xyz or "")


def calc_from_dict(d: dict, restoring: bool = False) -> Calculation:
    """
    Build a Calculation from a client payload dict (used by both the HTTP
    server and the Qt bridge). PyQt-independent so it lives here.

    ``restoring`` is passed straight to _validate_calc_name — see its docstring
    for why the session-restore path is held to a weaker name rule.
    """
    cfg = StepConfig.from_dict(d.get("config", {}))
    src = d.get("geometry_source", "direct")
    # This is the trust boundary for phone/HTTP payloads too: wrong-typed
    # values must surface as ValueError (the callers' 400/{"error"} path),
    # never as AttributeError/OverflowError escaping the slot or endpoint.
    raw_name = d.get("name", "")
    if not isinstance(raw_name, str):
        raise ValueError("Calculation name must be a string.")
    name = raw_name.strip()
    if not name:
        raise ValueError("Calculation name is required.")
    kind = str(d.get("kind", "") or "").strip()
    if kind not in KNOWN_KINDS:
        raise ValueError(
            f"Unknown calculation type '{kind}'. Supported types: "
            + ", ".join(sorted(KNOWN_KINDS)) + ".")
    _validate_calc_name(name, kind, restoring=restoring)
    # charge/multiplicity are f-string-interpolated into the .inp's
    # "* xyz {charge} {multiplicity}" line. int() already blocks string
    # injection, but not absurd magnitudes — clamp to physically generous
    # ranges so a bogus client payload can't emit a nonsense input file.
    try:
        charge = max(-100, min(100, int(d.get("charge", 0))))
        multiplicity = max(1, min(200, int(d.get("multiplicity", 1))))
    except OverflowError:                    # int(float("inf")) from JSON 1e999
        raise ValueError("charge/multiplicity must be finite integers.")

    def _s(key: str) -> str:
        v = d.get(key, "")
        return v if isinstance(v, str) else ""

    # A config sent without its own kind (an inp-set template carries none —
    # its text is the input) describes this calc's kind, not StepConfig's
    # default "opt".
    if "kind" not in (d.get("config") or {}):
        cfg.kind = kind
    is_raw = bool(d.get("is_raw", False))
    # An MLIP inp set is `key value` lines, not an ORCA input: the template
    # sets the MLIP fields of the config (inpsets.mlip_config_from_text) and
    # the calc runs as an ordinary MLIP calc. The text is kept as raw_text,
    # where the queue editor shows it; is_raw is what says it is not used.
    if is_raw and kind.startswith("mlip"):
        # The text is the whole MLIP setting: a key it leaves out is the
        # default, not whatever an edited calc's old config held (update_calc
        # merges the old config in before this runs).
        defaults = StepConfig()
        for field in MLIP_KEYS.values():
            setattr(cfg, field, getattr(defaults, field))
        for field, value in mlip_config_from_text(_s("raw_text")).items():
            setattr(cfg, field, value)
        is_raw = False
    # An ORCA inp set is a level: its `!` line holds {{JOB}}, filled from this
    # calc's kind at launch (input_generator.render_template_text). A kind
    # with no job keyword is refused HERE, when it is added or its Type is
    # changed -- at launch it would be a FAILED calc, and FAILED is locked
    # (P24). Never on restore, which must not evict a run's record.
    if (not restoring and is_raw and JOB_PLACEHOLDER in _s("raw_text")
            and template_job_error(kind)):
        raise ValueError(template_job_error(kind))
    # A level block's config is its OPTIONS (constraints, scan, MaxIter, IRC /
    # Freq / NMR / TD-DFT / NEB settings, extra keywords, solvation, SCF),
    # merged into the level at launch by render_template_text; its kind is
    # the block's own, and an IRC Hessian it does not name is "auto".
    level = is_raw and is_level_text(_s("raw_text"))
    if level:
        cfg = template_config(d.get("config"), kind)
    graph = _graph_fields(d, restoring)
    # A text that reads a parent's Hessian ({{HESS}}, or an inp-set IRC whose
    # %irc block reads one) with no parent to take it from could only fail at
    # launch -- refused here, never on restore (the same rule as above).
    if not restoring and is_raw:
        err = template_hess_problem(
            name, kind, _s("raw_text"), graph.get("set_name", ""),
            graph.get("after", ""), src == "reference" and bool(_s("ref_name")),
            cfg if level else None)
        if err:
            raise ValueError(err)
    # Options the level cannot take -- a key both set, constraints for a %geom
    # written on one line, an IRC "read" with no file -- are refused when the
    # block is added or edited (template_option_conflicts; the engine BLOCKs
    # on the same answer before launch). Never on restore.
    if not restoring and level and not template_job_error(kind):
        err = template_option_conflicts(_s("raw_text"), kind, cfg)
        if err:
            raise ValueError(f"'{name}': {err}")

    # A constraint naming an atom the coordinates do not have is refused when it
    # is ADDED, not at launch: build_input refuses it too, but by then the calc
    # is FAILED and locked (P24). Only for coordinates in hand — a reference's
    # geometry exists at run time, where build_input checks it — and never on
    # restore, which must not evict an entry for a rule a new run obeys.
    # (A valued angle ORCA will stop on as a linear bend is NOT refused: it is
    # a warning — constraint_warning below — and the user's call.)
    if (not restoring and (not is_raw or level) and src == "direct"
            and cfg.geom_constraints and cfg.kind in CONSTRAINED_KINDS):
        n_atoms = len(parse_block(_s("xyz")))
        err = constraint_index_error(cfg.geom_constraints, n_atoms) if n_atoms else ""
        if err:
            raise ValueError(err)
    # A scan coordinate is held to the same rule, for the same reason.
    if (not restoring and (not is_raw or level) and src == "direct"
            and cfg.geom_scan and cfg.kind in SCAN_KINDS):
        n_atoms = len(parse_block(_s("xyz")))
        err = constraint_index_error(cfg.geom_scan, n_atoms, "scan") if n_atoms else ""
        if err:
            raise ValueError(err)

    kind = d.get("kind", cfg.kind)
    return Calculation(
        name=name,
        kind=kind if isinstance(kind, str) else cfg.kind,
        config=cfg,
        charge=charge,
        multiplicity=multiplicity,
        geometry_source=GeometrySource(src),
        xyz=_s("xyz"),
        ref_name=_s("ref_name"),
        is_raw=is_raw,
        raw_text=_s("raw_text"),
        **graph,
    )


# Ids the queue editor mints for systems and nodes: short, printable, and
# never a path (they are not folder names, but they ride session.json and the
# wire, and a bounded alphabet keeps them from carrying anything else).
_GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{0,64}$")
_GRID_MAX = 10_000


#: the queue editor's Calculation fields (core.queue.Calculation)
GRAPH_FIELDS = ("system", "node", "col", "row", "after", "gate", "auto_pass",
                "approved", "ref_point", "set_name")


def keep_graph_fields(d: dict, existing: "Optional[Calculation]") -> dict:
    """``d`` with every graph field it does not mention taken from
    ``existing``. A whole-calc edit from a client that knows nothing of the
    queue editor (the guided builder's update_calc, sent without these keys)
    must not pull the calc out of its lane or cut its edge."""
    if existing is None or not isinstance(d, dict):
        return d
    out = dict(d)
    for k in GRAPH_FIELDS:
        if k not in out:
            out[k] = getattr(existing, k)
    return out


def _graph_fields(d: dict, restoring: bool) -> dict:
    """The queue editor's fields of a calc payload (core.queue.Calculation:
    system, node, col, row, after, gate, auto_pass, approved, ref_point,
    set_name), checked at the trust boundary. A missing key is "standalone, as
    today". A wrong-typed or out-of-set value is a ValueError for a new
    payload; on restore it degrades to the default instead, because evicting a
    calculation from the queue over a layout field would cost the user the
    record of a run (the rule _validate_calc_name's ``restoring`` follows)."""
    def bad(msg: str):
        if not restoring:
            raise ValueError(msg)

    def ident(key: str) -> str:
        v = d.get(key, "")
        if v is None or v == "":
            return ""
        if isinstance(v, str) and _GRAPH_ID_RE.match(v.strip()):
            return v.strip()
        bad(f"'{key}' must be up to 64 letters, digits or - _ . :")
        return ""

    def grid(key: str) -> int:
        v = d.get(key, 0)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            if v not in (None, 0):
                bad(f"'{key}' must be a whole number.")
            return 0
        try:
            return max(0, min(_GRID_MAX, int(v)))
        except (OverflowError, ValueError):
            bad(f"'{key}' must be a finite number.")
            return 0

    def flag(key: str, default: bool) -> bool:
        v = d.get(key, default)
        if isinstance(v, bool):
            return v
        if v is not None:
            bad(f"'{key}' must be true or false.")
        return default

    def choice(key: str, allowed, default: str) -> str:
        v = d.get(key, default)
        if v is None:
            return default
        if isinstance(v, str) and v in allowed:
            return v
        bad(f"'{key}' must be one of: " + ", ".join(repr(a) for a in allowed) + ".")
        return default

    after = d.get("after", "")
    if not isinstance(after, str):
        if after is not None:
            bad("'after' must be a calculation name.")
        after = ""
    set_name = d.get("set_name", "")
    set_name = set_name.strip()[:200] if isinstance(set_name, str) else ""
    return dict(
        system=ident("system"), node=ident("node"),
        col=grid("col"), row=grid("row"),
        after=after.strip(),
        gate=choice("gate", GATES, "auto"),
        auto_pass=flag("auto_pass", True),
        approved=flag("approved", False),
        ref_point=choice("ref_point", REF_POINTS, ""),
        set_name=set_name,
    )


# ---- systems (the queue editor's lanes) ------------------------------------
# A system is one molecule: {id, name, charge, multiplicity, inputs: [{label,
# xyz}]}. Its name is the prefix of every calculation name in its lane; its
# inputs are the structures a lane's calcs may start from (a calc keeps its
# own copy in xyz — the system is where the editor offers them).

_MAX_SYSTEM_INPUTS = 50
_MAX_LABEL = 120


def strip_xyz_header(text: str) -> str:
    """The coordinate block of an .xyz text: the two header lines (atom count,
    comment) dropped when the first line is a bare count; anything else is
    returned as given (already a block). Line endings normalized."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = lines[0].strip().lstrip("﻿") if lines else ""
    if len(lines) >= 2 and first.isdigit():
        lines = lines[2:]
    return "\n".join(lines).strip("\n")


def new_system_id() -> str:
    return "sys-" + secrets.token_hex(4)


def system_from_dict(d, restoring: bool = False) -> dict:
    """A validated system dict from a client payload (or the session file).
    A missing id is minted. The name obeys the calc-name rules — it is the
    prefix of the lane's calculation names, so a name a calculation could not
    carry is refused here rather than at every add (ORCA's rule too, unless
    restoring; see _validate_calc_name). Charge/multiplicity are clamped like a
    calc's; an input's xyz is kept as a coordinate block."""
    if not isinstance(d, dict):
        raise ValueError("A system must be an object.")
    sid = d.get("id") or new_system_id()
    if not isinstance(sid, str) or not sid.strip() or not _GRAPH_ID_RE.match(sid.strip()):
        raise ValueError("A system id is 1 to 64 letters, digits or - _ . :")
    name = d.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A system needs a name.")
    name = name.strip()
    _validate_calc_name(name, "", restoring=restoring)
    try:
        charge = max(-100, min(100, int(d.get("charge", 0))))
        multiplicity = max(1, min(200, int(d.get("multiplicity", 1))))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("A system's charge/multiplicity must be whole numbers.")
    raw_inputs = d.get("inputs", [])
    if raw_inputs is None:
        raw_inputs = []
    if not isinstance(raw_inputs, list):
        raise ValueError("A system's inputs must be a list.")
    if len(raw_inputs) > _MAX_SYSTEM_INPUTS:
        raise ValueError(f"A system holds at most {_MAX_SYSTEM_INPUTS} inputs.")
    inputs = []
    for k, inp in enumerate(raw_inputs, start=1):
        if not isinstance(inp, dict):
            raise ValueError("Each system input must be an object {label, xyz}.")
        label = inp.get("label", "")
        label = label.strip()[:_MAX_LABEL] if isinstance(label, str) else ""
        xyz = inp.get("xyz", "")
        if not isinstance(xyz, str):
            raise ValueError("A system input's xyz must be text.")
        inputs.append({"label": label or f"input {k}", "xyz": strip_xyz_header(xyz)})
    return {"id": sid.strip(), "name": name, "charge": charge,
            "multiplicity": multiplicity, "inputs": inputs}


def _xyz_stem(label: str) -> str:
    """The system name a dropped file gives: its file name without .xyz."""
    name = Path(str(label).replace("\\", "/")).name.strip()
    if name.lower().endswith(".xyz"):
        name = name[:-4].strip()
    if not name:
        raise ValueError("The file has no name to give the system.")
    return name


def _single_structure(text: str) -> str:
    """The one structure of an .xyz text as a coordinate block. A multi-frame
    file is refused — a system starts from one structure, and which frame was
    meant is the user's choice, not ours. A text without the .xyz header is
    taken as a bare block when it holds atoms."""
    from ..molview import iter_xyz_frames
    if not isinstance(text, str):
        raise ValueError("The file's content must be text.")
    text = text.lstrip("﻿")
    frames = list(iter_xyz_frames(text))
    if len(frames) > 1:
        raise ValueError(f"It holds {len(frames)} structures; a system starts "
                         "from one. Split the file, or open it in the viewer "
                         "and save the frame you want.")
    first = text.strip().split("\n", 1)[0].strip()
    if not frames and first.isdigit():
        raise ValueError(f"Its first line says {first} atoms, and the file does "
                         "not hold them.")
    if frames:
        # Coordinate lines left after the one frame its header counts: the
        # header undercounts, and taking the frame would drop atoms silently.
        lines = text.splitlines()
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip() not in ("", ">"))
        count = int(lines[start].split()[0])
        extra = parse_block("\n".join(lines[start + count + 2:]))
        if extra:
            raise ValueError(
                f"Its first line says {count} atoms, but it holds "
                f"{count + len(extra)} coordinate lines. Fix the atom count.")
    coords = strip_xyz_header(frames[0][1]) if frames else strip_xyz_header(text)
    if not parse_block(coords):
        raise ValueError("No coordinates found in it.")
    return coords


_CHARGE_RE = re.compile(r"(?<![A-Za-z])(?:charge|chrg|chg)\s*[=:]?\s*([+-]?\d+)(?![\d.])", re.I)
_MULT_RE = re.compile(r"(?<![A-Za-z])(?:multiplicity|mult)\s*[=:]?\s*(\d+)(?![\d.])", re.I)
_UHF_RE = re.compile(r"(?<![A-Za-z])uhf\s*[=:]?\s*(\d+)(?![\d.])", re.I)


def xyz_charge_mult(text: str) -> "tuple[Optional[int], Optional[int]]":
    """The charge and multiplicity an .xyz file's comment line states, each
    None when it states none: ``charge=1 mult=2``, ``Charge: -1
    Multiplicity: 2``, ``chrg 1``; xtb's ``uhf=N`` (unpaired electrons) reads
    as multiplicity N+1 when no multiplicity is given. Only the comment line
    of a file with the .xyz header is read -- a bare coordinate block has
    none -- and a value no molecule can have (multiplicity 0, a charge past
    +-50) is not read at all, never "fixed" into one it might."""
    lines = str(text or "").lstrip("﻿").splitlines()
    head = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if head is None or not lines[head].strip().split()[0].isdigit() or head + 1 >= len(lines):
        return None, None
    comment = lines[head + 1]
    charge = mult = None
    m = _CHARGE_RE.search(comment)
    if m and abs(int(m.group(1))) <= 50:
        charge = int(m.group(1))
    m = _MULT_RE.search(comment)
    if m:
        mult = int(m.group(1))
    else:
        m = _UHF_RE.search(comment)
        if m:
            mult = int(m.group(1)) + 1
    if mult is not None and not 1 <= mult <= 20:
        mult = None
    return charge, mult


def xyz_file_entries(paths) -> "list[XyzFileEntry]":
    """Several .xyz files read for the queue editor (a multi-pick, a drop):
    per path its file name, the system name its stem gives, its one
    structure as a coordinate block and the charge / multiplicity its comment
    line states (xyz_charge_mult) -- or why it cannot start a row (an
    unreadable file, several frames, no coordinates, a header that counts
    fewer atoms than follow it). Read utf-8-sig (a BOM ORCA would refuse)."""
    out: "list[XyzFileEntry]" = []
    for p in paths or []:
        path = str(p)
        label = Path(path.replace("\\", "/")).name
        entry = XyzFileEntry(path=path, label=label, name="", xyz="", error="",
                             charge=None, multiplicity=None)
        try:
            entry["name"] = _xyz_stem(path)
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
            entry["xyz"] = _single_structure(text)
            entry["charge"], entry["multiplicity"] = xyz_charge_mult(text)
        except OSError as e:
            entry["error"] = e.strerror or str(e)
        except ValueError as e:
            entry["error"] = str(e)
        out.append(entry)
    return out


def system_summary(s: dict) -> SystemSummary:
    """The polled form of a system: its inputs' labels, not their coordinates
    (get_system serves those)."""
    return SystemSummary(
        id=s["id"], name=s["name"], charge=s["charge"],
        multiplicity=s["multiplicity"],
        inputs=[SystemInputSummary(label=i["label"]) for i in s["inputs"]])


# ---- session persistence (autosave / restore) --------------------------
# The whole queue is mirrored to a JSON file so closing ORCAdesk does not lose
# it, and a calculation left RUNNING (its detached ORCA still going) can be
# reattached on the next launch.
#
# Schema 2: a level block's config holds its options (render_template_text
# reads them). A schema-1 file's level blocks carry config nobody chose as
# options, and load_session reads it as none.
SESSION_SCHEMA = 2


def _session_file():
    return user_data_root() / "session.json"


def calc_to_session_dict(c: Calculation) -> CalcFull:
    """Full-fidelity serialization of a Calculation for the session file
    (unlike calc_to_dict, which is the compact UI snapshot)."""
    gs = c.geometry_source.value if isinstance(c.geometry_source, GeometrySource) else str(c.geometry_source)
    st = c.state.value if isinstance(c.state, CalcState) else str(c.state)
    return CalcFull(
        name=c.name,
        kind=c.kind,
        config=c.config.to_dict() if c.config else {},
        charge=c.charge,
        multiplicity=c.multiplicity,
        geometry_source=gs,
        xyz=c.xyz,
        ref_name=c.ref_name,
        is_raw=c.is_raw,
        raw_text=c.raw_text,
        state=st,
        message=c.message,
        output_path=c.output_path,
        pid=c.pid,
        create_time=c.create_time,
        system=c.system,
        node=c.node,
        col=c.col,
        row=c.row,
        after=c.after,
        gate=c.gate,
        auto_pass=c.auto_pass,
        approved=c.approved,
        ref_point=c.ref_point,
        set_name=c.set_name,
        rerun=bool(c.rerun),
    )


def calc_from_session_dict(d: dict) -> Calculation:
    """Rebuild a Calculation from the session file, restoring runtime state
    (state/message/output_path/pid) on top of calc_from_dict."""
    c = calc_from_dict(d, restoring=True)   # config + geometry + name validation
    st = d.get("state", "pending")
    try:
        c.state = CalcState(st)
    except ValueError:
        c.state = CalcState.PENDING
    # type-checked like output_path/pid below: it rides the CalcSummary wire
    # declared `str`, and a dict from a hand-edited session.json rendered as
    # "[object Object]" in the queue row
    msg = d.get("message", "")
    c.message = msg if isinstance(msg, str) else ""
    out = d.get("output_path", "") or ""
    c.output_path = out if isinstance(out, str) else ""
    # pid/create_time flow into psutil.Process(int(pid)) during reconciliation;
    # a wrong-typed value (hand-edited/corrupted session) must degrade to
    # "no recorded process", not crash every startup (P32)
    pid = d.get("pid")
    try:
        c.pid = int(pid) if pid is not None else None
    except (TypeError, ValueError, OverflowError):
        c.pid = None
    ct = d.get("create_time")
    try:
        c.create_time = float(ct) if ct is not None else None
    except (TypeError, ValueError):
        c.create_time = None
    # restored here only, never read by calc_from_dict: no client can hand a
    # new calc the overwrite answer (a rerun row skips keep-existing)
    c.rerun = d.get("rerun") is True
    return c


def _restore_systems(entries) -> "list[dict]":
    """The systems of a session file, each re-validated (restoring rules);
    a corrupt entry or a duplicate id/name is skipped on its own, never the
    whole list (P32). Anything but a list reads as none."""
    if not isinstance(entries, list):
        return []
    out: list[dict] = []
    ids: set[str] = set()
    names: set[str] = set()
    for d in entries:
        try:
            s = system_from_dict(d, restoring=True)
        except Exception:
            continue
        if not (isinstance(d, dict) and d.get("id")):
            continue     # an id is minted only for a NEW system
        if s["id"] in ids or s["name"].casefold() in names:
            continue
        ids.add(s["id"])
        names.add(s["name"].casefold())
        out.append(s)
    return out


def _parse_if_exists(calc):
    """Parse a calc's on-disk output if present, via the engine's own kind
    dispatch (result_from_output — one dispatch for engine and reconciliation).
    Returns None on any read/parse error so reconciliation can fall back to
    FAILED rather than crash."""
    from pathlib import Path
    path = getattr(calc, "output_path", "") or ""
    try:
        # is_file(), not exists(): Windows resolves device names like "CON" to
        # the console, which exists() passes and open() then blocks on forever
        # — a corrupted session.json must not hang startup (P32)
        if not (path and Path(path).is_file()):
            return None
        return result_from_output(calc)
    except Exception:
        return None


def _auto_export_crest(calc) -> None:
    """Best-effort split of a finished CREST search's ensemble into per-conformer
    ``.xyz`` files under ``conformers/`` (mirrors the engine's finish-time export,
    for a search judged DONE at startup that never went through the engine). Pure
    file I/O, all errors swallowed — reconciliation must never fail on it. Lazy
    import keeps state/ free of the crest package at import time."""
    from pathlib import Path
    path = getattr(calc, "output_path", "") or ""
    if not path:
        return
    try:
        from ..crest.export import export_conformers
        calc_dir = Path(path).parent
        export_conformers(calc_dir / "crest_conformers.xyz",
                          calc_dir / "conformers", calc.name)
    except Exception:
        pass


def _crest_calc_alive(calc) -> bool:
    """Transport-aware liveness for a RUNNING crest_* calc. Under WSL the pid is
    a Linux one that psutil (process_matches) cannot see, so the check goes
    through the CrestRunner (kill -0 + start-time guard) on the target persisted
    at launch in config.crest_env_id. Locally that pid IS a native one, but the
    same check is used rather than psutil: one code path, and the start-time
    comparison against /proc field 22 is the same guard either way. Lazy import
    keeps state/ free of the crest package at import time."""
    if not getattr(calc, "pid", None):
        return False
    distro = (getattr(calc.config, "crest_env_id", "") or "").strip() if calc.config else ""
    if not distro:
        # A session written before the target was persisted (or by an older
        # build) has nothing here. On a local transport there is only ever one
        # place the job can be, so the answer is knowable; under WSL it is not,
        # and an unknown distro still means "judge it from its files".
        from ..crest.shell import LOCAL_TARGET, is_local
        if not is_local():
            return False
        distro = LOCAL_TARGET
    try:
        from ..crest.runner import CrestRunner
        runner = CrestRunner(distro)
        runner.adopt(calc.pid, calc.create_time)
        return runner.is_alive()
    except Exception:
        return False


def reconcile_calcs(calcs: "list[Calculation]") -> None:
    """Square a freshly loaded session with reality:

    * A calc persisted as RUNNING whose process is genuinely still alive keeps
      its RUNNING state (it will be reattached when the queue resumes).
    * A calc persisted as RUNNING whose process is gone is judged from its .out:
      terminated normally + valid -> DONE, otherwise FAILED (interrupted).

    DONE calcs are deliberately NOT re-parsed here — their ParseResult isn't
    persisted, but the only consumers (geometry references in QueueEngine, and
    the free-energy profile) parse it on demand, so eagerly reading every DONE
    .out on the UI thread before the window paints would be a pure startup stall
    that scales with the restored queue size and .out file size.
    """
    for c in calcs:
        if c.state == CalcState.RUNNING:
            # CREST runs in WSL (Linux pid), so psutil can't judge it — use the
            # WSL-aware check. Everything else (ORCA) uses psutil process_matches.
            if c.kind.startswith("crest"):
                alive = _crest_calc_alive(c)
            else:
                alive = bool(c.pid and process_matches(c.pid, c.create_time))
            if alive:
                continue  # genuinely still running — reattach on resume
            c.pid = None
            c.create_time = None
            r = _parse_if_exists(c)
            if r is not None and r.terminated_normally:
                c.result = r
                try:
                    validate_result(c, r)
                    c.state = CalcState.DONE
                    c.message = "Completed (finished while ORCAdesk was closed)."
                    if c.kind.startswith("crest"):
                        _auto_export_crest(c)
                except OrcaRunError as e:
                    c.state = CalcState.FAILED
                    c.message = str(e)
            elif c.kind.startswith("mlip"):
                # An MLIP worker never survives shutdown (detach terminates it —
                # mlip/runner.py), so a still-RUNNING mlip calc here was stopped
                # deliberately with the app: CANCELLED (re-runnable), not
                # FAILED-locked. (The engine stamps this at detach too; this
                # covers the race where the app exits before that lands.)
                c.state = CalcState.CANCELLED
                c.message = "Stopped when ORCAdesk closed."
            else:
                c.state = CalcState.FAILED
                c.message = "Interrupted while ORCAdesk was closed."


class QueueStore:
    """Thread-safe container for the calculation queue + run state + log buffer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._calcs: list[Calculation] = []
        # the queue editor's lanes (system_from_dict dicts), in lane order;
        # persisted beside the calculations in session.json
        self._systems: list[dict] = []
        self._running = False
        # monotonically increasing version, bumped on every mutation so clients
        # can cheaply poll "did anything change?"
        self._version = 0
        # log buffer: list of (seq, level, message, calc); clients poll with ?since=
        self._log: list[tuple[int, str, str, str]] = []
        self._log_seq = 0
        # env id -> MLIP probe state, published by the desktop Bridge so the
        # phone's /api/run can resolve mlip_env_id == "" the same way (P4)
        self._mlip_readiness: dict = {}
        # the background engine + thread while a run is in progress
        self._engine = None
        self._thread: Optional[threading.Thread] = None
        # access token (6-digit PIN), generated once per app launch (at store
        # construction) — it lives for the whole app session, across server
        # stop/starts
        self._token = _new_pin()
        # connected phone clients: {client_id: last_seen_epoch}
        self._clients: dict[str, float] = {}
        self._client_ttl = 15.0   # seconds without heartbeat = considered gone
        # rerun_calc's undo stash: name -> the DONE/FAILED row it replaced, put
        # back by undo_rerun while the rerun has not started, and the BLOCKED
        # rows the rerun of a FAILED one freed, as (blocked row, freed row)
        # pairs. In memory only.
        self._rerun_stash: dict[str, Calculation] = {}
        self._rerun_freed: "dict[str, list[tuple[Calculation, Calculation]]]" = {}

    # ---- auth token ----
    @property
    def token(self) -> str:
        with self._lock:
            return self._token

    def check_token(self, supplied: Optional[str]) -> bool:
        """Constant-time-ish comparison of a supplied token against the PIN."""
        if not supplied:
            return False
        import hmac
        with self._lock:
            # compare as bytes: compare_digest on str requires ASCII and raises
            # TypeError otherwise — a non-ASCII token pasted on the phone must
            # be an auth failure, not an HTTP 500.
            return hmac.compare_digest(
                str(supplied).encode("utf-8"), self._token.encode("utf-8"))

    # ---- connected clients (phones) ----
    def heartbeat(self, client_id: str) -> None:
        """Record that a client is alive right now (prunes stale ones — without
        this, clients minting fresh ids per page load would grow the dict for as
        long as the server runs when nothing polls active_clients())."""
        import time
        now = time.time()
        if not client_id:
            return
        with self._lock:
            stale = [cid for cid, t in self._clients.items()
                     if now - t > self._client_ttl]
            for cid in stale:
                del self._clients[cid]
            self._clients[client_id] = now

    def active_clients(self) -> int:
        """Number of clients seen within the TTL window (prunes stale ones)."""
        import time
        now = time.time()
        with self._lock:
            stale = [cid for cid, t in self._clients.items()
                     if now - t > self._client_ttl]
            for cid in stale:
                del self._clients[cid]
            return len(self._clients)

    # ---- reads ----
    def snapshot(self) -> QueueSnapshot:
        """Full state for a client (queue list + running flag + version +
        the run's live resource occupancy)."""
        with self._lock:
            by_name = {c.name: c for c in self._calcs}
            return QueueSnapshot(
                running=self._running,
                version=self._version,
                calculations=[
                    calc_to_dict(c, waiting=bool(c.after) and gate_waiting(
                        c, by_name.get(c.after)))
                    for c in self._calcs],
                resources=self.run_resources(),
                systems=[system_summary(s) for s in self._systems],
            )

    def get_system(self, system_id: str) -> "Optional[dict]":
        """One system in full (its inputs' coordinates included), or None."""
        with self._lock:
            s = next((s for s in self._systems if s["id"] == system_id), None)
            return copy.deepcopy(s) if s is not None else None

    def systems(self) -> "list[dict]":
        with self._lock:
            return copy.deepcopy(self._systems)

    def run_resources(self) -> RunResources:
        """Cores/memory the in-flight jobs reserved, against the run's budget.
        All zeros between runs: the budget lives on the engine, and an idle
        client reads the limits from the settings payload instead. Duck-typed so
        the store never imports the engine."""
        idle = RunResources(cores_used=0, cores_budget=0, ram_used_mb=0,
                            ram_budget_mb=0, jobs=0, max_jobs=0)
        eng = self._engine
        if eng is None:
            return idle
        try:
            return RunResources(**eng.resource_usage())
        except Exception:
            return idle

    def version(self) -> int:
        with self._lock:
            return self._version

    def names(self) -> list[str]:
        with self._lock:
            return [c.name for c in self._calcs]

    def get(self, name: str) -> Optional[Calculation]:
        with self._lock:
            for c in self._calcs:
                if c.name == name:
                    return c
        return None

    # ---- mutations ----
    # While a run is in progress the queue is LIVE, not frozen (P29): the
    # engine walks this store's own list under this store's own lock
    # (make_engine_factory passes both), picking the next unhandled row each
    # iteration — so a row added mid-run executes in the same run, and a
    # removed/edited PENDING row is simply never picked / picked in its edited
    # form. What the guards below still protect mid-run:
    #   - the engine's in-flight calc (state RUNNING, plus _engine_active for
    #     the picked-but-not-yet-stamped window) is untouchable;
    #   - non-EDITABLE_STATES rows (DONE/FAILED) keep their mid-run immunity —
    #     a DONE row may be a reference parent the walk still needs;
    #   - a mid-run add/edit must not carry a dangling reference, and a
    #     mid-run remove must not create one: the idle-time laxness is safe
    #     only because the pre-run check (find_dangling_reference) re-screens,
    #     which a mid-run mutation bypasses — an unresolvable reference would
    #     FAIL (and lock, P24) the dependent instead of refusing up front.

    def _engine_active(self, name: str) -> bool:
        """True when the running engine is currently handling `name` (duck-typed
        so the store never imports the engine)."""
        eng = self._engine
        if eng is None:
            return False
        try:
            return name in (getattr(eng, "active_names", None) or ())
        except Exception:
            return False

    @staticmethod
    def _is_reference(calc: Calculation) -> bool:
        """Robust GeometrySource.REFERENCE test (enum or its str value)."""
        src = calc.geometry_source
        return src == GeometrySource.REFERENCE or str(
            getattr(src, "value", src)) == "reference"

    @classmethod
    def _depends_on(cls, calc: Calculation, name: str) -> str:
        """How `calc` depends on the calc called `name`: "ref" (its geometry
        reference), "after" (it runs after it), or ""."""
        if cls._is_reference(calc) and calc.ref_name == name:
            return "ref"
        if calc.after == name:
            return "after"
        return ""

    @classmethod
    def _missing_dependency(cls, calc: Calculation, names) -> str:
        """The refusal for a calc whose geometry reference or `after` names no
        calc in `names`, or ""."""
        if cls._is_reference(calc) and calc.ref_name not in names:
            return (f"'{calc.name}' references '{calc.ref_name}', "
                    "which is not in the queue.")
        if calc.after and calc.after not in names:
            return (f"'{calc.name}' runs after '{calc.after}', "
                    "which is not in the queue.")
        return ""

    def add(self, calc: Calculation) -> None:
        """Append a calculation. Raises ValueError on duplicate name (and, while
        the queue is running, on a reference that isn't in the queue)."""
        with self._lock:
            self._check_ref_point(calc, self._calcs)
            self._add_into(self._calcs, self._systems, calc)
            self._bump_and_save()

    def _add_into(self, calcs: "list[Calculation]", systems: "list[dict]",
                  calc: Calculation, check_refs: bool = True) -> None:
        """add()'s rules against a given list (the live one, or apply_ops'
        working copy). Caller holds the lock."""
        # case-insensitive: calc.name is an on-disk folder name and Windows
        # (the primary target) resolves "water" and "Water" to the SAME
        # folder — accepting both would silently share one .out between two
        # calculations.
        if any(c.name.casefold() == calc.name.casefold() for c in calcs):
            raise ValueError(f"A calculation named '{calc.name}' already exists.")
        if calc.system and calc.system not in {s["id"] for s in systems}:
            raise ValueError(f"'{calc.name}' belongs to a system that does not "
                             "exist.")
        if check_refs and self._running:
            # no pre-run screen will catch this one — it would run (and
            # FAIL, locking) with an unresolvable reference
            problem = self._missing_dependency(calc, {c.name for c in calcs})
            if problem:
                raise ValueError(problem)
        calcs.append(calc)

    def remove(self, name: str) -> bool:
        with self._lock:
            if self._remove_from(self._calcs, name):
                self._bump_and_save()
                return True
        return False

    def _remove_from(self, calcs: "list[Calculation]", name: str,
                     check_refs: bool = True) -> bool:
        """remove()'s rules against a given list. Caller holds the lock."""
        if self._running:
            target = next((c for c in calcs if c.name == name), None)
            if target is None:
                return False
            # A finished row (DONE / FAILED) leaves the LIST mid-run too:
            # removal touches no file, the engine walks rows by identity,
            # and the reference check below still keeps a parent that a
            # row yet to finish reads its geometry from. Only the in-flight
            # one is held. (It used to be refused until the run ended, so a
            # long run left its finished rows impossible to tidy.)
            if (target.state not in EDITABLE_STATES
                    and target.state not in (CalcState.DONE, CalcState.FAILED)):
                raise ValueError(
                    "While the queue is running, a running calculation "
                    "cannot be removed.")
            if self._engine_active(name):
                raise ValueError(
                    "Cannot remove: this calculation is about to run.")
            dep = next(
                (c for c in calcs
                 if check_refs and c.name != name
                 and c.state not in (CalcState.DONE, CalcState.FAILED)
                 and self._depends_on(c, name)),
                None)
            if dep is not None:
                how = ("references its geometry"
                       if self._depends_on(dep, name) == "ref"
                       else "runs after it")
                raise ValueError(
                    f"Cannot remove '{name}' while the queue is running: "
                    f"'{dep.name}' {how}.")
        for i, c in enumerate(calcs):
            if c.name == name:
                # belt-and-suspenders: never remove the in-flight calc
                if c.state == CalcState.RUNNING:
                    raise ValueError("Cannot remove a running calculation.")
                del calcs[i]
                return True
        return False

    def clear(self) -> None:
        with self._lock:
            if self._running:
                raise ValueError("Cannot clear the queue while it is running.")
            self._calcs.clear()
            # the lanes are the queue editor's view of this list — an empty
            # queue has none
            self._systems.clear()
            self._bump_and_save()

    def replace(self, name: str, new_calc: Calculation) -> bool:
        """Replace an editable calculation in place (keeps its queue position).
        Editable = pending, cancelled, or blocked (see EDITABLE_STATES). Editing
        resets the entry to PENDING (and clears the old result/message) so it
        runs on the next Run.
        Raises ValueError if the target isn't editable, or on a name clash."""
        with self._lock:
            self._check_ref_point(
                new_calc, [c for c in self._calcs if c.name != name])
            self._replace_in(self._calcs, self._systems, name, new_calc)
            self._bump_and_save()
            return True

    @staticmethod
    def _check_ref_point(calc: Calculation, calcs: "list[Calculation]") -> None:
        """Refuse a ref_point irc_f / irc_b on a parent that writes no IRC end
        points (ref_point_problem): it could only stop at run time."""
        parent = next((c for c in calcs if c.name == calc.ref_name), None)
        problem = ref_point_problem(calc, parent)
        if problem:
            raise ValueError(problem)

    def _replace_in(self, calcs: "list[Calculation]", systems: "list[dict]",
                    name: str, new_calc: Calculation,
                    check_refs: bool = True) -> None:
        """replace()'s rules against a given list. Caller holds the lock."""
        idx = None
        for i, c in enumerate(calcs):
            if c.name == name:
                idx = i
                break
        if idx is None:
            raise ValueError(f"No calculation named '{name}'.")
        if calcs[idx].state not in EDITABLE_STATES:
            raise ValueError("Only pending, cancelled, or blocked calculations can be edited.")
        if new_calc.system and new_calc.system not in {s["id"] for s in systems}:
            raise ValueError(f"'{new_calc.name}' belongs to a system that does "
                             "not exist.")
        if self._running:
            if self._engine_active(name):
                raise ValueError(
                    "Cannot edit: this calculation is about to run.")
            if check_refs and new_calc.name != name and any(
                    c.name != name
                    and c.state not in (CalcState.DONE, CalcState.FAILED)
                    and self._depends_on(c, name)
                    for c in calcs):
                raise ValueError(
                    f"Cannot rename '{name}' while the queue is running: "
                    "another calculation references its geometry or runs "
                    "after it.")
            if check_refs:
                problem = self._missing_dependency(
                    new_calc, {c.name for c in calcs if c.name != name})
                if problem:
                    raise ValueError(problem)
        # if renaming, the new name must not collide with a DIFFERENT entry
        # (case-insensitive — names are Windows folder names, see add())
        if new_calc.name != name and any(
            c.name.casefold() == new_calc.name.casefold()
            for j, c in enumerate(calcs) if j != idx
        ):
            raise ValueError(f"A calculation named '{new_calc.name}' already exists.")
        # a freshly edited calc is always pending again, with no stale result
        new_calc.state = CalcState.PENDING
        new_calc.message = ""
        new_calc.result = None
        new_calc.output_path = ""
        calcs[idx] = new_calc

    def reorder(self, from_idx: int, to_idx: int) -> bool:
        """Move an editable calculation to a new position. Both endpoints must
        be editable (see EDITABLE_STATES) so running/done/failed items keep
        their place. Returns True on move. Allowed mid-run too: the engine
        picks the first unhandled row in live list order, so moving editable
        rows genuinely reorders the remaining execution."""
        with self._lock:
            n = len(self._calcs)
            if not (0 <= from_idx < n) or not (0 <= to_idx < n):
                raise ValueError("Index out of range.")
            if from_idx == to_idx:
                return False
            if self._calcs[from_idx].state not in EDITABLE_STATES:
                raise ValueError("Only pending, cancelled, or blocked calculations can be moved.")
            if self._calcs[to_idx].state not in EDITABLE_STATES:
                raise ValueError("Can only reorder within editable calculations.")
            item = self._calcs.pop(from_idx)
            self._calcs.insert(to_idx, item)
            self._bump_and_save()
            return True

    # ---- the queue editor's batched edits ----
    def apply_ops(self, ops) -> None:
        """Apply a list of queue-editor operations atomically: validated as a
        whole and applied under the store lock, all or nothing.

        Every operation runs against a WORKING COPY of the calc list and the
        systems, through the same rules the single mutations use (_add_into /
        _replace_in / _remove_from: name rules and uniqueness, EDITABLE_STATES,
        the engine-active refusal). Only when all of them succeed is the copy
        committed — in place, because a running engine walks this very list
        object (P29). Reference integrity is judged once, on the final state,
        rather than op by op: a batch that adds a parent after its child, or
        removes a node together with its descendants, is consistent as a whole
        even though its intermediate states are not. (Idle, a dangling
        reference is left to the pre-run screen, exactly as for add().)

        Raises QueueOpError (a ValueError) naming the operation that failed.
        """
        if not isinstance(ops, list):
            raise QueueOpError("Operations must be a list.", None)
        with self._lock:
            calcs = list(self._calcs)
            systems = copy.deepcopy(self._systems)
            removed: set[str] = set()
            # place_calc works on a batch-private copy: id(copy) -> (copy, the
            # live row). The copy is held here so its id cannot be reused.
            placed: dict[int, tuple] = {}
            for k, op in enumerate(ops):
                try:
                    self._apply_op(calcs, systems, op, removed, placed)
                except QueueOpError:
                    raise
                except (ValueError, TypeError, KeyError, AttributeError,
                        OverflowError) as e:
                    raise QueueOpError(str(e), k) from e
            if self._running:
                names = {c.name for c in calcs}
                for c in calcs:
                    if c.state in (CalcState.DONE, CalcState.FAILED):
                        continue
                    problem = self._missing_dependency(c, names)
                    if problem:
                        raise QueueOpError(problem, None)
            # A ref_point on a parent that writes no IRC end points, judged on
            # the final state for every row this batch added or rebuilt (or
            # whose parent it rebuilt) -- a batch may add the parent last.
            live_ids = {id(c) for c in self._calcs} | set(placed)
            by_name = {c.name: c for c in calcs}
            for c in calcs:
                if not c.ref_point or c.state not in EDITABLE_STATES:
                    continue
                parent = by_name.get(c.ref_name)
                if id(c) in live_ids and (parent is None or id(parent) in live_ids):
                    continue
                problem = ref_point_problem(c, parent)
                if problem:
                    raise QueueOpError(problem, None)
            # A placed row goes back into the list as the SAME object, with
            # only its placement written onto it: the engine tracks rows by
            # identity and may hold this one (a running or finished row).
            for i, c in enumerate(calcs):
                entry = placed.get(id(c))
                if entry is not None and entry[0] is c:
                    live = entry[1]
                    for f in self._PLACE_KEYS + ("approved",):
                        setattr(live, f, getattr(c, f))
                    calcs[i] = live
            self._calcs[:] = calcs
            self._systems[:] = systems
            self._bump_and_save()

    _SYSTEM_PATCH_KEYS = {"name", "charge", "multiplicity", "inputs"}
    _STARTED = (CalcState.RUNNING, CalcState.DONE, CalcState.FAILED)
    #: what place_calc may change: where a row is DRAWN, never what it runs
    _PLACE_KEYS = ("system", "node", "col", "row")

    def _place_calc(self, calcs: "list[Calculation]", systems: "list[dict]",
                    op: dict, placed: "dict[int, tuple]") -> None:
        """place_calc: move a calculation in the editor's layout — its lane
        (`system`), its node id, its grid cell — and nothing else.

        Unlike update_calc this is allowed in EVERY state. A finished
        calculation is frozen (P24) in what it ran and what it produced; where
        the editor draws it is not part of that. It is what lets a finished
        standalone calc become the first node of a new lane, so a follow-up
        (a Freq after a done OptTS, an SP after a done IRC) can hang off it.
        The state, message, result, name, `after` and every input field are
        untouched, and the row is not reset to PENDING. The live object is
        kept (apply_ops writes the placement back onto it at commit)."""
        name = op.get("name")
        idx = next((i for i, c in enumerate(calcs) if c.name == name), None)
        if idx is None:
            raise ValueError(f"No calculation named '{name}'.")
        place = op.get("place")
        if not isinstance(place, dict) or not place:
            raise ValueError("place_calc needs a 'place' object.")
        unknown = set(place) - set(self._PLACE_KEYS)
        if unknown:
            raise ValueError("place_calc can change only system, node, col and "
                             "row, not: " + ", ".join(sorted(map(str, unknown))))
        checked = _graph_fields(place, restoring=False)
        if "system" in place and checked["system"] and \
                checked["system"] not in {s["id"] for s in systems}:
            raise ValueError(f"'{name}' cannot go into a system that does not exist.")
        current = calcs[idx]
        entry = placed.get(id(current))
        if entry is not None and entry[0] is current:
            moved = current                      # already this batch's copy
        else:
            moved = copy.copy(current)
            placed[id(moved)] = (moved, current)
        for k in place:
            setattr(moved, k, checked[k])
        calcs[idx] = moved

    @staticmethod
    def _patched(current: Calculation, patch: dict) -> Calculation:
        """The calculation `current` becomes under an update_calc / rerun_calc
        patch, validated (calc_from_dict). What the patch leaves out keeps the
        current value (so {"approved": true} approves a gate without resending
        the rest); state and result are not carried."""
        merged = {k: v for k, v in calc_to_session_dict(current).items()
                  if k not in ("state", "message", "output_path", "pid",
                               "create_time", "rerun")}
        merged.update(patch)
        if "kind" in patch and "config" not in patch and isinstance(
                merged.get("config"), dict):
            # a Type change: the config describes the calc's kind too
            # (StepConfig.kind), so it follows rather than keeping the old
            merged["config"] = {**merged["config"], "kind": patch["kind"]}
        if (({"raw_text", "config", "kind"} & set(patch))
                and merged.get("set_name") and merged.get("raw_text")
                and str(merged.get("kind", "")).startswith("mlip")):
            # an MLIP template's text (a calc that came from an inp set)
            # is applied to the config again by calc_from_dict — the
            # stored calc reads is_raw false. Also for an options edit
            # (config: T / P for an MLIP frequency) or a Type change, or
            # the patch's config would replace the model the text sets.
            merged["is_raw"] = True
        if ("config" not in patch and merged.get("is_raw")
                and is_level_text(merged.get("raw_text") or "")
                and not (current.is_raw and is_level_text(current.raw_text))):
            # a calc BECOMING a level block (a level dropped on a guided
            # calc): its old config was a whole guided input, not options
            # for the level -- none of it carries over
            merged["config"] = {}
        return calc_from_dict(merged)

    #: what rerun_calc may change: the set a calculation runs, nothing else
    _RERUN_KEYS = ("raw_text", "set_name", "set_id", "is_raw", "config")

    def _rerun_calc(self, calcs: "list[Calculation]", systems: "list[dict]",
                    op: dict) -> None:
        """rerun_calc: put a set (a level, or MLIP settings) on a DONE or
        FAILED calculation and queue it to run again -- the one way a finished
        or failed calculation is recomputed (P24). The queue editor sends it
        only after an overwrite warning: the row goes back to PENDING with no
        message or result, keeps its name, place and everything it hangs off,
        and is marked `rerun`, so the old output in its folder is overwritten
        when it runs, never kept. The row it replaced is held (in memory) for
        undo_rerun. The rows a FAILED one held BLOCKED go back to PENDING
        with it (_unblock_after). A row the engine is handling is never
        touched."""
        name = op.get("name")
        idx = next((i for i, c in enumerate(calcs) if c.name == name), None)
        if idx is None:
            raise ValueError(f"No calculation named '{name}'.")
        current = calcs[idx]
        if current.state not in RERUNNABLE_STATES:
            raise ValueError(f"'{name}' has not finished: only a done or failed "
                             "calculation runs again this way.")
        if self._engine_active(name):
            raise ValueError(f"'{name}' cannot change: the run is handling it.")
        patch = op.get("calc")
        if not isinstance(patch, dict) or not patch:
            raise ValueError("rerun_calc needs a 'calc' object.")
        unknown = set(patch) - set(self._RERUN_KEYS)
        if unknown:
            raise ValueError("rerun_calc can change only the set a calculation "
                             "runs (" + ", ".join(self._RERUN_KEYS) + "), not: "
                             + ", ".join(sorted(map(str, unknown))))
        new_calc = self._patched(current, patch)
        if new_calc.system and new_calc.system not in {s["id"] for s in systems}:
            raise ValueError(f"'{name}' belongs to a system that does not exist.")
        new_calc.state = CalcState.PENDING
        new_calc.message = ""
        new_calc.result = None
        new_calc.output_path = ""
        new_calc.pid = None
        new_calc.create_time = None
        new_calc.rerun = True
        calcs[idx] = new_calc
        self._rerun_stash[name] = current
        self._rerun_freed[name] = (self._unblock_after(calcs, name)
                                   if current.state == CalcState.FAILED else [])

    def _unblock_after(self, calcs: "list[Calculation]",
                       name: str) -> "list[tuple[Calculation, Calculation]]":
        """The rows downstream of `name` (by geometry reference or `after`,
        transitively) that are BLOCKED go back to PENDING, each as a new
        object, so a running engine, which has already handled the blocked
        one, picks it again (P29). A row still blocked by another failure is
        stamped BLOCKED again when the run reaches it, as at a run's start.
        Returns the (blocked row, freed row) pairs undo_rerun puts back."""
        down, grew = {name}, True
        while grew:
            grew = False
            for c in calcs:
                ref = c.ref_name if self._is_reference(c) else ""
                if c.name not in down and (ref in down or c.after in down):
                    down.add(c.name)
                    grew = True
        freed = []
        for i, c in enumerate(calcs):
            if (c.name == name or c.name not in down
                    or c.state != CalcState.BLOCKED or self._engine_active(c.name)):
                continue
            again = copy.copy(c)
            again.state = CalcState.PENDING
            again.message = ""
            calcs[i] = again
            freed.append((c, again))
        return freed

    def _undo_rerun(self, calcs: "list[Calculation]", systems: "list[dict]",
                    op: dict) -> None:
        """undo_rerun: rerun_calc's inverse -- the done or failed row comes
        back as it was (the same object, result and all), only while the row
        under that name is still the unstarted rerun of it, and so does each
        row its rerun freed that is still the freed object. Once it has
        started, or after a restart (the stash is not persisted), its output
        may already be overwritten, and the old result is gone for good."""
        name = op.get("name")
        idx = next((i for i, c in enumerate(calcs) if c.name == name), None)
        old = self._rerun_stash.get(name)
        if (idx is None or old is None or not is_rerun(calcs[idx])
                or self._engine_active(name)):
            raise ValueError(f"'{name}' cannot go back to its finished result: "
                             "it has started since (or the app was restarted).")
        if old.system and old.system not in {s["id"] for s in systems}:
            raise ValueError(f"'{name}' cannot go back: its system is gone.")
        calcs[idx] = old
        for blocked, freed in self._rerun_freed.get(name, ()):
            j = next((k for k, c in enumerate(calcs) if c is freed), None)
            if j is not None and not self._engine_active(freed.name):
                calcs[j] = blocked

    def _apply_op(self, calcs: "list[Calculation]", systems: "list[dict]",
                  op, removed: "set[str]",
                  placed: "dict[int, tuple] | None" = None) -> None:
        if not isinstance(op, dict):
            raise ValueError("Each operation must be an object.")
        kind = op.get("op")
        if kind == "add_system":
            s = system_from_dict(op.get("system"))
            if any(o["id"] == s["id"] for o in systems):
                raise ValueError(f"A system with id '{s['id']}' already exists.")
            self._check_system_name(systems, s["name"], s["id"])
            systems.append(s)
        elif kind == "update_system":
            self._update_system(calcs, systems, op)
        elif kind == "remove_system":
            sid = op.get("id")
            s = next((o for o in systems if o["id"] == sid), None)
            if s is None:
                raise ValueError(f"No system with id '{sid}'.")
            lane = [c for c in calcs if c.system == sid]
            started = next((c for c in lane if c.state in self._STARTED
                            or self._engine_active(c.name)), None)
            if started is not None:
                raise ValueError(
                    f"'{s['name']}' holds '{started.name}', which has already "
                    "run (or is running); remove its finished calculations "
                    "first.")
            for c in lane:
                self._remove_from(calcs, c.name, check_refs=False)
                removed.add(c.name)
            systems.remove(s)
        elif kind == "add_calc":
            self._add_into(calcs, systems, calc_from_dict(op.get("calc")),
                           check_refs=False)
        elif kind == "update_calc":
            name = op.get("name")
            current = next((c for c in calcs if c.name == name), None)
            if current is None:
                raise ValueError(f"No calculation named '{name}'.")
            patch = op.get("calc")
            if not isinstance(patch, dict):
                raise ValueError("update_calc needs a 'calc' object.")
            new_calc = self._patched(current, patch)
            # still the row the user chose to overwrite, while it has not started
            new_calc.rerun = is_rerun(current)
            if set(patch) == {"approved"} and current.state == CalcState.PENDING:
                # An approval-only patch on a waiting row keeps the LIVE object
                # (written back at commit, like place_calc): the engine pins
                # its keep-existing answers to row identity (_skip_ids), and a
                # replacement would make a row the user chose to keep re-run.
                if self._running and self._engine_active(name):
                    raise ValueError(
                        "Cannot edit: this calculation is about to run.")
                idx = calcs.index(current)
                entry = placed.get(id(current)) if placed is not None else None
                if entry is not None and entry[0] is current:
                    moved = current
                else:
                    moved = copy.copy(current)
                    if placed is not None:
                        placed[id(moved)] = (moved, current)
                moved.approved = new_calc.approved
                calcs[idx] = moved
                return
            self._replace_in(calcs, systems, name, new_calc,
                             check_refs=False)
        elif kind == "rerun_calc":
            self._rerun_calc(calcs, systems, op)
        elif kind == "undo_rerun":
            self._undo_rerun(calcs, systems, op)
        elif kind == "place_calc":
            self._place_calc(calcs, systems, op, {} if placed is None else placed)
        elif kind == "remove_calc":
            name = op.get("name")
            if not self._remove_from(calcs, name, check_refs=False):
                if name not in removed:
                    raise ValueError(f"No calculation named '{name}'.")
            removed.add(name)
        elif kind == "order":
            names = op.get("names")
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise ValueError("order needs 'names', a list of calculation names.")
            by_name = {c.name: c for c in calcs}
            if len(names) != len(set(names)) or set(names) != set(by_name):
                raise ValueError("order must name every calculation in the queue "
                                 "exactly once.")
            # Any permutation: the editor's run order is lane by lane, and a
            # lane moves with its finished rows. The engine walks rows by
            # identity and picks the first unhandled one, so where a finished
            # or running row sits changes nothing it does.
            calcs[:] = [by_name[n] for n in names]
        else:
            raise ValueError(f"Unknown operation '{kind}'.")

    @staticmethod
    def _check_system_name(systems: "list[dict]", name: str, sid: str) -> None:
        if any(o["name"].casefold() == name.casefold() and o["id"] != sid
               for o in systems):
            raise ValueError(f"A system named '{name}' already exists.")

    def _update_system(self, calcs: "list[Calculation]", systems: "list[dict]",
                       op: dict) -> None:
        """update_system: patch a lane's name / charge / multiplicity / inputs.

        A rename renames every calc of the lane whose name starts with the old
        name (new + the rest of the old name) and re-points every editable
        calc's geometry reference and `after` at the new names. It is refused
        once any calc of the lane has started — a started calc's name is its
        folder on disk — and while the engine holds one. Charge and
        multiplicity propagate to the lane's editable calcs that had the
        lane's old value; a calc set apart (its own multiplicity) keeps it. Every calc this
        touches is an edit (a new object, PENDING again), like replace()."""
        sid = op.get("id")
        idx = next((i for i, o in enumerate(systems) if o["id"] == sid), None)
        if idx is None:
            raise ValueError(f"No system with id '{sid}'.")
        old = systems[idx]
        patch = op.get("patch")
        if not isinstance(patch, dict):
            raise ValueError("update_system needs a 'patch' object.")
        unknown = set(patch) - self._SYSTEM_PATCH_KEYS
        if unknown:
            raise ValueError("update_system cannot change: " + ", ".join(sorted(unknown)))
        new = system_from_dict({**old, **patch})
        self._check_system_name(systems, new["name"], sid)
        lane = [c for c in calcs if c.system == sid]

        rename: dict[str, str] = {}
        if new["name"] != old["name"]:
            started = next((c for c in lane if c.state in self._STARTED
                            or self._engine_active(c.name)), None)
            if started is not None:
                raise ValueError(
                    f"'{old['name']}' cannot be renamed: '{started.name}' has "
                    "already run (or is running), and its name is its folder.")
            for c in lane:
                if c.name.startswith(old["name"]):
                    new_name = new["name"] + c.name[len(old["name"]):]
                    _validate_calc_name(new_name, c.kind)
                    rename[c.name] = new_name
            keep = {c.name.casefold() for c in calcs if c.name not in rename}
            seen_new: set[str] = set()
            for new_name in rename.values():
                key = new_name.casefold()
                if key in keep or key in seen_new:
                    raise ValueError(f"A calculation named '{new_name}' already exists.")
                seen_new.add(key)
        recharge = (new["charge"], new["multiplicity"]) != (old["charge"], old["multiplicity"])

        for i, c in enumerate(calcs):
            if c.state not in EDITABLE_STATES:
                continue
            changes: dict = {}
            if c.name in rename:
                changes["name"] = rename[c.name]
            if self._is_reference(c) and c.ref_name in rename:
                changes["ref_name"] = rename[c.ref_name]
            if c.after in rename:
                changes["after"] = rename[c.after]
            if recharge and c.system == sid:
                # only a member that ran at the row's old value follows: one
                # set apart on purpose (a triplet SP in a singlet row) keeps
                # its own, and undoing the recharge returns exactly these
                if new["charge"] != old["charge"] and c.charge == old["charge"]:
                    changes["charge"] = new["charge"]
                if (new["multiplicity"] != old["multiplicity"]
                        and c.multiplicity == old["multiplicity"]):
                    changes["multiplicity"] = new["multiplicity"]
            if not changes:
                continue
            if self._engine_active(c.name):
                raise ValueError(f"Cannot edit '{c.name}': it is about to run.")
            edited = copy.copy(c)
            for field, value in changes.items():
                setattr(edited, field, value)
            edited.state = CalcState.PENDING
            edited.message = ""
            edited.result = None
            edited.output_path = ""
            calcs[i] = edited
        systems[idx] = new

    def list(self) -> list[Calculation]:
        with self._lock:
            return list(self._calcs)

    # ---- run flag ----
    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def set_running(self, value: bool) -> None:
        with self._lock:
            self._running = bool(value)
            self._bump_and_save()

    def touch(self) -> None:
        """Bump version, e.g. after the engine mutates a calc's state in place."""
        with self._lock:
            self._bump_and_save()

    # ---- log buffer ----
    def append_log(self, message: str, level: str = "info",
                   calc: str = "") -> None:
        """Append one log line. `calc` names the calculation that produced it
        ("" = engine/app level): with several jobs in flight their output
        interleaves here, and the tag is what lets a client separate them."""
        with self._lock:
            self._log_seq += 1
            self._log.append((self._log_seq, level, message, calc))
            # cap buffer so a long run doesn't grow memory without bound.
            # Preferential retention for "[web] " lines: ORCA stdout floods this
            # ring (a single run emits thousands of lines) and would evict the
            # rate-limited console-capture lines whose whole purpose is post-hoc
            # front-end diagnosis (window.py _ConsoleCapturePage) — keep the
            # newest 4000 lines plus up to 500 older [web] lines (seq order is
            # preserved: every retained [web] seq predates the tail's).
            if len(self._log) > 5000:
                tail = self._log[-4000:]
                web_keep = [t for t in self._log[:-4000]
                            if t[2].startswith("[web] ")][-500:]
                self._log = web_keep + tail

    def log_since(self, since: int = 0) -> LogPayload:
        """Return log lines with seq > since, plus the latest seq."""
        with self._lock:
            lines = [
                LogLine(seq=s, level=lv, msg=m, calc=c)
                for (s, lv, m, c) in self._log if s > since
            ]
            return LogPayload(lines=lines, latest=self._log_seq)

    # (No backend log-clear: the Log tab's Clear is deliberately view-only on
    # every client. If one is ever added, it must keep _log_seq monotonic —
    # resetting it would corrupt every client's since-cursor.)

    # ---- run management ----
    def start_run(self, engine_factory, precheck=None) -> None:
        """
        Start running the queue in a background thread.

        engine_factory() must return a QueueEngine already wired with callbacks
        that update THIS store (log -> append_log, calc_update -> touch). We
        keep store framework-agnostic by having the caller build the engine.

        ``precheck(calcs) -> str | None`` is the caller's pre-run screen, run
        HERE, under the lock, against the live list. Callers used to screen
        their own snapshot and then call this — and `add()` only enforces
        reference integrity while a run is in progress, so a calculation added
        in that window (another window, a phone client) slipped past both
        guards and was FAILED-locked at run time for a reference the screen
        would have refused. Returning a message aborts the start.

        Raises RuntimeError if a run is already in progress, ValueError if the
        queue is empty or the precheck refuses.
        """
        with self._lock:
            if self._running:
                raise RuntimeError("A run is already in progress.")
            if not self._calcs:
                raise ValueError("The queue is empty.")
            if precheck is not None:
                problem = precheck(list(self._calcs))
                if problem:
                    raise ValueError(problem)
            # the engine walks the store's OWN list (live queue, P29) — the
            # factory also hands it this store's lock, so its per-iteration
            # pick and the user mutations above are serialized
            calcs = self._calcs
            engine = engine_factory()
            self._engine = engine
            self._running = True
            self._bump_and_save()

        def _worker():
            try:
                engine.run_all(calcs)
            except Exception as e:  # engine should handle most, but be safe
                self.append_log(f"Run aborted: {e}", "err")
            finally:
                with self._lock:
                    self._running = False
                    self._engine = None
                    self._thread = None
                    self._bump_and_save()
                self.append_log("Queue finished.", "info")

        t = threading.Thread(target=_worker, name="orcadesk-run", daemon=True)
        with self._lock:
            self._thread = t
        t.start()

    def cancel_run(self) -> bool:
        """Signal the running engine to cancel. Returns False if nothing runs."""
        with self._lock:
            if not self._running or self._engine is None:
                return False
            engine = self._engine
        # call cancel outside the lock (it may touch the runner/subprocess)
        engine.cancel()
        self.append_log("Cancellation requested...", "info")
        return True

    def request_stop_after_current(self) -> bool:
        """Ask the running engine to stop AFTER the current job finishes (a
        graceful drain). The in-flight job is left to complete; remaining calcs
        stay PENDING. Returns False if nothing is running."""
        with self._lock:
            if not self._running or self._engine is None:
                return False
            engine = self._engine
        engine.request_stop_after_current()
        self.append_log("Will stop after the current job finishes...", "info")
        return True

    def pause_run(self) -> bool:
        """Stop processing the queue WITHOUT killing the in-flight ORCA — used on
        app shutdown so the running calculation survives (its detached ORCA keeps
        going) and can be reattached on the next launch. Returns False if nothing
        is running."""
        with self._lock:
            if not self._running or self._engine is None:
                return False
            engine = self._engine
        engine.detach()
        return True

    def wait_for_run(self, timeout: "float | None" = None) -> bool:
        """Block until the run worker thread finishes (or timeout elapses).
        Returns True if no run is in progress afterwards. Used on app shutdown so
        we don't orphan orca.exe / leave a half-written .out behind."""
        with self._lock:
            t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()

    # ---- session persistence ----
    def _bump_and_save(self) -> None:
        """Bump the change version AND autosave the queue. Replaces the bare
        version increments so every mutation is persisted. The caller holds the
        lock; the RLock is reentrant, so save_session re-acquiring is fine."""
        self._version += 1
        self.save_session()

    # --- MLIP readiness (published by the desktop probe, read by both runs) ---
    def set_mlip_readiness(self, states: dict) -> None:
        """Publish env id -> probe state ("ready"/"checking"/"error").

        Readiness is a live probe result that only the desktop Bridge produces
        (it costs an interpreter launch and a torch import), but BOTH run entry
        points have to honour `mlip_env_id == "" -> first ready env`. Putting it
        on the shared store is what lets the phone's /api/run order the env list
        the same way the desktop does, instead of taking settings order and
        sending a phone-started calculation into a broken interpreter while the
        desktop pill reads "ready" off a different env.
        """
        with self._lock:
            self._mlip_readiness = dict(states or {})

    def mlip_readiness(self) -> dict:
        """The last published readiness map ({} before the first probe)."""
        with self._lock:
            return dict(self._mlip_readiness)

    def save_session(self) -> None:
        """Persist the full queue to the session file (atomic replace). Best-
        effort: a save failure must never break the running app."""
        with self._lock:
            payload = {
                "schema": SESSION_SCHEMA,
                "calculations": [calc_to_session_dict(c) for c in self._calcs],
                "systems": copy.deepcopy(self._systems),
            }
        try:
            path = _session_file()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, UnicodeError):
            # UnicodeError is not hypothetical: json.loads turns the escape
            # "\ud800" into a lone surrogate, which no UTF-8 encoder will take.
            # It is a ValueError, so it escaped this guard and propagated out of
            # _bump_and_save and out of add/remove/replace/reorder — and since
            # the calc is already in the list by then, EVERY later mutation
            # raised too and the session was never written again. Names are
            # refused surrogates at the validator now; this is the backstop for
            # every other persisted string.
            try:
                tmp.unlink()
            except (OSError, NameError, UnboundLocalError):
                pass

    def load_session(self) -> None:
        """Restore the queue from the session file and reconcile it with reality
        (see reconcile_calcs). Call once at startup. No-op if missing/unreadable."""
        path = _session_file()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        # Valid JSON that isn't an object (a list, null, a bare number — e.g. a
        # hand-repaired or externally-truncated file) must degrade like corrupt
        # JSON does: .get() on a non-dict would crash EVERY startup until the
        # user deletes session.json by hand (same guard as Settings.load, P32).
        if not isinstance(data, dict):
            return
        # ...and neither is the value under "calculations" necessarily a list:
        # null or a number is not iterable, and the TypeError would propagate
        # out of MainWindow.__init__ — the app would not start at all until the
        # file was deleted by hand (P32).
        entries = data.get("calculations", [])
        if not isinstance(entries, list):
            entries = []
        restored = []
        seen_names: set[str] = set()
        schema = data.get("schema", 1)
        legacy = not (isinstance(schema, int) and not isinstance(schema, bool)
                      and schema >= SESSION_SCHEMA)
        for d in entries:
            try:
                c = calc_from_session_dict(d)
            except Exception:
                continue  # skip a corrupt entry rather than lose the whole queue
            if legacy and c.is_raw and is_level_text(c.raw_text):
                # Before schema 2 a level block's config was never read (its
                # text was the whole input), so whatever it holds -- the
                # defaults, or a guided calc's settings the level was dropped
                # onto -- is not options anybody chose: read as none, the
                # block renders exactly as it did.
                c.config = template_config({}, c.kind)
            # The restore path must uphold the same case-insensitive name
            # uniqueness add() enforces (Windows folder identity): a session
            # written by an older build (or edited/merged externally) could
            # otherwise resurrect two calcs sharing one on-disk folder.
            key = c.name.casefold()
            if key in seen_names:
                continue  # first occurrence wins, matching store.get()
            seen_names.add(key)
            restored.append(c)
        systems = _restore_systems(data.get("systems"))
        # A calc pointing at a lane that did not come back (a session written
        # before systems existed has none; a hand-edited one may lose one) is
        # shown as what it then is: a standalone calc.
        ids = {s["id"] for s in systems}
        for c in restored:
            if c.system and c.system not in ids:
                c.system = ""
        if not restored and not systems:
            return
        reconcile_calcs(restored)
        with self._lock:
            self._calcs = restored
            self._systems = systems
            self._version += 1

    def rerun_names(self) -> "set[str]":
        """The rows rerun_calc put back that have not started (is_rerun)."""
        with self._lock:
            return {c.name for c in self._calcs if is_rerun(c)}

    def has_live_running(self) -> bool:
        """True if a calculation is still RUNNING after reconciliation — i.e. a
        detached ORCA survived a previous session and should be reattached."""
        with self._lock:
            return any(c.state == CalcState.RUNNING for c in self._calcs)


def queue_needs_orca(calcs: "list[Calculation]") -> bool:
    """True if running these calculations would launch ORCA at least once —
    i.e. some calc is not DONE and its kind is neither an mlip_* nor a crest_*
    kind (both run outside ORCA: mlip in the user's MACE env, crest in a POSIX
    shell -- WSL on Windows, this machine on Linux).

    Single shared decision (P4) for both run entry points (the desktop bridge
    and the phone server): an all-MLIP or all-CREST queue must be runnable with
    no ORCA executable configured, so requiring a valid ORCA path is gated on
    this instead of being unconditional. DONE calcs are never recomputed and
    FAILED calcs are locked (P24) — neither will ever launch ORCA, so they don't
    count (a stale FAILED ORCA calc must not force an ORCA path onto an otherwise
    all-MLIP/CREST queue).
    """
    return any(
        c.state not in (CalcState.DONE, CalcState.FAILED)
        and not c.kind.startswith("mlip")
        and not c.kind.startswith("crest")
        for c in calcs
    )


def find_dangling_reference(calcs: "list[Calculation]") -> "str | None":
    """First error message for a REFERENCE geometry that resolves to no queued
    calc name, else None. Like queue_needs_orca, a single shared pre-run check
    (P4) for both run entry points — the desktop bridge and the phone's
    /api/run — so a dangling reference is refused up front with the same
    message everywhere instead of failing mid-run with a murkier one."""
    names = {c.name for c in calcs}
    for c in calcs:
        # DONE results are frozen and FAILED calcs are locked (P24) — neither
        # will ever run again, so a dangling reference on them must not veto
        # the whole run (e.g. a FAILED calc whose referenced parent was later
        # removed would otherwise lock the Run button for good).
        if c.state in (CalcState.DONE, CalcState.FAILED):
            continue
        src = c.geometry_source
        is_ref = src == GeometrySource.REFERENCE or str(
            getattr(src, "value", src)) == "reference"
        if is_ref and c.ref_name == c.name:
            # Trivially "in the queue", so the name test below waves it
            # through; the engine then fails it at run time, and FAILED is
            # locked (P24). A calculation cannot be its own geometry source, and
            # that is knowable before anything starts.
            return (f"'{c.name}' uses its own geometry as its input. "
                    "Pick another calculation, or load coordinates directly.")
        if is_ref and c.ref_name not in names:
            return (f"'{c.name}' references '{c.ref_name}', "
                    "which is not in the queue.")
        # the queue editor's edge is a dependency too, screened the same way
        if c.after == c.name:
            return f"'{c.name}' is set to run after itself."
        if c.after and c.after not in names:
            return (f"'{c.name}' runs after '{c.after}', "
                    "which is not in the queue.")
    return None


def make_engine_factory(store: "QueueStore", orca_path: str, workspace_root: str,
                        skip_names: "set[str] | None" = None,
                        mlip_envs: "list | None" = None,
                        crest_distro: str = "",
                        budget=None):
    """
    Returns a zero-arg factory that builds a QueueEngine whose callbacks feed
    the given store (log buffer + version bumps). Used by start_run().

    skip_names: calculations the user chose not to run (e.g. to avoid
    overwriting existing results on disk).
    mlip_envs: registered MLIP environments [{id, name, python}], so the engine
    can run mlip_* calcs in the user's MACE interpreter. None/empty disables MLIP.
    crest_distro: preferred target for crest_* calcs -- a WSL distro, or
        "local" on a native transport ("" = auto-detect the
    first distro that has CREST).
    budget: a core/resources.ResourceBudget capping how many calculations run at
    once and how many cores / how much memory they may occupy together. None =
    the default one-at-a-time queue.
    """
    skip = set(skip_names or ())
    envs = list(mlip_envs or [])
    crest_pref = (crest_distro or "").strip()

    def factory() -> QueueEngine:
        cb = QueueCallbacks(
            log=lambda msg, level, calc="": store.append_log(msg, level, calc),
            calc_update=lambda i, c: store.touch(),
        )
        # the store's own lock doubles as the engine's queue lock: run_all
        # walks the store's live list (start_run), so the walk's pick step and
        # user mutations must serialize on the same lock (P29)
        # a rerun row's old output is one the user chose to overwrite: never
        # kept, whatever the keep answers (Run modal, resume) say
        keep = skip - store.rerun_names()
        return QueueEngine(orca_path, workspace_root, cb, skip_names=keep,
                           mlip_envs=envs, crest_distro=crest_pref,
                           queue_lock=store._lock, budget=budget)
    return factory
