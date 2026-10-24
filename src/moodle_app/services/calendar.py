"""Calendar read-model (deadlines + your own events) and the CRUD for your own events."""
from __future__ import annotations

from . import tracker

DAY = 86400


def build(ctx, start_ts, end_ts, include_hidden=False, now=None):
    now = now or ctx.ts()
    if end_ts <= start_ts or end_ts - start_ts > 400 * DAY:
        raise ValueError("bad date range")
    items = []
    for r in ctx.deadlines.between(start_ts, end_ts, include_hidden):
        done = bool(r["done"]) or r["status"] in ("submitted", "graded")
        items.append({"id": r["id"], "type": "deadline", "title": r["title"], "course": r["course_short"], "course_id": r["course_id"],
                      "start": r["due_ts"], "end": None, "all_day": False, "kind": r["kind"], "done": done, "hidden": bool(r["hidden"]),
                      "overdue": not done and r["due_ts"] < now, "url": r["url"], "status": r["status"], "note": r["note"]})
    for e in ctx.events.between(start_ts, end_ts):
        items.append({"id": f"e{e['id']}", "type": "event", "event_id": e["id"], "title": e["title"], "course": e["course_short"] or "",
                      "course_id": e["course_id"], "start": e["start_ts"], "end": e["end_ts"], "all_day": bool(e["all_day"]),
                      "kind": "event", "done": False, "hidden": False, "overdue": False, "url": "", "status": "", "note": e["note"]})
    items.sort(key=lambda i: (i["start"], i["title"]))
    terms = [c["startdate"] for c in ctx.courses.tracked() if c["startdate"]]
    return {"from": start_ts, "to": end_ts, "now": now, "term_start": min(terms) if terms else None,
            "courses": [{"id": c["id"], "short": c["shortname"]} for c in ctx.courses.tracked()], "items": items}


def _clean(title, start, end, all_day, course_id, note):
    title = (title or "").strip()
    if not title:
        raise ValueError("an event needs a title")
    start = int(start)
    end = int(end) if end not in (None, "") else None
    if end is not None and end < start:
        raise ValueError("the end is before the start")
    return title[:200], start, end, 1 if all_day else 0, (int(course_id) if course_id not in (None, "") else None), (note or "")[:1000]


def add_event(ctx, title, start, end=None, all_day=False, course_id=None, note=""):
    t, s, e, a, c, n = _clean(title, start, end, all_day, course_id, note)
    if c is not None and not ctx.courses.get(c):
        raise ValueError("unknown course")
    with ctx.conn:
        return ctx.events.add(t, s, e, a, c, n, ctx.ts())


def update_event(ctx, id_, **f):
    old = ctx.events.get(id_)
    if old is None:
        raise ValueError("unknown event")
    merged = {"title": old["title"], "start": old["start_ts"], "end": old["end_ts"], "all_day": old["all_day"],
              "course_id": old["course_id"], "note": old["note"], **f}
    t, s, e, a, c, n = _clean(merged["title"], merged["start"], merged["end"], merged["all_day"], merged["course_id"], merged["note"])
    with ctx.conn:
        ctx.events.update(id_, title=t, start_ts=s, end_ts=e, all_day=a, course_id=c, note=n)


def delete_event(ctx, id_):
    if ctx.events.get(id_) is None:
        raise ValueError("unknown event")
    with ctx.conn:
        ctx.events.delete(id_)
