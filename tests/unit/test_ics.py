import pytest

from helpers import close_env, make_env
from moodle_app.services.ics import build_ics, _fold
from moodle_app.services.sync import run_sync

NOW, DAY = 1_800_000_000, 86400


def unfold(text):
    return text.replace("\r\n ", "").split("\r\n")


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    e.mock.assignments[7] = [{"id": 11, "cmid": 301, "course": 7, "name": "Coursework 1, part A; \"final\"", "duedate": NOW + 5 * DAY,
                              "cutoffdate": NOW + 8 * DAY}]
    e.mock.quizzes = [{"course": 7, "coursemodule": 401, "name": "Quiz A", "timeclose": NOW + 3 * DAY}]
    run_sync(e.ctx, what={"deadlines"})
    yield e
    close_env(e)


def feed(env):
    return build_ics(env.ctx.deadlines.all_for_ics())


def events(text):
    out, cur = [], None
    for ln in unfold(text):
        if ln == "BEGIN:VEVENT":
            cur = {}
        elif ln == "END:VEVENT":
            out.append(cur)
            cur = None
        elif cur is not None and ":" in ln and not ln.startswith(("BEGIN", "END")):
            k, v = ln.split(":", 1)
            cur[k.split(";")[0]] = v
    return out


def test_structure_crlf_and_stable_uids(env):
    t = feed(env)
    assert t.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and t.endswith("END:VCALENDAR\r\n") and "\n" not in t.replace("\r\n", "")
    ev = {e["UID"]: e for e in events(t)}
    assert set(ev) == {"7:301:assign@moodle-app", "7:401:quiz_close@moodle-app"}
    a = ev["7:301:assign@moodle-app"]
    assert (a["DTSTART"], a["DTEND"]) == ("20270120T080000Z", "20270120T083000Z")
    assert a["SUMMARY"] == '[CSCI.999] Coursework 1\\, part A\\; "final"' and a["SEQUENCE"] == "0" and a["STATUS"] == "CONFIRMED"
    assert a["URL"].endswith("/mod/assign/view.php?id=301") and a["CATEGORIES"] == "CSCI.999"


def test_lines_are_folded_to_75_octets_including_multibyte():
    long = "SUMMARY:" + "é" * 80
    folded = _fold(long)
    assert all(len(p.encode("utf-8")) <= 75 for p in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == long


def test_alarms_present_for_open_absent_for_done_and_gone(env):
    assert feed(env).count("BEGIN:VALARM") == 4
    with env.conn:
        env.ctx.deadlines.set_flag("7:401:quiz_close", done=1)
    t = feed(env)
    assert t.count("BEGIN:VALARM") == 2 and "✓ [CSCI.999] Quiz A" in t


def test_moved_date_keeps_uid_and_bumps_sequence(env):
    before = {e["UID"]: e for e in events(feed(env))}
    env.mock.assignments[7][0]["duedate"] = NOW + 9 * DAY
    run_sync(env.ctx, what={"deadlines"})
    after = {e["UID"]: e for e in events(feed(env))}
    a, b = before["7:301:assign@moodle-app"], after["7:301:assign@moodle-app"]
    assert set(before) == set(after) and b["SEQUENCE"] == "1" and b["DTSTART"] != a["DTSTART"]


def test_deleted_on_moodle_becomes_cancelled_and_hidden_disappears(env):
    env.mock.quizzes = []
    run_sync(env.ctx, what={"deadlines"})
    ev = {e["UID"]: e for e in events(feed(env))}
    assert ev["7:401:quiz_close@moodle-app"]["STATUS"] == "CANCELLED"
    with env.conn:
        env.ctx.deadlines.set_flag("7:301:assign", hidden=1)
    assert "7:301:assign@moodle-app" not in feed(env)


def test_parses_with_a_real_ical_library(env):
    ical = pytest.importorskip("icalendar")
    cal = ical.Calendar.from_ical(feed(env))
    evs = [c for c in cal.walk("VEVENT")]
    assert len(evs) == 2 and all(e.decoded("DTSTART").tzinfo is not None for e in evs)
    assert '[CSCI.999] Coursework 1, part A; "final"' in {str(e["SUMMARY"]) for e in evs}
