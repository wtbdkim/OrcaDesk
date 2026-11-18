"""
Inp sets: the queue editor's saved input templates.

An inp set is ``{id, name, kind, text}``, ``kind`` one of SET_KINDS:

* ``"ORCA"`` -- a LEVEL of theory, not a job. ``text`` is a whole ORCA input
  whose ``!`` line holds ``{{JOB}}`` where the job keyword goes, with
  ``{{GEOMETRY}}`` where the coordinates go (render_raw_input) and optionally
  ``{{CHARGE}}`` / ``{{MULT}}``. The block made from a set says which job it
  runs by its own kind (the editor's Type): the engine fills ``{{JOB}}`` from
  it and adds that kind's extra block (input_generator.render_template_text).
  One level serves every step of a lane, so the library no longer repeats a
  level once per job type.
* ``"MLIP"`` -- not an ORCA input at all but ``key value`` lines (MLIP_KEYS)
  mapped onto the MLIP fields of StepConfig (mlip_config_from_text).

Stored in ``user_data_root()/inp_sets.json`` (tmp + ``os.replace``, like the
settings and the session), schema 2. The first load -- no file yet -- seeds one
ORCA level from ORCAdesk's own defaults (StepConfig + build_input at the user's
default %pal / %maxcore, ``{{JOB}}`` in the job keyword's place) and the MACE
MLIP set. A file that cannot be read degrades to the seeds in memory and is
replaced on the next save (P32); it is never a crash.

A schema-1 file (one set per job kind: MLIP / Opt / OptTS / Freq / IRC / SP)
is migrated on first read: copied once to ``inp_sets.v1.json`` beside it (an
existing backup is never overwritten), each ORCA set's job keyword(s) replaced
by ``{{JOB}}`` and exactly the extra block ORCAdesk now adds removed when it is
the seeded one (anything else the user wrote is kept), then sets whose text is
now identical merged into the first. Blocks already in the queue keep their
raw text: a text without ``{{JOB}}`` still runs as written.

Qt-free, so it is tested directly.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Optional

from ..core.input_generator import (CHARGE_PLACEHOLDER, GEOMETRY_PLACEHOLDER,
                                    HESS_PLACEHOLDER, JOB_PLACEHOLDER,
                                    MULT_PLACEHOLDER, StepConfig,
                                    _geom_job_lines, _irc_lines, build_input)
from ..paths import user_data_root

FILE_NAME = "inp_sets.json"
#: where a schema-1 file is copied before it is migrated (once, never overwritten)
BACKUP_NAME = "inp_sets.v1.json"
SCHEMA = 2

#: what a set may be, in the order the editor lists them
SET_KINDS = ("ORCA", "MLIP")
#: schema 1's per-job kinds, read only to migrate them
LEGACY_KINDS = ("Opt", "OptTS", "Freq", "IRC", "SP")

#: the natural next step after a calculation of each kind -- what a level
#: dropped on the editor's + slot runs until the user picks another Type.
#: "" is the start of a row (after its input).
NEXT_KIND = {"": "opt", "mlip_opt": "opt", "opt": "freq", "ts_opt": "ts_freq",
              "ts_freq": "irc", "ts_opt_freq": "irc", "irc": "sp"}

#: an MLIP set's keys -> the StepConfig field each one sets
#: the placeholders an ORCA set's text may hold -- each is filled at launch
#: (render_raw_input / render_template_text); any other {{...}} would reach
#: ORCA verbatim, so validate_set refuses it
KNOWN_PLACEHOLDERS = (GEOMETRY_PLACEHOLDER, JOB_PLACEHOLDER, CHARGE_PLACEHOLDER,
                      MULT_PLACEHOLDER, HESS_PLACEHOLDER)
_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")

MLIP_KEYS = {"model": "mlip_model", "device": "mlip_device",
             "nprocs": "nprocs", "env": "mlip_env_id"}
_MLIP_DEVICES = {"": "", "auto": "", "cpu": "cpu", "cuda": "cuda"}

#: the job keywords a schema-1 set's `!` line may carry (the vocabulary the
#: queue editor's kind detection read), each replaced by the one {{JOB}}
_JOB_TOKEN = re.compile(
    r"^(OPT|TIGHTOPT|LOOSEOPT|VERYTIGHTOPT|COPT|ZOPT|OPTTS|SCANTS|FREQ|NUMFREQ"
    r"|ANFREQ|IRC|SP)$", re.I)

_MAX_NAME = 120
_MAX_TEXT = 200_000
_MAX_ID = 64
_MAX_SETS = 500
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_lock = threading.Lock()


def sets_file(root: Optional[Path] = None) -> Path:
    return (root or user_data_root()) / FILE_NAME


def default_kind_after(after_kind: str = "") -> str:
    """The calculation kind a level placed after ``after_kind`` runs by
    default: the start of a row (or after an MLIP pre-optimization) is an
    optimization, then Freq after Opt, TS Freq after OptTS, IRC after a TS
    frequency, a single point after an IRC (the -F / -B pair) and after
    anything else."""
    return NEXT_KIND.get(after_kind or "", "sp")


def calc_kind_for_set(set_kind: str, after_kind: str = "") -> str:
    """The calculation kind a node built from a ``set_kind`` set runs: an MLIP
    set's is ``mlip_opt``; an ORCA level's is the natural next step
    (default_kind_after), which the editor's Type then changes."""
    if set_kind == "MLIP":
        return "mlip_opt"
    if set_kind == "ORCA":
        return default_kind_after(after_kind)
    raise ValueError(f"Unknown inp set kind '{set_kind}'.")


