"""Week sub-folders: <module>/<category>/Week NN."""
from __future__ import annotations

import re
from pathlib import Path

WEEK_DIR = re.compile(r"^week[\s_\-]*0*(\d+)$", re.I)


def week_dir(folder: Path, cat: str, section: int) -> Path:
    """An existing folder for that week ('Week 1', 'week_01', ...) is reused; otherwise 'Week NN'."""
    base = folder / cat
    if base.is_dir():
        for d in sorted(base.iterdir()):
            m = WEEK_DIR.match(d.name)
            if d.is_dir() and m and int(m.group(1)) == section:
                return d
    return base / f"Week {section:02d}"


def week_of_path(parts) -> "int | None":
    for p in parts:
        m = re.search(r"week[\s_\-]*0*(\d+)", p, re.I)
        if m:
            return int(m.group(1))
    return None


SECTION_WEEK = re.compile(r"\b(?:week|wk|session)[\s_\-]*0*(\d{1,2})\b", re.I)
NAME_WEEK = re.compile(r"(?<![A-Za-z])(?:week|wk)[\s_\-]*0*(\d{1,2})(?!\d)|(?<![A-Za-z])[wW]0*(\d{1,2})(?!\d|[cC]\b)")
LEAD_NUM = re.compile(r"^0*(\d{1,2})(?=[\s_\-.])")


def week_for(section_no: int, section_name: str = "", *names: str, lead: bool = False):
    """(week, why). Moodle's section index is often off from the real week (a 'General' block, extra sections),
    so, in order: a 'Week N' in the section name, a week token in a file/activity name (week2, w02Lab), a leading
    lecture number (03_Runtime.pdf; only when `lead`), then the section index."""
    for n in names:
        m = NAME_WEEK.search(n or "")
        if m:
            v = int(m.group(1) or m.group(2))
            if 0 < v <= 60:
                return v, "file name"
    if lead and names:
        m = LEAD_NUM.match(names[0] or "")
        if m and 0 < int(m.group(1)) <= 60:
            return int(m.group(1)), "lecture number"
    m = SECTION_WEEK.search(section_name or "")
    if m and 0 < int(m.group(1)) <= 60:
        return int(m.group(1)), "section name"
    if section_no and section_no > 0:
        return section_no, "section index"
    return 0, ""
