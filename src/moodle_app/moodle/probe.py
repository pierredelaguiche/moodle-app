#!/usr/bin/env python3
"""
Capability probe - find out what your Moodle token can do (read-only).

Run:  moodle-app probe
It calls a list of read-only web service functions, records which work, how many items they return and what FIELDS
those items have (never the values), and writes probe_report.json into the data directory. The report holds no
token, no names, no dates, no file contents: only function names, counts, error codes and field names, so it is safe
to paste into a chat. Nothing is created, changed or submitted.
"""
import json
import time
from pathlib import Path


# Functions the planned features would use. Only their presence in the site's function list is checked, never called
# (they write data), so the report can say whether a feature such as "submit from the app" is even possible.
WRITE_FUNCTIONS = [
    "mod_assign_save_submission", "mod_assign_submit_for_grading", "core_files_upload",
    "core_calendar_create_calendar_events", "core_calendar_get_calendar_export_token",
    "core_completion_update_activity_completion_status_manually", "core_message_send_instant_messages",
]


def shape(v, depth=4):
    """Field names and types only, never values."""
    if isinstance(v, dict):
        if depth == 0:
            return "object"
        return {k: shape(x, depth - 1) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [shape(v[0], depth - 1)] if v else []
    return type(v).__name__ if v is not None else "null"


def count(v):
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        for k in ("assignments", "events", "courses", "forums", "quizzes", "discussions", "urls", "pages", "ltis",
                  "usergrades", "statuses", "items", "instances", "resources", "folders"):
            if isinstance(v.get(k), list):
                return len(v[k])
    return None


def attempt(m, available, fn, **params):
    """One probe: returns a result dict (never raises)."""
    res = {"function": fn}
    if available is not None and fn not in available:
        res.update(status="not_enabled", note="not in this token's function list")
        return res, None
    t0 = time.time()
    try:
        out = m.call(fn, **params)
    except Exception as e:                                   # access denied, bad params, network ...
        msg = str(e)
        res.update(status="error", error=msg.split(" - ")[0].split(": ", 1)[-1][:60], detail=msg[:160])
        return res, None
    res.update(status="ok", ms=int((time.time() - t0) * 1000), items=count(out), fields=shape(out))
    return res, out


def first(lst):
    return lst[0] if isinstance(lst, list) and lst else None


def run(m, courses, report_path):
    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "calls": []}
    log = report["calls"]

    def go(fn, **p):
        r, out = attempt(m, available, fn, **p)
        log.append(r)
        mark = {"ok": "ok", "not_enabled": "--", "error": "!!"}[r["status"]]
        extra = f"{r['items']} items" if r.get("items") is not None else r.get("error", r.get("note", ""))
        print(f"  [{mark}] {fn:60s} {extra if r['status'] != 'ok' or r.get('items') is not None else ''}")
        return out

    print("Site info")
    try:
        info = m.call("core_webservice_get_site_info")
    except Exception as e:
        print(f"! could not read site info: {e}")
        return
    funcs = {f["name"] for f in info.get("functions", [])}
    available = funcs or None
    report["site"] = {"release": info.get("release"), "version": info.get("version"),
                      "mobile_service_functions": len(funcs), "downloadfiles": info.get("downloadfiles"),
                      "uploadfiles": info.get("uploadfiles"), "usercanmanageownfiles": info.get("usercanmanageownfiles"),
                      "userquota": info.get("userquota"), "site_info_fields": shape(info, 1)}
    report["functions_available"] = sorted(funcs)
    report["write_functions_present"] = {f: (f in funcs) for f in WRITE_FUNCTIONS}
    print(f"  Moodle {info.get('release')}, {len(funcs)} web service functions enabled for this token")

    uid = info["userid"]
    ids = [c["id"] for c in courses]
    report["courses"] = {"count": len(ids), "fields": shape(first(courses), 1)}
    print(f"Courses: {len(ids)}  (fields: {', '.join(sorted(first(courses) or {}))})")

    print("Assignments, deadlines, submissions")
    ass = go("mod_assign_get_assignments", **{f"courseids[{i}]": c for i, c in enumerate(ids)})
    a1 = None
    if isinstance(ass, dict):
        for c in ass.get("courses", []):
            a1 = first(c.get("assignments"))
            if a1:
                break
        report["assignments_total"] = sum(len(c.get("assignments", [])) for c in ass.get("courses", []))
        report["assignment_fields"] = shape(a1, 1) if a1 else None     # the first course may have none, so shape() of the reply is empty
    if a1:
        go("mod_assign_get_submission_status", assignid=a1["id"])
        go("mod_assign_list_participants", assignid=a1["id"], groupid=0, filter="")
    go("mod_assign_get_user_flags", **{"assignmentids[0]": a1["id"]}) if a1 else None

    print("Calendar")
    now = int(time.time())
    go("core_calendar_get_action_events_by_timesort", timesortfrom=now - 14 * 86400, limitnum=50)
    go("core_calendar_get_calendar_upcoming_view")
    t = time.localtime()
    go("core_calendar_get_calendar_monthly_view", year=t.tm_year, month=t.tm_mon)
    go("core_calendar_get_calendar_events", **{"options[userevents]": 1, "options[siteevents]": 1,
                                                 "options[timestart]": now - 86400, "options[timeend]": now + 120 * 86400})

    print("Announcements and forums")
    forums = go("mod_forum_get_forums_by_courses", **{f"courseids[{i}]": c for i, c in enumerate(ids)})
    news = [f for f in forums if f.get("type") == "news"] if isinstance(forums, list) else []
    report["announcement_forums"] = len(news)
    if isinstance(forums, list):
        report["forum_types"] = sorted({f.get("type") for f in forums})
    if news:
        go("mod_forum_get_forum_discussions", forumid=news[0]["id"], perpage=5)
        go("mod_forum_get_forum_discussions_paginated", forumid=news[0]["id"], perpage=5)

    print("Other activities")
    go("mod_quiz_get_quizzes_by_courses", **{f"courseids[{i}]": c for i, c in enumerate(ids)})
    go("mod_url_get_urls_by_courses", **{f"courseids[{i}]": c for i, c in enumerate(ids)})
    go("mod_page_get_pages_by_courses", **{f"courseids[{i}]": c for i, c in enumerate(ids)})
    go("mod_lti_get_ltis_by_courses", **{f"courseids[{i}]": c for i, c in enumerate(ids)})
    go("mod_folder_get_folders_by_courses", **{f"courseids[{i}]": c for i, c in enumerate(ids)})

    print("Course structure, change detection, progress, grades")
    c1 = ids[0] if ids else None
    if c1:
        contents = go("core_course_get_contents", courseid=c1)
        mod = None
        if isinstance(contents, list):
            kinds = {}
            for sec in contents:
                for md in sec.get("modules", []):
                    kinds[md.get("modname")] = kinds.get(md.get("modname"), 0) + 1
                    mod = mod or md
            report["course_module_types_first_course"] = dict(sorted(kinds.items(), key=lambda kv: -kv[1]))
            report["module_fields"] = shape(mod, 1) if mod else None
            report["section_fields"] = shape(first(contents), 1)
        go("core_course_get_updates_since", courseid=c1, since=now - 7 * 86400)
        go("core_completion_get_activities_completion_status", courseid=c1, userid=uid)
        go("core_completion_get_course_completion_status", courseid=c1, userid=uid)
        go("gradereport_user_get_grade_items", courseid=c1, userid=uid)
        go("core_course_get_course_module", cmid=(mod or {}).get("id", 0)) if mod else None

    print("User")
    go("core_user_get_course_user_profiles", **{"userlist[0][userid]": uid, "userlist[0][courseid]": c1 or 0}) if c1 else None
    go("tool_mobile_get_config")

    ok = sum(1 for r in log if r["status"] == "ok")
    report["summary"] = {"ok": ok, "error": sum(1 for r in log if r["status"] == "error"),
                         "not_enabled": sum(1 for r in log if r["status"] == "not_enabled")}
    Path(report_path).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n{ok} of {len(log)} probes worked. Report (field names and counts only): {report_path}")
    print("Write functions present (never called):",
          ", ".join(f for f, v in report["write_functions_present"].items() if v) or "none")
