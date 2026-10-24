"""A seeded app + mock Moodle for the browser tests (real clock, so dates in the UI are today-relative)."""
import threading
import time

from pathlib import Path
from helpers import PDF, make_env
from moodle_app.host.bridge import HostBridge
from moodle_app.services.sync import run_sync
from moodle_app.web import server

def seed(tmp, now=None):
    now = now or int(time.time())
    DAY = 86400
    env = make_env(Path(tmp), clock=lambda: int(time.time()))
    m = env.mock
    m.courses[0].update(startdate=now - 10 * DAY, enddate=now + 80 * DAY)
    for sec, n in ((1, 3), (2, 4), (3, 2), (4, 1)):
        for i in range(n):
            kind = "Workshop" if i % 2 else "Lecture"
            m.add_file(7, sec, sec * 100 + i, f"{kind} {sec}.{i}.pdf", PDF + bytes([sec, i]), activity=f"{kind} {sec}.{i}")
    m.assignments[7] = [{"id": 11, "cmid": 301, "course": 7, "name": "Coursework 1", "duedate": now + 2 * DAY + 3600, "cutoffdate": now + 5 * DAY},
                        {"id": 12, "cmid": 302, "course": 7, "name": "Lab report", "duedate": now - 1 * DAY},
                        {"id": 13, "cmid": 303, "course": 7, "name": "Project plan", "duedate": now + 12 * DAY}]
    m.quizzes = [{"course": 7, "coursemodule": 401, "name": "Quiz A", "timeopen": now, "timeclose": now + 6 * DAY}]
    m.events = [{"id": 1000 + i, "name": f"Seminar {i}", "modulename": "page", "timesort": now + (i + 1) * DAY + 7200, "course": {"id": 7}, "action": {"url": f"http://x/mod/page/view.php?id={900+i}"}} for i in range(6)]
    m.forums = [{"id": 61, "course": 7, "type": "news"}]
    m.discussions = {61: [{"discussion": 5000 + i, "subject": f"Notice {i}", "message": f"<p>Hello <b>class</b> {i}</p><script>alert(1)</script>", "created": now - i * 3600, "modified": now - i * 3600} for i in range(3)]}
    (env.folder / "Notes").mkdir()
    (env.folder / "Notes" / "week1.md").write_text("# Week 1\n\n- [ ] read **slides**\n- item two\n\n```python\nprint('hi')\n```\n")
    (env.folder / "Notes" / "todo.txt").write_text("one\ntwo\n")
    (env.folder / "Notes" / "sheet.pdf").write_bytes(PDF)
    app, srv = server.make_server(env.ctx, bridge=HostBridge(Path(tmp) / "trash"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    app.refresh()
    env.app, env.srv, env.port = app, srv, server.Handler.port
    env.url = f"http://127.0.0.1:{env.port}/?t={app.token}"
    env.now = now
    return env
