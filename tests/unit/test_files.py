"""Regression tests for the behaviour that must not change (docs/ARCHITECTURE.md), now against the database."""
from pathlib import Path

import pytest

from helpers import PDF, close_env, make_env
from moodle_app.moodle import fetch
from moodle_app.services.files import FileService
from moodle_app.services.sync import run_sync


@pytest.fixture
def env(tmp_path):
    e = make_env(tmp_path)
    yield e
    close_env(e)


def sync(env, **kw):
    return run_sync(env.ctx, what={"files"}, **kw)


def rec(env, name):
    return next(r for r in env.ctx.files.all() if r.filename == name)


def pending(env):
    return {e.item.filename: e for e in env.ctx.pending.values()}


def download(env, *names):
    return FileService(env.ctx).download([pending(env)[n] for n in names])


def test_new_files_pending_at_future_folder_and_nothing_downloads(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    env.mock.add_file(7, 1, 101, "01 Intro.pdf", PDF + b"y")
    sync(env)
    p = pending(env)
    assert p["Workshop 1.pdf"].dest == env.folder / "Workshops" / "Workshop 1.pdf"
    assert p["01 Intro.pdf"].dest == env.folder / "Lectures" / "01 Intro.pdf"
    assert {r.status for r in env.ctx.files.all()} == {"pending"}
    assert rec(env, "Workshop 1.pdf").rel_path == "CSCI.999 Test Module/Workshops/Workshop 1.pdf"
    assert not (env.folder / "Workshops").exists()


def test_only_ticked_files_download_unticked_stay_pending(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    env.mock.add_file(7, 1, 101, "01 Intro.pdf", PDF + b"y")
    sync(env)
    c = download(env, "Workshop 1.pdf")
    assert c["new"] == 1 and (env.folder / "Workshops" / "Workshop 1.pdf").read_bytes() == PDF
    assert not (env.folder / "Lectures").exists()
    sync(env)
    assert sorted(r.status for r in env.ctx.files.all()) == ["downloaded", "pending"]
    r = rec(env, "Workshop 1.pdf")
    assert r.moodle_name == "Resource" and r.sec_name == "Week 1" and r.first_seen          # metadata survives the download


def test_existing_file_is_matched_not_new(env):
    (env.folder / "Misc").mkdir()
    (env.folder / "Misc" / "my copy of intro.pdf").write_bytes(PDF)
    env.mock.add_file(7, 1, 101, "01 Intro.pdf", PDF)
    sync(env)
    r = rec(env, "01 Intro.pdf")
    assert r.adopted and r.status == "downloaded" and r.rel_path.endswith("Misc/my copy of intro.pdf") and not env.ctx.pending


def test_ignored_persists_across_syncs_and_unignore_restores(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    sync(env)
    svc = FileService(env.ctx)
    key = rec(env, "Workshop 1.pdf").key
    assert svc.ignore([key]) == 1
    sync(env)
    assert rec(env, "Workshop 1.pdf").status == "ignored" and not env.ctx.pending
    assert svc.unignore([key]) == 1
    sync(env)
    assert rec(env, "Workshop 1.pdf").status == "pending"


def test_ignore_only_applies_to_pending(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    sync(env)
    download(env, "Workshop 1.pdf")
    assert FileService(env.ctx).ignore([rec(env, "Workshop 1.pdf").key]) == 0


def test_moved_file_is_followed_not_redownloaded(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    sync(env)
    download(env, "Workshop 1.pdf")
    old, new = env.folder / "Workshops" / "Workshop 1.pdf", env.folder / "Coursework" / "Workshop 1.pdf"
    new.parent.mkdir()
    old.replace(new)
    sync(env)
    assert not env.ctx.pending and rec(env, "Workshop 1.pdf").rel_path.endswith("Coursework/Workshop 1.pdf")
    assert any(c["kind"] == "moved" for c in env.ctx.history.recent_changes())


def test_updated_file_archives_old_version(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF, modified=1_700_000_000)
    sync(env)
    download(env, "Workshop 1.pdf")
    env.mock.clear_files(7)
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF + b"NEW", modified=1_700_100_000)
    sync(env)
    e = pending(env)["Workshop 1.pdf"]
    assert e.kind == "UPD" and rec(env, "Workshop 1.pdf").has_update
    download(env, "Workshop 1.pdf")
    assert (env.folder / "Workshops" / "Workshop 1.pdf").read_bytes().endswith(b"NEW")
    assert len(list((env.folder / "Workshops" / "_old_versions").glob("*.pdf"))) == 1
    assert not rec(env, "Workshop 1.pdf").has_update


def test_week_folder_created_at_threshold_and_existing_reused(env):
    for i in range(3):
        env.mock.add_file(7, 2, 100 + i, f"Workshop 2 part {i}.pdf", PDF + bytes([i]))
    sync(env)
    assert all(e.dest.parent == env.folder / "Workshops" / "Week 02" for e in env.ctx.pending.values())
    (env.folder / "Lectures" / "week_03").mkdir(parents=True)
    env.mock.add_file(7, 3, 200, "Lecture 3.pdf", PDF + b"L")
    sync(env)
    assert pending(env)["Lecture 3.pdf"].dest.parent.name == "week_03"


def test_divided_folder_takes_new_files_into_their_week_below_the_threshold(env):
    (env.folder / "Lectures" / "Week 1").mkdir(parents=True)                 # the folder is already divided by weeks
    env.mock.add_file(7, 5, 400, "Lecture 5.pdf", PDF + b"5")                # one file only: far below the threshold of 3
    sync(env)
    dest = pending(env)["Lecture 5.pdf"].dest
    assert dest.parent == env.folder / "Lectures" / "Week 5"                 # a new week folder, named like its sibling
    download(env, "Lecture 5.pdf")
    assert (env.folder / "Lectures" / "Week 5" / "Lecture 5.pdf").exists()


def test_divided_rule_covers_any_category_and_leaves_undivided_ones_alone(env):
    (env.folder / "Coursework" / "week_02").mkdir(parents=True)              # not a week-folder category by default
    env.mock.add_file(7, 3, 410, "Coursework brief.pdf", PDF + b"c")
    env.mock.add_file(7, 3, 411, "Workshop 3.pdf", PDF + b"w")
    sync(env)
    p = pending(env)
    assert p["Coursework brief.pdf"].dest.parent == env.folder / "Coursework" / "week_03"
    assert p["Workshop 3.pdf"].dest.parent == env.folder / "Workshops"       # not divided, below the threshold: stays flat


def test_divided_folder_keeps_a_file_without_a_week_and_a_chosen_folder(env):
    (env.folder / "Lectures" / "Week 01").mkdir(parents=True)
    env.mock.add_file(7, 0, 420, "Lecture overview.pdf", PDF + b"o")         # section 0: no week to go to
    env.mock.add_file(7, 2, 421, "Lecture 2.pdf", PDF + b"2")
    sync(env)
    assert pending(env)["Lecture overview.pdf"].dest.parent == env.folder / "Lectures"
    env.ctx.targets.set(pending(env)["Lecture 2.pdf"].key, "CSCI.999 Test Module/Lectures")
    sync(env)
    assert pending(env)["Lecture 2.pdf"].dest.parent == env.folder / "Lectures"          # you chose: week grouping leaves it alone


def test_regroup_moves_flat_files_once_threshold_reached(env):
    for i in range(2):
        env.mock.add_file(7, 4, 300 + i, f"Workshop 4 part {i}.pdf", PDF + bytes([i + 10]))
    sync(env)
    download(env, *pending(env))
    assert all(Path(r.rel_path).parent.name == "Workshops" for r in env.ctx.files.all())
    env.mock.add_file(7, 4, 302, "Workshop 4 part 2.pdf", PDF + bytes([99]))
    sync(env)
    download(env, *pending(env))
    FileService(env.ctx).regroup()
    assert all(Path(r.rel_path).parent.name == "Week 04" for r in env.ctx.files.all())


def test_adopted_files_are_never_regrouped(env):
    (env.folder / "Workshops").mkdir()
    for i in range(3):
        (env.folder / "Workshops" / f"Workshop 5 part {i}.pdf").write_bytes(PDF + bytes([i]))
        env.mock.add_file(7, 5, 400 + i, f"Workshop 5 part {i}.pdf", PDF + bytes([i]))
    sync(env)
    assert FileService(env.ctx).regroup() == 0 and all((env.folder / "Workshops" / f"Workshop 5 part {i}.pdf").exists() for i in range(3))


def test_state_key_survives_url_revision_change(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    sync(env)
    download(env, "Workshop 1.pdf")
    keys = {r.key for r in env.ctx.files.all()}
    for s in env.mock.sections[7]:
        for m in s["modules"]:
            for ct in m["contents"]:
                ct["fileurl"] += "?forcedownload=1&rev=2"
    sync(env)
    assert not env.ctx.pending and {r.key for r in env.ctx.files.all()} == keys


def test_missing_then_close_stays_closed_and_redownload(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    env.mock.add_file(7, 1, 101, "01 Intro.pdf", PDF + b"y")
    sync(env)
    download(env, "Workshop 1.pdf", "01 Intro.pdf")
    (env.folder / "Workshops" / "Workshop 1.pdf").unlink()
    (env.folder / "Lectures" / "01 Intro.pdf").unlink()
    svc = FileService(env.ctx)
    assert svc.close_missing([rec(env, "Workshop 1.pdf").key]) == 1
    sync(env)
    assert rec(env, "Workshop 1.pdf").status == "ignored"
    svc.redownload(rec(env, "01 Intro.pdf").key)
    sync(env)
    assert rec(env, "01 Intro.pdf").status == "pending"
    assert svc.close_missing([rec(env, "01 Intro.pdf").key]) == 0          # not downloaded, so nothing to close


def test_pending_removed_from_moodle_is_forgotten(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    sync(env)
    env.mock.clear_files(7)
    sync(env)
    assert env.ctx.files.all() == []


def test_failed_download_leaves_record_pending(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    sync(env)
    env.mock.blobs.clear()
    c = download(env, "Workshop 1.pdf")
    assert c["failed"] == 1 and rec(env, "Workshop 1.pdf").status == "pending"
    assert not (env.folder / "Workshops").exists()
    assert not list(env.folder.rglob("*.part"))


def test_identical_small_file_is_not_kept_twice(env):
    (env.folder / "Misc").mkdir()
    (env.folder / "Misc" / "completely different name.pdf").write_bytes(b"tiny")
    env.mock.add_file(7, 1, 100, "Brand New.pdf", b"tiny")
    sync(env)
    assert pending(env)                                            # small same-size file is not matched by size alone
    c = download(env, "Brand New.pdf")
    assert c["same"] == 1 and not (env.folder / "Lectures").exists() and rec(env, "Brand New.pdf").adopted


def test_subsection_files_go_to_the_parent_week(env):
    env.mock.add_subsection(7, parent_section=2, cmid=900, instance=55, delegated_section=40, name="Optional extras")
    env.mock.add_file(7, 40, 901, "Lecture extra.pdf", PDF)
    env.mock.sections[7][-1]["itemid"] = 55
    sync(env)
    r = rec(env, "Lecture extra.pdf")
    assert r.section_no == 2 and r.sec_name == "Week 2"


def test_no_files_for_untracked_course(env):
    env.mock.courses.append({"id": 8, "fullname": "CSCI.888 Other", "shortname": "CSCI.888"})
    env.mock.sections[8] = []
    env.mock.add_file(8, 1, 500, "Lecture 1.pdf", PDF)
    sync(env)
    assert env.ctx.files.all() == [] and [c["id"] for c in env.ctx.courses.tracked()] == [7]
    assert {c["id"]: c["tracked"] for c in env.ctx.courses.all()} == {7: 1, 8: 0}


def test_bad_token_raises_and_records_failed_run(env):
    from moodle_app.moodle.client import Moodle, MoodleError
    env.ctx.moodle = Moodle(env.mock.base, "wrong")
    with pytest.raises(MoodleError, match="invalidtoken"):
        run_sync(env.ctx, what={"files"})
    last = env.ctx.history.last_run("sync", ok_only=False)
    assert last["ok"] == 0 and "invalidtoken" in last["error"]


def test_ignored_file_stays_ignored_even_when_moodle_changes_it(env):
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF, modified=1_700_000_000)
    sync(env)
    FileService(env.ctx).ignore([rec(env, "Workshop 1.pdf").key])
    env.mock.clear_files(7)
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF + b"NEW", modified=1_700_100_000)
    sync(env)
    assert rec(env, "Workshop 1.pdf").status == "ignored" and not env.ctx.pending