def mlip_config_from_text(text: str) -> dict:
    """The StepConfig fields an MLIP set's text sets, as a dict.

    One ``key value`` per line (the value is the rest of the line, so a model
    name may hold spaces); blank lines and ``#`` comments are skipped. Keys are
    MLIP_KEYS; an unknown key or a bad value is a ValueError naming the line —
    never silently dropped, or the run would use a setting nobody wrote."""
    out: dict = {}
    for n, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        key, value = key.strip().lower(), value.strip()
        if key not in MLIP_KEYS:
            raise ValueError(
                f"MLIP set line {n}: unknown key '{key}' (known: "
                + ", ".join(MLIP_KEYS) + ").")
        field = MLIP_KEYS[key]
        if key == "device":
            if value.lower() not in _MLIP_DEVICES:
                raise ValueError(f"MLIP set line {n}: device must be auto, cpu or cuda.")
            out[field] = _MLIP_DEVICES[value.lower()]
        elif key == "nprocs":
            try:
                out[field] = max(1, min(1024, int(value)))
            except ValueError:
                raise ValueError(f"MLIP set line {n}: nprocs must be a whole number.") from None
        else:
            out[field] = value
    return out


def job_line_has_placeholder(text: str) -> bool:
    """True when {{JOB}} stands on one of the text's ``!`` lines."""
    return any(ln.strip().startswith("!") and JOB_PLACEHOLDER in ln
               for ln in (text or "").splitlines())


def seed_sets(nprocs: int = 6, maxcore_mb: int = 2400) -> "list[dict]":
    """The first library: one ORCA level from ORCAdesk's own defaults
    (StepConfig + build_input, ``{{JOB}}`` where the job keyword goes) at the
    given %pal nprocs / %maxcore, written literally into the text so the engine
    admits the job by what it asks (core/resources.py reads a raw input's own
    %pal / %maxcore), and the default MACE model as an MLIP set."""
    from ..mlip.runner import DEFAULT_MACE_MODEL   # noqa: PLC0415 (lazy, like the bridge)
    cfg = StepConfig(kind="sp", calculation_type=JOB_PLACEHOLDER,
                     nprocs=int(nprocs), maxcore_mb=int(maxcore_mb))
    level = f"{cfg.functional} / {cfg.basis_set}"
    return [
        {"id": "seed-orca", "name": level, "kind": "ORCA",
         "text": build_input(cfg, GEOMETRY_PLACEHOLDER, CHARGE_PLACEHOLDER,
                             MULT_PLACEHOLDER)},
        {"id": "seed-mlip", "name": DEFAULT_MACE_MODEL, "kind": "MLIP",
         "text": (f"model {DEFAULT_MACE_MODEL}\ndevice auto\n"
                  f"nprocs {int(nprocs)}\n")},
    ]


def seed_texts(nprocs: int = 6, maxcore_mb: int = 2400) -> "dict[str, str]":
    """Each kind's seeded text — what the editor's "+ New" starts a set from."""
    return {s["kind"]: s["text"] for s in seed_sets(nprocs, maxcore_mb)}


