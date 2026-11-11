"""Background work: every long action (checking Moodle, downloading, writing notes) is a Job, run by Jobs.

What this gives the page:
  * starting something never fails because something else is running: it is queued and the page stays usable;
  * an order (the operation hierarchy): quitting interrupts everything; what you start yourself (downloads, notes) runs
    before background checks that are still waiting; a second "check Moodle" joins the one already waiting or running;
  * every job can be cancelled: a waiting one at once, a running one at its next safe point (between files, courses or
    notes), so nothing is left half-written;
  * a log and a progress count per job, kept for the last few jobs.

Jobs run one at a time because they share one database connection; the slow part of a download (the network) runs in
parallel inside its job (see services/files.py). Output printed while a job runs goes to that job's log, per thread, so
a job never swallows what another thread prints (the address at start-up, for one)."""
from __future__ import annotations

import secrets
import sys
import threading
import time

from ..services.context import Cancelled

USER, BACKGROUND = 0, 1                  # lower runs first
KEEP = 12                                # finished jobs remembered for the activity list
LOG_CAP = 4000                           # lines kept per job

_local = threading.local()


def current():
    """The job this thread is working for, or None."""
    return getattr(_local, "job", None)


def check():
    """Raise Cancelled when the current job was asked to stop. Safe to call anywhere (no job: nothing happens)."""
    j = current()
    if j is not None:
        j.check()


def progress(done, total):
    j = current()
    if j is not None:
        j.progress(done, total)


def bound(fn):
    """`fn` wrapped so that it runs for the calling thread's job when another thread (a pool worker) executes it."""
    job = current()

    def run(*a, **kw):
        _local.job = job
        try:
            return fn(*a, **kw)
        finally:
            _local.job = None
    return run


class _Router:
    """Standard output while jobs run: a thread that works for a job writes to its log, every other thread to the real
    stream. Installed when the first job starts and removed when the last one ends."""

    def __init__(self):
        self.real, self.n, self.lock = None, 0, threading.Lock()

    def install(self):
        with self.lock:
            if self.n == 0:
                self.real, sys.stdout = sys.stdout, self
            self.n += 1

    def uninstall(self):
        with self.lock:
            self.n -= 1
            if self.n == 0:
                if sys.stdout is self:
                    sys.stdout = self.real
                self.real = None

    def write(self, text):
        j = current()
        if j is not None:
            return j.write(text)
        return (self.real or sys.__stdout__).write(text)

    def flush(self):
        r = self.real or sys.__stdout__
        if r is not None:
            r.flush()

    def __getattr__(self, name):         # isatty, encoding, ...
        return getattr(self.real or sys.__stdout__, name)


_router = _Router()


class Job:
    def __init__(self, name, fn, title="", priority=USER):
        self.id, self.name, self.title, self.priority, self.fn = secrets.token_hex(4), name, title or name, priority, fn
        self.log, self.state, self.error = [], "queued", None
        self.created, self.started, self.finished = time.time(), None, None
        self.done, self.total = 0, 0
        self._cancel, self._over = threading.Event(), threading.Event()

    @property
    def running(self):                   # "not finished yet": waiting counts, the page shows it as work in hand
        return self.state in ("queued", "running")

    def write(self, text):
        for line in str(text).splitlines():
            if line.strip() and len(self.log) < LOG_CAP:
                self.log.append(line.rstrip())
        return len(text)

    def flush(self):
        pass

    def check(self):
        if self._cancel.is_set():
            raise Cancelled()

    def progress(self, done, total):
        self.done, self.total = int(done), int(total)

    def wait(self, timeout=None):
        return self._over.wait(timeout)

    def info(self):
        return {"id": self.id, "n": getattr(self, "seq", 0), "name": self.name, "title": self.title, "state": self.state, "error": self.error,
                "done": self.done, "total": self.total, "created": self.created, "started": self.started,
                "finished": self.finished, "lines": len(self.log), "last": self.log[-1] if self.log else "",
                "cancelling": self._cancel.is_set() and self.state == "running"}


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.all, self.queue, self.active, self.closing, self._seq = [], [], None, False, 0

    # ---------- starting
    def submit(self, name, fn, title="", priority=USER, coalesce=False):
        """Queue `fn`. With `coalesce`, a job of the same name that is waiting or running is returned instead of a second one."""
        with self.lock:
            if self.closing:
                raise RuntimeError("the app is stopping")
            if coalesce:
                same = next((j for j in self.all if j.name == name and j.running and not j._cancel.is_set()), None)
                if same is not None:
                    return same
            job = Job(name, fn, title, priority)
            self._seq += 1
            job.seq = self._seq
            self.all.append(job)
            over = [j for j in self.all if not j.running]
            for old in over[:-KEEP]:
                self.all.remove(old)
            self.queue.append(job)
            self.queue.sort(key=lambda j: (j.priority, j.seq))
            self._pump()
        return job

    def _pump(self):                     # caller holds the lock
        if self.active is None and self.queue and not self.closing:
            job = self.active = self.queue.pop(0)
            job.state, job.started = "running", time.time()
            threading.Thread(target=self._run, args=(job,), daemon=True, name=f"job-{job.name}").start()

    def _run(self, job):
        _local.job = job
        _router.install()
        try:
            job.check()
            job.fn()
            job.state = "done"
        except Cancelled:
            job.state = "cancelled"
            job.log.append("Cancelled.")
        except Exception as e:           # a job never takes the app down
            job.state, job.error = "failed", str(e) or type(e).__name__
            job.log.append(f"! {job.error}")
        finally:
            job.finished = time.time()
            _local.job = None
            _router.uninstall()
            with self.lock:
                self.active = None
                self._pump()
            job._over.set()

    # ---------- asking
    def get(self, job_id):
        with self.lock:
            return next((j for j in self.all if j.id == job_id), None)

    def latest(self):
        with self.lock:
            return self.all[-1] if self.all else None

    def list(self):
        with self.lock:
            return [j.info() for j in self.all]

    def busy(self, name=None):
        with self.lock:
            return any(j.running and (name is None or j.name == name) for j in self.all)

    # ---------- stopping
    def cancel(self, job_id):
        """A waiting job is dropped at once; a running one stops at its next safe point. Returns False when there is nothing to stop."""
        with self.lock:
            job = next((j for j in self.all if j.id == job_id), None)
            if job is None or not job.running:
                return False
            job._cancel.set()
            if job in self.queue:
                self.queue.remove(job)
                job.state, job.finished = "cancelled", time.time()
                job.log.append("Cancelled before it started.")
                job._over.set()
        return True

    def shutdown(self, timeout=8.0):
        """Quitting interrupts everything: nothing new starts, waiting jobs are dropped, the running one is asked to stop and
        given `timeout` seconds to reach a safe point. Returns True when nothing is left running."""
        with self.lock:
            self.closing = True
            waiting, running = list(self.queue), self.active
            self.queue.clear()
            for j in waiting:
                j._cancel.set()
                j.state, j.finished = "cancelled", time.time()
                j._over.set()
            if running is not None:
                running._cancel.set()
        return running is None or running.wait(timeout)
