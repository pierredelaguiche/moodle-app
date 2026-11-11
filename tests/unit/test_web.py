"""HTTP-level tests: the security rules of the local server (carried over from the first version) and the main flows."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from helpers import PDF, close_env, make_env
from moodle_app.host.bridge import HostBridge
from moodle_app.services.sync import run_sync
from moodle_app.web import server

NOW, DAY = 1_800_000_000, 86400


@pytest.fixture
def web(tmp_path):
    env = make_env(tmp_path)
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    env.mock.add_file(7, 1, 101, "01 Intro.pdf", PDF + b"y")
    env.mock.assignments[7] = [{"id": 11, "cmid": 301, "course": 7, "name": "CW1", "duedate": NOW + DAY}]
    env.mock.forums = [{"id": 61, "course": 7, "type": "news"}]
    env.mock.discussions = {61: [{"discussion": 5001, "subject": "Hi", "message": "<p>Hello</p><script>alert(1)</script><b>there</b>",
                                  "created": NOW, "modified": NOW}]}
    app, srv = server.make_server(env.ctx, bridge=HostBridge(tmp_path / "trash"))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    app.refresh()
    env.app, env.srv, env.port, env.trash = app, srv, server.Handler.port, tmp_path / "trash"
    yield env
    srv.shutdown()
    srv.server_close()
    close_env(env)


def req(env, path, body=None, token=True, host=None, method=None, page=None):
    headers = {}
    if page:
        headers["X-Page"] = page
    if token is True:
        headers["X-Token"] = env.app.token
    elif token:
        headers["X-Token"] = token
    if host is not None:
        headers["Host"] = host
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"http://127.0.0.1:{env.port}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def wait_job(env):
    for _ in range(100):
        s, j = req(env, "/api/job?since=0")
        if not j["running"]:
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def tree_files(node, out=None):
    out = [] if out is None else out
    for k in node.get("kids", []):
        (tree_files(k, out) if k["type"] == "dir" else out.append(k))
    return out


def test_api_rejects_missing_or_wrong_token_and_bad_host(web):
    assert req(web, "/api/tree", token=False)[0] == 403
    assert req(web, "/api/tree", token="nope")[0] == 403
    assert req(web, "/api/tree", host="evil.example")[0] == 403
    assert req(web, "/api/refresh", {}, token=False)[0] == 403
    assert req(web, "/api/tree")[0] == 200


def test_page_needs_the_run_token_and_is_not_cached(web):
    assert req(web, "/", token=False)[0] == 403
    s, body = req(web, f"/?t={web.app.token}", token=False)
    assert s == 200 and b"Moodle App" in body


def test_tree_shows_new_files_at_future_folders(web):
    s, t = req(web, "/api/tree")
    files = {f["name"]: f for f in tree_files(t["root"])}
    assert files["Workshop 1.pdf"]["status"] == "new" and files["Workshop 1.pdf"]["rel"] == "CSCI.999 Test Module/Workshops/Workshop 1.pdf"
    assert t["totals"]["new"] == 2 and t["scan_error"] is None


def test_download_selected_only_then_ignore(web):
    key = next(f["key"] for f in tree_files(req(web, "/api/tree")[1]["root"]) if f["name"] == "Workshop 1.pdf")
    s, r = req(web, "/api/download", {"keys": [key]})
    assert s == 200
    wait_job(web)
    assert (web.folder / "Workshops" / "Workshop 1.pdf").exists() and not (web.folder / "Lectures").exists()
    files = {f["name"]: f for f in tree_files(req(web, "/api/tree")[1]["root"])}
    assert files["Workshop 1.pdf"]["status"] == "downloaded" and files["01 Intro.pdf"]["status"] == "new"
    k2 = files["01 Intro.pdf"]["key"]
    assert req(web, "/api/ignore", {"keys": [k2]})[1] == {"ignored": 1}
    assert {f["name"]: f for f in tree_files(req(web, "/api/tree")[1]["root"])}["01 Intro.pdf"]["status"] == "ignored"


def test_delete_moves_one_regular_file_to_trash(web):
    p = web.folder / "Misc" / "mine.txt"
    p.parent.mkdir()
    p.write_text("x")
    s, r = req(web, "/api/delete", {"rel": "CSCI.999 Test Module/Misc/mine.txt"})
    assert s == 200 and not p.exists() and (web.trash / "mine.txt").exists()


def test_delete_refuses_traversal_directories_and_symlinks(web):
    outside = web.root.parent / "outside.txt"
    outside.write_text("keep me")
    assert req(web, "/api/delete", {"rel": "../outside.txt"})[0] == 400
    assert req(web, "/api/delete", {"rel": "CSCI.999 Test Module"})[0] == 400
    link = web.folder / "link.txt"
    link.symlink_to(outside)
    assert req(web, "/api/delete", {"rel": "CSCI.999 Test Module/link.txt"})[0] == 400
    assert outside.read_text() == "keep me" and link.is_symlink()


def test_text_extraction_has_no_endpoint_of_its_own(web):
    """PDF text is an internal step of notes: nothing on the page can ask for it, and nothing writes it into the study folder."""
    for path in ("/api/read", "/api/ai/read", "/api/ai/folders", "/api/pdf/text"):
        assert req(web, path, {"rel": "CSCI.999 Test Module", "rels": [], "module": "CSCI.999 Test Module"}, page="ai")[0] == 404, path
    assert "read_ready" not in req(web, "/api/tree")[1]


def test_deadlines_endpoint_and_flags(web):
    s, d = req(web, "/api/deadlines")
    (item,) = [i for i in d["items"] if i["title"] == "CW1"]
    assert item["course"] == "CSCI.999" and item["status"] == "open" and not item["overdue"]
    assert req(web, "/api/deadline", {"id": item["id"], "done": True})[0] == 200
    assert req(web, "/api/deadline", {"id": "nope", "done": True})[0] == 400
    assert req(web, "/api/deadline", {"id": item["id"], "evil": 1})[1] == {"ok": True}          # unknown flags are ignored
    assert [i for i in req(web, "/api/deadlines")[1]["items"] if i["title"] == "CW1"][0]["done"] is True


def test_announcements_are_served_as_plain_text_only(web):
    s, a = req(web, "/api/announcements")
    (item,) = a["items"]
    assert "<" not in item["text"] and "alert(1)" not in item["text"] and "Hello" in item["text"] and "there" in item["text"]
    assert req(web, "/api/announcements/read", {"ids": [5001]})[0] == 200
    assert req(web, "/api/announcements")[1]["items"][0]["read"] is True


def test_history_endpoint(web):
    s, h = req(web, "/api/history")
    kinds = {i["kind"] for i in h["items"]}
    assert {"new_file", "deadline_new", "announcement"} <= kinds


def test_calendar_feed_needs_its_own_key_and_host_check(web):
    assert req(web, "/calendar.ics?k=wrong", token=False)[0] == 403
    s, link = req(web, "/api/ics-link")
    s, body = req(web, link["path"], token=False)
    assert s == 200 and b"BEGIN:VCALENDAR" in body and b"CW1" in body
    assert req(web, link["path"], token=False, host="evil.example")[0] == 403
    assert req(web, "/api/ics-link")[1] == link                                  # key is stable


def test_refresh_job_reports_failure_when_token_is_bad(web):
    from moodle_app.moodle.client import Moodle
    web.ctx.moodle = Moodle(web.mock.base, "wrong")
    req(web, "/api/refresh", {})
    wait_job(web)
    s, t = req(web, "/api/tree")
    assert "invalidtoken" in t["scan_error"]
    assert t["totals"]["new"] == 2                                              # last known state is still shown


def test_quit_stops_the_server(web):
    assert req(web, "/api/quit", {})[1] == {"stopped": True, "clean": True}
    time.sleep(0.5)
    with pytest.raises(Exception):
        req(web, "/api/tree")


# ------------------------------------------------------------------ pages added later: digest, tracker, calendar, editor
NEW_GET = ["/api/badges", "/api/digest", "/api/tracker", f"/api/calendar?from={NOW}&to={NOW + 30 * DAY}", "/api/file?rel=x.md", "/api/raw?rel=x.pdf"]


@pytest.mark.parametrize("path", NEW_GET)
def test_new_get_endpoints_need_the_token_and_host(web, path):
    assert req(web, path, token=False)[0] == 403
    assert req(web, path, token="wrong")[0] == 403
    assert req(web, path, host="evil.example.com")[0] == 403


@pytest.mark.parametrize("path", ["/api/reviewed", "/api/events", "/api/events/update", "/api/events/delete", "/api/digest/seen",
                                  "/api/file/save", "/api/file/new"])
def test_new_post_endpoints_need_the_token(web, path):
    assert req(web, path, {}, token=False)[0] == 403


def test_badges_digest_tracker_overview(web):
    s, b = req(web, "/api/badges")
    assert s == 200 and b["new_files"] == 2 and b["unread"] == 1 and b["running"] is False
    s, d = req(web, "/api/digest")
    assert s == 200 and d["files"]["new"] == 2 and d["announcements"]["items"][0]["text"] == "Hello\nthere"
    s, t = req(web, "/api/tracker")
    assert s == 200 and t["courses"][0]["short"] == "CSCI.999"


def test_reviewed_flow_over_http_marks_only_downloaded_files(web):
    keys = [k for k in web.ctx.pending]
    assert req(web, "/api/reviewed", {"keys": keys, "on": True}) == (200, {"changed": 0})
    req(web, "/api/download", {"keys": keys})
    wait_job(web)
    s, out = req(web, "/api/reviewed", {"keys": keys, "on": True})
    assert (s, out["changed"]) == (200, 2)
    s, t = req(web, "/api/tracker")
    assert t["courses"][0]["reviewed"] == 2
    s, out = req(web, "/api/reviewed", {"week": {"course": 7, "section": 1}, "on": False})
    assert out["changed"] == 2
    s, tree = req(web, "/api/tree")
    assert tree["totals"]["downloaded"] == 2


def test_calendar_and_event_crud_over_http(web):
    s, out = req(web, "/api/events", {"title": "Study", "start": NOW + DAY, "end": NOW + DAY + 3600, "course_id": 7})
    assert s == 200
    eid = out["id"]
    s, c = req(web, f"/api/calendar?from={NOW}&to={NOW + 30 * DAY}")
    assert [(i["type"], i["title"]) for i in c["items"]] == [("deadline", "CW1"), ("event", "Study")] or \
        [(i["type"], i["title"]) for i in c["items"]] == [("event", "Study"), ("deadline", "CW1")]
    assert req(web, "/api/events/update", {"id": eid, "title": "Study more"})[0] == 200
    assert req(web, "/api/events", {"title": "", "start": NOW})[0] == 400
    assert req(web, "/api/events/delete", {"id": eid})[0] == 200
    assert req(web, "/api/events/delete", {"id": eid})[0] == 400
    assert req(web, f"/api/calendar?from={NOW}&to={NOW - 5}")[0] == 400
    s, ics = req(web, f"/api/ics-link")
    s, body = req(web, ics["path"], token=False)
    assert s == 200 and b"CW1" in body


def test_editor_endpoints_open_save_conflict_new_and_escape(web):
    f = web.folder / "Workshops"
    f.mkdir()
    (f / "w.md").write_text("one\n")
    s, r = req(web, "/api/file?rel=CSCI.999%20Test%20Module/Workshops/w.md")
    assert s == 200 and r["text"] == "one\n" and r["editable"]
    rel = r["rel"]
    s, saved = req(web, "/api/file/save", {"rel": rel, "text": "two\n", "sha": r["sha"], "eol": r["eol"]})
    assert s == 200 and (f / "w.md").read_text() == "two\n"
    s, bad = req(web, "/api/file/save", {"rel": rel, "text": "three\n", "sha": r["sha"]})                  # stale hash
    assert s == 409 and bad["conflict"] and (f / "w.md").read_text() == "two\n"
    assert req(web, "/api/file/save", {"rel": rel, "text": "three\n", "sha": r["sha"], "force": True})[0] == 200
    s, n = req(web, "/api/file/new", {"rel": "CSCI.999 Test Module/Notes/new.md", "text": "# n\n"})
    assert s == 200 and (web.folder / "Notes" / "new.md").read_text() == "# n\n"
    assert req(web, "/api/file/new", {"rel": "CSCI.999 Test Module/Notes/new.md"})[0] == 400
    for evil in ("../x.md", "/etc/passwd", ""):
        assert req(web, f"/api/file?rel={evil}")[0] == 400
        assert req(web, "/api/file/save", {"rel": evil, "text": "x", "sha": ""})[0] == 400
    assert req(web, "/api/file/new", {"rel": "../escape.md"})[0] == 400


def test_raw_preview_is_sandboxed(web):
    (web.folder / "a.pdf").write_bytes(PDF)
    r = urllib.request.Request(f"http://127.0.0.1:{web.port}/api/raw?rel=CSCI.999 Test Module/a.pdf".replace(" ", "%20"),
                               headers={"X-Token": web.app.token})
    with urllib.request.urlopen(r) as resp:
        assert resp.headers["Content-Type"] == "application/pdf" and resp.headers["Content-Security-Policy"] == "sandbox"
        assert resp.headers["X-Content-Type-Options"] == "nosniff" and resp.read() == PDF
    assert req(web, "/api/raw?rel=CSCI.999%20Test%20Module/a.md")[0] == 400


def test_announcements_can_be_marked_read_and_unread(web):
    s, a = req(web, "/api/announcements")
    aid = a["items"][0]["id"]
    assert req(web, "/api/announcements/read", {"ids": [aid]})[0] == 200
    assert req(web, "/api/announcements")[1]["items"][0]["read"] is True
    req(web, "/api/announcements/read", {"ids": [aid], "read": False})
    assert req(web, "/api/announcements")[1]["items"][0]["read"] is False
    assert req(web, "/api/digest")[1]["text"].startswith("Digest, ")


# ------------------------------------------------------------------ organise (folders, moves, allocation, PDF text)
FOLDER = "CSCI.999 Test Module"


def test_organise_endpoints_need_the_token(web):
    for path in ("/api/fs/mkdir", "/api/fs/move", "/api/fs/rename", "/api/fs/rmdir", "/api/fs/undo", "/api/fs/subdivide", "/api/place",
                 "/api/retarget"):
        assert req(web, path, {}, token=False)[0] == 403
        assert req(web, path, {}, token="wrong")[0] == 403
    assert req(web, "/api/alloc", token=False)[0] == 403


def test_alloc_lists_moodle_files_with_state(web):
    code, a = req(web, "/api/alloc")
    assert code == 200 and {f["name"]: f["status"] for f in a["files"]} == {"Workshop 1.pdf": "new", "01 Intro.pdf": "new"}
    assert a["courses"][0]["folder"] == FOLDER and all(f["key"] and f["folder"] for f in a["files"])


def test_mkdir_move_rename_undo_over_http(web):
    assert req(web, "/api/fs/mkdir", {"rel": f"{FOLDER}/Research"})[0] == 200
    assert req(web, "/api/fs/mkdir", {"rel": f"{FOLDER}/Research"})[0] == 400                 # already there
    (web.folder / "n.txt").write_text("n")
    code, out = req(web, "/api/fs/move", {"rels": [f"{FOLDER}/n.txt"], "dir": f"{FOLDER}/Research"})
    assert code == 200 and out["moved"][0]["to"] == f"{FOLDER}/Research/n.txt" and (web.folder / "Research" / "n.txt").exists()
    code, out2 = req(web, "/api/fs/rename", {"rel": f"{FOLDER}/Research/n.txt", "name": "m.txt"})
    assert code == 200 and (web.folder / "Research" / "m.txt").exists()
    code, back = req(web, "/api/fs/undo", {"moved": out2["moved"]})
    assert code == 200 and (web.folder / "Research" / "n.txt").exists()
    assert req(web, "/api/fs/move", {"rels": [f"{FOLDER}/Research/n.txt"], "dir": "../../etc"})[0] == 400
    assert req(web, "/api/fs/rename", {"rel": f"{FOLDER}/Research/n.txt", "name": "../x"})[0] == 400
    assert req(web, "/api/fs/rmdir", {"rel": FOLDER})[0] == 400
    code, t = req(web, "/api/tree")
    assert code == 200


def test_subdivide_dry_run_changes_nothing(web):
    (web.folder / "Lectures").mkdir()
    (web.folder / "Lectures" / "Lecture 2 intro.pdf").write_bytes(PDF)
    code, plan = req(web, "/api/fs/subdivide", {"rel": f"{FOLDER}/Lectures", "dry": True})
    assert code == 200 and plan["weeks"][0]["week"] == 2 and (web.folder / "Lectures" / "Lecture 2 intro.pdf").exists()
    code, done = req(web, "/api/fs/subdivide", {"rel": f"{FOLDER}/Lectures"})
    assert code == 200 and (web.folder / "Lectures" / "Week 02" / "Lecture 2 intro.pdf").exists() and done["created"]


def test_place_over_http_downloads_into_the_chosen_folder(web):
    _, a = req(web, "/api/alloc")
    key = next(f["key"] for f in a["files"] if f["name"] == "Workshop 1.pdf")
    code, j = req(web, "/api/place", {"keys": [key], "dir": f"{FOLDER}/Research"})
    assert code == 200
    for _ in range(100):
        _, st = req(web, "/api/job?since=0")
        if not st["running"]:
            break
        time.sleep(0.1)
    assert (web.folder / "Research" / "Workshop 1.pdf").exists()
    assert req(web, "/api/place", {"keys": [key], "dir": "../x"})[0] == 400
    assert req(web, "/api/place", {"keys": [], "dir": "x"})[0] == 400


def test_open_with_system_program_is_limited_to_documents(web):
    opened = []
    web.app.bridge.open = lambda p: opened.append(p.name)
    (web.folder / "Notes").mkdir()
    (web.folder / "Notes" / "a.docx").write_bytes(b"x")
    (web.folder / "Notes" / "run.command").write_bytes(b"x")
    assert req(web, "/api/open", {"rel": f"{FOLDER}/Notes/a.docx"})[0] == 200 and opened == ["a.docx"]
    assert req(web, "/api/open", {"rel": f"{FOLDER}/Notes/run.command"})[0] == 400
    assert req(web, "/api/open", {"rel": "../x.pdf"})[0] == 400
    assert req(web, "/api/open", {"rel": "x"}, token=False)[0] == 403


def test_preview_shows_a_moodle_file_without_saving_anything(web):
    web.mock.add_file(7, 2, 200, "Notes 2.txt", b"plain notes\n")
    web.app.refresh()
    _, a = req(web, "/api/alloc")
    key = next(f["key"] for f in a["files"] if f["name"] == "Workshop 1.pdf")
    tkey = next(f["key"] for f in a["files"] if f["name"] == "Notes 2.txt")
    before = sorted(p.name for p in web.root.rglob("*"))
    code, data = req(web, "/api/preview?key=" + urllib.parse.quote(key))
    assert code == 200 and data.startswith(b"%PDF")
    code, txt = req(web, "/api/preview?key=" + urllib.parse.quote(tkey))
    assert code == 200 and txt == b"plain notes\n"
    assert sorted(p.name for p in web.root.rglob("*")) == before                         # nothing landed in your folders
    assert not list((web.home / "tmp" / "preview").glob("*"))                            # and the temporary copy is already gone
    _, a2 = req(web, "/api/alloc")
    assert {f["name"]: f["status"] for f in a2["files"]}["Workshop 1.pdf"] == "new"       # still just "new"
    assert req(web, "/api/preview?key=nope")[0] == 400
    web.mock.add_file(7, 2, 201, "pic.xyz", b"data")
    web.app.refresh()
    _, a3 = req(web, "/api/alloc")
    xkey = next(f["key"] for f in a3["files"] if f["name"] == "pic.xyz")
    assert req(web, "/api/preview?key=" + urllib.parse.quote(xkey))[0] == 400
    assert req(web, "/api/preview?key=" + urllib.parse.quote(key), token=False)[0] == 403
    web.app.preview_dir.mkdir(parents=True, exist_ok=True)
    (web.app.preview_dir / "left.pdf").write_bytes(b"x")
    web.app.clean_previews()
    assert not web.app.preview_dir.exists()


def test_page_answers_while_the_first_check_is_still_running(web, monkeypatch):
    from moodle_app.moodle.client import Moodle
    real = Moodle.call

    def slow(self, fn, **p):
        time.sleep(0.4)
        return real(self, fn, **p)
    monkeypatch.setattr(Moodle, "call", slow)
    t0 = time.time()
    s, j = req(web, "/api/refresh", {})
    assert s == 200
    seen = []
    for _ in range(4):
        t1 = time.time()
        s, b = req(web, "/api/badges")
        seen.append((s, b["syncing"], time.time() - t1))
        s, _ = req(web, "/api/tree")
        assert s == 200
        time.sleep(0.1)
    assert all(x[0] == 200 and x[2] < 0.35 for x in seen), seen       # reads are not held up by a request in flight
    assert any(x[1] for x in seen)
    deadline = time.time() + 30
    while time.time() < deadline:
        s, st = req(web, "/api/job?since=0")
        if not st["running"]:
            break
        time.sleep(0.1)
    assert not st["running"] and not st["error"], st
    assert req(web, "/api/badges")[1]["syncing"] is False


def test_web_token_is_stable_between_runs(tmp_path):
    from moodle_app.config import Settings
    s = Settings(home=tmp_path)
    t1 = server._web_token(s)
    assert t1 == server._web_token(s) and len(t1) >= 20 and s.port == 8765
    assert (tmp_path / "web_token").stat().st_mode & 0o077 == 0


def test_create_notes_for_picked_file_plans_then_runs(web, tmp_path):
    fake = tmp_path / "claude"
    body = json.dumps({"result": "# Notes\nbody", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 3}})
    (tmp_path / "out.json").write_text(body)
    fake.write_text(f"#!/bin/sh\ncat >/dev/null\ncat {tmp_path}/out.json")
    fake.chmod(0o755)
    web.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    mod = next(d for d in web.ctx.root.iterdir() if d.is_dir())
    src = mod / "Lectures" / "01 Intro.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("Some lecture text about compilers.")
    rel = src.relative_to(web.ctx.root).as_posix()
    s, plan = req(web, "/api/ai/plan", {"module": mod.name, "rels": [rel]}, page="ai")
    assert s == 200 and plan["runnable"] == 1 and "claude command" in plan["to"]
    assert not (mod / "Notes").exists()                                       # planning writes nothing
    s, _ = req(web, "/api/ai/notes", {"module": mod.name, "rels": [rel]}, page="ai")
    assert s == 200
    j = wait_job(web)
    assert (mod / "Notes" / "Lectures" / "01 Intro.md").exists(), str(req(web, '/api/job?since=0')[1])
    assert "body" in (mod / "Notes" / "Lectures" / "01 Intro.md").read_text()
    s, plan2 = req(web, "/api/ai/plan", {"module": mod.name, "rels": [rel]}, page="ai")
    assert plan2["runnable"] == 0                                             # already written: nothing to redo
    assert req(web, "/api/ai/plan", {"module": mod.name, "rels": ["../x"]}, page="ai")[0] == 400


def test_notes_plan_stops_at_claude_plan_limit(web, tmp_path):
    import time as _t
    reset = int(web.ctx.clock()) + 3600
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour", "overageStatus": "rejected",
          "unifiedWindows": {"five_hour": {"utilization": 0.93, "resetsAt": reset, "status": "allowed"}, "seven_day": {"utilization": 0.2, "resetsAt": reset + 9999, "status": "allowed"}}}}
    res = {"type": "result", "result": "ok", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
    (tmp_path / "out.txt").write_text(json.dumps(ev) + "\n" + json.dumps(res) + "\n")
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat >/dev/null\ncat {tmp_path}/out.txt\n")
    fake.chmod(0o755)
    web.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    mod = next(d for d in web.ctx.root.iterdir() if d.is_dir())
    src = mod / "Lectures" / "02 More.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("text " * 50)
    rel = src.relative_to(web.ctx.root).as_posix()
    s, plan = req(web, "/api/ai/plan", {"module": mod.name, "rels": [rel]}, page="ai")                    # no limits known: asks Claude (probe), learns 93%
    assert "93%" in plan["limit_problem"] and "5-hour 93%" in plan["usage"]
    web.ctx.settings.llm["stop_at"] = 0.95
    s, plan = req(web, "/api/ai/plan", {"module": mod.name, "rels": [rel]}, page="ai")
    assert plan["limit_problem"] is None                                    # your own threshold, applied to Claude's real numbers


def test_llm_endpoints_only_answer_to_the_ai_page(web):
    for path in sorted(server.Handler.LLM_PATHS):
        s, r = req(web, path, {"module": "x", "question": "q"})
        assert s == 403 and "AI page" in r["error"], path
    assert req(web, "/api/ai/state", {}, page="ai")[0] == 200            # reading state calls no model
    # and no other page's code even names an LLM endpoint, except the two that are meant to:
    # the Study assistant (its one chat call) and Settings (the "Test connection" button, which sends no course text)
    allowed = {"ai.js": set(server.Handler.LLM_PATHS), "editor.js": {"/api/ai/chat"}, "settings.js": {"/api/ai/check"}}
    for f in server.STATIC.glob("*.js"):
        txt = f.read_text()
        named = {p for p in server.Handler.LLM_PATHS if p in txt}
        assert named <= allowed.get(f.name, set()), (f.name, named)
    assert req(web, "/api/ai/chat", {"messages": [{"role": "user", "text": "x"}]}, page="settings")[0] == 403      # Settings may only test the connection


def test_ai_prompts_files_and_ask(web, tmp_path):
    s, st = req(web, "/api/ai/state", {}, page="ai")
    assert {p["name"] for p in st["prompts"]} >= {"lecture_notes", "study_guide"} and st["modules"]
    mod = st["modules"][0]
    p = next(x for x in st["prompts"] if x["name"] == "lecture_notes")
    assert req(web, "/api/ai/prompt", {"name": "lecture_notes", "text": "no placeholder"}, page="ai")[0] == 400
    s, r = req(web, "/api/ai/prompt", {"name": "lecture_notes", "text": p["text"] + "\nAlways end with a joke."}, page="ai")
    assert next(x for x in r["prompts"] if x["name"] == "lecture_notes")["custom"] is True
    s, r = req(web, "/api/ai/prompt", {"name": "lecture_notes", "text": None}, page="ai")
    assert next(x for x in r["prompts"] if x["name"] == "lecture_notes")["custom"] is False
    src = web.ctx.root / mod / "Lectures" / "x.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("The answer is 42.")
    assert f"{mod}/Lectures/x.txt" in req(web, "/api/ai/files", {"module": mod})[1]["files"]
    body = json.dumps({"result": "It says 42.", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 9, "output_tokens": 4}})
    (tmp_path / "o.json").write_text(body)
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat > {tmp_path}/stdin.txt\ncat {tmp_path}/o.json\n")
    fake.chmod(0o755)
    web.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    s, r = req(web, "/api/ai/ask", {"question": "What is the answer?", "rels": [f"{mod}/Lectures/x.txt"]}, page="ai")
    assert s == 200 and r["answer"] == "It says 42." and r["files"] == ["x.txt"]
    sent = (tmp_path / "stdin.txt").read_text()
    assert "The answer is 42." in sent and "What is the answer?" in sent
    assert req(web, "/api/ai/ask", {"question": "", "rels": []}, page="ai")[0] == 400
    # the Study assistant: conversation + attached files + text handed over by the page (a selection, unsaved edits)
    chat = {"messages": [{"role": "user", "text": "Summarise."}, {"role": "assistant", "text": "It is about 42."}, {"role": "user", "text": "Now tidy my notes."}],
            "rels": [f"{mod}/Lectures/x.txt"], "snippets": [{"label": "Selection from draft.md", "text": "my rough notes"}, {"label": "empty", "text": "  "}]}
    assert req(web, "/api/ai/chat", chat)[0] == 403 and req(web, "/api/ai/chat", chat, page="files")[0] == 403      # only the two AI surfaces
    s, r = req(web, "/api/ai/chat", chat, page="study")
    assert s == 200 and r["answer"] == "It says 42." and r["context"] == ["Selection from draft.md", "FILE: x.txt"]
    sent = (tmp_path / "stdin.txt").read_text()
    assert "my rough notes" in sent and "The answer is 42." in sent and "Assistant: It is about 42." in sent and sent.rstrip().endswith("Student: Now tidy my notes.")
    assert req(web, "/api/ai/chat", {"messages": [{"role": "assistant", "text": "hi"}]}, page="study")[0] == 400
    assert req(web, "/api/ai/chat", {"messages": [{"role": "user", "text": "x"}], "rels": ["../secret.txt"]}, page="study")[0] == 400


def _free_port():
    import socket
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        return k.getsockname()[1]


def test_address_is_printed_even_when_the_first_check_is_slow(tmp_path, monkeypatch, capsys):
    """The first check captures standard output for its log; the address must reach the terminal anyway."""
    env = make_env(tmp_path)
    monkeypatch.setattr(server.App, "refresh", lambda self, what=None: time.sleep(0.6))
    monkeypatch.setattr(server.ThreadingHTTPServer, "serve_forever", lambda self, *a, **k: time.sleep(0.2))
    port = _free_port()
    try:
        server.run(env.ctx, port=port, open_browser=False)
        out = capsys.readouterr().out
        assert f"http://127.0.0.1:{port}/?t=" in out and "GUI running at" in out
    finally:
        time.sleep(0.6)                                       # let the stub job finish before the database closes
        close_env(env)


def test_a_port_someone_else_listens_on_is_never_shared(tmp_path, monkeypatch, capsys):
    import socket
    env = make_env(tmp_path)
    monkeypatch.setattr(server.App, "refresh", lambda self, what=None: None)
    monkeypatch.setattr(server.ThreadingHTTPServer, "serve_forever", lambda self, *a, **k: None)
    other = socket.socket()
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    other.bind(("127.0.0.1", 0))
    other.listen(1)
    port = other.getsockname()[1]
    try:
        server.run(env.ctx, port=port, open_browser=False)
        out = capsys.readouterr().out
        assert "in use by another program" in out and f"127.0.0.1:{port}/" not in out and "GUI running at" in out
    finally:
        other.close()
        close_env(env)


def test_connections_lists_every_connector_with_its_state(web):
    assert req(web, "/api/connections", token=False)[0] == 403
    s, r = req(web, "/api/connections")
    by = {i["id"]: i for i in r["items"]}
    assert s == 200 and list(by) == ["moodle", "folder", "model", "calendar"]
    assert by["moodle"]["state"] == "ok" and by["moodle"]["summary"] == "Signed in" and any("1 course matched" in d for d in by["moodle"]["detail"])
    assert by["folder"]["state"] == "ok" and by["folder"]["detail"] == [str(web.root)]
    assert by["model"]["state"] in ("ok", "off") and by["calendar"]["path"].startswith("/calendar.ics?k=")
    assert not any("token" in k for i in r["items"] for k in i)                     # never the token itself
    web.ctx.settings.dest_root = web.root / "gone"
    assert {i["id"]: i for i in req(web, "/api/connections")[1]["items"]}["folder"]["state"] == "todo"
    assert req(web, "/api/ai/check", {}, page="files")[0] == 403                    # the test button is for Settings (and the AI pages) only
    assert req(web, "/api/ai/plan", {"module": "x"}, page="settings")[0] == 403
