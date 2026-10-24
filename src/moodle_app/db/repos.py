"""Repositories: the only place SQL is written. Each takes an open sqlite3 connection.

Writes do not commit by themselves; wrap a unit of work in `with conn:` (services do).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, fields
from typing import Optional


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------- settings
class Settings:
    def __init__(self, conn):
        self.c = conn

    def get(self, key, default=None):
        r = self.c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set(self, key, value):
        self.c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value)))


# ---------------------------------------------------------------- courses / sections / activities
class Courses:
    def __init__(self, conn):
        self.c = conn

    def upsert(self, course: dict, folder_rel: Optional[str], tracked: bool, ts: int):
        self.c.execute(
            """INSERT INTO courses(id,shortname,fullname,startdate,enddate,hidden,progress,folder_rel,tracked,first_seen,last_seen)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET shortname=excluded.shortname, fullname=excluded.fullname,
                 startdate=excluded.startdate, enddate=excluded.enddate, hidden=excluded.hidden, progress=excluded.progress,
                 folder_rel=excluded.folder_rel, tracked=excluded.tracked, last_seen=excluded.last_seen""",
            (course["id"], course.get("shortname") or "", course.get("fullname") or "", course.get("startdate") or 0,
             course.get("enddate") or 0, 1 if course.get("hidden") else 0, course.get("progress"), folder_rel,
             1 if tracked else 0, ts, ts))

    def all(self):
        return self.c.execute("SELECT * FROM courses ORDER BY fullname").fetchall()

    def tracked(self):
        return self.c.execute("SELECT * FROM courses WHERE tracked=1 ORDER BY fullname").fetchall()

    def get(self, cid):
        return self.c.execute("SELECT * FROM courses WHERE id=?", (cid,)).fetchone()


class Structure:
    """sections + activities of a course, replaced wholesale on each fetch."""
    def __init__(self, conn):
        self.c = conn

    def replace_course(self, course_id, sections, activities, ts):
        self.c.execute("DELETE FROM sections WHERE course_id=?", (course_id,))
        self.c.executemany("INSERT INTO sections(course_id,section_no,name,visible) VALUES(?,?,?,?)",
                           [(course_id, s["section_no"], s["name"], 1 if s.get("visible", True) else 0) for s in sections])
        for a in activities:
            self.c.execute(
                """INSERT INTO activities(cmid,course_id,section_no,modname,instance,name,url,visible,first_seen,last_seen)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(cmid) DO UPDATE SET course_id=excluded.course_id, section_no=excluded.section_no,
                     modname=excluded.modname, instance=excluded.instance, name=excluded.name, url=excluded.url,
                     visible=excluded.visible, last_seen=excluded.last_seen""",
                (a["cmid"], course_id, a["section_no"], a["modname"], a.get("instance"), a["name"], a.get("url") or "",
                 1 if a.get("visible", True) else 0, ts, ts))
        keep = [a["cmid"] for a in activities]
        if keep:
            self.c.execute(f"DELETE FROM activities WHERE course_id=? AND cmid NOT IN ({','.join('?' * len(keep))})",
                           [course_id, *keep])
        else:
            self.c.execute("DELETE FROM activities WHERE course_id=?", (course_id,))

    def sections(self, course_id):
        return self.c.execute("SELECT * FROM sections WHERE course_id=? ORDER BY section_no", (course_id,)).fetchall()

    def activity(self, cmid):
        return self.c.execute("SELECT * FROM activities WHERE cmid=?", (cmid,)).fetchone()


# ---------------------------------------------------------------- files
@dataclass
class FileRec:
    key: str
    course_id: int
    status: str                      # pending | downloaded | ignored
    rel_path: str = ""
    cmid: Optional[int] = None
    section_no: int = 0
    sec_name: str = ""
    moodle_name: str = ""
    filename: str = ""
    adopted: bool = False
    size: int = 0
    sig_modified: int = 0
    sig_size: int = 0
    upd_modified: Optional[int] = None
    upd_size: Optional[int] = None
    first_seen: int = 0
    last_seen: int = 0

    @property
    def sig(self):
        return (self.sig_modified, self.sig_size)

    @property
    def has_update(self):
        return self.upd_modified is not None

    @classmethod
    def from_row(cls, r):
        d = {f.name: r[f.name] for f in fields(cls)}
        d["adopted"] = bool(d["adopted"])
        return cls(**d)


class Files:
    COLS = [f.name for f in fields(FileRec)]

    def __init__(self, conn):
        self.c = conn

    def get(self, key) -> Optional[FileRec]:
        r = self.c.execute("SELECT * FROM files WHERE key=?", (key,)).fetchone()
        return FileRec.from_row(r) if r else None

    def by_course(self, course_id):
        return [FileRec.from_row(r) for r in self.c.execute("SELECT * FROM files WHERE course_id=? ORDER BY key", (course_id,))]

    def all(self):
        return [FileRec.from_row(r) for r in self.c.execute("SELECT * FROM files ORDER BY key")]

    def put(self, rec: FileRec):
        vals = [int(getattr(rec, c)) if c == "adopted" else getattr(rec, c) for c in self.COLS]
        self.c.execute(f"INSERT OR REPLACE INTO files({','.join(self.COLS)}) VALUES({','.join('?' * len(self.COLS))})", vals)

    def delete(self, key):
        self.c.execute("DELETE FROM files WHERE key=?", (key,))

    def rewrite_paths(self, old, new):
        """A file or folder moved from `old` to `new`: follow it in every record. Returns [(key, course_id, status, old_path, new_path)]."""
        n, out = len(old), []
        rows = self.c.execute("SELECT key, course_id, status, rel_path FROM files WHERE rel_path=? OR substr(rel_path,1,?)=?",
                              (old, n + 1, old + "/")).fetchall()
        for r in rows:
            path = new + r["rel_path"][n:]
            self.c.execute("UPDATE files SET rel_path=? WHERE key=?", (path, r["key"]))
            out.append((r["key"], r["course_id"], r["status"], r["rel_path"], path))
        return out

    def counts(self):
        return {r["status"]: r["n"] for r in self.c.execute("SELECT status, COUNT(*) n FROM files GROUP BY status")}


# ---------------------------------------------------------------- deadlines
class Deadlines:
    def __init__(self, conn):
        self.c = conn

    def get(self, id_):
        return self.c.execute("SELECT * FROM deadlines WHERE id=?", (id_,)).fetchone()

    def put(self, d: dict, ts: int):
        """Insert or update from Moodle. Your own fields (hidden, done, note) are never touched here."""
        old = self.get(d["id"])
        if old is None:
            self.c.execute(
                """INSERT INTO deadlines(id,course_id,cmid,kind,title,due_ts,cutoff_ts,allow_from_ts,url,source,status,grade_text,
                   seq,gone,first_seen,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?)""",
                (d["id"], d["course_id"], d.get("cmid"), d["kind"], d["title"], d["due_ts"], d.get("cutoff_ts"),
                 d.get("allow_from_ts"), d.get("url") or "", d["source"], d.get("status", "open"), d.get("grade_text", ""), ts, ts))
            return "new", None
        seq = old["seq"] + (1 if old["due_ts"] != d["due_ts"] else 0)
        self.c.execute(
            """UPDATE deadlines SET course_id=?,cmid=?,kind=?,title=?,due_ts=?,cutoff_ts=?,allow_from_ts=?,url=?,source=?,status=?,
               grade_text=?,seq=?,gone=0,updated=? WHERE id=?""",
            (d["course_id"], d.get("cmid"), d["kind"], d["title"], d["due_ts"], d.get("cutoff_ts"), d.get("allow_from_ts"),
             d.get("url") or "", d["source"], d.get("status", old["status"]), d.get("grade_text", old["grade_text"]), seq, ts, d["id"]))
        if old["due_ts"] != d["due_ts"]:
            return "moved", old["due_ts"]
        return "same", None

    def mark_gone(self, ids_present, sources, ts):
        """Deadlines of the given sources that Moodle no longer returns (deleted/hidden there)."""
        q = f"SELECT id FROM deadlines WHERE gone=0 AND source IN ({','.join('?' * len(sources))})"
        gone = [r["id"] for r in self.c.execute(q, list(sources)) if r["id"] not in ids_present]
        for i in gone:
            self.c.execute("UPDATE deadlines SET gone=1, updated=? WHERE id=?", (ts, i))
        return gone

    def upcoming(self, since_ts, include_hidden=False, include_gone=False):
        q = "SELECT d.*, c.shortname AS course_short, c.fullname AS course_name FROM deadlines d JOIN courses c ON c.id=d.course_id WHERE d.due_ts>=?"
        if not include_hidden:
            q += " AND d.hidden=0"
        if not include_gone:
            q += " AND d.gone=0"
        return self.c.execute(q + " ORDER BY d.due_ts", (since_ts,)).fetchall()

    def between(self, start_ts, end_ts, include_hidden=False):
        q = ("SELECT d.*, c.shortname AS course_short, c.fullname AS course_name FROM deadlines d "
             "JOIN courses c ON c.id=d.course_id WHERE d.gone=0 AND d.due_ts>=? AND d.due_ts<?")
        if not include_hidden:
            q += " AND d.hidden=0"
        return self.c.execute(q + " ORDER BY d.due_ts", (start_ts, end_ts)).fetchall()

    def all_for_ics(self):
        return self.c.execute("SELECT d.*, c.shortname AS course_short, c.fullname AS course_name FROM deadlines d "
                              "JOIN courses c ON c.id=d.course_id WHERE d.hidden=0 ORDER BY d.due_ts").fetchall()

    def set_flag(self, id_, **flags):
        for k, v in flags.items():
            if k not in ("hidden", "done", "note"):
                raise ValueError(k)
            self.c.execute(f"UPDATE deadlines SET {k}=? WHERE id=?", (v, id_))


# ---------------------------------------------------------------- announcements
class Announcements:
    def __init__(self, conn):
        self.c = conn

    def put(self, a: dict, ts: int):
        old = self.c.execute("SELECT id FROM announcements WHERE id=?", (a["id"],)).fetchone()
        if old is None:
            self.c.execute(
                """INSERT INTO announcements(id,course_id,forum_id,subject,message,author,created,modified,pinned,read,first_seen)
                   VALUES(?,?,?,?,?,?,?,?,?,0,?)""",
                (a["id"], a["course_id"], a.get("forum_id"), a["subject"], a["message"], a.get("author", ""),
                 a.get("created", 0), a.get("modified", 0), 1 if a.get("pinned") else 0, ts))
            return True
        self.c.execute("UPDATE announcements SET subject=?, message=?, modified=?, pinned=? WHERE id=?",
                       (a["subject"], a["message"], a.get("modified", 0), 1 if a.get("pinned") else 0, a["id"]))
        return False

    def recent(self, limit=50, unread_only=False):
        q = "SELECT a.*, c.shortname AS course_short FROM announcements a JOIN courses c ON c.id=a.course_id"
        if unread_only:
            q += " WHERE a.read=0"
        return self.c.execute(q + " ORDER BY a.created DESC LIMIT ?", (limit,)).fetchall()

    def mark_read(self, ids, read=True):
        self.c.executemany("UPDATE announcements SET read=? WHERE id=?", [(1 if read else 0, i) for i in ids])


# ---------------------------------------------------------------- runs / changes
class History:
    def __init__(self, conn):
        self.c = conn

    def start(self, kind, ts) -> int:
        cur = self.c.execute("INSERT INTO runs(kind,started) VALUES(?,?)", (kind, ts))
        return cur.lastrowid

    def finish(self, run_id, ts, ok, error=None, summary=None):
        self.c.execute("UPDATE runs SET finished=?, ok=?, error=?, summary=? WHERE id=?",
                       (ts, 1 if ok else 0, error, json.dumps(summary) if summary is not None else None, run_id))

    def add(self, run_id, ts, kind, course_id=None, ref="", detail=None):
        self.c.execute("INSERT INTO changes(run_id,ts,kind,course_id,ref,detail) VALUES(?,?,?,?,?,?)",
                       (run_id, ts, kind, course_id, ref, json.dumps(detail) if detail is not None else ""))

    def recent_changes(self, limit=200, since=0, kind=None):
        q, args = "SELECT * FROM changes WHERE ts>=?", [since]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        return self.c.execute(q + " ORDER BY ts DESC, id DESC LIMIT ?", [*args, limit]).fetchall()

    def last_run(self, kind="sync", ok_only=True):
        q = "SELECT * FROM runs WHERE kind=?" + (" AND ok=1" if ok_only else "") + " ORDER BY id DESC LIMIT 1"
        return self.c.execute(q, (kind,)).fetchone()


# ---------------------------------------------------------------- notes manifest
class NotesManifest:
    def __init__(self, conn):
        self.c = conn

    def get(self, rel):
        r = self.c.execute("SELECT * FROM notes_manifest WHERE rel=?", (rel,)).fetchone()
        return dict(r) if r else None

    def put(self, rel, input_hash, version, content_hash, generated):
        self.c.execute("INSERT OR REPLACE INTO notes_manifest(rel,input_hash,version,content_hash,generated) VALUES(?,?,?,?,?)",
                       (rel, input_hash, version, content_hash, generated))

    def all(self):
        return {r["rel"]: dict(r) for r in self.c.execute("SELECT * FROM notes_manifest")}

    def rewrite(self, old, new):
        """A generated file (or the folder holding it) was moved by you: keep it known as generated."""
        n = len(old)
        for r in self.c.execute("SELECT rel FROM notes_manifest WHERE rel=? OR substr(rel,1,?)=?", (old, n + 1, old + "/")).fetchall():
            self.c.execute("UPDATE notes_manifest SET rel=? WHERE rel=?", (new + r["rel"][n:], r["rel"]))


# ---------------------------------------------------------------- where a not-yet-downloaded file should go
class Targets:
    def __init__(self, conn):
        self.c = conn

    def all(self):
        return {r["key"]: r["folder_rel"] for r in self.c.execute("SELECT key, folder_rel FROM file_targets")}

    def set(self, key, folder_rel):
        self.c.execute("INSERT OR REPLACE INTO file_targets(key,folder_rel) VALUES(?,?)", (key, folder_rel))

    def delete(self, keys):
        for k in keys:
            self.c.execute("DELETE FROM file_targets WHERE key=?", (k,))

    def rewrite(self, old, new):
        n = len(old)
        for r in self.c.execute("SELECT key, folder_rel FROM file_targets WHERE folder_rel=? OR substr(folder_rel,1,?)=?", (old, n + 1, old + "/")).fetchall():
            self.c.execute("UPDATE file_targets SET folder_rel=? WHERE key=?", (new + r["folder_rel"][n:], r["key"]))

    def prune(self):
        """Forget choices for files that are no longer waiting to be downloaded."""
        self.c.execute("DELETE FROM file_targets WHERE key NOT IN (SELECT key FROM files WHERE status IN ('pending','ignored'))")


# ---------------------------------------------------------------- reviewed / todos (schema ready; UI comes with the Tracker)
class Reviewed:
    def __init__(self, conn):
        self.c = conn

    def mark(self, scope, ref, ts, source="manual"):
        self.c.execute("INSERT OR REPLACE INTO reviewed(scope,ref,ts,source) VALUES(?,?,?,?)", (scope, ref, ts, source))

    def unmark(self, scope, ref):
        self.c.execute("DELETE FROM reviewed WHERE scope=? AND ref=?", (scope, ref))

    def is_marked(self, scope, ref):
        return self.c.execute("SELECT 1 FROM reviewed WHERE scope=? AND ref=?", (scope, ref)).fetchone() is not None

    def refs(self, scope):
        return {r["ref"]: r["ts"] for r in self.c.execute("SELECT ref, ts FROM reviewed WHERE scope=?", (scope,))}


# ---------------------------------------------------------------- your own calendar entries
class Events:
    def __init__(self, conn):
        self.c = conn

    def add(self, title, start_ts, end_ts=None, all_day=False, course_id=None, note="", ts=0):
        cur = self.c.execute("INSERT INTO events(title,start_ts,end_ts,all_day,course_id,note,created) VALUES(?,?,?,?,?,?,?)",
                             (title, start_ts, end_ts, 1 if all_day else 0, course_id, note, ts))
        return cur.lastrowid

    def get(self, id_):
        return self.c.execute("SELECT * FROM events WHERE id=?", (id_,)).fetchone()

    def update(self, id_, **fields):
        allowed = {"title", "start_ts", "end_ts", "all_day", "course_id", "note"}
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            self.c.execute(f"UPDATE events SET {k}=? WHERE id=?", (v, id_))

    def delete(self, id_):
        self.c.execute("DELETE FROM events WHERE id=?", (id_,))

    def between(self, start_ts, end_ts):
        return self.c.execute(
            "SELECT e.*, c.shortname AS course_short FROM events e LEFT JOIN courses c ON c.id=e.course_id "
            "WHERE e.start_ts<? AND COALESCE(e.end_ts, e.start_ts)>=? ORDER BY e.start_ts", (end_ts, start_ts)).fetchall()

    def all(self):
        return self.c.execute("SELECT e.*, c.shortname AS course_short FROM events e LEFT JOIN courses c ON c.id=e.course_id "
                              "ORDER BY e.start_ts").fetchall()
