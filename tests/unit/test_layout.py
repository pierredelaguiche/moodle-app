"""Organising the study folder: folders, tracked moves, rename, undo, subdivide by week, chosen destinations, allocation, PDF text."""
import threading
import time

import pytest

from helpers import PDF, close_env, make_env
from moodle_app.host.bridge import HostBridge
from moodle_app.services.files import FileService
from moodle_app.services.layout import Layout, LayoutError
from moodle_app.services.sync import run_sync
from moodle_app.web import server

NOW, DAY = 1_800_000_000, 86400


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    e.mock.courses[0].update(startdate=NOW - 10 * DAY, enddate=NOW + 80 * DAY)
    for sec, n in ((1, 2), (2, 2), (3, 1)):
        for i in range(n):
            e.mock.add_file(7, sec, sec * 100 + i, f"Workshop {sec}.{i}.pdf", PDF + bytes([sec, i]), activity=f"Workshop {sec}.{i}")
    run_sync(e.ctx)
    e.lay = Layout(e.ctx)
    yield e
    close_env(e)


def download(env, pred=lambda p: True):
    FileService(env.ctx).download([p for p in env.ctx.pending.values() if pred(p)])


def rec(env, name):
    return next(r for r in env.ctx.files.all() if r.filename == name)


def statuses(env):
    return {r.filename: r.status for r in env.ctx.files.all()}


MOD = "CSCI.999 Test Module"


def test_mkdir_nested_and_refusals(env):
    out = env.lay.mkdir(f"{MOD}/Research/Tools")
    assert (env.folder / "Research" / "Tools").is_dir() and out["created"] == [f"{MOD}/Research", f"{MOD}/Research/Tools"]
    for bad in ("", "../x", f"{MOD}/.hidden", f"{MOD}/a:b", f"{MOD}/Research/Tools"):
        with pytest.raises(LayoutError):
            env.lay.mkdir(bad)
    (env.folder / "link").symlink_to(env.root)
    with pytest.raises(LayoutError, match="symbolic"):
        env.lay.mkdir(f"{MOD}/link/inside")


def test_moved_file_is_followed_not_missing_or_new(env):
    download(env, lambda p: p.item.filename == "Workshop 1.0.pdf")
    r = rec(env, "Workshop 1.0.pdf")
    src = r.rel_path
    out = env.lay.move([src], f"{MOD}/Research")
    assert out["moved"][0]["to"] == f"{MOD}/Research/Workshop 1.0.pdf" and not out["skipped"]
    r = rec(env, "Workshop 1.0.pdf")
    assert r.rel_path == f"{MOD}/Research/Workshop 1.0.pdf" and (env.root / r.rel_path).exists() and not (env.root / src).exists()
    assert any(c["kind"] == "moved" and '"by": "you"' in c["detail"] for c in env.ctx.history.recent_changes())
    run_sync(env.ctx)                                             # a later sync sees nothing new and nothing missing
    assert statuses(env)["Workshop 1.0.pdf"] == "downloaded"
    assert rec(env, "Workshop 1.0.pdf").rel_path == r.rel_path and "Workshop 1.0.pdf" not in {e.item.filename for e in env.ctx.pending.values()}


def test_moving_a_folder_rewrites_every_record_inside(env):
    download(env)
    out = env.lay.move([f"{MOD}/Workshops"], f"{MOD}/Archive")
    assert out["moved"][0]["dir"] and (env.folder / "Archive" / "Workshops").is_dir()
    assert all(r.rel_path.startswith(f"{MOD}/Archive/Workshops/") for r in env.ctx.files.all() if r.filename.startswith("Workshop"))
    assert all((env.root / r.rel_path).exists() for r in env.ctx.files.all())


