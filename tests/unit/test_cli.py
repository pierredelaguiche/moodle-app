import json

import pytest

from helpers import PDF, close_env, make_env
from mock_moodle import TOKEN
from moodle_app import cli

NOW, DAY = 1_800_000_000, 86400


@pytest.fixture
def cenv(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    monkeypatch.setenv("MOODLE_TOKEN", TOKEN)
    env.mock.add_file(7, 1, 100, "Workshop 1.pdf", PDF)
    env.mock.assignments[7] = [{"id": 11, "cmid": 301, "course": 7, "name": "CW1", "duedate": 4_000_000_000}]
    yield env
    close_env(env)


def run(env, *args):
    cli.main(["--home", str(env.home), *args])


def test_init_creates_config_and_database(tmp_path, capsys):
    home = tmp_path / "fresh"
    cli.main(["--home", str(home), "init"])
    assert (home / "config.json").exists() and (home / "moodle_app.db").exists()
    assert json.loads((home / "config.json").read_text()).get("site_url") == ""
    cli.main(["--home", str(home), "init"])
    assert "already exists" in capsys.readouterr().out


def test_sync_pending_download_deadlines_ics_status(cenv, capsys, tmp_path):
    run(cenv, "courses")
    assert "CSCI.999 Test Module  ->  CSCI.999 Test Module" in capsys.readouterr().out
    run(cenv, "pending")
    out = capsys.readouterr().out
    assert "NEW Workshops/Workshop 1.pdf" in out
    run(cenv, "download", "--all")
    assert (cenv.folder / "Workshops" / "Workshop 1.pdf").exists()
    run(cenv, "sync", "--only", "deadlines")
    capsys.readouterr()
    run(cenv, "deadlines")
    assert "[CSCI.999] CW1" in capsys.readouterr().out
    out_file = tmp_path / "d.ics"
    run(cenv, "ics", "--out", str(out_file))
    assert b"UID:7:301:assign@moodle-app\r\n" in out_file.read_bytes()
    run(cenv, "history")
    assert "downloaded" in capsys.readouterr().out
    run(cenv, "status")
    s = capsys.readouterr().out
    assert "schema v3" in s and "4.5.15" in s


def test_sync_if_due_skips_a_recent_run(cenv, capsys):
    run(cenv, "sync", "--only", "deadlines")
    capsys.readouterr()
    import time
    # the run was recorded with the real clock in this process, so it is "recent"
    run(cenv, "sync", "--if-due")
    assert "skipping" in capsys.readouterr().out


def test_download_requires_all(cenv):
    with pytest.raises(SystemExit):
        run(cenv, "download")


def test_bad_token_gives_a_helpful_message(cenv, monkeypatch):
    monkeypatch.setenv("MOODLE_TOKEN", "wrong")
    with pytest.raises(SystemExit) as e:
        run(cenv, "sync")
    assert "invalidtoken" in str(e.value) and "moodle-app token" in str(e.value)


def test_missing_config_gives_a_helpful_message(tmp_path):
    (tmp_path / "h").mkdir()
    with pytest.raises(SystemExit) as e:
        cli.main(["--home", str(tmp_path / "h"), "sync"])
    assert "site_url" in str(e.value)


def test_notes_dry_run_through_the_cli(cenv, capsys):
    import pymupdf
    pdf = cenv.folder / "Lectures" / "Week 01" / "01 Intro.pdf"
    pdf.parent.mkdir(parents=True)
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Intro")
    d.save(str(pdf))
    with pytest.raises(SystemExit) as e:
        run(cenv, "notes", "CSCI", "--dry-run")
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "Lectures/01 Intro.md" in out and "Dry run" in out and not (cenv.folder / "Notes").exists()
