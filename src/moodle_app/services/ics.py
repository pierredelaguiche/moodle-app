"""RFC 5545 calendar feed from the deadlines table.

UIDs are stable (<deadline id>@moodle-app) so re-importing or re-subscribing updates events in place; a moved due date bumps
SEQUENCE; a deadline Moodle no longer lists is emitted as STATUS:CANCELLED. Times are UTC, so no VTIMEZONE is needed.
"""
from __future__ import annotations

import time

CRLF = "\r\n"
DEFAULT_ALARMS = ("-P1D", "-PT1H")        # one day and one hour before
EVENT_MINUTES = 30


def _esc(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n")
            .replace("\n", "\\n"))


def _fold(line):
    """Content lines are limited to 75 octets; continuation lines start with one space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return (CRLF + " ").join(out)


def _utc(ts):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))


def build_ics(rows, name="Moodle deadlines", alarms=DEFAULT_ALARMS, events=()):
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//moodle-app//deadlines//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{_esc(name)}"]
    for r in rows:
        done = r["status"] in ("submitted", "graded") or r["done"]
        title = ("\u2713 " if done else "") + f"[{r['course_short']}] {r['title']}"
        desc = [r["course_name"]]
        if r["status"] == "submitted":
            desc.append("Submitted")
        if r["status"] == "graded":
            desc.append("Graded" + (f": {r['grade_text']}" if r["grade_text"] else ""))
        if r["cutoff_ts"]:
            desc.append("Cut-off: " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(r["cutoff_ts"])))
        if r["note"]:
            desc.append(r["note"])
        if r["url"]:
            desc.append(r["url"])
        lines += ["BEGIN:VEVENT", f"UID:{r['id']}@moodle-app", f"DTSTAMP:{_utc(r['updated'])}", f"DTSTART:{_utc(r['due_ts'])}",
                  f"DTEND:{_utc(r['due_ts'] + EVENT_MINUTES * 60)}", f"SEQUENCE:{r['seq']}", f"SUMMARY:{_esc(title)}",
                  f"DESCRIPTION:{_esc(chr(10).join(desc))}", f"CATEGORIES:{_esc(r['course_short'])}",
                  f"STATUS:{'CANCELLED' if r['gone'] else 'CONFIRMED'}"]
        if r["url"]:
            lines.append(f"URL:{r['url']}")
        if not r["gone"] and not done:
            for a in alarms:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(title)}", f"TRIGGER:{a}", "END:VALARM"]
        lines.append("END:VEVENT")
    for e in events:                                      # your own entries
        title = e["title"] + (f" [{e['course_short']}]" if e["course_short"] else "")
        lines += ["BEGIN:VEVENT", f"UID:event-{e['id']}@moodle-app", f"DTSTAMP:{_utc(e['created'])}"]
        if e["all_day"]:
            d0 = time.strftime("%Y%m%d", time.gmtime(e["start_ts"]))
            d1 = time.strftime("%Y%m%d", time.gmtime((e["end_ts"] or e["start_ts"]) + 86400))
            lines += [f"DTSTART;VALUE=DATE:{d0}", f"DTEND;VALUE=DATE:{d1}"]
        else:
            lines += [f"DTSTART:{_utc(e['start_ts'])}", f"DTEND:{_utc(e['end_ts'] or e['start_ts'] + 3600)}"]
        lines += [f"SUMMARY:{_esc(title)}", f"DESCRIPTION:{_esc(e['note'])}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return CRLF.join(_fold(ln) for ln in lines) + CRLF