def test_move_rules_nothing_overwritten_module_folder_protected(env):
    (env.folder / "A").mkdir()
    (env.folder / "B").mkdir()
    (env.folder / "A" / "same.txt").write_text("a")
    (env.folder / "B" / "same.txt").write_text("b")
    out = env.lay.move([f"{MOD}/A/same.txt"], f"{MOD}/B")
    assert out["moved"] == [] and "already exists" in out["skipped"][0]["reason"] and (env.folder / "B" / "same.txt").read_text() == "b"
    out = env.lay.move([f"{MOD}/A"], MOD)
    assert out["skipped"][0].get("noop")
    out = env.lay.move([f"{MOD}/A"], f"{MOD}/A")
    assert "inside itself" in out["skipped"][0]["reason"]
    out = env.lay.move([f"{MOD}/A"], f"{MOD}/A/inner")
    assert "inside itself" in out["skipped"][0]["reason"]
    out = env.lay.move([MOD], "Elsewhere")
    assert "module folder" in out["skipped"][0]["reason"] and env.folder.is_dir()
    with pytest.raises(LayoutError, match="module folder"):
        env.lay.rename(MOD, "Renamed")
    with pytest.raises(LayoutError):
        env.lay.move([f"{MOD}/A"], "../outside")
    out = env.lay.move(["../x", f"{MOD}/nope.txt"], MOD)
    assert len(out["skipped"]) == 2 and not out["moved"]


def test_nested_selection_moves_the_parent_only(env):
    (env.folder / "A").mkdir()
    (env.folder / "A" / "x.txt").write_text("x")
    out = env.lay.move([f"{MOD}/A", f"{MOD}/A/x.txt"], f"{MOD}/B")
    assert [m["from"] for m in out["moved"]] == [f"{MOD}/A"] and (env.folder / "B" / "A" / "x.txt").exists()


def test_rename_follows_records_and_rejects_bad_names(env):
    download(env)
    env.lay.rename(f"{MOD}/Workshops", "Practicals")
    assert all("/Practicals/" in r.rel_path for r in env.ctx.files.all() if r.status == "downloaded" and r.filename.startswith("Workshop"))
    for bad in ("", "a/b", ".x", ".."):
        with pytest.raises(LayoutError):
            env.lay.rename(f"{MOD}/Practicals", bad)


def test_notes_manifest_follows_a_moved_note(env):
    (env.folder / "Notes").mkdir()
    (env.folder / "Notes" / "w1.md").write_text("# n")
    with env.conn:
        env.ctx.notes_manifest.put(f"{MOD}/Notes/w1.md", "h", "v", "c", "now")
    env.lay.move([f"{MOD}/Notes/w1.md"], f"{MOD}/Research")
    assert f"{MOD}/Research/w1.md" in env.ctx.notes_manifest.all()


def test_undo_restores_and_removes_folders_it_created(env):
    (env.folder / "a.txt").write_text("a")
    out = env.lay.move([f"{MOD}/a.txt"], f"{MOD}/New/Deep")
    assert out["created"] == [f"{MOD}/New", f"{MOD}/New/Deep"]
    res = env.lay.undo(out["moved"], out["created"])
    assert res["restored"] == [f"{MOD}/a.txt"] and (env.folder / "a.txt").exists() and not (env.folder / "New").exists()


def test_rmdir_only_when_empty(env):
    env.lay.mkdir(f"{MOD}/E")
    (env.folder / "E" / ".DS_Store").write_text("")
    env.lay.rmdir(f"{MOD}/E")
    assert not (env.folder / "E").exists()
    (env.folder / "F").mkdir()
    (env.folder / "F" / "f.txt").write_text("")
    with pytest.raises(LayoutError, match="not empty"):
        env.lay.rmdir(f"{MOD}/F")
    with pytest.raises(LayoutError, match="module folder"):
        env.lay.rmdir(MOD)


