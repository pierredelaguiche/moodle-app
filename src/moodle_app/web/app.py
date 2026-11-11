"""The backend of the web interface: everything the page can ask for, as plain methods that return plain data.

No HTTP here. `server.py` is the only caller and the only code that knows about requests; this module, `jobs.py` and the
services below it are the back end, and `static/` is the front end, which talks to it through `/api/*` only.
Long actions are started with `App.start` and run as jobs (queued, cancellable, ordered; see jobs.py)."""
from __future__ import annotations

import json
import secrets
import shutil
import threading
import time
from pathlib import Path

from ..domain import years
from ..domain.text import html_to_text
from ..host import get_bridge
from ..services import calendar as calendar_service
from ..services import digest as digest_service
from ..services import ics as ics_service
from ..services import sync as sync_service
from ..services import tracker as tracker_service
from ..services.editor import MAX_BYTES, RAW_TYPES, TEXT_SUFFIXES, Editor
from ..services.files import FileService
from ..services.layout import Layout
from ..services.setup import Setup
from ..services.tree import build_tree
from . import jobs
from .jobs import BACKGROUND, USER, Jobs


class App:
    def __init__(self, ctx, bridge=None):
        self.x = ctx
        self.bridge = bridge or get_bridge(ctx.settings.home)
        self.lock = threading.RLock()            # guards the one database connection; held for database work only
        self.jobs = Jobs()
        self.scanned_at, self.scan_error = None, None
        self.token = secrets.token_urlsafe(24)
        self.files = FileService(ctx)
        self.layout = Layout(ctx)
        self._editor = None
        ctx.log = self._log
        ctx.check, ctx.progress = jobs.check, jobs.progress      # services stop at safe points and report how far they are

    def _log(self, msg):
        print(msg)                               # inside a job this lands in the job log; otherwise on the terminal

    # ---------- jobs (see jobs.py: queued, cancellable, ordered) ----------
    TITLES = {"refresh": "Checking Moodle", "download": "Downloading", "notes": "Writing notes"}

    def start(self, name, fn, title=None):
        """Queue a long action and return its Job at once. Checks of Moodle are background work: they wait behind what you
        started yourself, and a second one joins the first."""
        background = name == "refresh"
        return self.jobs.submit(name, fn, title or self.TITLES.get(name, name), priority=BACKGROUND if background else USER, coalesce=background)

    @property
    def job(self):                               # the most recent job (what /api/job reports when no id is given)
        return self.jobs.latest()

    def stop(self, timeout=8.0):
        """Quitting: interrupt every job at its next safe point, then tidy up. Returns False if a job did not stop in time."""
        ok = self.jobs.shutdown(timeout)
        self.clean_previews()
        if ok:
            with self.lock:
                try:
                    self.x.conn.commit()
                except Exception:
                    pass
        return ok

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
        keys = list(keys)

        def work():
            with self.lock:
                chosen = [self.x.pending[k] for k in keys if k in self.x.pending]
            if not chosen:
                print("Nothing to download.")
                return
            print(f"Downloading {len(chosen)} file(s)...")
            c = self.files.download(chosen, lock=self.lock)          # fetched in parallel; the lock is held only to put each file in place
            with self.lock, self.x.conn:
                self.files.regroup()
            print(f"Done: {c['new']} new, {c['upd']} updated, {c['same']} identical to existing, {c['failed']} failed.")
            jobs.check()
            print("Re-checking Moodle to update the record...")
            self.refresh()
        return self.start("download", work, f"Downloading {len(keys)} file{'s' if len(keys) != 1 else ''}")

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
            with self.lock:
                chosen = [self.x.pending[k] for k in keys if k in self.x.pending and self.x.pending[k].kind == "NEW"]
            if not chosen:
                print("Nothing to download (Moodle did not list them, or they are already here).")
                return
            print(f"Downloading {len(chosen)} file(s) into {dir_rel or 'the study folder'}...")
            c = self.files.download(chosen, lock=self.lock)
            print(f"Done: {c['new']} new, {c['same']} identical to existing, {c['failed']} failed.")
        return self.start("download", work, f"Downloading {len(keys)} file{'s' if len(keys) != 1 else ''}" if download else "Saving the destination")

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

    def delete(self, rel):
        p = self.safe_path(rel)
        if p.is_symlink() or not p.is_file():
            raise ValueError("only regular files can be deleted")
        return self.bridge.trash(p)

    def tree(self):
        with self.lock:
            return build_tree(self.x, self.scanned_at, self.scan_error)

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
                    "running": self.jobs.busy(), "syncing": self.jobs.busy("refresh")}

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

    # ---------- courses: which Moodle courses are followed, and adding the ones the folder names did not match
    def courses(self):
        with self.lock:
            added = sync_service.course_map(self.x)
            all_ = [dict(c) for c in self.x.courses.all()]
        root = self.x.root
        divided = bool(years.year_dirs(root))
        starts = [c["startdate"] for c in all_ if c["startdate"]]
        first = years.academic_year(min(starts)) if starts else None
        rows = []
        for c in all_:
            yr = years.year_of(c, first)
            name = self._folder_name(c)
            rows.append({"id": c["id"], "name": c["fullname"], "short": c["shortname"], "folder": c["folder_rel"] or "", "tracked": bool(c["tracked"]),
                         "added": str(c["id"]) in added, "start": c["startdate"], "year": yr,
                         "suggest": f"{years.year_folder(root, yr)}/{name}" if divided and yr else name})
        rows.sort(key=lambda r: (not r["tracked"], r["year"] or 99, r["name"].lower()))
        return {"courses": rows, "connected": self.x.moodle is not None, "years": divided}

    def _folder_name(self, info):
        """The folder name a course gets, in the style of your module folders: '<CODE> <Title>' (LZSCC.321 Languages and
        Compilation). The code comes from the short or full name, written with or without the dot like most of your folders;
        the title loses the code, separators and year tags such as (2025/26)."""
        import re
        from ..domain.years import module_dirs
        full, short = (info.get("fullname") or "").strip(), (info.get("shortname") or "").strip()
        code_re = re.compile(r"\b([A-Z]{3,6})\.?(\d{3})\b")
        m = code_re.search(short.upper()) or code_re.search(full.upper())
        title = full or short or f"Course {info.get('id')}"
        if m:
            title = re.sub(r"\b" + m.group(1) + r"\.?" + m.group(2) + r"\b", " ", title, flags=re.I)
        title = re.sub(r"[\(\[]\s*(?:\d{4}(?:\s*[/\-–]\s*\d{2,4})?|20\d\d\w*|AY[^\)\]]*|Y\d)\s*[\)\]]", " ", title)   # (2025/26), [2024-25]
        title = re.sub(r"\b20\d\d\s*[/\-–]\s*\d{2,4}\b", " ", title)
        title = re.sub(r'[\\/:*?"<>|]+', " ", title)
        title = re.sub(r"\s+", " ", title).strip(" -–_.,;")
        if not m:
            return (title or f"Course {info.get('id')}")[:120]
        names = [r.rsplit("/", 1)[-1] for r in module_dirs(self.x.root)]
        coded = [code_re.match(n.upper()) for n in names]
        dots = sum(1 for n, c in zip(names, coded) if c and "." in n[:c.end()])
        plain = sum(1 for n, c in zip(names, coded) if c and "." not in n[:c.end()])
        code = m.group(1) + ("" if plain > dots else ".") + m.group(2)
        return f"{code} {title}".strip()[:120] if title and title.upper() != code.upper() else code

    def add_all_courses(self):
        """Follow every course that is not followed yet, each in the folder `courses()` suggests (its year folder when the
        study folder is divided by years). Returns what was added and what could not be."""
        done, failed = [], []
        for c in self.courses()["courses"]:
            if c["tracked"]:
                continue
            try:
                done.append(self.add_course(str(c["id"]), c["suggest"]))
            except ValueError as e:
                failed.append({"id": c["id"], "name": c["name"], "error": str(e)})
        return {"added": done, "failed": failed}

    def add_course(self, ref, folder=""):
        """Follow a course: `ref` is its Moodle id or the address of its page (…/course/view.php?id=123). The course gets a
        folder in the study folder (made if missing) and is checked from now on, enrolled or not, as long as Moodle lets you open it."""
        import re
        from ..moodle import fetch
        from ..services.layout import LayoutError, clean_name
        m = re.search(r"[?&]id=(\d+)", str(ref)) or re.fullmatch(r"\s*(\d+)\s*", str(ref))
        if not m:
            raise ValueError("give the course's number or the address of its Moodle page (…/course/view.php?id=123)")
        cid = int(m.group(1))
        with self.lock:
            row = self.x.courses.get(cid)
        info = {"id": cid, "fullname": row["fullname"], "shortname": row["shortname"]} if row else None
        if info is None:
            if self.x.moodle is None:
                raise ValueError("sign in to Moodle first")
            try:
                got = fetch.fetch_courses_by_id(self.x.moodle, [cid])
            except Exception as e:
                raise ValueError(f"Moodle would not show course {cid}: {e}")
            if not got:
                raise ValueError(f"Moodle has no course {cid} that you can open")
            info = got[0]
        parts = [p.strip() for p in (folder or self._folder_name({**info, "id": cid})).replace("\\", "/").split("/")]
        try:
            parts = [clean_name(p) for p in parts]
        except LayoutError as e:
            raise ValueError(str(e))
        if any(p.startswith("_") for p in parts):
            raise ValueError("a module folder cannot start with an underscore")
        if len(parts) > 2 or (len(parts) == 2 and not years.YEAR_DIR.match(parts[0])):
            raise ValueError("use 'Folder' or 'Y1/Folder': a module folder sits in the study folder or inside a year folder (Y1, Year 2, …)")
        if len(parts) == 1 and years.YEAR_DIR.match(parts[0]):
            raise ValueError(f"'{parts[0]}' is a year folder: add the course's own folder after it, like {parts[0]}/Course name")
        name = "/".join(parts)
        with self.lock:
            taken = next((c for c in self.x.courses.tracked() if c["folder_rel"] == name and c["id"] != cid), None)
            if taken:
                raise ValueError(f"the folder '{name}' already belongs to {taken['fullname']}")
            added = sync_service.course_map(self.x)
            added[str(cid)] = name
            with self.x.conn:
                self.x.settings_repo.set(sync_service.COURSE_MAP, added)
                self.x.courses.upsert(info, name, True, self.x.ts())
            (self.x.root / name).mkdir(parents=True, exist_ok=True)
        return {"id": cid, "name": info.get("fullname") or "", "folder": name}

    def remove_course(self, cid):
        """Stop following a course you added. Its folder and files stay; a folder named after its module code would still match it."""
        with self.lock:
            added = sync_service.course_map(self.x)
            if added.pop(str(int(cid)), None) is None:
                raise ValueError("that course was matched by its folder name, not added here: rename the folder or list the course under Sync > Courses to ignore")
            with self.x.conn:
                self.x.settings_repo.set(sync_service.COURSE_MAP, added)
                self.x.conn.execute("UPDATE courses SET tracked=0, folder_rel=NULL WHERE id=?", (int(cid),))
        return {"removed": int(cid)}

    def connections(self):
        """What the app is plugged into, one entry per connector, for Settings > Connections: where files come from (Moodle),
        where they live (the study folder), which model writes notes, and the calendar feed other programs read."""
        from ..llm.providers import LLMError
        s, st = self.x.settings, self.setup.status()
        with self.lock:
            last = self.x.history.last_run("sync")
            site = self.x.settings_repo.get("site", {}) or {}
            tracked = len(self.x.courses.tracked())
        items = []
        if not st["site"]:
            m = ("todo", "No Moodle address yet", [])
        elif not (st["token"] or st["connected"]):
            m = ("todo", "Not signed in", [s.site_url])
        else:
            when = time.strftime("%d %b %H:%M", time.localtime(last["finished"])) if last else "never"
            m = ("warn" if self.scan_error else "ok", "Last check failed" if self.scan_error else "Signed in",
                 [s.site_url, f"{tracked} course{'s' if tracked != 1 else ''} matched to folders", f"Last check: {when}"]
                 + ([f"Moodle {site['release']}"] if site.get("release") else []) + ([str(self.scan_error)] if self.scan_error else []))
        items.append({"id": "moodle", "role": "Source", "name": "Moodle", "state": m[0], "summary": m[1], "detail": m[2]})
        items.append({"id": "folder", "role": "Storage", "name": "Study folder", "state": "ok" if st["root_ok"] else "todo",
                      "summary": "In use" if st["root_ok"] else ("Folder not found" if st["dest_root"] else "Not chosen yet"),
                      "detail": [st["dest_root"]] if st["dest_root"] else []})
        try:
            p = self.ai.provider()
            llm = ("ok", "Ready", [p.destination])
        except LLMError as e:
            llm = ("off", "Not connected (optional)", [str(e)])
        items.append({"id": "model", "role": "Notes and assistant", "name": "AI model", "state": llm[0], "summary": llm[1], "detail": llm[2],
                      "api_key": st["api_key"], "claude_login": st["claude_login"], "provider": st["provider"]})
        items.append({"id": "calendar", "role": "Calendar", "name": "Calendar feed", "state": "ok", "summary": "Available while the app runs",
                      "detail": ["Deadlines and your own events as an iCalendar feed."], "path": f"/calendar.ics?k={self.ics_key()}"})
        return {"items": items, "home": str(s.home), "config_path": str(s.config_path)}

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
