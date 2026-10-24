"""Deadlines, announcements, capability handling and run bookkeeping against the mock Moodle."""
import pytest

from helpers import PDF, close_env, make_env
from moodle_app.services.sync import run_sync, too_soon

NOW = 1_800_000_000
DAY = 86400


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    m = e.mock
    m.assignments[7] = [
        {"id": 11, "cmid": 301, "course": 7, "name": "Coursework 1", "duedate": NOW + 5 * DAY, "cutoffdate": NOW + 8 * DAY,
         "allowsubmissionsfromdate": NOW - DAY},
        {"id": 12, "cmid": 302, "course": 7, "name": "No due date", "duedate": 0},
        {"id": 13, "cmid": 303, "course": 7, "name": "Old one", "duedate": NOW - 90 * DAY},
    ]
    m.quizzes = [{"course": 7, "coursemodule": 401, "name": "Quiz A", "timeopen": NOW, "timeclose": NOW + 3 * DAY},
                 {"course": 7, "coursemodule": 402, "name": "Quiz no close", "timeclose": 0}]
    m.events = [{"id": 900, "name": "Assignment due", "modulename": "assign", "timesort": NOW + 5 * DAY, "course": {"id": 7},
                 "action": {"url": "http://x/mod/assign/view.php?id=301"}},
                {"id": 901, "name": "Reading check", "modulename": "page", "timesort": NOW + 2 * DAY, "course": {"id": 7},
                 "action": {"url": "http://x/mod/page/view.php?id=555"}},
                {"id": 902, "name": "Other course", "modulename": "page", "timesort": NOW + DAY, "course": {"id": 99}, "action": {}}]
    m.forums = [{"id": 61, "course": 7, "type": "news"}, {"id": 62, "course": 7, "type": "general"}]
    m.discussions = {61: [{"discussion": 5001, "subject": "Welcome", "message": "<p>Hi</p>", "userfullname": "Dr X", "created": NOW - DAY,
                           "modified": NOW - DAY, "pinned": True}]}
    yield e
    close_env(e)


def deadlines(env):
    return {d["id"]: d for d in env.ctx.deadlines.all_for_ics()}


def test_deadlines_from_assignments_quizzes_events(env):
    s = run_sync(env.ctx, what={"deadlines"})
    d = deadlines(env)
    assert set(d) == {"7:301:assign", "7:303:assign", "7:401:quiz_close", "event:901"}
    a = d["7:301:assign"]
    assert (a["title"], a["due_ts"], a["cutoff_ts"], a["source"]) == ("Coursework 1", NOW + 5 * DAY, NOW + 8 * DAY, "assign")
    assert a["url"] == f"{env.mock.base}/mod/assign/view.php?id=301"
    assert d["7:401:quiz_close"]["title"] == "Quiz A (closes)" and d["event:901"]["cmid"] == 555
    assert s["deadlines"]["new"] == 4


def test_calendar_event_for_an_assignment_is_not_duplicated(env):
    run_sync(env.ctx, what={"deadlines"})
    assert "event:900" not in deadlines(env)


def test_second_sync_changes_nothing_and_logs_nothing_new(env):
    run_sync(env.ctx, what={"deadlines"})
    n = len(env.ctx.history.recent_changes())
    s = run_sync(env.ctx, what={"deadlines"})
    assert s["deadlines"] == {"new": 0, "moved": 0, "status": 0, "gone": 0} and len(env.ctx.history.recent_changes()) == n


def test_moved_due_date_is_detected_and_bumps_sequence(env):
    run_sync(env.ctx, what={"deadlines"})
    env.mock.assignments[7][0]["duedate"] = NOW + 9 * DAY
    s = run_sync(env.ctx, what={"deadlines"})
    assert s["deadlines"]["moved"] == 1
    row = env.ctx.deadlines.get("7:301:assign")
    assert row["due_ts"] == NOW + 9 * DAY and row["seq"] == 1
    ch = [c for c in env.ctx.history.recent_changes(kind="deadline_changed")]
    assert len(ch) == 1 and '"from": %d' % (NOW + 5 * DAY) in ch[0]["detail"]


def test_submission_status_graded_and_extension(env):
    env.mock.submission[11] = {"lastattempt": {"submission": {"status": "submitted"}, "extensionduedate": NOW + 7 * DAY}}
    run_sync(env.ctx, what={"deadlines"})
    r = env.ctx.deadlines.get("7:301:assign")
    assert r["status"] == "submitted" and r["due_ts"] == NOW + 7 * DAY
    env.mock.submission[11] = {"lastattempt": {"submission": {"status": "submitted"}, "gradingstatus": "graded"},
                               "feedback": {"gradefordisplay": "72.00 / 100.00"}}
    s = run_sync(env.ctx, what={"deadlines"})
    r = env.ctx.deadlines.get("7:301:assign")
    assert r["status"] == "graded" and r["grade_text"] == "72.00 / 100.00" and s["deadlines"]["status"] == 1
    calls_before = sum(1 for f, _ in env.mock.calls if f == "mod_assign_get_submission_status")
    run_sync(env.ctx, what={"deadlines"})
    assert sum(1 for f, _ in env.mock.calls if f == "mod_assign_get_submission_status") == calls_before   # graded: not asked again


