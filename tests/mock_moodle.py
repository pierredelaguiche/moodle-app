"""A small stand-in for the Moodle web service (stdlib only). Synthetic data only: no real names, dates or tokens."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "testtoken"


def _list(q, name):
    out = []
    for k, v in q.items():
        m = re.fullmatch(re.escape(name) + r"\[(\d+)\]", k)
        if m:
            out.append((int(m.group(1)), v[0]))
    return [v for _, v in sorted(out)]


class MockMoodle:
    def __init__(self):
        self.functions = None            # None = all; or a set of names the token may call
        self.courses = [{"id": 7, "fullname": "CSCI.999 Test Module", "shortname": "CSCI.999", "startdate": 1, "enddate": 2}]
        self.sections = {7: []}          # courseid -> moodle section list
        self.open_courses = {}           # id -> course dict: visible by id although you are not enrolled
        self.blobs = {}
        self.calls = []
        self.assignments = {}            # courseid -> [assignment dicts]
        self.submission = {}             # assignid -> status payload
        self.quizzes = []
        self.events = []
        self.forums = []                 # [{"id","course","type"}]
        self.discussions = {}            # forumid -> [discussion dicts]
        self.fail = set()                # function names that answer with an access exception
        self.srv = None

    # ---- content builders
    def _section(self, course, section, name=None, component=None, itemid=None):
        sec = next((s for s in self.sections[course] if s["section"] == section), None)
        if sec is None:
            sec = {"section": section, "name": name or f"Week {section}", "modules": [], "component": component, "itemid": itemid}
            self.sections[course].append(sec)
        return sec

    def add_file(self, course, section, cmid, name, data, modname="resource", modified=1_700_000_000, path="/", activity="Resource"):
        sec = self._section(course, section)
        url = f"/pluginfile/{course}/{cmid}/{name.replace(' ', '_')}"
        self.blobs[url] = data
        mod = next((m for m in sec["modules"] if m["id"] == cmid), None)
        if mod is None:
            mod = {"id": cmid, "name": activity, "modname": modname, "instance": cmid, "contents": []}
            sec["modules"].append(mod)
        mod["contents"].append({"type": "file", "filename": name, "filepath": path, "filesize": len(data),
                                "timemodified": modified, "fileurl": self.base + url})

    def add_subsection(self, course, parent_section, cmid, instance, delegated_section, name="Sub"):
        parent = self._section(course, parent_section)
        parent["modules"].append({"id": cmid, "name": name, "modname": "subsection", "instance": instance})
        self._section(course, delegated_section, name=name, component="mod_subsection", itemid=instance)

    def clear_files(self, course):
        self.sections[course] = []

    # ---- server
    def start(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                q = parse_qs(self.rfile.read(n).decode())
                fn = (q.get("wsfunction") or [""])[0]
                mock.calls.append((fn, {k: v[0] for k, v in q.items() if k not in ("wstoken", "wsfunction", "moodlewsrestformat")}))
                if (q.get("wstoken") or [""])[0] != TOKEN:
                    return self._json({"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token"})
                one = lambda k: (q.get(k) or [""])[0]
                if fn in mock.fail or (mock.functions is not None and fn != "core_webservice_get_site_info" and fn not in mock.functions):
                    return self._json({"exception": "moodle_exception", "errorcode": "accessexception", "message": fn})
                if fn == "core_webservice_get_site_info":
                    names = sorted(mock.functions) if mock.functions is not None else [
                        "core_enrol_get_users_courses", "core_course_get_contents", "mod_assign_get_assignments",
                        "mod_assign_get_submission_status", "mod_quiz_get_quizzes_by_courses",
                        "core_calendar_get_action_events_by_timesort", "mod_forum_get_forums_by_courses", "mod_forum_get_forum_discussions"]
                    return self._json({"sitename": "Mock", "fullname": "Test Student", "userid": 1, "release": "4.5.15",
                                       "functions": [{"name": f, "version": "1"} for f in names]})
                if fn == "core_enrol_get_users_courses":
                    return self._json(mock.courses)
                if fn == "core_course_get_courses_by_field":
                    want = {int(x) for x in one("value").split(",") if x}
                    pool = {**{c["id"]: c for c in mock.courses}, **mock.open_courses}
                    return self._json({"courses": [pool[i] for i in sorted(want) if i in pool], "warnings": []})
                if fn == "core_course_get_contents":
                    return self._json(mock.sections[int(one("courseid"))])
                ids = [int(x) for x in _list(q, "courseids")]
                if fn == "mod_assign_get_assignments":
                    return self._json({"courses": [{"id": c, "assignments": mock.assignments.get(c, [])} for c in ids], "warnings": []})
                if fn == "mod_assign_get_submission_status":
                    return self._json(mock.submission.get(int(one("assignid")), {"lastattempt": {"submission": {"status": "new"}}}))
                if fn == "mod_quiz_get_quizzes_by_courses":
                    return self._json({"quizzes": [z for z in mock.quizzes if z["course"] in ids], "warnings": []})
                if fn == "core_calendar_get_action_events_by_timesort":
                    lim = int(one("limitnum") or 0)
                    if not 1 <= lim <= 50:
                        return self._json({"exception": "invalid_parameter_exception", "errorcode": "invalidparameter",
                                           "message": "Limit must be between 1 and 50 (inclusive)"})
                    after = int(one("aftereventid") or 0)
                    evs = [e for e in sorted(mock.events, key=lambda e: e["id"]) if e["id"] > after][:lim]
                    return self._json({"events": evs, "firstid": evs[0]["id"] if evs else 0, "lastid": evs[-1]["id"] if evs else 0})
                if fn == "mod_forum_get_forums_by_courses":
                    return self._json([f for f in mock.forums if f["course"] in ids])
                if fn == "mod_forum_get_forum_discussions":
                    return self._json({"discussions": mock.discussions.get(int(one("forumid")), []), "warnings": []})
                return self._json({"exception": "moodle_exception", "errorcode": "unknownfunction", "message": fn})

            def do_GET(self):
                u = urlparse(self.path)
                if parse_qs(u.query).get("token", [""])[0] != TOKEN or u.path not in mock.blobs:
                    self.send_response(403)
                    self.end_headers()
                    return
                b = mock.blobs[u.path]
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
