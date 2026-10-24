"""Fetchers: turn Moodle responses into plain records. All field reads are defensive (.get) because Moodle versions differ;
anything unexpected is reported through `warn`, never raised."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .client import Moodle, MoodleError


@dataclass
class FileItem:
    course_id: int
    cmid: int
    modname: str
    activity_name: str
    section_no: int
    section_name: str
    filepath: str
    filename: str
    size: int
    timemodified: int
    fileurl: str

    @property
    def key(self):
        # stable across Moodle bumping the revision number in the URL
        return f"{self.course_id}:{self.cmid}:{self.filepath or '/'}{self.filename}"


class Capabilities:
    def __init__(self, functions=None):
        self.functions = set(functions) if functions is not None else None   # None = unknown, assume everything

    def has(self, fn):
        return self.functions is None or fn in self.functions


def fetch_site(m: Moodle):
    info = m.call("core_webservice_get_site_info")
    return {"userid": info["userid"], "sitename": info.get("sitename", ""), "fullname": info.get("fullname", ""),
            "release": info.get("release", ""), "functions": [f["name"] for f in info.get("functions", [])]}


def fetch_courses(m: Moodle, userid):
    return m.call("core_enrol_get_users_courses", userid=userid)


def fetch_contents(m: Moodle, course_id):
    """Returns (sections, activities, files).

    Moodle 4.5 'subsection' activities own a delegated section (component mod_subsection). Files inside one belong to
    the week of the section that contains the subsection, so they are re-homed to that week."""
    raw = m.call("core_course_get_contents", courseid=course_id)
    parent_of = {}                     # subsection instance id -> (section_no, section_name) of the containing section
    for sec in raw:
        if sec.get("component") == "mod_subsection":
            continue
        for mod in sec.get("modules", []):
            if mod.get("modname") == "subsection" and mod.get("instance") is not None:
                parent_of[mod["instance"]] = (sec.get("section", 0), sec.get("name") or "")
    sections, activities, files = [], [], []
    for sec in raw:
        delegated = sec.get("component") == "mod_subsection"
        if delegated and sec.get("itemid") in parent_of:
            secn, secname = parent_of[sec["itemid"]]
        else:
            secn, secname = sec.get("section", 0), sec.get("name") or ""
        if not delegated:
            sections.append({"section_no": secn, "name": secname, "visible": bool(sec.get("visible", 1))})
        for mod in sec.get("modules", []):
            cmid = mod.get("id")
            activities.append({"cmid": cmid, "section_no": secn, "modname": mod.get("modname", ""), "instance": mod.get("instance"),
                               "name": mod.get("name") or "", "url": mod.get("url") or "", "visible": bool(mod.get("visible", 1))})
            for ct in mod.get("contents") or []:
                if ct.get("type") != "file" or not ct.get("fileurl"):
                    continue
                files.append(FileItem(course_id, cmid, mod.get("modname", ""), mod.get("name") or "", secn, secname,
                                      ct.get("filepath") or "/", ct["filename"], ct.get("filesize", 0) or 0,
                                      ct.get("timemodified", 0) or 0, ct["fileurl"]))
    return sections, activities, files


def _try(m, fn, caps, warn, **params):
    if not caps.has(fn):
        warn(f"{fn} is not enabled on this token; skipped")
        return None
    try:
        return m.call(fn, **params)
    except MoodleError as e:
        warn(f"{fn} failed: {e.code}")
        return None


def fetch_assignments(m: Moodle, caps, course_ids, site_url, warn: Callable = print):
    out = _try(m, "mod_assign_get_assignments", caps, warn, courseids=list(course_ids))
    if out is None:
        return None                      # unavailable or failed: the caller must not treat this as "no assignments"
    items = []
    for c in (out or {}).get("courses", []):
        for a in c.get("assignments", []):
            due = a.get("duedate") or 0
            cmid = a.get("cmid")
            if not due or not cmid:
                if not cmid:
                    warn("an assignment came back without cmid; skipped")
                continue
            items.append({"id": f"{c['id']}:{cmid}:assign", "course_id": c["id"], "cmid": cmid, "kind": "assign",
                          "title": a.get("name") or "Assignment", "due_ts": due,
                          "cutoff_ts": a.get("cutoffdate") or None, "allow_from_ts": a.get("allowsubmissionsfromdate") or None,
                          "url": f"{site_url.rstrip('/')}/mod/assign/view.php?id={cmid}", "source": "assign",
                          "assignid": a.get("id"), "nosubmissions": bool(a.get("nosubmissions"))})
    return items


def fetch_submission_status(m: Moodle, caps, assignid, warn: Callable = print):
    """Returns ('open'|'submitted'|'graded', grade_text, extension_ts) or None when unavailable."""
    out = _try(m, "mod_assign_get_submission_status", caps, warn, assignid=assignid)
    if out is None:
        return None
    last = out.get("lastattempt") or {}
    sub = last.get("submission") or {}
    fb = out.get("feedback") or {}
    graded = last.get("graded") or (last.get("gradingstatus") == "graded") or bool(fb.get("gradefordisplay"))
    if graded:
        return "graded", (fb.get("gradefordisplay") or "").strip(), last.get("extensionduedate") or None
    if sub.get("status") == "submitted":
        return "submitted", "", last.get("extensionduedate") or None
    return "open", "", last.get("extensionduedate") or None


def fetch_quizzes(m: Moodle, caps, course_ids, site_url, warn: Callable = print):
    out = _try(m, "mod_quiz_get_quizzes_by_courses", caps, warn, courseids=list(course_ids))
    if out is None:
        return None
    items = []
    for q in (out or {}).get("quizzes", []):
        close, cmid = q.get("timeclose") or 0, q.get("coursemodule")
        if not close or not cmid:
            continue
        items.append({"id": f"{q['course']}:{cmid}:quiz_close", "course_id": q["course"], "cmid": cmid, "kind": "quiz_close",
                      "title": f"{q.get('name') or 'Quiz'} (closes)", "due_ts": close, "allow_from_ts": q.get("timeopen") or None,
                      "url": f"{site_url.rstrip('/')}/mod/quiz/view.php?id={cmid}", "source": "quiz"})
    return items


EVENT_PAGE = 50            # Moodle rejects limitnum outside 1..50
EVENT_MAX_PAGES = 12       # at most 600 events per sync


def fetch_events(m: Moodle, caps, tracked_ids, now, warn: Callable = print, horizon_days=240):
    """Calendar action events that are not already covered by assignments/quizzes. Pages through the results 50 at a time."""
    raw, after = [], 0
    for _ in range(EVENT_MAX_PAGES):
        params = dict(timesortfrom=now - 86400, timesortto=now + horizon_days * 86400, limitnum=EVENT_PAGE)
        if after:
            params["aftereventid"] = after
        out = _try(m, "core_calendar_get_action_events_by_timesort", caps, warn, **params)
        if out is None:
            return None if not raw else _events(raw, tracked_ids)      # keep what earlier pages gave us
        page = (out or {}).get("events", []) or []
        raw += page
        last = (out or {}).get("lastid")
        if len(page) < EVENT_PAGE or not (last or page):
            break
        after = last or page[-1].get("id") or 0
        if not after:
            break
    return _events(raw, tracked_ids)


def _events(raw, tracked_ids):
    items, seen = [], set()
    for e in raw:
        if e.get("modulename") in ("assign", "quiz") or e.get("id") in seen:
            continue
        seen.add(e.get("id"))
        cid = (e.get("course") or {}).get("id")
        if cid not in tracked_ids or not e.get("timesort"):
            continue
        url = (e.get("action") or {}).get("url") or e.get("url") or ""
        mm = re.search(r"[?&]id=(\d+)", url)
        items.append({"id": f"event:{e['id']}", "course_id": cid, "cmid": int(mm.group(1)) if mm else None, "kind": "event",
                      "title": e.get("name") or e.get("activityname") or "Event", "due_ts": e["timesort"], "url": url,
                      "source": "calendar"})
    return items


def fetch_announcements(m: Moodle, caps, course_ids, warn: Callable = print, per_forum=10):
    forums = _try(m, "mod_forum_get_forums_by_courses", caps, warn, courseids=list(course_ids))
    if forums is None:
        return None
    items = []
    for f in forums:
        if f.get("type") != "news":
            continue
        out = _try(m, "mod_forum_get_forum_discussions", caps, warn, forumid=f["id"], page=0, perpage=per_forum)
        for d in (out or {}).get("discussions", []):
            did = d.get("discussion") or d.get("id")
            items.append({"id": did, "course_id": f.get("course"), "forum_id": f["id"],
                          "subject": d.get("subject") or d.get("name") or "", "message": d.get("message") or "",
                          "author": d.get("userfullname") or "", "created": d.get("created") or d.get("timemodified") or 0,
                          "modified": d.get("modified") or d.get("timemodified") or 0, "pinned": bool(d.get("pinned"))})
    return items
