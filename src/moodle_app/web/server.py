"""Local web GUI server (standard library only).

Safety (unchanged from the first version): listens on localhost only; every API call needs a random per-run token
(`X-Token` header; `?t=` for the page); the Host header is checked; every path is resolved and must lie inside dest_root;
delete moves ONE regular file to the trash and never recurses. The calendar feed has its own secret key in the URL.
"""
from __future__ import annotations

import contextlib
import json
import re
import secrets
import shutil
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config
from ..domain.text import html_to_text  # noqa: F401  (re-exported)
from ..host import get_bridge
from ..services import calendar as calendar_service
from ..services import digest as digest_service
from ..services import ics as ics_service
from ..services import tracker as tracker_service
from ..services.editor import MAX_BYTES, RAW_TYPES, TEXT_SUFFIXES, Conflict, Editor, EditorError
from ..services import sync as sync_service
from ..services.files import FileService
from ..services.setup import Setup
from ..services.layout import Layout, LayoutError
from ..services.read import pdf_text, read_folder
from ..services.tree import build_tree

STATIC = Path(__file__).parent / "static"
# scripts and styles only from this server; no inline script; previews are blob: URLs made by the page itself
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob: data:; font-src 'self'; "
       "frame-src blob:; object-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Job:
    def __init__(self, name):
        self.name, self.log, self.running, self.error, self.id = name, [], True, None, secrets.token_hex(4)

    def write(self, text):                       # used as a stdout replacement while the job runs
        for line in str(text).splitlines():
            if line.strip():
                self.log.append(line.rstrip())
        return len(text)

    def flush(self):
        pass


