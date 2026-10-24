"""The digest: what changed since you last looked, what is due, where you are behind, and what to do first.

Pure read-model over the database. `build` returns data for the page, `render_text` the same as plain text for the terminal.
"""
from __future__ import annotations

import json
import time

from ..domain.text import html_to_text
from . import tracker

DAY = 86400


def _day_label(due, now):
    d = (due - now) / DAY
    if d < -1:
        return f"{int(-d)} days overdue" if int(-d) != 1 else "1 day overdue"
    if d < 0:
        return "overdue"
    if d < 1:
        return "due within 24 h"
    return f"due in {int(d)} day{'s' if int(d) != 1 else ''}"


def build(ctx, since=None, now=None):
    now = now or ctx.ts()
    seen = ctx.settings_repo.get("digest_seen")
    since = since if since is not None else (seen or now - 7 * DAY)
    rows = ctx.deadlines.upcoming(now - 14 * DAY, include_hidden=False)
    open_ = [r for r in rows if not r["done"] and r["status"] == "open"]
    overdue = [r for r in open_ if r["due_ts"] < now]
    soon = [r for r in open_ if now <= r["due_ts"] < now + 7 * DAY]
    later = [r for r in open_ if now + 7 * DAY <= r["due_ts"] < now + 21 * DAY]

    def dl(r):
        return {"id": r["id"], "course": r["course_short"], "title": r["title"], "due_ts": r["due_ts"], "url": r["url"],
                "when": _day_label(r["due_ts"], now), "kind": r["kind"]}

    ann = ctx.announcements.recent(8, unread_only=True)
    unread = ctx.conn.execute("SELECT COUNT(*) FROM announcements WHERE read=0").fetchone()[0]
    new_files = ctx.conn.execute("SELECT COUNT(*) FROM files WHERE status='pending' AND upd_modified IS NULL").fetchone()[0]
    upd_files = ctx.conn.execute("SELECT COUNT(*) FROM files WHERE status='downloaded' AND upd_modified IS NOT NULL").fetchone()[0]
    fresh = ctx.conn.execute("SELECT COUNT(*) FROM files WHERE first_seen>=? AND status!='ignored'", (since,)).fetchone()[0]
    moved = []
    for ch in ctx.history.recent_changes(100, since=since, kind="deadline_changed"):
        try:
            d = json.loads(ch["detail"] or "{}")
        except ValueError:
            d = {}
        moved.append({"ref": ch["ref"], "title": d.get("title", ch["ref"]), "from": d.get("from"), "to": d.get("to"), "ts": ch["ts"]})

    tr = tracker.build(ctx, now)
    behind = []
    for c in tr["courses"]:
        for w in c["weeks"]:
            if w["state"] == "behind":
                behind.append({"course": c["short"], "course_id": c["id"], "week": w["no"], "name": w["name"],
                               "left": w["total"] - w["reviewed"], "total": w["total"]})
    cur = [{"course": c["short"], "week": c["current_week"], "pct": c["pct"], "behind": c["behind"]} for c in tr["courses"]
           if c["current_week"] and c["phase"] == "running"]

    focus = []
    for r in overdue[:3]:
        focus.append({"level": "urgent", "text": f"{r['title']} ({r['course_short']}) is overdue. Submit it or tick it off.", "go": "deadlines"})
    for r in soon[:4]:
        focus.append({"level": "soon", "text": f"{r['title']} ({r['course_short']}) is {_day_label(r['due_ts'], now)}.", "go": "deadlines"})
    for b in sorted(behind, key=lambda b: -b["left"])[:3]:
        focus.append({"level": "study", "text": f"{b['course']}: {b['left']} file(s) left to review in {b['name']}.", "go": "tracker"})
    if new_files:
        focus.append({"level": "files", "text": f"{new_files} new file(s) on Moodle are waiting for you to download or ignore.", "go": "files"})
    if unread:
        focus.append({"level": "info", "text": f"{unread} unread announcement(s).", "go": "announcements"})

    focus = focus[:6]
    last = ctx.history.last_run("sync")
    return {"now": now, "since": since, "last_sync": last["finished"] if last else None,
            "overdue": [dl(r) for r in overdue], "soon": [dl(r) for r in soon], "later": [dl(r) for r in later],
            "announcements": {"unread": unread, "items": [{"id": a["id"], "course": a["course_short"], "subject": a["subject"],
                                                           "created": a["created"], "text": html_to_text(a["message"])[:300]} for a in ann]},
            "files": {"new": new_files, "updated": upd_files, "since": fresh},
            "moved": moved, "behind": behind, "weeks_now": cur, "focus": focus}


def mark_seen(ctx, now=None):
    with ctx.conn:
        ctx.settings_repo.set("digest_seen", now or ctx.ts())


def render_text(d, tz=None):
    def t(ts):
        return time.strftime("%a %d %b %H:%M", time.localtime(ts))
    out = [f"Digest, {time.strftime('%A %d %B %Y', time.localtime(d['now']))}"]
    out.append("Last sync: " + (t(d["last_sync"]) if d["last_sync"] else "never"))
    if d["focus"]:
        out += ["", "Do first:"] + [f"  - {f['text']}" for f in d["focus"]]
    for title, key in (("Overdue", "overdue"), ("Due in the next 7 days", "soon"), ("Coming up (3 weeks)", "later")):
        if d[key]:
            out += ["", title + ":"] + [f"  {t(x['due_ts'])}  {x['course']:<10} {x['title']}  ({x['when']})" for x in d[key]]
    if d["moved"]:
        out += ["", "Moved deadlines:"] + [f"  {m['title']}: {t(m['from']) if m['from'] else '?'} -> {t(m['to']) if m['to'] else '?'}" for m in d["moved"]]
    if d["behind"]:
        out += ["", "Behind:"] + [f"  {b['course']} {b['name']}: {b['left']}/{b['total']} to review" for b in d["behind"]]
    a = d["announcements"]
    if a["unread"]:
        out += ["", f"Unread announcements ({a['unread']}):"] + [f"  {x['course']}: {x['subject']}" for x in a["items"]]
    f = d["files"]
    out += ["", f"Files: {f['new']} new, {f['updated']} updated, {f['since']} appeared since you last looked."]
    return "\n".join(out)
