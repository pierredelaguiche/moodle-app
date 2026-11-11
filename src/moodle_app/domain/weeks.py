"""Week sub-folders: <module>/<category>/Week NN."""
from __future__ import annotations

import re
from pathlib import Path

WEEK_DIR = re.compile(r"^week[\s_\-]*0*(\d+)$", re.I)


WEEK_STYLE = re.compile(r"^(week)([\s_\-]*)(\d+)$", re.I)


def week_dirs(base: Path) -> dict:
    """{week number: folder} of the week sub-folders directly inside `base` ('Week 1', 'week_01', ...)."""
    out = {}
    if base.is_dir():
        for d in sorted(base.iterdir()):
            m = WEEK_DIR.match(d.name)
            if m and d.is_dir() and not d.is_symlink():
                out.setdefault(int(m.group(1)), d)
    return out


def week_name(existing, section: int) -> str:
    """Name for a new week folder that follows the ones already there ('week_03' next to 'week_02'); 'Week NN' otherwise."""
    styles = {}
    for name in existing:
        m = WEEK_STYLE.match(name)
        if m:
            digits = m.group(3)
            width = len(digits) if digits.startswith("0") or len(digits) > 1 else 0     # 'Week 1' alone says nothing about padding
            key = (m.group(1), m.group(2))
            styles.setdefault(key, []).append(width)
    if not styles:
        return f"Week {section:02d}"
    (word, sep), widths = max(styles.items(), key=lambda kv: len(kv[1]))
    padded = [w for w in widths if w]
    width = max(padded) if padded and len(padded) * 2 >= len(widths) else (1 if not padded else max(padded))
    return f"{word}{sep}{section:0{width}d}"


def week_dir(folder: Path, cat: str, section: int) -> Path:
    """An existing folder for that week ('Week 1', 'week_01', ...) is reused; otherwise a new one named like its siblings
    (default 'Week NN'). `cat` '.' means the module folder itself."""
    base = folder if cat in (".", "") else folder / cat
    have = week_dirs(base)
    if section in have:
        return have[section]
    return base / week_name([d.name for d in have.values()], section)


def is_divided(base: Path) -> bool:
    """True when `base` already has at least one week sub-folder: new files then go straight to their week."""
    return bool(week_dirs(base))


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
