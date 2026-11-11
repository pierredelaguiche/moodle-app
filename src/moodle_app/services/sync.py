"""One sync run: courses -> structure + files -> deadlines -> announcements, recorded as a run with its changes.

Network (Moodle fetchers) and bookkeeping (repositories) meet only here, so each can be tested alone.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.duplicates import match_folder
from ..domain.years import module_dirs, year_dirs
from ..moodle import fetch
from ..moodle.client import MoodleError, NetworkError
from .files import FileService


def too_soon(ctx) -> bool:
    last = ctx.history.last_run("sync")
    minutes = ctx.settings.sync.get("min_interval_minutes", 15)
    return bool(last and last["finished"] and ctx.ts() - last["finished"] < minutes * 60)


COURSE_MAP = "course_folders"


def course_map(ctx) -> dict:
    """{course id (text): folder name in the study folder} for courses added by hand. A course is otherwise tracked only
    when a folder starts with its module code, which leaves out earlier years and courses without a code."""
    return dict(ctx.settings_repo.get(COURSE_MAP, {}) or {})


def _holds_files(p: Path) -> bool:
    return p.is_dir() and any(x.is_file() and x.name != ".DS_Store" for x in p.rglob("*"))


def _adopt_folder(ctx, root, old, new):
    ctx.files.rewrite_paths(old, new)
    ctx.notes_manifest.rewrite(old, new)
    ctx.targets.rewrite(old, new)
    p = root / old
    if p.is_dir() and not p.is_symlink():                      # only ever an empty tree the app made itself
        for d in sorted((x for x in p.rglob("*")), key=lambda x: len(x.parts), reverse=True):
            try:
                d.unlink() if d.is_file() else d.rmdir()
            except OSError:
                pass
        try:
            p.rmdir()
        except OSError:
            pass
    ctx.log(f"  {old}: using the existing folder {new}")


def follow_root(ctx):
    """The study folder was changed to a folder above the old one (to hold Y1, Y2, Y3) or back down: keep every record
    pointing at the same file by adding or removing the part of the path in between. Anything else is left alone."""
    root = ctx.root.resolve()
    old = ctx.settings_repo.get("root_path")
    if old and old != str(root):
        oldp = Path(old)
        add = oldp.relative_to(root).as_posix() if root in oldp.parents else None
        cut = root.relative_to(oldp).as_posix() if oldp in root.parents else None
        c = ctx.conn
        if add:
            c.execute("UPDATE files SET rel_path = ? || '/' || rel_path WHERE rel_path IS NOT NULL AND rel_path != ''", (add,))
            c.execute("UPDATE courses SET folder_rel = ? || '/' || folder_rel WHERE folder_rel IS NOT NULL AND folder_rel != ''", (add,))
            c.execute("UPDATE notes_manifest SET rel = ? || '/' || rel", (add,))
            c.execute("UPDATE file_targets SET folder_rel = CASE WHEN folder_rel = '' THEN ? ELSE ? || '/' || folder_rel END", (add, add))
            ctx.settings_repo.set(COURSE_MAP, {k: f"{add}/{v}" for k, v in course_map(ctx).items()})
            ctx.log(f"  study folder moved up: records now start with {add}/")
        elif cut:
            n = len(cut) + 1
            for table, col in (("files", "rel_path"), ("courses", "folder_rel"), ("notes_manifest", "rel"), ("file_targets", "folder_rel")):
                c.execute(f"UPDATE OR IGNORE {table} SET {col} = substr({col}, ?) WHERE substr({col}, 1, ?) = ?", (n + 1, n, cut + "/"))
            ctx.settings_repo.set(COURSE_MAP, {k: v[n:] for k, v in course_map(ctx).items() if v.startswith(cut + "/")})
            ctx.log(f"  study folder moved down into {cut}: records shortened")
    if old != str(root):
        ctx.settings_repo.set("root_path", str(root))
    follow_modules(ctx, root)


def follow_modules(ctx, root):
    """A module folder you moved into a year folder by hand (CSCI.323 X -> Y3/CSCI.323 X), or records written before the
    study folder moved up a level: when a recorded module folder is gone from where it was and exactly one year folder
    holds a folder of that name, every record follows it there (otherwise the old place would show as 'will be created')."""
    c = ctx.conn
    firsts = {r[0].split("/")[0] for r in c.execute("SELECT rel_path FROM files WHERE rel_path IS NOT NULL AND rel_path != ''")}
    firsts |= {r[0] for r in c.execute("SELECT folder_rel FROM courses WHERE folder_rel IS NOT NULL AND folder_rel != ''") if "/" not in r[0]}
    ydirs = list(year_dirs(root).values())
    for m in sorted(firsts):
        if not ydirs or (root / m).exists():
            continue
        hits = [y for y in ydirs if (y / m).is_dir()]
        if len(hits) != 1:
            continue
        new = f"{hits[0].name}/{m}"
        ctx.files.rewrite_paths(m, new)
        ctx.notes_manifest.rewrite(m, new)
        ctx.targets.rewrite(m, new)
        c.execute("UPDATE courses SET folder_rel=? WHERE folder_rel=?", (new, m))
        ctx.settings_repo.set(COURSE_MAP, {k: (new if v == m else v) for k, v in course_map(ctx).items()})
        ctx.log(f"  {m} is now in {hits[0].name}: records follow it")


def sync_courses(ctx, m, only=None):
    """Update the courses table; returns (site, tracked course rows)."""
    site = fetch.fetch_site(m)
    ctx.settings_repo.set("site", {"release": site["release"], "sitename": site["sitename"], "functions": len(site["functions"])})
    ctx.caps = fetch.Capabilities(site["functions"] or None)
    exclude = ctx.settings.exclude_course_ids
    root = ctx.root
    follow_root(ctx)
    folders = [root / rel for rel in module_dirs(root)]       # module folders: directly here, or inside a year folder (Y1/...)
    now = ctx.ts()
    added = course_map(ctx)                                   # courses you added by hand: {course id: folder name}
    courses = list(fetch.fetch_courses(m, site["userid"]))
    extra = [int(i) for i in added if int(i) not in {c["id"] for c in courses}]
    if extra:                                                 # not enrolled (any more): ask for them by id; a failure keeps the old rows
        try:
            courses += fetch.fetch_courses_by_id(m, extra)
        except Exception as e:
            ctx.log(f"  ! could not read the courses you added ({e})")
    for c in courses:
        if str(c["id"]) in exclude:
            continue
        name = added.get(str(c["id"]))
        if name:
            f = match_folder(c, [x for x in folders if x != root / name])
            if f is not None and not _holds_files(root / name):
                # you followed this course into a new folder, but a folder with its module code exists (in a year folder,
                # say) and the new one is still empty: the existing folder is the course's home, not a second copy
                _adopt_folder(ctx, root, name, f.relative_to(root).as_posix())
                added.pop(str(c["id"]))
                ctx.settings_repo.set(COURSE_MAP, added)
                folders = [x for x in folders if x != root / name]       # the emptied folder is gone: never match it again
                ctx.courses.upsert(c, f.relative_to(root).as_posix(), True, now)
                continue
        if name:
            (root / name).mkdir(parents=True, exist_ok=True)
            ctx.courses.upsert(c, name, True, now)
            continue
        f = match_folder(c, folders)
        ctx.courses.upsert(c, f.relative_to(root).as_posix() if f else None, f is not None, now)
    rows = ctx.courses.tracked()
    if only:
        rows = [r for r in rows if only.lower() in (r["fullname"] + r["shortname"]).lower()]
    return site, rows


def run_sync(ctx, m=None, what=None, only_course=None):
    """Returns a summary dict. Never leaves a half-written run: errors are recorded on the run and re-raised."""
    m = m or ctx.moodle
    what = set(what or [k for k in ("files", "deadlines", "announcements") if ctx.settings.sync.get(k, True)])
    ctx.run_id = ctx.history.start("sync", ctx.ts())
    ctx.conn.commit()
    summary = {"warnings": []}
    warn = lambda msg: (summary["warnings"].append(msg), ctx.log(f"  ! {msg}"))
    try:
        site, rows = sync_courses(ctx, m, only_course)
        summary["courses"] = len(rows)
        files = FileService(ctx)
        if "files" in what:
            new = upd = adopted = unchanged = 0
            ctx.pending.clear()
            for c in rows:
                ctx.check()                                        # a safe point: between courses, outside any transaction
                try:
                    sections, activities, items = fetch.fetch_contents(m, c["id"])
                except (MoodleError, NetworkError) as e:
                    warn(f"could not read {c['fullname']}: {e}")
                    continue
                with ctx.conn:
                    ctx.structure.replace_course(c["id"], sections, activities, ctx.ts())
                    r = files.reconcile(c, items)
                new += sum(1 for e in r.pending if e.kind == "NEW")
                upd += sum(1 for e in r.pending if e.kind == "UPD")
                adopted += r.adopted
                unchanged += r.unchanged
            with ctx.conn:
                summary["regrouped"] = files.regroup()
            summary.update(files_new=new, files_updated=upd, files_matched=adopted, files_unchanged=unchanged)
            ctx.log(f"Files: {unchanged} downloaded, {adopted} existing files matched, {new} new, {upd} updated.")
        ids = [c["id"] for c in rows]
        if "deadlines" in what and ids:
            ctx.check()
            summary["deadlines"] = sync_deadlines(ctx, m, ids, warn)
        if "announcements" in what and ids:
            ctx.check()
            summary["announcements"] = sync_announcements(ctx, m, ids, warn)
        ctx.history.finish(ctx.run_id, ctx.ts(), True, None, summary)
        ctx.conn.commit()
        return summary
    except BaseException as e:                                     # includes a cancelled run: the record still says how it ended
        ctx.conn.rollback()
        ctx.history.finish(ctx.run_id, ctx.ts(), False, str(e) or type(e).__name__.lower(), summary)
        ctx.conn.commit()
        raise


def sync_deadlines(ctx, m, course_ids, warn):
    caps, site_url, now = getattr(ctx, "caps", fetch.Capabilities()), ctx.site_url, ctx.ts()
    got = {"assign": fetch.fetch_assignments(m, caps, course_ids, site_url, warn),
           "quiz": fetch.fetch_quizzes(m, caps, course_ids, site_url, warn),
           "calendar": fetch.fetch_events(m, caps, set(course_ids), now, warn)}
    days = ctx.settings.sync.get("submission_status_days", 30)
    stats = {"new": 0, "moved": 0, "status": 0, "gone": 0}
    present = set()
    with ctx.conn:
        for source, items in got.items():
            if items is None:
                continue
            for d in items:
                present.add(d["id"])
                old = ctx.deadlines.get(d["id"])
                if source == "assign" and d.get("assignid") and d["due_ts"] >= now - days * 86400 and not (old and old["status"] == "graded"):
                    st = fetch.fetch_submission_status(m, caps, d["assignid"], warn)
                    if st:
                        d["status"], d["grade_text"] = st[0], st[1]
                        if st[2] and st[2] > d["due_ts"]:
                            d["due_ts"] = st[2]                        # an extension granted to you is your real deadline
                result, was = ctx.deadlines.put(d, now)
                if result == "new":
                    stats["new"] += 1
                    ctx.change("deadline_new", d["course_id"], d["id"], {"title": d["title"], "due": d["due_ts"]})
                elif result == "moved":
                    stats["moved"] += 1
                    ctx.change("deadline_changed", d["course_id"], d["id"], {"title": d["title"], "from": was, "to": d["due_ts"]})
                if old and d.get("status") and old["status"] != d["status"]:
                    stats["status"] += 1
                    ctx.change("deadline_status", d["course_id"], d["id"], {"title": d["title"], "from": old["status"], "to": d["status"]})
        ok_sources = [s for s, items in got.items() if items is not None]
        gone = ctx.deadlines.mark_gone(present, ok_sources, now)
        stats["gone"] = len(gone)
        for g in gone:
            ctx.change("deadline_gone", None, g)
    ctx.log(f"Deadlines: {stats['new']} new, {stats['moved']} moved, {stats['status']} status changes, {stats['gone']} gone.")
    return stats


def sync_announcements(ctx, m, course_ids, warn):
    caps, now = getattr(ctx, "caps", fetch.Capabilities()), ctx.ts()
    items = fetch.fetch_announcements(m, caps, course_ids, warn)
    if items is None:
        return {"new": 0}
    new = 0
    with ctx.conn:
        for a in items:
            if a["course_id"] not in course_ids:
                continue
            if ctx.announcements.put(a, now):
                new += 1
                ctx.change("announcement", a["course_id"], str(a["id"]), {"subject": a["subject"]})
    ctx.log(f"Announcements: {new} new.")
    return {"new": new}
