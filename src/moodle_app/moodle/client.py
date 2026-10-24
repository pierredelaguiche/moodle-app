"""Moodle web-service client (standard library only). Read-only by policy: write functions are never called."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class MoodleError(RuntimeError):
    def __init__(self, fn, code, message):
        super().__init__(f"{fn}: {code} - {message}")
        self.fn, self.code, self.message = fn, code, message


class NetworkError(RuntimeError):
    pass


def flatten(params, prefix=""):
    """Moodle's REST encoding of nested params: courseids[0]=1, options[0][name]=x ..."""
    out = []
    for k, v in params.items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            out += flatten(v, key)
        elif isinstance(v, (list, tuple)):
            for i, x in enumerate(v):
                if isinstance(x, (dict, list, tuple)):
                    out += flatten({i: x}, key)
                else:
                    out.append((f"{key}[{i}]", x))
        elif isinstance(v, bool):
            out.append((key, int(v)))
        else:
            out.append((key, v))
    return out


class Moodle:
    def __init__(self, base, token, timeout=60):
        self.base, self.token, self.timeout = base.rstrip("/"), token, timeout
        self.calls = 0

    def call(self, fn, **params):
        self.calls += 1
        body = flatten(params) + [("wstoken", self.token), ("wsfunction", fn), ("moodlewsrestformat", "json")]
        req = urllib.request.Request(f"{self.base}/webservice/rest/server.php", data=urllib.parse.urlencode(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise NetworkError(f"{fn}: {e}")
        if isinstance(out, dict) and "exception" in out:
            raise MoodleError(fn, out.get("errorcode"), out.get("message"))
        return out

    def download(self, fileurl, dest):
        sep = "&" if "?" in fileurl else "?"
        url = f"{fileurl}{sep}token={urllib.parse.quote(self.token)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
        except (urllib.error.URLError, TimeoutError) as e:
            tmp.unlink(missing_ok=True)
            raise NetworkError(str(e))
        tmp.replace(dest)
