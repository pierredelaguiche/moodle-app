"""Check where files were put: the week folder and the category against what the file's own names say."""
from __future__ import annotations

from ..domain.classify import classify
from ..domain.weeks import week_dir, week_for, week_of_path


def audit(ctx):
    """Rows for files whose folder disagrees with the derived week/category. `auto` = the old section-index rule put it
    there (safe to move); otherwise you probably placed it yourself and it is only reported."""
    cats, wk_cats = ctx.settings.categories, ctx.settings.week_folders["categories"]
    names = {c["id"]: c for c in ctx.courses.tracked()}
    out = []
    for r in ctx.files.all():
        if r.status not in ("downloaded", "pending") or not r.rel_path or r.course_id not in names:
            continue
        parts = r.rel_path.split("/")
        want, why = week_for(r.section_no, r.sec_name, r.filename, r.moodle_name, lead=True)
        have = week_of_path(parts[1:-1])
        cat = parts[1] if len(parts) > 2 else "."
        row = dict(key=r.key, file=r.filename, path=r.rel_path, section=r.section_no, section_name=r.sec_name,
                   week_now=have, week_should=want or None, why=why, issues=[])
        if cat in wk_cats and want and have and have != want:
            row["issues"].append(f"week {have} -> {want} ({why})")
            row["auto"] = have == r.section_no
        if cat in wk_cats and want and not have and len(parts) > 2:
            row["issues"].append(f"no week folder; belongs to week {want} ({why})")
        guess = classify(cats, r.filename, r.moodle_name, r.sec_name)
        if not r.adopted and guess != "." and cat not in (guess, ".") and cat != parts[0] and guess in cats:
            row["issues"].append(f"category folder '{cat}' but name suggests '{guess}'")
        if row["issues"]:
            out.append(row)
    return out


def fix(ctx, rows, layout):
    """Move the auto-placed downloaded files with a wrong week into the right Week folder."""
    moved = []
    for row in rows:
        if not row.get("auto") or row["week_should"] is None:
            continue
        rec = ctx.files.get(row["key"])
        if rec is None or rec.status != "downloaded":
            continue
        parts = rec.rel_path.split("/")
        target = layout.rel(week_dir(ctx.root / parts[0], parts[1], row["week_should"]))
        res = layout.move([rec.rel_path], target)
        moved += res["moved"]
    return moved
