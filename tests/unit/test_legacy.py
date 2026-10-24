import json
from pathlib import Path

from moodle_app import config, db
from moodle_app.services.context import Ctx
from moodle_app.services.legacy import import_legacy


def setup(tmp_path):
    root = tmp_path / "Y3"
    (root / "LZSCC.999 Test" / "Workshops").mkdir(parents=True)
    f = root / "LZSCC.999 Test" / "Workshops" / "W1.pdf"
    f.write_bytes(b"x" * 100)
    legacy = tmp_path / "moodle-tool"
    legacy.mkdir()
    (legacy / "moodle_sync.config.json").write_text(json.dumps({"site_url": "https://m.example", "dest_root": str(root),
                                                               "exclude_course_ids": [3], "read_command": "x.py"}))
    (legacy / ".moodle_token").write_text("abc123\n")
    state = {
        "7:100:/W1.pdf": {"sig": [111, 100], "path": str(f), "section": 1, "adopted": True},
        "7:101:/New.pdf": {"sig": [5, 50], "path": str(root / "LZSCC.999 Test" / "Lectures" / "New.pdf"), "section": 1,
                           "status": "pending", "moodle_name": "Lec", "sec_name": "Week 1", "size": 50, "first_seen": 9},
        "7:102:/Skip.pdf": {"sig": [5, 50], "path": str(root / "LZSCC.999 Test" / "Other" / "Skip.pdf"), "status": "ignored"},
        "7:103:/Upd.pdf": {"sig": [1, 10], "update_sig": [2, 20], "path": str(f.parent / "Upd.pdf")},
        "7:104:/Away.pdf": {"sig": [1, 10], "path": "/somewhere/else/Away.pdf"},
        "garbage": {"sig": [0, 0]},
    }
    (legacy / ".moodle_sync_state.json").write_text(json.dumps(state))
    home = tmp_path / "home"
    home.mkdir()
    s = config.load(home)
    conn = db.connect(s.db_path)
    db.migrate(conn)
    return Ctx(s, conn, None, log=lambda *_: None), legacy, root


def test_import_maps_state_config_token(tmp_path):
    ctx, legacy, root = setup(tmp_path)
    out = import_legacy(ctx, legacy)
    assert out["config"] == "imported" and out["token"] == "imported" and out["files"] == 4 and out["outside_root"] == 1
    assert ctx.settings.token_path.read_text().strip() == "abc123" and oct(ctx.settings.token_path.stat().st_mode)[-3:] == "600"
    saved = json.loads(ctx.settings.config_path.read_text())
    assert saved["site_url"] == "https://m.example" and "read_command" not in saved and saved["exclude_course_ids"] == [3]
    recs = {r.filename: r for r in ctx.files.all()}
    assert recs["W1.pdf"].status == "downloaded" and recs["W1.pdf"].adopted and recs["W1.pdf"].rel_path == "LZSCC.999 Test/Workshops/W1.pdf"
    assert recs["New.pdf"].status == "pending" and recs["New.pdf"].moodle_name == "Lec" and recs["New.pdf"].first_seen == 9
    assert recs["Skip.pdf"].status == "ignored"
    assert recs["Upd.pdf"].has_update and (recs["Upd.pdf"].upd_modified, recs["Upd.pdf"].upd_size) == (2, 20)
    assert ctx.courses.get(7)["folder_rel"] == "LZSCC.999 Test"


def test_import_twice_changes_nothing_and_never_overwrites(tmp_path):
    ctx, legacy, root = setup(tmp_path)
    import_legacy(ctx, legacy)
    r = ctx.files.get("7:101:/New.pdf")
    r.status = "ignored"
    with ctx.conn:
        ctx.files.put(r)
    (legacy / ".moodle_token").write_text("different\n")
    out = import_legacy(ctx, legacy)
    assert out["files"] == 0 and out["skipped_existing"] == 4 and out["token"] == "kept" and out["config"] == "kept"
    assert ctx.files.get("7:101:/New.pdf").status == "ignored" and ctx.settings.token_path.read_text().strip() == "abc123"


def test_rejects_a_folder_that_is_not_moodle_tool(tmp_path):
    import pytest
    ctx, legacy, root = setup(tmp_path)
    with pytest.raises(ValueError):
        import_legacy(ctx, tmp_path / "Y3")
