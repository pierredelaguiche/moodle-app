"""Tracker, digest, calendar (+ your own events) and the file editor."""
import os
import time

import pytest

from helpers import PDF, close_env, make_env
from moodle_app.services import calendar as cal
from moodle_app.services import digest, tracker
from moodle_app.services.editor import Conflict, Editor, EditorError, sha
from moodle_app.services.files import FileService
from moodle_app.services.ics import build_ics
from moodle_app.services.sync import run_sync

NOW, DAY = 1_800_000_000, 86400


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    e.mock.courses[0].update(startdate=NOW - 10 * DAY, enddate=NOW + 80 * DAY)        # today is in week 2
    for sec, n in ((1, 3), (2, 2), (3, 1)):
        for i in range(n):
            e.mock.add_file(7, sec, sec * 100 + i, f"W{sec} file {i}.pdf", PDF + bytes([sec, i]))
    e.mock.assignments[7] = [{"id": 11, "cmid": 301, "course": 7, "name": "Coursework 1", "duedate": NOW + 2 * DAY}]
    e.mock.forums = [{"id": 61, "course": 7, "type": "news"}]
    e.mock.discussions = {61: [{"discussion": 5001, "subject": "Welcome", "message": "<p>Hi <b>all</b></p>", "created": NOW - 3600,
                                "modified": NOW - 3600}]}
    run_sync(e.ctx)
    yield e
    close_env(e)


def download_week(env, section):
    pend = [p for p in env.ctx.pending.values() if p.item.section_no == section]
    FileService(env.ctx).download(pend)
    return [p.item.key for p in pend]


# ------------------------------------------------------------------ tracker
def test_tracker_weeks_states_and_progress(env):
    keys1 = download_week(env, 1)
    download_week(env, 2)
    assert tracker.set_reviewed(env.ctx, keys1[:2], True) == 2
    t = tracker.build(env.ctx, NOW)
    c = t["courses"][0]
    assert (c["current_week"], c["phase"]) == (2, "running")
    w = {x["no"]: x for x in c["weeks"]}
    assert (w[1]["total"], w[1]["reviewed"], w[1]["pct"], w[1]["state"]) == (3, 2, 67, "behind")       # earlier week, not finished
    assert (w[2]["state"], w[2]["reviewed"]) == ("current", 0)
    assert w[3]["state"] == "upcoming" and w[3]["downloaded"] == 0                                       # nothing downloaded yet
    assert (c["reviewed"], c["total"], c["pct"], c["behind"]) == (2, 6, 33, 1)
    assert c["next_deadline"]["title"] == "Coursework 1"


def test_finishing_a_week_makes_it_done_and_unreview_works(env):
    keys = download_week(env, 3)
    tracker.set_reviewed(env.ctx, keys, True)
    w3 = next(w for w in tracker.build(env.ctx, NOW)["courses"][0]["weeks"] if w["no"] == 3)
    assert w3["state"] == "done" and w3["pct"] == 100
    tracker.set_reviewed(env.ctx, keys, False)
    assert next(w for w in tracker.build(env.ctx, NOW)["courses"][0]["weeks"] if w["no"] == 3)["state"] == "upcoming"


def test_only_downloaded_files_can_be_reviewed_and_ignored_files_leave_the_total(env):
    pend = list(env.ctx.pending.values())
    assert tracker.set_reviewed(env.ctx, [pend[0].item.key], True) == 0                  # still pending
    FileService(env.ctx).ignore([p.item.key for p in pend if p.item.section_no == 1])
    c = tracker.build(env.ctx, NOW)["courses"][0]
    assert sum(w["total"] for w in c["weeks"]) == 3 and all(w["no"] != 1 for w in c["weeks"])


def test_week_keys_and_unknown_dates(env):
    keys = download_week(env, 1)
    assert sorted(tracker.week_keys(env.ctx, 7, 1)) == sorted(keys)
    assert tracker.term_week(0, 0, NOW) == (None, "unknown")
    assert tracker.term_week(NOW + DAY, 0, NOW) == (0, "before")
    assert tracker.term_week(NOW - 100 * DAY, NOW - 50 * DAY, NOW)[1] == "over"


# ------------------------------------------------------------------ digest
def test_digest_focus_and_counts(env):
    d = digest.build(env.ctx, now=NOW)
    assert [x["title"] for x in d["soon"]] == ["Coursework 1"] and not d["overdue"]
    assert d["files"]["new"] == 6 and d["announcements"]["unread"] == 1
    levels = [f["level"] for f in d["focus"]]
    assert levels[0] == "soon" and "files" in levels and "info" in levels
    assert d["announcements"]["items"][0]["text"] == "Hi all"
    txt = digest.render_text(d)
    assert "Coursework 1" in txt and "Do first:" in txt


