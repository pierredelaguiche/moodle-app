"""One sync run: courses -> structure + files -> deadlines -> announcements, recorded as a run with its changes.

Network (Moodle fetchers) and bookkeeping (repositories) meet only here, so each can be tested alone.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..domain.duplicates import match_folder
from ..moodle import fetch
from ..moodle.client import MoodleError, NetworkError
from .files import FileService


def too_soon(ctx) -> bool:
    last = ctx.history.last_run("sync")
    minutes = ctx.settings.sync.get("min_interval_minutes", 15)
    return bool(last and last["finished"] and ctx.ts() - last["finished"] < minutes * 60)


def sync_courses(ctx, m, only=None):
    """Update the courses table; returns (site, tracked course rows)."""
    site = fetch.fetch_site(m)
    ctx.settings_repo.set("site", {"release": site["release"], "sitename": site["sitename"], "functions": len(site["functions"])})
    ctx.caps = fetch.Capabilities(site["functions"] or None)
    exclude = ctx.settings.exclude_course_ids
    root = ctx.root
    folders = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    now = ctx.ts()
    for c in fetch.fetch_courses(m, site["userid"]):
        if str(c["id"]) in exclude:
            continue
        f = match_folder(c, folders)
        ctx.courses.upsert(c, f.name if f else None, f is not None, now)
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
            summary["deadlines"] = sync_deadlines(ctx, m, ids, warn)
        if "announcements" in what and ids:
            summary["announcements"] = sync_announcements(ctx, m, ids, warn)
        ctx.history.finish(ctx.run_id, ctx.ts(), True, None, summary)
        ctx.conn.commit()
        return summary
    except Exception as e:
        ctx.conn.rollback()
        ctx.history.finish(ctx.run_id, ctx.ts(), False, str(e), summary)
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