class App:
    def __init__(self, ctx, bridge=None):
        self.x = ctx
        self.bridge = bridge or get_bridge(ctx.settings.home)
        self.lock = threading.RLock()
        self.job = None
        self.scanned_at, self.scan_error = None, None
        self.token = secrets.token_urlsafe(24)
        self.files = FileService(ctx)
        self.layout = Layout(ctx)
        self._editor = None
        ctx.log = self._log

    def _log(self, msg):
        print(msg)                               # inside a job this lands in the job log; otherwise on the terminal

    # ---------- jobs ----------
    def start(self, name, fn):
        with self.lock:
            if self.job and self.job.running:
                raise RuntimeError("another action is still running")
            job = self.job = Job(name)

        def run():
            try:
                with contextlib.redirect_stdout(job):
                    fn()
            except Exception as e:
                job.error = str(e)
                job.log.append(f"! {e}")
            finally:
                job.running = False
        threading.Thread(target=run, daemon=True).start()
        return job

    def _idle_during_network(self, m):
        """Let the page read the database while a Moodle request is in flight (only when no transaction is open)."""
        orig = m.call

        def call(fn, **params):
            free = not self.x.conn.in_transaction
            if free:
                try:
                    self.lock.release()
                except RuntimeError:                 # this thread does not hold it
                    free = False
            try:
                return orig(fn, **params)
            finally:
                if free:
                    self.lock.acquire()
        m.call = call
        return lambda: m.__dict__.pop("call", None)

    @property
    def setup(self):
        if not hasattr(self, "_setup"):
            self._setup = Setup(self.x, self.lock)
        return self._setup

    def refresh(self, what=None):
        import time
        if self.x.moodle is None:
            self.scan_error = "Moodle is not set up yet. Open Help and follow the quick start."
            return
        with self.lock:
            m = self.x.moodle
            restore = self._idle_during_network(m) if m is not None else (lambda: None)
            try:
                sync_service.run_sync(self.x, what=what)
                self.scanned_at, self.scan_error = time.time(), None
            except Exception as e:               # network down, token expired, ...
                self.scan_error = str(e)
                print(f"! Moodle check failed: {e}")
            finally:
                restore()

    def download(self, keys):
        def work():
            with self.lock:
                chosen = [self.x.pending[k] for k in keys if k in self.x.pending]
                if not chosen:
                    print("Nothing to download.")
                    return
                print(f"Downloading {len(chosen)} file(s)...")
                c = self.files.download(chosen)
                with self.x.conn:
                    self.files.regroup()
                print(f"Done: {c['new']} new, {c['upd']} updated, {c['same']} identical to existing, {c['failed']} failed.")
            print("Re-checking Moodle to update the record...")
            self.refresh()
        return self.start("download", work)

    def place(self, keys, dir_rel, download=True):
        """Choose the folder for Moodle files that are not on disk (new, ignored or deleted here) and, if asked, download them there."""
        keys = [k for k in keys if self.x.files.get(k)]
        dir_rel = self.layout._norm(dir_rel)
        for part in (dir_rel.split("/") if dir_rel else []):
            from ..services.layout import clean_name
            clean_name(part)
        if not keys:
            raise ValueError("no Moodle files to place")

        def work():
            with self.lock:
                need_check = False
                with self.x.conn:
                    for k in keys:
                        r = self.x.files.get(k)
                        if not r:
                            continue
                        if r.status == "downloaded" and r.rel_path and (self.x.root / r.rel_path).exists():
                            continue                                   # already here: moving it is a different action
                        self.x.targets.set(k, dir_rel)
                        if r.status == "ignored":
                            self.files.unignore([k])
                            need_check = True
                        elif r.status == "downloaded":                 # deleted here: ask for it again
                            self.files.redownload(k)
                            need_check = True
                        else:
                            self.layout.retarget([k], dir_rel)
                if not download:
                    print(f"Saved the destination for {len(keys)} file(s).")
                    return
                if need_check:
                    print("Asking Moodle for those files...")
                    self.refresh(what={"files"})
                chosen = [self.x.pending[k] for k in keys if k in self.x.pending and self.x.pending[k].kind == "NEW"]
                if not chosen:
                    print("Nothing to download (Moodle did not list them, or they are already here).")
                    return
                print(f"Downloading {len(chosen)} file(s) into {dir_rel or 'the study folder'}...")
                c = self.files.download(chosen)
                print(f"Done: {c['new']} new, {c['same']} identical to existing, {c['failed']} failed.")
        return self.start("download", work)

    def read(self, rel):
        folder = self.safe_path(rel)
        if not folder.is_dir():
            raise ValueError("not a folder")

        def work():
            try:
                read_folder(folder, print, root=self.x.root)
            except RuntimeError as e:
                print(f"! {e}")
        return self.start("read", work)

    # ---------- AI page (the only place LLM calls start; see Handler.LLM_PATHS)
    @property
    def ai(self):
        from ..services.ai import AI
        if getattr(self, "_ai", None) is None:
            self._ai = AI(self.x, self.lock)
        return self._ai

    # ---------- sync actions that finish immediately ----------
    def safe_path(self, rel):
        root = self.x.root.resolve()
        p = (root / rel).resolve()
        if root not in p.parents and p != root:
            raise ValueError("path is outside the study folder")
        return p

    OPEN_OK = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".odp", ".ods",
               ".txt", ".md", ".csv", ".rtf", ".pages", ".key", ".numbers", ".epub", ".mp3", ".mp4", ".mov", ".wav", ".m4a"}

    def open_external(self, rel):
        p = self.editor.resolve(rel)
        if p.suffix.lower() not in self.OPEN_OK:             # never hand programs or scripts to the system
            raise ValueError(f"{p.suffix or 'this type'} files are not opened from here")
        self.bridge.open(p)

    PREVIEW_MAX = 60_000_000

    @property
    def preview_dir(self):
        return self.x.settings.home / "tmp" / "preview"

    def preview(self, key):
        """Fetch a Moodle file only to look at it. The temporary copy is deleted before this returns; nothing is saved."""
        rec, url = self.x.files.get(key), self.x.urls.get(key)
        if not rec:
            raise ValueError("unknown file")
        suffix = Path(rec.filename).suffix.lower()
        if suffix not in RAW_TYPES and suffix not in TEXT_SUFFIXES:
            raise ValueError(f"{suffix or 'this type of'} files have no preview; download it to open it")
        if not url:
            raise ValueError("Moodle has not listed this file in this session yet. Use Check Moodle, then try again")
        if (rec.size or 0) > self.PREVIEW_MAX:
            raise ValueError("this file is too large to preview")
        d = self.preview_dir
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{secrets.token_hex(8)}{suffix}"
        try:
            self.x.moodle.download(url, tmp)
            data = tmp.read_bytes()
        finally:
            tmp.unlink(missing_ok=True)
        if suffix in TEXT_SUFFIXES:
            if len(data) > MAX_BYTES:
                raise ValueError("this text file is too large to preview")
            return data, "text/plain; charset=utf-8"
        return data, RAW_TYPES[suffix]

    def clean_previews(self):
        shutil.rmtree(self.preview_dir, ignore_errors=True)

    def pdf_text(self, rels):
        rels = [r for r in rels if r]
        if not rels:
            raise ValueError("no PDF selected")
        files = []
        for r in rels[:40]:
            p = self.editor.resolve(r)
            if p.suffix.lower() != ".pdf":
                raise ValueError(f"{p.name} is not a PDF")
            files.append((p.name, p))
        with self.lock:
            return pdf_text(files, self.x.settings.home / "tmp")

    def delete(self, rel):
        p = self.safe_path(rel)
        if p.is_symlink() or not p.is_file():
            raise ValueError("only regular files can be deleted")
        return self.bridge.trash(p)

    def tree(self):
        from importlib.util import find_spec
        with self.lock:
            return build_tree(self.x, self.scanned_at, self.scan_error,
                              read_ready=find_spec("pymupdf") is not None or find_spec("fitz") is not None)

    def deadlines(self):
        now = self.x.ts()
        with self.lock:
            rows = self.x.deadlines.upcoming(now - 3 * 86400, include_hidden=True)
        return {"now": now, "items": [{
            "id": r["id"], "course": r["course_short"], "title": r["title"], "due_ts": r["due_ts"], "kind": r["kind"],
            "status": r["status"], "grade": r["grade_text"], "url": r["url"], "done": bool(r["done"]), "hidden": bool(r["hidden"]),
            "overdue": r["status"] == "open" and r["due_ts"] < now, "cutoff_ts": r["cutoff_ts"], "gone": bool(r["gone"]),
            "note": r["note"]} for r in rows]}

    def set_deadline(self, id_, **flags):
        with self.lock, self.x.conn:
            if not self.x.deadlines.get(id_):
                raise ValueError("unknown deadline")
            self.x.deadlines.set_flag(id_, **{k: int(v) if k != "note" else str(v)[:500] for k, v in flags.items()})

    def announcements(self):
        with self.lock:
            rows = self.x.announcements.recent(60)
        return {"items": [{"id": r["id"], "course": r["course_short"], "subject": r["subject"], "author": r["author"],
                           "created": r["created"], "read": bool(r["read"]), "text": html_to_text(r["message"])[:4000]} for r in rows]}

    def history(self, limit=150):
        with self.lock:
            rows = self.x.history.recent_changes(limit)
            courses = {c["id"]: c["shortname"] for c in self.x.courses.all()}
        out = []
        for r in rows:
            try:
                detail = json.loads(r["detail"]) if r["detail"] else {}
            except ValueError:
                detail = {}
            out.append({"ts": r["ts"], "kind": r["kind"], "course": courses.get(r["course_id"], ""), "ref": r["ref"], "detail": detail})
        return {"items": out}

    @property
    def editor(self):
        if self._editor is None:
            self._editor = Editor(self.x.root, self.x.settings.home)
        return self._editor

    def badges(self):
        """Counts for the menu: things that want your attention."""
        with self.lock:
            now = self.x.ts()
            c = self.x.conn
            return {"new_files": c.execute("SELECT COUNT(*) FROM files WHERE status='pending'").fetchone()[0],
                    "updates": c.execute("SELECT COUNT(*) FROM files WHERE status='downloaded' AND upd_modified IS NOT NULL").fetchone()[0],
                    "unread": c.execute("SELECT COUNT(*) FROM announcements WHERE read=0").fetchone()[0],
                    "overdue": c.execute("SELECT COUNT(*) FROM deadlines WHERE gone=0 AND hidden=0 AND done=0 AND status='open' AND due_ts<? "
                                         "AND due_ts>=?", (now, now - 14 * 86400)).fetchone()[0],
                    "due_soon": c.execute("SELECT COUNT(*) FROM deadlines WHERE gone=0 AND hidden=0 AND done=0 AND status='open' "
                                          "AND due_ts>=? AND due_ts<?", (now, now + 3 * 86400)).fetchone()[0],
                    "scanned_at": self.scanned_at, "scan_error": self.scan_error, "now": now,
                    "running": bool(self.job and self.job.running),
                    "syncing": bool(self.job and self.job.running and self.job.name == "refresh")}

    def digest(self):
        with self.lock:
            d = digest_service.build(self.x)
            d["text"] = digest_service.render_text(d)
            return d

    def tracker(self):
        with self.lock:
            return tracker_service.build(self.x)

    def calendar(self, start, end, include_hidden):
        with self.lock:
            return calendar_service.build(self.x, start, end, include_hidden)

    def ics_key(self):
        with self.lock, self.x.conn:
            key = self.x.settings_repo.get("ics_key")
            if not key:
                key = secrets.token_urlsafe(18)
                self.x.settings_repo.set("ics_key", key)
            return key

    def ics(self):
        with self.lock:
            return ics_service.build_ics(self.x.deadlines.all_for_ics(), events=self.x.events.all()).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    app = None
    port = 0
    # every endpoint that can send text to the LLM; the page that owns them sends X-Page: ai, no other page does
    LLM_PATHS = {"/api/ai/check", "/api/ai/plan", "/api/ai/notes", "/api/ai/ask"}

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _authed(self):
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Token", ""), self.app.token)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._host_ok():
            return self._send(403, b"bad host", "text/plain")
        a = self.app
        if u.path == "/":
            if q.get("t", [""])[0] != a.token:
                return self._send(403, b"Open the link printed in the terminal (it contains a one-time token).", "text/plain")
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8", extra={"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"})
        m = re.fullmatch(r"/static/([a-z0-9_-]+\.(js|css))", u.path)
        if m and (STATIC / m.group(1)).is_file():                       # no secrets in these; whitelisted names only
            return self._send(200, (STATIC / m.group(1)).read_bytes(),
                              "text/javascript; charset=utf-8" if m.group(2) == "js" else "text/css; charset=utf-8")
        m = re.fullmatch(r"/static/katex/(katex\.min\.(js|css)|fonts/KaTeX_[A-Za-z0-9_-]+\.woff2)", u.path)
        if m and (STATIC / "katex" / m.group(1)).is_file():             # vendored KaTeX (MIT): maths in the Markdown preview
            ext = m.group(1).rsplit(".", 1)[1]
            ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "woff2": "font/woff2"}[ext]
            return self._send(200, (STATIC / "katex" / m.group(1)).read_bytes(), ctype)
        if u.path == "/calendar.ics":
            if not secrets.compare_digest(q.get("k", [""])[0], a.ics_key()):
                return self._send(403, b"bad key", "text/plain")
            return self._send(200, a.ics(), "text/calendar; charset=utf-8")
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        try:
            if u.path == "/api/tree":
                return self._send(200, a.tree())
            if u.path == "/api/preview":
                data, ctype = a.preview(q.get("key", [""])[0])
                return self._send(200, data, ctype, extra={"Content-Security-Policy": "sandbox", "Content-Disposition": "inline"})
            if u.path == "/api/alloc":
                with a.lock:
                    return self._send(200, a.layout.allocation())
            if u.path == "/api/job":
                j = a.job
                since = int(q.get("since", ["0"])[0])
                if not j:
                    return self._send(200, {"id": None, "running": False, "lines": [], "next": 0})
                return self._send(200, {"id": j.id, "name": j.name, "running": j.running, "error": j.error,
                                        "lines": j.log[since:], "next": len(j.log)})
            if u.path == "/api/deadlines":
                return self._send(200, a.deadlines())
            if u.path == "/api/announcements":
                return self._send(200, a.announcements())
            if u.path == "/api/history":
                return self._send(200, a.history())
            if u.path == "/api/badges":
                return self._send(200, a.badges())
            if u.path == "/api/digest":
                return self._send(200, a.digest())
            if u.path == "/api/tracker":
                return self._send(200, a.tracker())
            if u.path == "/api/setup":
                return self._send(200, a.setup.status())
            if u.path == "/api/settings":
                return self._send(200, config.editable(a.x.settings))
            if u.path == "/api/calendar":
                return self._send(200, a.calendar(int(q["from"][0]), int(q["to"][0]), q.get("hidden", ["0"])[0] == "1"))
            if u.path == "/api/file":
                with a.lock:
                    return self._send(200, a.editor.read(q.get("rel", [""])[0]))
            if u.path == "/api/raw":
                with a.lock:
                    data, ctype = a.editor.raw(q.get("rel", [""])[0])
                return self._send(200, data, ctype, extra={"Content-Security-Policy": "sandbox", "Content-Disposition": "inline"})
            if u.path == "/api/ics-link":
                return self._send(200, {"path": f"/calendar.ics?k={a.ics_key()}"})
        except (EditorError, ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(min(n, 1_000_000)) or b"{}")
            a, path, svc = self.app, urlparse(self.path).path, self.app.files
            if path in self.LLM_PATHS and self.headers.get("X-Page") != "ai":
                return self._send(403, {"error": "LLM calls can only be made from the AI page"})
            if path == "/api/refresh":
                j = a.start("refresh", a.refresh)
            elif path == "/api/download":
                j = a.download(list(body.get("keys", [])))
            elif path == "/api/ignore":
                with a.lock:
                    return self._send(200, {"ignored": svc.ignore(list(body.get("keys", [])))})
            elif path == "/api/close":
                with a.lock:
                    return self._send(200, {"closed": svc.close_missing(list(body.get("keys", [])))})
            elif path == "/api/unignore":
                with a.lock:
                    svc.unignore(list(body.get("keys", [])))
                j = a.start("refresh", a.refresh)
            elif path == "/api/redownload":
                with a.lock:
                    svc.redownload(body["key"])
                j = a.start("refresh", a.refresh)
            elif path == "/api/ai/state":
                return self._send(200, a.ai.state())
            elif path == "/api/ai/files":
                return self._send(200, {"files": a.ai.files(body.get("module", ""))})
            elif path == "/api/setup/places":
                return self._send(200, a.setup.save_places(body.get("site_url"), body.get("dest_root")))
            elif path == "/api/setup/begin":
                return self._send(200, a.setup.begin())
            elif path == "/api/setup/token":
                r = a.setup.finish(body.get("pasted", ""))
                a.start("refresh", a.refresh)
                return self._send(200, r)
            elif path == "/api/setup/key":
                return self._send(200, a.setup.save_api_key(body.get("key", "")) if body.get("key") else a.setup.remove_api_key())
            elif path == "/api/settings":
                with a.lock:
                    restart = config.update(a.x.settings, body.get("changes") or {})
                return self._send(200, {**config.editable(a.x.settings), "restart": restart})
            elif path == "/api/ai/folders":
                return self._send(200, {"folders": a.ai.folders(body.get("module", ""))})
            elif path == "/api/ai/generated":
                return self._send(200, a.ai.generated())
            elif path == "/api/ai/read":
                j = a.start("read", a.ai.read_job(body.get("module", ""), body.get("sub", "")))
            elif path == "/api/ai/deadline/remove":
                a.ai.remove_deadline(int(body.get("id", 0)))
                return self._send(200, {"deadlines": a.ai.saved_deadlines()})
            elif path == "/api/ai/prompt":
                return self._send(200, {"prompts": a.ai.save_prompt(body.get("name", ""), body.get("text"))})
            elif path == "/api/ai/check":
                return self._send(200, a.ai.check())
            elif path == "/api/ai/plan":
                return self._send(200, a.ai.plan(body.get("module", ""), body.get("what"), body.get("week"), list(body.get("rels", [])), bool(body.get("force"))))
            elif path == "/api/ai/notes":
                j = a.start("notes", a.ai.notes_job(body.get("module", ""), body.get("what"), body.get("week"), list(body.get("rels", [])), bool(body.get("force")), body.get("select")))
            elif path == "/api/ai/ask":
                return self._send(200, a.ai.ask(body.get("question", ""), list(body.get("rels", [])), body.get("system") or None))
            elif path == "/api/read":
                j = a.read(body["rel"])
            elif path == "/api/place":
                j = a.place(list(body.get("keys", [])), body.get("dir", ""), bool(body.get("download", True)))
            elif path == "/api/retarget":
                with a.lock:
                    return self._send(200, {"changed": a.layout.retarget(list(body.get("keys", [])), body.get("dir", ""))})
            elif path == "/api/fs/mkdir":
                with a.lock:
                    return self._send(200, a.layout.mkdir(body["rel"]))
            elif path == "/api/fs/move":
                with a.lock:
                    return self._send(200, a.layout.move(list(body.get("rels", [])), body.get("dir", "")))
            elif path == "/api/fs/rename":
                with a.lock:
                    return self._send(200, a.layout.rename(body["rel"], body["name"]))
            elif path == "/api/fs/rmdir":
                with a.lock:
                    return self._send(200, a.layout.rmdir(body["rel"]))
            elif path == "/api/fs/undo":
                with a.lock:
                    return self._send(200, a.layout.undo(list(body.get("moved", [])), list(body.get("created", []))))
            elif path == "/api/fs/subdivide":
                with a.lock:
                    return self._send(200, a.layout.subdivide_plan(body["rel"]) if body.get("dry") else a.layout.subdivide(body["rel"]))
            elif path == "/api/open":
                with a.lock:
                    a.open_external(body["rel"])
                return self._send(200, {"ok": True})
            elif path == "/api/pdf/text":
                return self._send(200, a.pdf_text(list(body.get("rels", []))))
            elif path == "/api/delete":
                return self._send(200, {"moved_to": a.delete(body["rel"])})
            elif path == "/api/deadline":
                flags = {k: body[k] for k in ("hidden", "done", "note") if k in body}
                a.set_deadline(body["id"], **flags)
                return self._send(200, {"ok": True})
            elif path == "/api/announcements/read":
                with a.lock, a.x.conn:
                    a.x.announcements.mark_read([int(i) for i in body.get("ids", [])], bool(body.get("read", True)))
                return self._send(200, {"ok": True})
            elif path == "/api/reviewed":
                with a.lock:
                    keys = list(body.get("keys", []))
                    if "week" in body:
                        keys += tracker_service.week_keys(a.x, int(body["week"]["course"]), int(body["week"]["section"]))
                    return self._send(200, {"changed": tracker_service.set_reviewed(a.x, keys, bool(body.get("on", True)))})
            elif path == "/api/events":
                with a.lock:
                    return self._send(200, {"id": calendar_service.add_event(
                        a.x, body.get("title"), body.get("start"), body.get("end"), body.get("all_day"), body.get("course_id"), body.get("note", ""))})
            elif path == "/api/events/update":
                with a.lock:
                    fields = {k: body[k] for k in ("title", "start", "end", "all_day", "course_id", "note") if k in body}
                    calendar_service.update_event(a.x, int(body["id"]), **fields)
                return self._send(200, {"ok": True})
            elif path == "/api/events/delete":
                with a.lock:
                    calendar_service.delete_event(a.x, int(body["id"]))
                return self._send(200, {"ok": True})
            elif path == "/api/digest/seen":
                with a.lock:
                    digest_service.mark_seen(a.x)
                return self._send(200, {"ok": True})
            elif path == "/api/file/save":
                with a.lock:
                    return self._send(200, a.editor.save(body["rel"], body.get("text"), body.get("sha", ""), body.get("eol", "lf"),
                                                         force=bool(body.get("force"))))
            elif path == "/api/file/new":
                with a.lock:
                    return self._send(200, a.editor.create(body["rel"], body.get("text", "")))
            elif path == "/api/quit":
                if a.job and a.job.running:
                    raise RuntimeError("an action is still running; wait for it to finish")
                a.clean_previews()
                self._send(200, {"stopped": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"job": j.id})
        except Conflict as e:
            return self._send(409, {"error": str(e), "conflict": True})
        except (ValueError, KeyError, RuntimeError) as e:          # EditorError is a ValueError
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def make_server(ctx, port=0, bridge=None, check_first=True):
    app = App(ctx, bridge)
    app.clean_previews()
    Handler.app = app
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    Handler.port = srv.server_address[1]
    return app, srv