def test_digest_overdue_and_done_items_and_seen_marker(env):
    env.ctx.deadlines.set_flag("7:301:assign", done=1)
    env.conn.commit()
    assert digest.build(env.ctx, now=NOW)["soon"] == []
    env.ctx.deadlines.set_flag("7:301:assign", done=0)
    env.conn.commit()
    late = digest.build(env.ctx, now=NOW + 3 * DAY)
    assert [x["title"] for x in late["overdue"]] == ["Coursework 1"] and late["focus"][0]["level"] == "urgent"
    digest.mark_seen(env.ctx, now=NOW + 5)
    assert digest.build(env.ctx, now=NOW + 6)["since"] == NOW + 5


def test_digest_reports_a_moved_deadline(env):
    env.mock.assignments[7][0]["duedate"] = NOW + 6 * DAY
    run_sync(env.ctx, what={"deadlines"})
    d = digest.build(env.ctx, since=0, now=NOW)
    assert d["moved"][0]["title"] == "Coursework 1" and d["moved"][0]["to"] == NOW + 6 * DAY


# ------------------------------------------------------------------ calendar
def test_calendar_merges_deadlines_and_own_events_and_rejects_bad_ranges(env):
    eid = cal.add_event(env.ctx, "Study block", NOW + DAY, NOW + DAY + 7200, course_id=7, note="library")
    cal.add_event(env.ctx, "Reading day", NOW + 4 * DAY, all_day=True)
    out = cal.build(env.ctx, NOW, NOW + 30 * DAY, now=NOW)
    assert [(i["type"], i["title"]) for i in out["items"]] == [("event", "Study block"), ("deadline", "Coursework 1"), ("event", "Reading day")]
    assert out["term_start"] == NOW - 10 * DAY
    assert cal.build(env.ctx, NOW + 10 * DAY, NOW + 30 * DAY, now=NOW)["items"] == []
    with pytest.raises(ValueError):
        cal.build(env.ctx, NOW, NOW - 1)
    with pytest.raises(ValueError):
        cal.build(env.ctx, 0, 500 * DAY)
    cal.update_event(env.ctx, eid, title="Moved block", start=NOW + 2 * DAY, end=None)
    e = env.ctx.events.get(eid)
    assert (e["title"], e["start_ts"], e["end_ts"]) == ("Moved block", NOW + 2 * DAY, None)
    cal.delete_event(env.ctx, eid)
    assert env.ctx.events.get(eid) is None


@pytest.mark.parametrize("kw", [dict(title="  "), dict(title="x", start=10, end=5), dict(title="x", course_id=999)])
def test_event_validation(env, kw):
    args = dict(title="x", start=NOW, end=None, all_day=False, course_id=None)
    args.update(kw)
    with pytest.raises(ValueError):
        cal.add_event(env.ctx, args["title"], args["start"], args["end"], args["all_day"], args["course_id"])


def test_hidden_deadlines_are_left_out_unless_asked(env):
    env.ctx.deadlines.set_flag("7:301:assign", hidden=1)
    env.conn.commit()
    assert cal.build(env.ctx, NOW, NOW + 30 * DAY, now=NOW)["items"] == []
    assert len(cal.build(env.ctx, NOW, NOW + 30 * DAY, include_hidden=True, now=NOW)["items"]) == 1


def test_own_events_appear_in_the_ics_feed(env):
    cal.add_event(env.ctx, "Study, block; one", NOW + DAY, NOW + DAY + 3600, course_id=7)
    cal.add_event(env.ctx, "Holiday", NOW + 3 * DAY, all_day=True)
    text = build_ics(env.ctx.deadlines.all_for_ics(), events=env.ctx.events.all())
    assert "SUMMARY:Study\\, block\\; one [CSCI.999]" in text and "DTSTART;VALUE=DATE:" in text
    assert text.count("BEGIN:VEVENT") == 3 and "UID:event-1@moodle-app" in text


# ------------------------------------------------------------------ editor
@pytest.fixture
def ed(tmp_path):
    root, home = tmp_path / "root", tmp_path / "home"
    (root / "Mod").mkdir(parents=True)
    home.mkdir()
    (root / "Mod" / "notes.md").write_text("# Hi\nline\n", encoding="utf-8")
    return Editor(root, home), root, home