def test_your_own_flags_survive_a_sync(env):
    run_sync(env.ctx, what={"deadlines"})
    with env.conn:
        env.ctx.deadlines.set_flag("7:301:assign", hidden=1, done=1, note="started")
    run_sync(env.ctx, what={"deadlines"})
    r = env.ctx.deadlines.get("7:301:assign")
    assert (r["hidden"], r["done"], r["note"]) == (1, 1, "started")
    assert "7:301:assign" not in deadlines(env)               # hidden ones are left out of the feed


def test_removed_deadline_is_marked_gone_but_only_if_the_fetch_worked(env):
    run_sync(env.ctx, what={"deadlines"})
    env.mock.quizzes = []
    s = run_sync(env.ctx, what={"deadlines"})
    assert s["deadlines"]["gone"] == 1 and env.ctx.deadlines.get("7:401:quiz_close")["gone"] == 1
    # an API failure must not wipe deadlines
    env.mock.fail.add("mod_assign_get_assignments")
    s = run_sync(env.ctx, what={"deadlines"})
    assert env.ctx.deadlines.get("7:301:assign")["gone"] == 0 and any("mod_assign_get_assignments" in w for w in s["warnings"])


def test_disabled_function_is_skipped_with_warning(env):
    env.mock.functions = {"core_enrol_get_users_courses", "core_course_get_contents", "mod_assign_get_assignments"}
    s = run_sync(env.ctx, what={"deadlines", "announcements"})
    assert set(deadlines(env)) == {"7:301:assign", "7:303:assign"}
    assert any("mod_quiz_get_quizzes_by_courses is not enabled" in w for w in s["warnings"])
    assert not any(f == "mod_quiz_get_quizzes_by_courses" for f, _ in env.mock.calls)


def test_list_parameters_are_sent_in_moodle_format(env):
    run_sync(env.ctx, what={"deadlines"})
    params = next(p for f, p in env.mock.calls if f == "mod_assign_get_assignments")
    assert params == {"courseids[0]": "7"}


def test_announcements_only_from_news_forums(env):
    s = run_sync(env.ctx, what={"announcements"})
    assert s["announcements"]["new"] == 1
    (a,) = env.ctx.announcements.recent()
    assert (a["id"], a["subject"], a["author"], a["pinned"], a["read"]) == (5001, "Welcome", "Dr X", 1, 0)
    assert not any(f == "mod_forum_get_forum_discussions" and p.get("forumid") == "62" for f, p in env.mock.calls)
    s = run_sync(env.ctx, what={"announcements"})
    assert s["announcements"]["new"] == 0
    with env.conn:
        env.ctx.announcements.mark_read([5001])
    env.mock.discussions[61][0]["message"] = "<p>edited</p>"
    run_sync(env.ctx, what={"announcements"})
    (a,) = env.ctx.announcements.recent()
    assert a["read"] == 1 and "edited" in a["message"]


def test_run_history_and_changes(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    s = run_sync(env.ctx)
    last = env.ctx.history.last_run("sync")
    assert last["ok"] == 1 and last["finished"] >= last["started"]
    kinds = {c["kind"] for c in env.ctx.history.recent_changes()}
    assert {"new_file", "deadline_new", "announcement"} <= kinds
    assert s["files_new"] == 1 and s["courses"] == 1


def test_too_soon_respects_interval(env):
    assert not too_soon(env.ctx)
    run_sync(env.ctx, what={"deadlines"})
    assert too_soon(env.ctx)
    env.ctx.clock = lambda: NOW + 3600
    assert not too_soon(env.ctx)


def test_site_release_is_recorded(env):
    run_sync(env.ctx, what={"deadlines"})
    assert env.ctx.settings_repo.get("site")["release"] == "4.5.15"


def test_calendar_events_are_fetched_in_pages_of_at_most_50(env):
    """Regression: Moodle answers 'Limit must be between 1 and 50' to larger limits; the mock now enforces that too."""
    env.mock.events = [{"id": 1000 + i, "name": f"Event {i}", "modulename": "page", "timesort": NOW + (i + 1) * 3600,
                        "course": {"id": 7}, "action": {"url": f"http://x/mod/page/view.php?id={2000 + i}"}} for i in range(120)]
    warnings = []
    env.ctx.log = warnings.append
    run_sync(env.ctx, what={"deadlines"})
    got = [k for k in deadlines(env) if k.startswith("event:")]
    assert len(got) == 120
    assert not any("Limit must be" in str(w) for w in warnings)
