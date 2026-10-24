"""Per-course hidden texts, coursework notes, deadlines saved to the calendar, editable all-modules plan."""
import json
import pytest

from moodle_app.services import read as read_service
from moodle_app.notes import planner
from test_web import web, req, wait_job  # noqa: F401  (fixtures/helpers)

fitz = pytest.importorskip("fitz")


def _pdf(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    d = fitz.open()
    d.new_page().insert_text((72, 72), text)
    d.save(str(path))


def test_read_is_per_course_into_the_hidden_folder_and_adopts_the_old_one(tmp_path):
    root = tmp_path / "Y3"
    _pdf(root / "ABC" / "Lectures" / "a.pdf", "alpha")
    _pdf(root / "ABC" / "Workshops" / "b.pdf", "beta")
    old = root / "ABC" / "formatted txt"
    old.mkdir()
    (old / "legacy.txt").write_text("old")
    n = read_service.read_folder(root / "ABC", log=lambda m: None, root=root)
    out = root / "ABC" / ".formatted txt"
    assert n == 2 and not old.exists()
    assert sorted(p.name for p in out.iterdir()) == ["Lectures.txt", "Workshops.txt", "legacy.txt"]
    # any folder inside the course: one file named by its path, still in the course's hidden folder
    read_service.read_folder(root / "ABC" / "Lectures", log=lambda m: None, root=root)
    assert (out / "Lectures.txt").exists() and not (root / "ABC" / "Lectures" / ".formatted txt").exists()


def test_coursework_plan_and_deadlines_are_saved_once(web):  # noqa: F811
    s, st = req(web, "/api/ai/state", {}, page="ai")
    mod = st["modules"][0]
    src = web.ctx.root / mod / "Coursework" / "brief.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("Report due 2026-12-01 at 12:00.")
    s, p = req(web, "/api/ai/plan", {"module": mod, "what": "coursework", "week": None, "rels": []}, page="ai")
    job = next(j for j in p["jobs"] if j["kind"] == "coursework_notes")
    assert job["out"] == "Notes/Coursework/brief.md" and job["key"] == "coursework_notes|Coursework/brief.txt"
    note = web.ctx.root / mod / "Notes" / "Coursework" / "x.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text('# CW\n<!-- deadlines: [{"title": "Report", "due": "2026-12-01", "time": "12:00"}, {"title": "bad", "due": "soon"}] -->\n')
    assert web.app.ai.save_deadlines() == 1
    assert web.app.ai.save_deadlines() == 0                       # idempotent
    s, g = req(web, "/api/ai/generated", {})
    assert [d["title"] for d in g["deadlines"]] == ["Report"]
    assert any(n["name"] == "x.md" for m in g["modules"] for n in m["notes"])
    s, r = req(web, "/api/ai/deadline/remove", {"id": g["deadlines"][0]["id"]}, page="ai")
    assert r["deadlines"] == []


def test_plan_covers_all_modules_and_select_limits_what_runs(web, tmp_path):  # noqa: F811
    s, st = req(web, "/api/ai/state", {}, page="ai")
    mod = st["modules"][0]
    for n in ("L1", "L2"):
        f = web.ctx.root / mod / "Lectures" / f"{n}.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{n} text")
    body = json.dumps({"result": "# notes\nok", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 3}})
    (tmp_path / "o.json").write_text(body)
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat >/dev/null\ncat {tmp_path}/o.json\n")
    fake.chmod(0o755)
    web.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    s, p = req(web, "/api/ai/plan", {"module": "*", "what": "lectures", "week": None, "rels": []}, page="ai")
    assert p["scope"] == "all" and {"L1.txt", "L2.txt"} <= {j["key"].split("/")[-1] for j in p["jobs"]}
    pick = next(j for j in p["jobs"] if j["key"].endswith("L1.txt"))
    s, j = req(web, "/api/ai/notes", {"module": "*", "what": "lectures", "week": None, "rels": [], "select": [{"module": pick["module"], "key": pick["key"]}]}, page="ai")
    assert s == 200
    wait_job(web)
    notes = web.ctx.root / pick["module"] / pick["out"]
    assert notes.exists()
    assert not (web.ctx.root / mod / "Notes" / "Lectures" / "L2.md").exists()
    # tracker sees the note
    s, t = req(web, "/api/tracker", method="GET")
    assert s == 200 and "notes" in t["courses"][0]


def test_launch_setting_picks_tab_window_or_nothing(monkeypatch, tmp_path):
    from moodle_app.web import server
    calls = []
    monkeypatch.setattr(server.webbrowser, "open", lambda url, new=0: calls.append(new))
    server._open("http://x", "tab")
    server._open("http://x", "window")
    server._open("http://x", "none")
    assert calls == [2, 1]


