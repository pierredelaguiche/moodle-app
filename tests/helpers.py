"""Shared test setup: a temp data dir, a study folder, a database and a mock Moodle."""
import json
from pathlib import Path
from types import SimpleNamespace

from mock_moodle import TOKEN, MockMoodle
from moodle_app import config, db
from moodle_app.moodle.client import Moodle
from moodle_app.services.context import Ctx

PDF = b"%PDF-1.4 " + b"x" * 20000


def make_env(tmp_path, clock=None, cfg_extra=None):
    mock = MockMoodle().start()
    home, root = tmp_path / "home", tmp_path / "Y3"
    folder = root / "CSCI.999 Test Module"
    folder.mkdir(parents=True)
    home.mkdir()
    cfg = {"site_url": mock.base, "dest_root": str(root), **(cfg_extra or {})}
    (home / "config.json").write_text(json.dumps(cfg))
    s = config.load(home)
    conn = db.connect(s.db_path)
    db.migrate(conn)
    lines = []
    ctx = Ctx(s, conn, Moodle(mock.base, TOKEN), log=lines.append, clock=clock or (lambda: 1_800_000_000))
    return SimpleNamespace(mock=mock, root=root, folder=folder, ctx=ctx, conn=conn, settings=s, log=lines, home=home)


def close_env(env):
    env.conn.close()
    env.mock.stop()
