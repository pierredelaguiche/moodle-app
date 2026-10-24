"""Recognise files you already have, so nothing is downloaded twice. Reads module folders (read-only)."""
from __future__ import annotations

import re
from pathlib import Path

from .util import norm

ADOPT_MIN_SIZE = 10_000        # same-size local file >= this many bytes counts as "already have it"
VERSIONS_DIR = "_old_versions"


def build_index(folder: Path):
    """size -> [paths] for every real file already inside a module folder (hidden and underscore folders skipped)."""
    idx = {}
    if folder.exists():
        for p in folder.rglob("*"):
            rel = p.relative_to(folder).parts
            if p.is_file() and not any(x.startswith((".", "_")) for x in rel):
                idx.setdefault(p.stat().st_size, []).append(p)
    return idx


def find_duplicate(index, claimed, fname, size):
    """Order: same size + same name  >  same file name (any size)  >  same size when big enough to be unique.
    Paths in `claimed` (already tied to another Moodle file) are skipped."""
    allp = [p for ps in index.values() for p in ps if str(p) not in claimed]
    cands = [p for p in index.get(size, []) if str(p) not in claimed] if size else []
    stem = norm(Path(fname).stem)
    for p in cands:
        if norm(p.stem) == stem:
            return p
    for p in allp:
        if p.name.lower() == fname.lower():
            return p
    if cands and size >= ADOPT_MIN_SIZE:
        return cands[0]
    return None


def find_moved(index, claimed, old: Path, size):
    """A tracked file vanished from its recorded path: is it somewhere else in the module folder?"""
    cands = [p for p in index.get(size, []) if str(p) not in claimed] if size else []
    same = [p for p in cands if norm(p.stem) == norm(old.stem)]
    if same:
        return same[0]
    if len(cands) == 1 and size >= ADOPT_MIN_SIZE:
        return cands[0]
    return None


def match_folder(course, folders):
    """Match a Moodle course to a local module folder via module code (e.g. CSCI.323)."""
    text = f"{course.get('shortname', '')} {course.get('fullname', '')}"
    for code in re.findall(r"[A-Z]{3,5}\.?\d{3}", text.upper()):
        norm_code = code.replace(".", "")
        for f in folders:
            if f.name.upper().replace(".", "").startswith(norm_code):
                return f
    return None