def _web_token(settings):
    """The page's access token, kept in a private file so the address stays the same between runs (bookmark/pin it)."""
    from ..config import write_private
    p = settings.web_token_path
    try:
        tok = p.read_text().strip()
        if len(tok) >= 20:
            return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    write_private(p, tok + "\n")
    return tok


def _already_running(port, token):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/?t={token}", timeout=2) as r:
            return r.status == 200 and b"<title>" in r.read(4000)
    except Exception:
        return False


def _open(url, how):
    """how: 'tab' (new tab), 'window' (new window) or 'none' (print only)."""
    if how == "none":
        return
    try:
        webbrowser.open(url, new=1 if how == "window" else 2)
    except Exception:
        pass


def run(ctx, port=0, open_browser=True, how=None):
    """Serves on settings.port (default 8765) with a saved token, so http://127.0.0.1:8765/?t=... is a stable link.
    If that port is taken by this app already, just open it; if by something else, fall back to a free port."""
    token = _web_token(ctx.settings)
    how = "none" if not open_browser else (how or ctx.settings.open_on_launch)
    port = port or ctx.settings.port
    try:
        app, srv = make_server(ctx, port)
    except OSError:
        if _already_running(port, token):
            url = f"http://127.0.0.1:{port}/?t={token}"
            print(f"Already running: {url}")
            _open(url, how)
            return
        print(f"Port {port} is in use by another program; using a free one (the address will differ this time).")
        app, srv = make_server(ctx, 0)
    app.token = token
    app.start("refresh", app.refresh)            # the page opens at once and shows the check as it runs
    url = f"http://127.0.0.1:{Handler.port}/?t={app.token}"
    print(f"\nGUI running at:\n  {url}\n(Ctrl+C to stop; this address stays the same, so you can pin it)")
    _open(url, how)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        app.clean_previews()
        srv.server_close()