def new_set_id() -> str:
    return "set-" + secrets.token_hex(4)


def _checked_common(d) -> "tuple[str, str, str, str]":
    if not isinstance(d, dict):
        raise ValueError("An inp set must be an object.")
    sid = d.get("id", "")
    if sid in (None, ""):
        sid = new_set_id()
    if (not isinstance(sid, str) or len(sid) > _MAX_ID
            or not _ID_RE.match(sid)):
        raise ValueError("An inp set id is 1 to 64 letters, digits, '-', '_' or '.'.")
    name = d.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("An inp set needs a name.")
    name = name.strip()
    if len(name) > _MAX_NAME:
        raise ValueError(f"An inp set name is at most {_MAX_NAME} characters.")
    text = d.get("text", "")
    if not isinstance(text, str):
        raise ValueError("An inp set text must be a string.")
    if len(text) > _MAX_TEXT:
        raise ValueError("That inp set text is too long.")
    return sid, name, str(d.get("kind", "")), text.replace("\r\n", "\n")


def validate_set(d) -> dict:
    """The normalized ``{id, name, kind, text}`` of a client-sent set, or a
    ValueError saying what is wrong. A missing id is minted. An ORCA text must
    carry {{GEOMETRY}} (without it the run would use whatever coordinates the
    text happens to hold, never the lane's) and {{JOB}} on its ``!`` line
    (without it every block would run the one job the text names, whatever
    its Type says); an MLIP text must parse."""
    sid, name, kind, text = _checked_common(d)
    if kind not in SET_KINDS:
        raise ValueError("An inp set kind is one of " + ", ".join(SET_KINDS) + ".")
    if kind == "MLIP":
        mlip_config_from_text(text)
    else:
        if GEOMETRY_PLACEHOLDER not in text:
            raise ValueError(f"An ORCA set must contain {GEOMETRY_PLACEHOLDER} where "
                             "the coordinates go.")
        if not job_line_has_placeholder(text):
            raise ValueError(f"An ORCA set must hold {JOB_PLACEHOLDER} on its ! line, "
                             "where the job keyword goes (each block's Type fills it "
                             "in: TightOpt, Freq, IRC, SP ...).")
    return {"id": sid, "name": name, "kind": kind, "text": text}


def unknown_placeholder_error(text: str) -> str:
    """The refusal for an ORCA text holding a {{...}} that is not one of
    KNOWN_PLACEHOLDERS (a misspelling, a lower-case {{job}}), or "". Nothing
    fills it, so it would reach ORCA as written. Checked when a set is SAVED
    (save_set), not when the file is read: a stored set is never dropped for
    a rule it predates."""
    unknown = sorted({m for m in _PLACEHOLDER_RE.findall(text or "")
                      if m not in KNOWN_PLACEHOLDERS})
    if not unknown:
        return ""
    return ("Unknown placeholder " + ", ".join(unknown) + " (known: "
            + ", ".join(KNOWN_PLACEHOLDERS) + "). Nothing fills it in, so it "
            "would reach ORCA as written.")


# ---- schema 1 -> 2 ----------------------------------------------------------

def _seeded_block(legacy_kind: str) -> "list[str]":
    """The extra block a schema-1 seed of this kind carried -- exactly the one
    the renderer now adds for that job (the same helpers wrote both)."""
    if legacy_kind == "Opt":
        return ["%geom", *_geom_job_lines("opt"), "end"]
    if legacy_kind == "OptTS":
        return ["%geom", *_geom_job_lines("ts_opt"), "end"]
    if legacy_kind == "IRC":
        return _irc_lines(StepConfig(kind="irc", irc_init_hess="read",
                                     irc_hess_file=HESS_PLACEHOLDER))
    return []


def _norm(line: str) -> str:
    return " ".join(line.split()).lower()


def _job_to_placeholder(lines: "list[str]") -> "list[str]":
    """The `!` lines with every job keyword taken out and one {{JOB}} where
    the first one stood (appended to the first `!` line when there was none;
    a text with no `!` line at all gets one)."""
    out, placed = [], False
    for ln in lines:
        s = ln.strip()
        if not s.startswith("!"):
            out.append(ln)
            continue
        toks, kept = s[1:].split(), []
        for t in toks:
            if _JOB_TOKEN.match(t):
                if not placed:
                    kept.append(JOB_PLACEHOLDER)
                    placed = True
            else:
                kept.append(t)
        out.append(("! " + " ".join(kept)).rstrip())
    if not placed:
        bang = next((i for i, ln in enumerate(out) if ln.strip().startswith("!")), None)
        if bang is None:
            out.insert(0, "! " + JOB_PLACEHOLDER)
        else:
            out[bang] = out[bang].rstrip() + " " + JOB_PLACEHOLDER
    return out