def test_subdivide_groups_by_moodle_week_and_by_name_then_undo(env):
    download(env)
    (env.folder / "Workshops" / "my week 4 scribble.txt").write_text("x")
    (env.folder / "Workshops" / "random.txt").write_text("x")
    plan = env.lay.subdivide_plan(f"{MOD}/Workshops")
    assert [w["week"] for w in plan["weeks"]] == [1, 2, 3, 4] and [f["name"] for f in plan["left"]] == ["random.txt"]
    assert not (env.folder / "Workshops" / "Week 01").exists()                       # a plan changes nothing
    out = env.lay.subdivide(f"{MOD}/Workshops")
    assert (env.folder / "Workshops" / "Week 02" / "Workshop 2.1.pdf").exists() and (env.folder / "Workshops" / "Week 04" / "my week 4 scribble.txt").exists()
    assert (env.folder / "Workshops" / "random.txt").exists()
    assert rec(env, "Workshop 2.1.pdf").rel_path == f"{MOD}/Workshops/Week 02/Workshop 2.1.pdf"
    run_sync(env.ctx)
    assert set(statuses(env).values()) == {"downloaded"}
    env.lay.undo(out["moved"], out["created"])
    assert (env.folder / "Workshops" / "Workshop 2.1.pdf").exists() and not (env.folder / "Workshops" / "Week 02").exists()


def test_subdivide_reuses_an_existing_week_folder_and_sends_waiting_files_there(env):
    download(env, lambda p: p.item.section_no <= 2)
    (env.folder / "Workshops" / "week_03").mkdir()
    env.mock.add_file(7, 3, 399, "Workshop 3.9.pdf", PDF + b"late", activity="Workshop 3.9")
    run_sync(env.ctx)                                                                  # new file for week 3 waits in Workshops
    env.lay.subdivide(f"{MOD}/Workshops")
    run_sync(env.ctx)
    new = next(e for e in env.ctx.pending.values() if e.item.filename == "Workshop 3.9.pdf")
    assert new.dest.parent == env.folder / "Workshops" / "week_03" and rec(env, "Workshop 3.9.pdf").status == "pending"


def test_chosen_destination_survives_syncs_and_download_uses_it(env):
    key = rec(env, "Workshop 1.0.pdf").key
    assert env.lay.retarget([key], f"{MOD}/Research/Tools") == 1
    assert rec(env, "Workshop 1.0.pdf").rel_path == f"{MOD}/Research/Tools/Workshop 1.0.pdf"
    run_sync(env.ctx)
    run_sync(env.ctx)
    e = env.ctx.pending[key]
    assert e.dest == env.folder / "Research" / "Tools" / "Workshop 1.0.pdf" and e.fixed
    FileService(env.ctx).download([e])
    assert (env.folder / "Research" / "Tools" / "Workshop 1.0.pdf").exists() and key not in env.ctx.targets.all()
    assert rec(env, "Workshop 1.0.pdf").status == "downloaded"


def test_a_target_outside_the_module_folder_downloads_too(env):
    other = env.root / "Shared"
    key = rec(env, "Workshop 1.1.pdf").key
    env.lay.retarget([key], "Shared")
    FileService(env.ctx).download([env.ctx.pending[key]])
    assert (other / "Workshop 1.1.pdf").exists() and rec(env, "Workshop 1.1.pdf").rel_path == "Shared/Workshop 1.1.pdf"
    run_sync(env.ctx)
    assert rec(env, "Workshop 1.1.pdf").status == "downloaded"


def test_moving_a_folder_keeps_the_planned_place_of_waiting_files(env):
    (env.folder / "Workshops").mkdir()
    env.lay.move([f"{MOD}/Workshops"], f"{MOD}/Archive")
    run_sync(env.ctx)
    assert all(e.dest.parent == env.folder / "Archive" / "Workshops" for e in env.ctx.pending.values() if e.item.filename.startswith("Workshop") and e.fixed)


