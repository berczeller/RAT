"""RAT's per-game records, kept in a `.rat` folder next to the game's executable.

  record.json   the injector install (files, backups, options)
  backup/       game files the injector replaced
  reshade.json  ReShade install (proxy, files, preset)
  renodx.json   RenoDX HDR mod
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .util import read_json, write_json

REC_DIR = ".rat"
REC_FILE = "record.json"


def rec_file(d: Path) -> Path:
    """The injector install record in folder d."""
    return Path(d) / REC_DIR / REC_FILE


def extra_file(d: Path, name: str) -> Path:
    return Path(d) / REC_DIR / f"{name}.json"


def read_extra(d: Path | None, name: str) -> dict | None:
    if not d:
        return None
    return read_json(extra_file(d, name), None)


def write_extra(d: Path, name: str, data: dict) -> None:
    write_json(extra_file(d, name), data)


def remove_extra(d: Path, name: str) -> None:
    extra_file(d, name).unlink(missing_ok=True)
    cleanup(d)


def cleanup(d: Path) -> None:
    """Removes the record folder once nothing is recorded in it any more."""
    r = Path(d) / REC_DIR
    try:
        if r.is_dir() and not any(p.suffix == ".json" for p in r.iterdir()):
            shutil.rmtree(r, ignore_errors=True)
    except OSError:
        pass
