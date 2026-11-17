"""
Inp sets: the queue editor's saved input templates.

An inp set is ``{id, name, kind, text}``. ``kind`` is one of SET_KINDS and says
which step of a lane it is (MLIP / Opt / OptTS / Freq / IRC / SP); ``text`` is a
raw ORCA input carrying placeholders the engine fills at run time —
``{{GEOMETRY}}`` (render_raw_input), ``{{CHARGE}}`` / ``{{MULT}}`` and
``{{HESS}}`` (QueueEngine._render_template_values). An MLIP set's text is not
an ORCA input at all but ``key value`` lines (MLIP_KEYS) mapped onto the MLIP
fields of StepConfig (mlip_config_from_text).

Stored in ``user_data_root()/inp_sets.json`` (tmp + ``os.replace``, like the
settings and the session). The first load — no file yet — seeds one set per
kind from ORCAdesk's own defaults: StepConfig + build_input, so a seeded set is
exactly the input the guided builder writes at the user's default %pal /
%maxcore, with placeholders where the coordinates, charge, multiplicity and
Hessian go. A file that cannot be read degrades to the seeds in memory and is
replaced on the next save (P32); it is never a crash.

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
                                    HESS_PLACEHOLDER, MULT_PLACEHOLDER,
                                    StepConfig, build_input)
from ..paths import user_data_root

FILE_NAME = "inp_sets.json"

#: what a set may be, in the order the editor lists them
SET_KINDS = ("MLIP", "Opt", "OptTS", "Freq", "IRC", "SP")

#: the calculation kind a node built from a set runs (Freq after a TS
#: optimization is a ts_freq — see calc_kind_for_set)
_KIND_OF_SET = {"MLIP": "mlip_opt", "Opt": "opt", "OptTS": "ts_opt",
                "Freq": "freq", "IRC": "irc", "SP": "sp"}

#: an MLIP set's keys -> the StepConfig field each one sets
MLIP_KEYS = {"model": "mlip_model", "device": "mlip_device",
             "nprocs": "nprocs", "env": "mlip_env_id"}
_MLIP_DEVICES = {"": "", "auto": "", "cpu": "cpu", "cuda": "cuda"}

_MAX_NAME = 120
_MAX_TEXT = 200_000
_MAX_ID = 64
_MAX_SETS = 500
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_lock = threading.Lock()


def sets_file(root: Optional[Path] = None) -> Path:
    return (root or user_data_root()) / FILE_NAME


def calc_kind_for_set(set_kind: str, after_kind: str = "") -> str:
    """The calculation kind a node built from a ``set_kind`` set runs. A Freq
    set placed after a TS optimization is a ``ts_freq`` (its validation wants
    exactly one imaginary mode, not zero)."""
    if set_kind == "Freq" and after_kind in ("ts_opt", "ts_opt_freq"):
        return "ts_freq"
    try:
        return _KIND_OF_SET[set_kind]
    except KeyError:
        raise ValueError(f"Unknown inp set kind '{set_kind}'.") from None


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


def _orca_text(cfg: StepConfig) -> str:
    return build_input(cfg, GEOMETRY_PLACEHOLDER, CHARGE_PLACEHOLDER, MULT_PLACEHOLDER)


def seed_sets(nprocs: int = 6, maxcore_mb: int = 2400) -> "list[dict]":
    """One set per kind from ORCAdesk's own defaults (StepConfig + build_input),
    at the given %pal nprocs / %maxcore, written literally into the text so the
    engine admits the job by what it asks (core/resources.py reads a raw
    input's own %pal / %maxcore)."""
    from ..mlip.runner import DEFAULT_MACE_MODEL   # noqa: PLC0415 (lazy, like the bridge)
    common = dict(nprocs=int(nprocs), maxcore_mb=int(maxcore_mb))
    level = f"{StepConfig().functional} / {StepConfig().basis_set}"
    orca = [
        ("Opt", StepConfig(kind="opt", calculation_type="TightOpt", **common)),
        ("OptTS", StepConfig(kind="ts_opt", calculation_type="OptTS", **common)),
        ("Freq", StepConfig(kind="freq", calculation_type="Freq", **common)),
        ("IRC", StepConfig(kind="irc", calculation_type="IRC",
                           irc_init_hess="read", irc_hess_file=HESS_PLACEHOLDER,
                           **common)),
        ("SP", StepConfig(kind="sp", calculation_type="SP", **common)),
    ]
    sets = [{"id": "seed-mlip", "name": DEFAULT_MACE_MODEL, "kind": "MLIP",
             "text": (f"model {DEFAULT_MACE_MODEL}\ndevice auto\n"
                      f"nprocs {int(nprocs)}\n")}]
    sets += [{"id": f"seed-{kind.lower()}", "name": level, "kind": kind,
              "text": _orca_text(cfg)} for kind, cfg in orca]
    return sets


def new_set_id() -> str:
    return "set-" + secrets.token_hex(4)


def validate_set(d) -> dict:
    """The normalized ``{id, name, kind, text}`` of a client-sent set, or a
    ValueError saying what is wrong. A missing id is minted; a non-MLIP text
    must carry {{GEOMETRY}} (without it the run would use whatever coordinates
    the text happens to hold, never the lane's); an MLIP text must parse."""
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
    kind = d.get("kind", "")
    if kind not in SET_KINDS:
        raise ValueError("An inp set kind is one of " + ", ".join(SET_KINDS) + ".")
    text = d.get("text", "")
    if not isinstance(text, str):
        raise ValueError("An inp set text must be a string.")
    if len(text) > _MAX_TEXT:
        raise ValueError("That inp set text is too long.")
    text = text.replace("\r\n", "\n")
    if kind == "MLIP":
        mlip_config_from_text(text)
    elif GEOMETRY_PLACEHOLDER not in text:
        raise ValueError(f"An inp set must contain {GEOMETRY_PLACEHOLDER} where the "
                         "coordinates go.")
    return {"id": sid, "name": name, "kind": kind, "text": text}


def _read(path: Path) -> "Optional[list[dict]]":
    """The stored sets, or None when there is no usable file. Entries that do
    not validate are dropped one by one (a hand-edited file loses that entry,
    not the rest)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sets"), list):
        return None
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
    return out


def _write(path: Path, sets: "list[dict]") -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps({"schema": 1, "sets": sets}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def load_sets(root: Optional[Path] = None, nprocs: int = 6,
              maxcore_mb: int = 2400) -> "list[dict]":
    """Every saved set. The first time (no file at all) the seeds are written
    and returned; an unreadable file reads as the seeds without being
    overwritten until the next save. A user who deleted every set keeps an
    empty list — only a missing file seeds."""
    path = sets_file(root)
    with _lock:
        sets = _read(path)
        if sets is not None:
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
    (with its id). ValueError when it does not validate, OSError when the file
    cannot be written."""
    s = validate_set(d)
    path = sets_file(root)
    with _lock:
        sets = _read(path)
        if sets is None:
            sets = seed_sets(nprocs, maxcore_mb)
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
        sets = _read(path)
        if sets is None:
            sets = seed_sets(nprocs, maxcore_mb)
        kept = [s for s in sets if s["id"] != set_id]
        if len(kept) == len(sets):
            return False
        _write(path, kept)
    return True


def get_set(set_id: str, root: Optional[Path] = None, nprocs: int = 6,
            maxcore_mb: int = 2400) -> "Optional[dict]":
    return next((s for s in load_sets(root, nprocs, maxcore_mb)
                 if s["id"] == set_id), None)
