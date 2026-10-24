import sqlite3

import pytest

from moodle_app import db
from moodle_app.db import repos


def test_fresh_migrate_and_idempotent(tmp_path):
    c = db.connect(tmp_path / "a.db")
    latest = len(list(db.MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")))
    assert db.migrate(c) == latest and db.migrate(c) == latest
    tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert {"courses", "files", "deadlines", "announcements", "runs", "changes", "notes_manifest", "reviewed", "todos", "events"} <= tables
    assert c.execute("select count(*) from schema_version").fetchone()[0] == latest


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    bad = tmp_path / "mig"
    bad.mkdir()
    (bad / "0001_ok.sql").write_text("CREATE TABLE a(x);")
    (bad / "0002_bad.sql").write_text("CREATE TABLE b(x);\nCREATE TABLE a(x);")      # second statement fails
    monkeypatch.setattr(db, "MIGRATIONS", bad)
    c = db.connect(tmp_path / "b.db")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(c)
    assert db.schema_version(c) == 1
    assert c.execute("select name from sqlite_master where name='b'").fetchone() is None


def test_foreign_keys_enforced_and_cascade(tmp_path):
    c = db.connect(tmp_path / "c.db")
    db.migrate(c)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO sections(course_id,section_no) VALUES(99,1)")
    repos.Courses(c).upsert({"id": 1, "fullname": "A"}, "A", True, 1)
    repos.Structure(c).replace_course(1, [{"section_no": 1, "name": "W1"}], [], 1)
    c.execute("DELETE FROM courses WHERE id=1")
    assert c.execute("select count(*) from sections").fetchone()[0] == 0


def test_file_status_check_constraint(tmp_path):
    c = db.connect(tmp_path / "d.db")
    db.migrate(c)
    repos.Courses(c).upsert({"id": 1}, None, False, 1)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO files(key,course_id,status,first_seen,last_seen) VALUES('k',1,'weird',1,1)")


def test_settings_roundtrip_and_deadline_put_keeps_user_flags(tmp_path):
    c = db.connect(tmp_path / "e.db")
    db.migrate(c)
    repos.Settings(c).set("k", {"a": [1, 2]})
    assert repos.Settings(c).get("k") == {"a": [1, 2]} and repos.Settings(c).get("nope", 5) == 5
    repos.Courses(c).upsert({"id": 1, "shortname": "S"}, "S", True, 1)
    dl = repos.Deadlines(c)
    d = {"id": "1:2:assign", "course_id": 1, "cmid": 2, "kind": "assign", "title": "T", "due_ts": 100, "source": "assign"}
    assert dl.put(d, 1) == ("new", None)
    dl.set_flag("1:2:assign", hidden=1, done=1, note="mine")
    assert dl.put({**d, "title": "T2"}, 2) == ("same", None)
    assert dl.put({**d, "due_ts": 200}, 3) == ("moved", 100)
    r = dl.get("1:2:assign")
    assert (r["hidden"], r["done"], r["note"], r["seq"], r["title"]) == (1, 1, "mine", 1, "T")
