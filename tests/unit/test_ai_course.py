"""Text extraction stays internal; coursework notes, deadlines saved to the calendar, editable all-modules plan."""
import json
import pytest

from moodle_app.notes import planner
from test_web import web, req, wait_job  # noqa: F401  (fixtures/helpers)

fitz = pytest.importorskip("fitz")


def _pdf(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    d = fitz.open()
    d.new_page().insert_text((72, 72), text)
    d.save(str(path))


def test_text_bundles_of_early_versions_are_neither_listed_nor_used(web):  # noqa: F811
    """`.formatted txt` / `formatted txt` folders written by early versions stay on disk untouched and are never a source."""
    s, st = req(web, "/api/ai/state", {}, page="ai")
    mod = st["modules"][0]
    m = web.ctx.root / mod
    _pdf(m / "Lectures" / "a.pdf", "alpha")
    for old in (".formatted txt", "formatted txt"):
        (m / old).mkdir()
        (m / old / "Lectures.txt").write_text("stale bundle")
    files = web.app.ai.files(mod)
    assert f"{mod}/Lectures/a.pdf" in files and not any("formatted txt" in f for f in files)
    s, g = req(web, "/api/ai/generated", {})
    assert all("texts" not in x for x in g["modules"])
    s, p = req(web, "/api/ai/plan", {"module": mod, "what": "lectures", "week": None, "rels": []}, page="ai")
    assert [j["key"] for j in p["jobs"]] == ["lecture_notes|Lectures/a.pdf"], p["jobs"]
    assert (m / ".formatted txt" / "Lectures.txt").read_text() == "stale bundle"            # left alone
    assert not list(m.rglob("*.txt")) or all("formatted txt" in str(x) for x in m.rglob("*.txt"))   # extraction wrote nothing here
    assert list((web.home / "cache" / "text").glob("*.txt"))                                # ...it went to the private cache


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


def test_single_module_inside_a_year_folder_can_be_planned_and_written(web, tmp_path):  # noqa: F811
    m = web.ctx.root / "Y3" / "CSCI.323 Parsing"
    (m / "Lectures").mkdir(parents=True)
    (m / "Lectures" / "L1.txt").write_text("L1 text")
    fake = tmp_path / "claude"
    (tmp_path / "o.json").write_text(json.dumps({"result": "# notes\nok", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 3}}))
    fake.write_text(f"#!/bin/sh\ncat >/dev/null\ncat {tmp_path}/o.json\n")
    fake.chmod(0o755)
    web.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    mod = "Y3/CSCI.323 Parsing"
    s, st = req(web, "/api/ai/state", {}, page="ai")
    assert mod in st["modules"]
    s, p = req(web, "/api/ai/plan", {"module": mod, "what": "lectures", "week": None, "rels": []}, page="ai")
    assert s == 200 and p["jobs"] and {j["module"] for j in p["jobs"]} == {mod}
    s, _ = req(web, "/api/ai/notes", {"module": mod, "what": "lectures", "week": None, "rels": [],
                                      "select": [{"module": mod, "key": p["jobs"][0]["key"]}]}, page="ai")
    assert s == 200
    wait_job(web)
    assert (m / p["jobs"][0]["out"]).exists()


def test_unticked_files_are_left_out_of_a_weekly_note_and_stay_listed(web, tmp_path):  # noqa: F811
    mod = "Y3/CSCI.323 Weekly"
    m = web.ctx.root / mod
    for rel in ("Lectures/Week 2/a.txt", "Lectures/Week 2/slides/b.txt"):
        (m / rel).parent.mkdir(parents=True, exist_ok=True)
        (m / rel).write_text("text of " + rel)
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\ncat >/dev/null\n")
    fake.chmod(0o755)
    web.ctx.settings.llm = {"provider": "claude_cli", "model": "haiku", "executable": str(fake)}
    ask = {"module": mod, "what": "lectures", "week": None, "rels": []}
    s, p = req(web, "/api/ai/plan", ask, page="ai")
    j = p["jobs"][0]
    assert len(j["files"]) == 2 and j["excluded"] == []
    s, q = req(web, "/api/ai/plan", {**ask, "exclude": {mod: ["Lectures/Week 2/slides/b.txt"]}}, page="ai")
    j = q["jobs"][0]
    assert j["files"] == ["Lectures/Week 2/a.txt"] and j["excluded"] == ["Lectures/Week 2/slides/b.txt"]
    assert j["in_tokens"] < p["jobs"][0]["in_tokens"]
    s, r = req(web, "/api/ai/plan", {**ask, "exclude": {mod: ["Lectures/Week 2/a.txt", "Lectures/Week 2/slides/b.txt"]}}, page="ai")
    assert r["jobs"][0]["files"] == [] and len(r["jobs"][0]["excluded"]) == 2
