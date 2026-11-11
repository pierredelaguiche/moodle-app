"""The job manager and what it gives the app: queued instead of refused, parallel downloads, cancel, order, a clean quit."""
import sys
import threading
import time

import pytest

from helpers import PDF
from moodle_app.moodle.client import Moodle
from moodle_app.services.context import Cancelled
from moodle_app.web import jobs as J
from test_web import req, web  # noqa: F401  (fixture and helper)


def wait(job, t=15):
    assert job.wait(t), f"{job.name} did not finish: {job.state} {job.log[-3:]}"
    return job


# ---------------------------------------------------------------- the manager on its own
def test_jobs_queue_run_in_order_and_user_work_goes_before_waiting_background_checks():
    jobs, order, gate = J.Jobs(), [], threading.Event()
    first = jobs.submit("first", lambda: (gate.wait(5), order.append("first")))
    check = jobs.submit("refresh", lambda: order.append("refresh"), priority=J.BACKGROUND, coalesce=True)
    again = jobs.submit("refresh", lambda: order.append("refresh again"), priority=J.BACKGROUND, coalesce=True)
    mine = jobs.submit("download", lambda: order.append("download"))
    assert again is check                                           # a second check joins the one that is waiting
    assert [j["state"] for j in jobs.list()] == ["running", "queued", "queued"] and jobs.busy() and jobs.busy("refresh")
    gate.set()
    for j in (first, check, mine):
        wait(j)
    assert order == ["first", "download", "refresh"]                # what you started yourself overtakes the waiting check
    assert not jobs.busy() and [j["state"] for j in jobs.list()] == ["done"] * 3


def test_cancel_drops_a_waiting_job_and_stops_a_running_one_at_its_next_safe_point():
    jobs, gate, steps = J.Jobs(), threading.Event(), []

    def long():
        for i in range(50):
            steps.append(i)
            gate.wait(0.05)
            J.check()                                               # a safe point
    running = jobs.submit("long", long)
    waiting = jobs.submit("later", lambda: steps.append("later"))
    assert jobs.cancel(waiting.id) and waiting.state == "cancelled" and waiting.wait(0)
    time.sleep(0.12)
    assert jobs.cancel(running.id) and running.info()["cancelling"]
    wait(running)
    assert running.state == "cancelled" and running.log[-1] == "Cancelled." and 0 < len(steps) < 50 and "later" not in steps
    assert not jobs.cancel(running.id) and not jobs.cancel("nope")  # nothing left to stop
    failing = wait(jobs.submit("bad", lambda: 1 / 0))
    assert failing.state == "failed" and "division" in failing.error and wait(jobs.submit("after", lambda: None)).state == "done"


def test_output_goes_to_the_job_of_the_thread_that_prints_and_nowhere_else(capsys):
    jobs, gate, go = J.Jobs(), threading.Event(), threading.Event()

    def noisy():
        print("from the job")
        go.set()
        gate.wait(5)
    job = jobs.submit("noisy", noisy)
    assert go.wait(5)
    print("from the main thread")                                   # a job is running: this must still reach the terminal
    helper = threading.Thread(target=J.bound(lambda: print("from a helper of nobody")))
    helper.start()
    helper.join()
    gate.set()
    wait(job)
    out = capsys.readouterr().out
    assert job.log == ["from the job"] and "from the main thread" in out and "from the job" not in out
    assert sys.stdout is not J._router                              # put back when the last job ended


def test_shutdown_interrupts_everything_and_refuses_new_work():
    jobs, started = J.Jobs(), threading.Event()

    def long():
        started.set()
        while True:
            time.sleep(0.02)
            J.check()
    a, b = jobs.submit("a", long), jobs.submit("b", lambda: None)
    assert started.wait(5)
    assert jobs.shutdown(5) is True and a.state == "cancelled" and b.state == "cancelled"
    with pytest.raises(RuntimeError):
        jobs.submit("c", lambda: None)
    stuck = J.Jobs()
    block = threading.Event()
    stuck.submit("deaf", lambda: block.wait(3))                     # never reaches a safe point in time
    time.sleep(0.05)
    assert stuck.shutdown(0.2) is False
    block.set()


# ---------------------------------------------------------------- through the app
def slow_downloads(monkeypatch, seconds, log=None):
    real = Moodle.download

    def slow(self, url, dest):
        if log is not None:
            log.append(("start", time.time()))
        time.sleep(seconds)
        real(self, url, dest)
        if log is not None:
            log.append(("end", time.time()))
    monkeypatch.setattr(Moodle, "download", slow)