def _drop_block(lines: "list[str]", block: "list[str]") -> "list[str]":
    """``lines`` without the first run that IS ``block`` (whitespace and case
    aside); unchanged when there is none -- a block the user edited stays."""
    if not block:
        return lines
    want = [_norm(b) for b in block]
    n = len(want)
    for i in range(len(lines) - n + 1):
        if [_norm(x) for x in lines[i:i + n]] == want:
            return lines[:i] + lines[i + n:]
    return lines


def convert_legacy_text(legacy_kind: str, text: str) -> str:
    """A schema-1 ORCA set's text as a level: job keyword(s) -> {{JOB}}, the
    seeded extra block of its kind removed when it is there unchanged."""
    lines = text.replace("\r\n", "\n").split("\n")
    lines = _drop_block(lines, _seeded_block(legacy_kind))
    return "\n".join(_job_to_placeholder(lines))


def level_forms(text: str) -> "list[str]":
    """The level texts a schema-1 template's text becomes under the
    migration (convert_legacy_text, once per legacy kind; distinct, and not
    the text itself), or [] for a text that already holds {{JOB}}.

    A block made before sets were levels copied the OLD set's text
    (``... TightOpt``, its seeded ``%geom`` block); the migration turned the
    set into its level, so the block's text is no longer the set's verbatim
    although nothing was edited. The queue editor compares the set's text
    with these too (get_calc's ``levels``), so such a block does not read as
    modified, and finds the set it came from after a merge or a rename --
    by the migration's own rule, not a second copy of it."""
    if not text or JOB_PLACEHOLDER in text or GEOMETRY_PLACEHOLDER not in text:
        return []
    norm = text.replace("\r\n", "\n")
    out: "list[str]" = []
    for kind in LEGACY_KINDS:
        level = convert_legacy_text(kind, norm)
        if level != norm and level not in out:
            out.append(level)
    return out


def _free_name(name: str, legacy_kind: str, taken: "set[str]") -> str:
    if name.casefold() not in taken:
        return name
    base = f"{name} ({legacy_kind})"[:_MAX_NAME]
    out, i = base, 2
    while out.casefold() in taken:
        out = f"{base} {i}"
        i += 1
    return out


def migrate_sets(entries) -> "list[dict]":
    """Schema-1 entries as schema-2 sets: MLIP kept, each ORCA set converted
    (convert_legacy_text), sets whose converted text is identical merged into
    the first (its name and id kept), a name another kept ORCA set has given
    "(Kind)". Entries that were not valid sets are dropped one by one."""
    out: "list[dict]" = []
    texts: "dict[str, dict]" = {}
    ids: "set[str]" = set()
    orca_names: "set[str]" = set()
    for d in entries if isinstance(entries, list) else []:
        try:
            sid, name, kind, text = _checked_common(d)
        except ValueError:
            continue
        if sid in ids:
            continue
        if kind in LEGACY_KINDS:
            if GEOMETRY_PLACEHOLDER not in text:
                continue
            new_text = convert_legacy_text(kind, text)
            if new_text in texts:
                continue                     # merged into the first
            s = {"id": sid, "name": _free_name(name, kind, orca_names),
                 "kind": "ORCA", "text": new_text}
        else:
            s = {"id": sid, "name": name, "kind": kind, "text": text}
        try:
            s = validate_set(s)
        except ValueError:
            continue
        ids.add(sid)
        if s["kind"] == "ORCA":
            texts[s["text"]] = s
            orca_names.add(s["name"].casefold())
        out.append(s)
    return out


def _is_legacy(data: dict) -> bool:
    try:
        schema = int(data.get("schema", 1))
    except (TypeError, ValueError):
        schema = 1
    return schema < SCHEMA or any(
        isinstance(d, dict) and d.get("kind") in LEGACY_KINDS for d in data["sets"])