def test_allocation_states(env):
    download(env, lambda p: p.item.filename in ("Workshop 1.0.pdf", "Workshop 1.1.pdf", "Workshop 2.0.pdf"))
    fs = FileService(env.ctx)
    fs.ignore([rec(env, "Workshop 2.1.pdf").key])
    (env.folder / "Workshops" / "Workshop 1.1.pdf").unlink()
    env.mock.clear_files(7)
    env.mock.add_file(7, 1, 100, "Workshop 1.0.pdf", PDF + bytes([1, 0]), modified=1_900_000_000, activity="Workshop 1.0")
    for sec, n in ((1, 2), (2, 2), (3, 1)):
        for i in range(n):
            if (sec, i) != (1, 0):
                env.mock.add_file(7, sec, sec * 100 + i, f"Workshop {sec}.{i}.pdf", PDF + bytes([sec, i]), activity=f"Workshop {sec}.{i}")
    run_sync(env.ctx)
    a = env.lay.allocation()
    st = {f["name"]: f["status"] for f in a["files"]}
    assert st["Workshop 1.0.pdf"] == "update" and st["Workshop 1.1.pdf"] == "missing" and st["Workshop 2.1.pdf"] == "ignored"
    assert st["Workshop 3.0.pdf"] == "new" and st["Workshop 2.0.pdf"] == "downloaded"
    f = next(x for x in a["files"] if x["name"] == "Workshop 3.0.pdf")
    assert f["folder"] == f"{MOD}/Workshops" and f["course"] == 7 and a["courses"][0]["folder"] == MOD


# ------------------------------------------------------------------ place (needs the app, because it runs as a job)
def run_job(app, job):
    deadline = time.time() + 20
    while job.running and time.time() < deadline:
        time.sleep(0.05)
    assert not job.running, "job did not finish"
    assert not job.error, job.error


def test_place_downloads_new_ignored_and_deleted_files_into_a_folder(env):
    app = server.App(env.ctx, HostBridge(env.home / "trash"))
    download(env, lambda p: p.item.filename == "Workshop 3.0.pdf")
    (env.folder / "Workshops" / "Workshop 3.0.pdf").unlink()                          # deleted here
    FileService(env.ctx).ignore([rec(env, "Workshop 2.1.pdf").key])                   # ignored
    keys = [rec(env, n).key for n in ("Workshop 1.0.pdf", "Workshop 2.1.pdf", "Workshop 3.0.pdf")]
    run_job(app, app.place(keys, f"{MOD}/Research"))
    for n in ("Workshop 1.0.pdf", "Workshop 2.1.pdf", "Workshop 3.0.pdf"):
        assert (env.folder / "Research" / n).exists() and rec(env, n).status == "downloaded"
    assert env.ctx.targets.all() == {}


def test_place_can_just_remember_the_destination(env):
    app = server.App(env.ctx, HostBridge(env.home / "trash"))
    k = rec(env, "Workshop 1.0.pdf").key
    run_job(app, app.place([k], f"{MOD}/Later", download=False))
    assert not (env.folder / "Later").exists() and env.ctx.targets.all() == {k: f"{MOD}/Later"}
    with pytest.raises(ValueError):
        app.place([], "x")
    with pytest.raises(LayoutError):
        app.place([k], "../escape")


# ------------------------------------------------------------------ PDF -> temporary text
def make_pdf(path, *pages):
    import pymupdf
    d = pymupdf.open()
    for t in pages:
        d.new_page().insert_text((72, 72), t)
    d.save(str(path))


def test_audit_finds_and_fixes_auto_placed_wrong_week(tmp_path):
    from moodle_app.services import audit
    e = make_env(tmp_path)
    try:
        e.mock.courses[0].update(startdate=NOW - 10 * DAY, enddate=NOW + 80 * DAY)
        for i in range(3):
            e.mock.add_file(7, 1, 500 + i, f"w02Lab0{i}.pdf", PDF + bytes([i]), activity=f"Lab {i}")
        run_sync(e.ctx)
        recs = [r for r in e.ctx.files.all()]
        assert all("Week 02" in r.rel_path for r in recs)            # name beats the section index (1)
        # simulate the old behaviour: files sitting in Week 01
        FileService(e.ctx).download(list(e.ctx.pending.values()))
        lay = Layout(e.ctx)
        for r in e.ctx.files.all():
            lay.move([r.rel_path], r.rel_path.replace("Week 02", "Week 01").rsplit("/", 1)[0])
        rows = audit.audit(e.ctx)
        assert len(rows) == 3 and all("week 1 -> 2" in x["issues"][0] and x["auto"] for x in rows)
        audit.fix(e.ctx, rows, lay)
        assert audit.audit(e.ctx) == []
    finally:
        close_env(e)