def test_downloads_are_fetched_in_parallel_and_the_page_answers_meanwhile(web, monkeypatch):  # noqa: F811
    for i in range(6):
        web.mock.add_file(7, 3, 300 + i, f"Workshop 3 part {i}.pdf", PDF + bytes([40 + i]))
    web.app.refresh()
    seen = []
    slow_downloads(monkeypatch, 0.5, seen)
    keys = list(web.ctx.pending)
    assert len(keys) == 8
    t0 = time.time()
    s, r = req(web, "/api/download", {"keys": keys})
    job = web.app.jobs.get(r["job"])
    slow_reads = []
    while job.state in ("queued", "running") and time.time() - t0 < 20:
        t1 = time.time()
        assert req(web, "/api/tree")[0] == 200 and req(web, "/api/alloc")[0] == 200
        slow_reads.append(time.time() - t1)
        if job.total and job.done == job.total:
            break
        time.sleep(0.05)
    assert max(slow_reads) < 0.4, max(slow_reads)                   # the database is not held while files are in transit
    wait(job)
    took = max(t for k, t in seen if k == "end") - min(t for k, t in seen if k == "start")
    assert took < 8 * 0.5 * 0.6, took                               # 8 files of 0.5 s in well under 4 s: fetched together (4 at a time)
    assert job.state == "done" and (job.done, job.total) == (8, 8)
    files = sorted(p.name for p in web.folder.rglob("*.pdf"))
    assert len(files) == 8 and not list(web.folder.rglob(".moodle_download*"))
    assert all(f.status == "downloaded" for f in web.ctx.files.all())


def test_a_second_action_is_queued_not_refused_and_jobs_are_listed(web, monkeypatch):  # noqa: F811
    slow_downloads(monkeypatch, 0.4)
    keys = list(web.ctx.pending)
    s1, a = req(web, "/api/download", {"keys": keys[:1]})
    s2, b = req(web, "/api/download", {"keys": keys[1:]})            # while the first is still running
    s3, c = req(web, "/api/refresh", {})
    s4, d = req(web, "/api/refresh", {})
    assert (s1, s2, s3, s4) == (200, 200, 200, 200) and a["job"] != b["job"] and c["job"] == d["job"]
    s, listing = req(web, "/api/jobs")
    by = {j["id"]: j for j in listing["jobs"]}
    assert listing["busy"] and by[a["job"]]["state"] == "running" and by[b["job"]]["state"] == "queued" and by[a["job"]]["title"] == "Downloading 1 file"
    assert req(web, "/api/badges")[1]["running"] is True
    for jid in (a["job"], b["job"], c["job"]):
        wait(web.app.jobs.get(jid), 30)
    assert all(f.status == "downloaded" for f in web.ctx.files.all())
    s, one = req(web, f"/api/job?id={a['job']}&since=0")
    assert one["state"] == "done" and any("saved" in ln for ln in one["lines"]) and one["running"] is False
    assert req(web, "/api/jobs", token=False)[0] == 403 and req(web, "/api/job/cancel", {"id": a["job"]}, token=False)[0] == 403


def test_cancelling_a_download_keeps_what_is_in_place_and_leaves_no_partial_file(web, monkeypatch):  # noqa: F811
    for i in range(10):
        web.mock.add_file(7, 4, 400 + i, f"Lecture 4 part {i}.pdf", PDF + bytes([60 + i]))
    web.app.refresh()
    web.ctx.settings.sync["parallel_downloads"] = 2
    slow_downloads(monkeypatch, 0.25)
    s, r = req(web, "/api/download", {"keys": list(web.ctx.pending)})
    job = web.app.jobs.get(r["job"])
    while job.done < 2:
        time.sleep(0.02)
    assert req(web, "/api/job/cancel", {"id": job.id})[1] == {"cancelled": True}
    wait(job)
    recs = web.ctx.files.all()
    got = [f for f in recs if f.status == "downloaded"]
    assert job.state == "cancelled" and 2 <= len(got) < len(recs)
    assert all((web.root / f.rel_path).is_file() for f in got)      # what the record says is here, is here
    assert sorted(p.name for p in web.folder.rglob("*.pdf")) == sorted(f.rel_path.split("/")[-1] for f in got)
    assert not list(web.folder.rglob(".moodle_download*")) and not list(web.folder.rglob("*.part"))
    s, r = req(web, "/api/download", {"keys": [f.key for f in recs if f.status != "downloaded"]})    # and the rest can still be fetched
    wait(web.app.jobs.get(r["job"]), 30)
    assert all(f.status == "downloaded" for f in web.ctx.files.all())


def test_a_cancelled_check_closes_its_run_and_does_not_count_as_a_failed_check(web, monkeypatch):  # noqa: F811
    real, started = Moodle.call, threading.Event()

    def slow(self, fn, **p):
        started.set()
        time.sleep(0.15)
        return real(self, fn, **p)
    monkeypatch.setattr(Moodle, "call", slow)
    job = web.app.start("refresh", web.app.refresh)
    assert started.wait(5) and web.app.jobs.cancel(job.id)
    wait(job)
    assert job.state == "cancelled" and web.app.scan_error is None
    last = web.conn.execute("SELECT ok, error, finished FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert last["finished"] and not last["ok"] and "cancel" in (last["error"] or "")
    monkeypatch.setattr(Moodle, "call", real)
    assert wait(web.app.start("refresh", web.app.refresh)).state == "done"


def test_quit_interrupts_a_running_job_and_stops_cleanly(web, monkeypatch):  # noqa: F811
    slow_downloads(monkeypatch, 0.3)
    s, r = req(web, "/api/download", {"keys": list(web.ctx.pending)})
    job = web.app.jobs.get(r["job"])
    time.sleep(0.1)
    s, out = req(web, "/api/quit", {})
    assert s == 200 and out == {"stopped": True, "clean": True}
    assert job.state in ("cancelled", "done") and not web.app.jobs.busy()
    assert not list(web.folder.rglob(".moodle_download*"))


def test_cancelled_is_not_swallowed_by_error_handlers():
    assert not issubclass(Cancelled, Exception)
