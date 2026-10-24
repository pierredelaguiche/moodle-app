"""SQLite access: connection and forward-only migrations. SQL for the tables lives in repos.py."""
from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


def connect(path) -> sqlite3.Connection:
    """Open (creating if needed) the database. Autocommit off is avoided: callers use `with conn:` for transactions."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def schema_version(conn) -> int:
    try:
        return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    except sqlite3.OperationalError:
        return 0


def migrate(conn) -> int:
    """Apply every NNNN_name.sql newer than the stored version, each in its own transaction. Idempotent. Returns the version."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied TEXT NOT NULL)")
    conn.commit()
    current = schema_version(conn)
    for f in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        n = int(f.name[:4])
        if n <= current:
            continue
        sql = f.read_text(encoding="utf-8")
        try:
            conn.execute("BEGIN")
            for stmt in _statements(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version(version, name, applied) VALUES (?, ?, datetime('now'))", (n, f.stem))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        current = n
    return current


def _statements(sql: str):
    """Split a migration into statements (no triggers/semicolons in strings are used in our migrations)."""
    buf = ""
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        buf += line + "\n"
        if line.rstrip().endswith(";"):
            if sqlite3.complete_statement(buf):
                yield buf.strip()
                buf = ""
    if buf.strip():
        yield buf.strip()
