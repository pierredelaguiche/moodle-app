"""Year folders: <study folder>/Y1/<module>, the same idea as week folders one level up.

A study folder is "divided by years" when it has at least one folder named like a year (Y1, Year 2, yr_3). Module folders
then live inside the year folders (a module folder directly in the study folder still works)."""
from __future__ import annotations

import re
import time
from pathlib import Path

YEAR_DIR = re.compile(r"^(y|yr|year)([\s_\-]*)0*(\d{1,2})$", re.I)
CODE_LEVEL = re.compile(r"[A-Z]{3,5}\.?(\d)\d{2}(?!\d)")


def _visible(d: Path) -> bool:
    return d.is_dir() and not d.is_symlink() and not d.name.startswith((".", "_"))


def year_dirs(root: Path) -> dict:
    """{year number: folder} of the year folders directly inside the study folder."""
    out = {}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            m = YEAR_DIR.match(d.name)
            if m and _visible(d):
                out.setdefault(int(m.group(3)), d)
    return out


def module_dirs(root: Path) -> list:
    """Every folder that can be a module, as a path relative to the study folder with '/': the folders directly inside it,
    and the folders inside its year folders ('Y1/MATH.101 Calculus')."""
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not _visible(d):
            continue
        if YEAR_DIR.match(d.name):
            out += [f"{d.name}/{k.name}" for k in sorted(d.iterdir()) if _visible(k)]
        else:
            out.append(d.name)
    return out


def year_name(existing, n: int) -> str:
    """Name for a new year folder that follows the ones already there ('Year 2' next to 'Year 1'); 'Y<n>' otherwise."""
    for name in existing:
        m = YEAR_DIR.match(name)
        if m:
            return f"{m.group(1)}{m.group(2)}{n}"
    return f"Y{n}"


def year_folder(root: Path, n: int) -> str:
    have = year_dirs(root)
    return have[n].name if n in have else year_name([d.name for d in have.values()], n)


def academic_year(ts) -> int:
    t = time.gmtime(ts)
    return t.tm_year if t.tm_mon >= 8 else t.tm_year - 1


def year_of(course, first_academic_year=None):
    """Which year of study a course belongs to: the level digit of its module code (CSCI.323 -> 3, MATH101 -> 1), else how
    many academic years after your first course it started. None when neither says."""
    text = f"{course.get('shortname') or ''} {course.get('fullname') or ''}".upper()
    m = CODE_LEVEL.search(text)
    if m and 1 <= int(m.group(1)) <= 9:
        return int(m.group(1))
    start = course.get("startdate") or 0
    if start and first_academic_year:
        n = academic_year(start) - first_academic_year + 1
        return n if 1 <= n <= 9 else None
    return None
