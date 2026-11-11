"""Local web server (standard library only): the HTTP layer between the page (`static/`) and the backend (`app.py`).

It routes `/api/*` to `App`, serves the static front end, and enforces the access rules; it holds no business logic.
Safety (unchanged from the first version): listens on localhost only; every API call needs a random per-run token
(`X-Token` header; `?t=` for the page); the Host header is checked; every path is resolved and must lie inside dest_root;
delete moves ONE regular file to the trash and never recurses. The calendar feed has its own secret key in the URL.
"""
from __future__ import annotations

import json
import re
import secrets
import signal
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config
from ..notes import extract
from ..services import calendar as calendar_service
from ..services import digest as digest_service
from ..services import tracker as tracker_service
from ..services.editor import Conflict, EditorError
from .app import App  # noqa: F401  (the backend; re-exported for callers and tests)

STATIC = Path(__file__).parent / "static"
# scripts and styles only from this server; no inline script; previews are blob: URLs made by the page itself
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob: data:; font-src 'self'; "
       "frame-src blob:; object-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    app = None
    port = 0
    # every endpoint that can send text to the LLM. Only two pages may call them, and each says who it is: the AI page
    # (X-Page: ai) and the Study page's assistant (X-Page: study). No other page file contains these paths.
    LLM_PATHS = {"/api/ai/check", "/api/ai/plan", "/api/ai/notes", "/api/ai/ask", "/api/ai/chat"}
    LLM_PAGES = {"ai", "study"}
    LLM_EXTRA = {"/api/ai/check": {"settings"}}          # "Test connection" under Settings > Connections: one tiny call, no course text

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
            if u.path == "/api/courses":
                return self._send(200, a.courses())
            if u.path == "/api/connections":
                return self._send(200, a.connections())
            if u.path == "/api/alloc":
                with a.lock:
                    return self._send(200, a.layout.allocation())
            if u.path == "/api/jobs":                     # the activity list: what is waiting, running and recently finished
                return self._send(200, {"jobs": a.jobs.list(), "busy": a.jobs.busy()})
            if u.path == "/api/job":                      # one job's log from line `since` on (no id: the most recent job)
                jid = q.get("id", [""])[0]
                j = a.jobs.get(jid) if jid else a.job
                since = max(0, int(q.get("since", ["0"])[0]))
                if not j:
                    return self._send(200, {"id": None, "running": False, "lines": [], "next": 0})
                return self._send(200, {**j.info(), "running": j.running, "lines": j.log[since:], "next": len(j.log)})
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
            if path in self.LLM_PATHS and self.headers.get("X-Page") not in (self.LLM_PAGES | self.LLM_EXTRA.get(path, set())):
                return self._send(403, {"error": "LLM calls can only be made from the AI page or the Study assistant"})
            if path == "/api/refresh":
                j = a.start("refresh", a.refresh)
            elif path == "/api/courses/add":
                r = a.add_course(body.get("ref", ""), body.get("folder", ""))
                a.start("refresh", a.refresh)
                return self._send(200, r)
            elif path == "/api/courses/add-all":
                r = a.add_all_courses()
                if r["added"]:
                    a.start("refresh", a.refresh)
                return self._send(200, r)
            elif path == "/api/courses/remove":
                return self._send(200, a.remove_course(body.get("id", 0)))
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
            elif path == "/api/ai/generated":
                return self._send(200, a.ai.generated())
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
            elif path == "/api/ai/chat":
                return self._send(200, a.ai.chat(body.get("messages"), list(body.get("rels") or []), list(body.get("snippets") or [])))
            elif path == "/api/ai/ask":
                return self._send(200, a.ai.ask(body.get("question", ""), list(body.get("rels", [])), body.get("system") or None))
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
            elif path == "/api/job/cancel":
                return self._send(200, {"cancelled": a.jobs.cancel(str(body.get("id", "")))})
            elif path == "/api/quit":                     # quitting interrupts whatever runs (at its next safe point), then stops
                clean = a.stop()
                self._send(200, {"stopped": True, "clean": clean})
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
    extract.prune_cache()
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


def _port_answers(port):
    """True when something already accepts connections on 127.0.0.1:port (or the IPv6 loopback)."""
    import socket
    for fam, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as c:
                c.settimeout(0.5)
                if c.connect_ex((addr, port)) == 0:
                    return True
        except OSError:
            continue
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
        if _port_answers(port):                  # never share a port: a second listener can bind next to the first on some systems
            raise OSError(f"port {port} is in use")
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
    url = f"http://127.0.0.1:{Handler.port}/?t={app.token}"
    # Say where the page is before the first check starts (a job's output goes to its own log, per thread; see jobs.py).
    print(f"\nGUI running at:\n  {url}\n(Ctrl+C to stop; this address stays the same, so you can pin it)", flush=True)
    app.start("refresh", app.refresh)            # the page opens at once and shows the check as it runs
    _open(url, how)

    def on_term(*_):                             # `kill`, a closing terminal tab, a service manager: leave the same way as Ctrl+C
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError, AttributeError):    # not the main thread, or a platform without it
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        clean = app.stop()                       # interrupt whatever runs at its next safe point, then tidy up
        srv.server_close()
        print("\nStopped." if clean else "\nStopped (an action was still running and was left unfinished; nothing is half-written).")
