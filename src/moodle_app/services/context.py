from __future__ import annotations

import time
from pathlib import Path

from ..db import repos
from ..notes import extract


class Ctx:
    """Everything a service needs, passed explicitly so tests can swap the clock, the logger and the Moodle client."""

    def __init__(self, settings, conn, moodle=None, log=print, clock=time.time):
        self.settings, self.conn, self.moodle, self.log, self.clock = settings, conn, moodle, log, clock
        self.run_id = None
        extract.configure_cache(settings.home / "cache" / "text")      # one place for extracted PDF text
        self.urls = {}                          # key -> Moodle download link of every file in the latest sync (memory only; used for previews)
        self.pending = {}                       # key -> files.Pending from the latest sync (needs fileurl, so memory only)
        self.site_url = settings.site_url
        self.settings_repo = repos.Settings(conn)
        self.courses = repos.Courses(conn)
        self.structure = repos.Structure(conn)
        self.files = repos.Files(conn)
        self.deadlines = repos.Deadlines(conn)
        self.announcements = repos.Announcements(conn)
        self.history = repos.History(conn)
        self.notes_manifest = repos.NotesManifest(conn)
        self.reviewed = repos.Reviewed(conn)
        self.events = repos.Events(conn)
        self.targets = repos.Targets(conn)

    @property
    def root(self) -> Path:
        return self.settings.require_root()

    def ts(self) -> int:
        return int(self.clock())

    def change(self, kind, course_id=None, ref="", detail=None):
        self.history.add(self.run_id, self.ts(), kind, course_id, ref, detail)