def _read(path: Path) -> "tuple[Optional[list[dict]], bool, bytes]":
    """(the stored sets, whether they were migrated from schema 1, the file's
    bytes), or (None, False, b"") when there is no usable file. Entries that
    do not validate are dropped one by one (a hand-edited file loses that
    entry, not the rest)."""
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None, False, b""
    if not isinstance(data, dict) or not isinstance(data.get("sets"), list):
        return None, False, b""
    if _is_legacy(data):
        return migrate_sets(data["sets"]), True, raw
    out, ids = [], set()
    for d in data["sets"]:
        try:
            s = validate_set(d)
        except ValueError:
            continue
        if s["id"] in ids:
            continue
        ids.add(s["id"])
        out.append(s)
    return out, False, raw


def _backup(path: Path, raw: bytes) -> None:
    """Keep the schema-1 file as BACKUP_NAME beside it -- once: a backup that
    is already there (an earlier migration's) is never overwritten."""
    dest = path.with_name(BACKUP_NAME)
    if dest.exists():
        return
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, dest)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _write(path: Path, sets: "list[dict]") -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"schema": SCHEMA, "sets": sets}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _current(path: Path, nprocs: int, maxcore_mb: int) -> "list[dict]":
    """The sets to change (caller holds the lock): the stored ones, a schema-1
    file migrated -- backed up first, OSError when that cannot be done, so the
    original is never replaced without its copy -- else the seeds."""
    sets, migrated, raw = _read(path)
    if sets is None:
        return seed_sets(nprocs, maxcore_mb)
    if migrated:
        _backup(path, raw)
    return sets


def load_sets(root: Optional[Path] = None, nprocs: int = 6,
              maxcore_mb: int = 2400) -> "list[dict]":
    """Every saved set. The first time (no file at all) the seeds are written
    and returned; an unreadable file reads as the seeds without being
    overwritten until the next save; a schema-1 file is backed up and
    migrated (in memory only, should either write fail). A user who deleted
    every set keeps an empty list — only a missing file seeds."""
    path = sets_file(root)
    with _lock:
        sets, migrated, raw = _read(path)
        if sets is not None:
            if migrated:
                try:
                    _backup(path, raw)
                    _write(path, sets)
                except OSError:
                    pass
            return sets
        seeds = seed_sets(nprocs, maxcore_mb)
        if not path.exists():
            try:
                _write(path, seeds)
            except OSError:
                pass
        return seeds


def save_set(d, root: Optional[Path] = None, nprocs: int = 6,
             maxcore_mb: int = 2400) -> dict:
    """Add a set, or replace the one with the same id; returns it as stored
    (with its id). ValueError when it does not validate or its name is taken
    by another set of its kind, OSError when the file cannot be written."""
    s = validate_set(d)
    if s["kind"] != "MLIP":
        err = unknown_placeholder_error(s["text"])
        if err:
            raise ValueError(err)
    path = sets_file(root)
    with _lock:
        sets = _current(path, nprocs, maxcore_mb)
        # a block names the set it came from by (kind, name), so two sets of
        # one kind may not share a name
        clash = next((o for o in sets if o["id"] != s["id"] and o["kind"] == s["kind"]
                      and o["name"].casefold() == s["name"].casefold()), None)
        if clash is not None:
            raise ValueError(f"There is already an {s['kind']} set named "
                             f"'{clash['name']}'.")
        for i, old in enumerate(sets):
            if old["id"] == s["id"]:
                sets[i] = s
                break
        else:
            if len(sets) >= _MAX_SETS:
                raise ValueError(f"At most {_MAX_SETS} inp sets can be kept.")
            sets.append(s)
        _write(path, sets)
    return s


def delete_set(set_id: str, root: Optional[Path] = None, nprocs: int = 6,
               maxcore_mb: int = 2400) -> bool:
    """Remove the set with this id. False when there is none."""
    path = sets_file(root)
    with _lock:
        sets = _current(path, nprocs, maxcore_mb)
        kept = [s for s in sets if s["id"] != set_id]
        if len(kept) == len(sets):
            return False
        _write(path, kept)
    return True


def get_set(set_id: str, root: Optional[Path] = None, nprocs: int = 6,
            maxcore_mb: int = 2400) -> "Optional[dict]":
    return next((s for s in load_sets(root, nprocs, maxcore_mb)
                 if s["id"] == set_id), None)