def test_settings_page_saves_checks_and_keeps_other_keys(web):  # noqa: F811
    from moodle_app import config
    s = web.ctx.settings
    s.config_path.write_text(json.dumps({"dest_root": str(s.dest_root), "keywords": {"X": ["y"]}, "llm": {"key_file": ".k", "model": "haiku"}}))
    config.ensure_defaults(s)
    raw = json.loads(s.config_path.read_text())
    assert raw["open_on_launch"] == "tab" and raw["port"] == 8765 and raw["keywords"] == {"X": ["y"]}
    st, g = req(web, "/api/settings", method="GET")
    assert st == 200 and {f["key"] for f in g["fields"]} >= {"open_on_launch", "llm.stop_at", "sync.files"}
    st, r = req(web, "/api/settings", {"changes": {"open_on_launch": "none", "llm.stop_at": 0.6, "sync.files": False, "port": 9000, "tree_hide": "a, b"}})
    assert st == 200 and r["restart"] == ["port"]
    raw = json.loads(s.config_path.read_text())
    assert raw["open_on_launch"] == "none" and raw["llm"] == {"key_file": ".k", "model": "haiku", "stop_at": 0.6}
    assert raw["sync"]["files"] is False and raw["tree_hide"] == ["a", "b"] and raw["keywords"] == {"X": ["y"]}
    assert s.open_on_launch == "none" and s.llm["stop_at"] == 0.6 and s.sync["files"] is False   # live
    for bad in ({"open_on_launch": "popup"}, {"llm.stop_at": 7}, {"timezone": "Mars/Base"}, {"dest_root": "/no/such/dir"}, {"nope": 1}, {"port": "x"}):
        assert req(web, "/api/settings", {"changes": bad})[0] == 400, bad
    assert json.loads(s.config_path.read_text()) == raw                                         # nothing half-saved
    assert req(web, "/api/settings", {"changes": {"llm.model": ""}})[0] == 200
    assert "model" not in json.loads(s.config_path.read_text())["llm"]
    assert req(web, "/api/settings", token=False)[0] == 403


def test_first_run_serves_without_setup_and_the_guide_can_finish_it(tmp_path):
    import base64, hashlib, threading
    from moodle_app import config, db
    from moodle_app.services.context import Ctx
    from moodle_app.web import server
    from moodle_app.host import HostBridge
    home = tmp_path / "home"
    s = config.load(home)
    s.home.mkdir(parents=True)
    conn = db.connect(s.db_path)
    db.migrate(conn)
    ctx = Ctx(s, conn, None)
    app, srv = server.make_server(ctx, bridge=HostBridge(tmp_path / "trash"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    class E: pass
    env = E(); env.app, env.port = app, server.Handler.port
    try:
        st = req(env, "/api/setup", method="GET")[1]
        assert st["complete"] is False and st["token"] is False and st["connected"] is False
        assert req(env, "/api/setup/begin", {})[0] == 400                                    # no Moodle address yet
        study = tmp_path / "study"; study.mkdir()
        st = req(env, "/api/setup/places", {"site_url": "https://moodle.example.org/", "dest_root": str(study)})[1]
        assert st["site"] and st["root_ok"] and not st["complete"] and st["site_url"] == "https://moodle.example.org"
        url = req(env, "/api/setup/begin", {})[1]["url"]
        assert url.startswith("https://moodle.example.org/admin/tool/mobile/launch.php")
        passport = url.split("passport=")[1]
        assert req(env, "/api/setup/token", {"pasted": "nonsense"})[0] == 400
        sig = hashlib.md5(("https://moodle.example.org" + passport).encode()).hexdigest()
        pasted = "moodlemobile://token=" + base64.b64encode(f"{sig}:::sometoken:::x".encode()).decode()
        class Fake:
            def __init__(self, *a): pass
            def call(self, fn, **k): return {"fullname": "A Student"}
        import moodle_app.services.setup as setup_mod
        setup_mod.Moodle = Fake
        r = req(env, "/api/setup/token", {"pasted": pasted})[1]
        assert r["complete"] and r["connected"] and r["user"] == "A Student"
        assert (home / "token").read_text().strip() == "sometoken" and oct((home / "token").stat().st_mode)[-3:] == "600"
        assert req(env, "/api/setup/key", {"key": "short"})[0] == 400
        r = req(env, "/api/setup/key", {"key": "sk-ant-" + "x" * 30})[1]
        assert r["api_key"] and r["provider"] == "claude" and json.loads(s.config_path.read_text())["llm"]["key_file"] == ".llm_key"
        r = req(env, "/api/setup/key", {"key": ""})[1]
        assert not r["api_key"]
    finally:
        srv.shutdown(); srv.server_close(); conn.close()