def test_read_save_roundtrip_with_backup_and_atomic_replace(ed):
    e, root, home = ed
    r = e.read("Mod/notes.md")
    assert (r["kind"], r["editable"], r["text"], r["eol"], r["lang"]) == ("text", True, "# Hi\nline\n", "lf", "markdown")
    out = e.save("Mod/notes.md", "# Hi\nnew\n", r["sha"])
    assert out["changed"] and (root / "Mod" / "notes.md").read_text() == "# Hi\nnew\n"
    backups = list((home / "backups").rglob("*notes.md"))
    assert len(backups) == 1 and backups[0].read_text() == "# Hi\nline\n"
    assert not [p for p in (root / "Mod").iterdir() if p.name.endswith(".tmp")]
    assert e.save("Mod/notes.md", "# Hi\nnew\n", out["sha"])["changed"] is False                  # no-op does not make a backup
    assert len(list((home / "backups").rglob("*notes.md"))) == 1


def test_save_refuses_when_file_changed_on_disk_unless_forced(ed):
    e, root, _ = ed
    r = e.read("Mod/notes.md")
    (root / "Mod" / "notes.md").write_text("edited elsewhere\n")
    with pytest.raises(Conflict):
        e.save("Mod/notes.md", "mine", r["sha"])
    assert (root / "Mod" / "notes.md").read_text() == "edited elsewhere\n"
    e.save("Mod/notes.md", "mine\n", r["sha"], force=True)
    assert (root / "Mod" / "notes.md").read_text() == "mine\n"


def test_crlf_files_keep_their_line_endings(ed):
    e, root, _ = ed
    (root / "Mod" / "win.txt").write_bytes(b"a\r\nb\r\n")
    r = e.read("Mod/win.txt")
    assert r["eol"] == "crlf" and r["text"] == "a\nb\n"
    e.save("Mod/win.txt", "a\nc\n", r["sha"], eol=r["eol"])
    assert (root / "Mod" / "win.txt").read_bytes() == b"a\r\nc\r\n"


def test_editor_refuses_escapes_symlinks_binaries_and_bad_encodings(ed, tmp_path):
    e, root, _ = ed
    (tmp_path / "secret.txt").write_text("top secret")
    os.symlink(tmp_path / "secret.txt", root / "Mod" / "link.txt")
    os.symlink(tmp_path, root / "outdir")
    (root / "Mod" / "latin.txt").write_bytes("café".encode("latin-1"))
    (root / "Mod" / "prog.bin").write_bytes(b"\x00\x01")
    for rel in ("../secret.txt", "Mod/../../secret.txt", "Mod/link.txt", "outdir/secret.txt", "/etc/passwd", "", "Mod"):
        with pytest.raises(EditorError):
            e.read(rel)
    assert e.read("Mod/latin.txt")["editable"] is False and e.read("Mod/prog.bin")["kind"] == "binary"
    with pytest.raises(EditorError):
        e.save("Mod/prog.bin", "x", "")
    with pytest.raises(EditorError):
        e.raw("Mod/notes.md")                                       # only pdf/images are previewed raw
    (root / "Mod" / "big.txt").write_bytes(b"x" * 2_000_001)
    assert e.read("Mod/big.txt")["kind"] == "toolarge"


def test_create_new_file_rules(ed):
    e, root, _ = ed
    out = e.create("Mod/Notes/week1.md", "# W1\n")
    assert out["rel"] == "Mod/Notes/week1.md" and (root / "Mod" / "Notes" / "week1.md").read_text() == "# W1\n"
    for bad in ("Mod/Notes/week1.md", "../x.md", "Mod/.hidden/x.md", "Mod/x.exe", "Mod/", ""):
        with pytest.raises(EditorError):
            e.create(bad)


def test_old_backups_are_pruned(ed):
    e, root, home = ed
    cur = e.read("Mod/notes.md")["sha"]
    for i in range(14):
        cur = e.save("Mod/notes.md", f"v{i}\n", cur)["sha"]
    assert len(list((home / "backups").rglob("*notes.md"))) == 10


def test_pdf_preview_content_type(ed):
    e, root, _ = ed
    (root / "Mod" / "a.pdf").write_bytes(PDF)
    data, ctype = e.raw("Mod/a.pdf")
    assert ctype == "application/pdf" and data == PDF and e.read("Mod/a.pdf")["kind"] == "pdf"
