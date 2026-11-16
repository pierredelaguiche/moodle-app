"""The Chat page: a conversation with Claude Code that works inside a folder you pick, like the chat panel of an editor.

Each conversation belongs to a *project*: a module folder, the whole study folder, or another folder you added. A message runs
the signed-in `claude` command in that folder (`claude -p --output-format stream-json`), so Claude can read the files there
(and, if you allow it, edit them or run commands). The command's session id is kept, so the next message continues the same
conversation (`--resume`) instead of resending it. The answer streams: the page polls the conversation while a turn runs.

What Claude may do is chosen per conversation:
    ask    read and search files only (Read, Grep, Glob)                                   the default
    edit   also create and change files (Edit, Write)
    run    also run shell commands (Bash), without asking first
Anything else is unavailable (`--tools`) and refused (`--permission-mode dontAsk`): a headless run cannot ask you.

Conversations are saved as JSON under <data dir>/chat/ and survive restarts. Nothing is sent before you press Send.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path

from ..llm.providers import find_claude_cli, limits_problem, parse_limits
from . import usage as usage_service

MODES = {
    "ask": ("Read files", "Read,Grep,Glob"),
    "edit": ("Read and edit files", "Read,Grep,Glob,Edit,Write"),
    "run": ("Edit files and run commands", "Read,Grep,Glob,Edit,Write,Bash"),
}
MODELS = ("haiku", "sonnet", "opus", "fable")
SYSTEM = ("You are working for a university student inside their study app, in the folder named in your working directory. "
          "Their course files, notes and code are there. Answer in Markdown; write maths as LaTeX between $ signs. "
          "Be concise. When you change files, say which ones.")
MAX_RUNNING = 3                 # conversations that may run a turn at the same time
MAX_TEXT = 40_000               # characters in one message
KEEP_RESULT = 1500              # characters of a tool's output kept for display
ID_RE = re.compile(r"^[a-f0-9]{16}$")
LOST_SESSION = re.compile(r"no conversation found|session.{0,40}not found|could not resume|cannot resume", re.I)


class ChatError(ValueError):
    pass


def _summary(name, inp):
    """One line saying what a tool call did: 'Read notes/week1.md', 'Bash: pytest -q'."""
    inp = inp or {}
    for k in ("file_path", "path", "notebook_path"):
        if inp.get(k):
            return f"{name} {inp[k]}"
    if inp.get("pattern"):
        return f"{name} {inp['pattern']}" + (f" in {inp['path']}" if inp.get("path") else "")
    if inp.get("command"):
        return f"{name}: {str(inp['command'])[:200]}"
    if inp.get("url") or inp.get("query"):
        return f"{name} {inp.get('url') or inp.get('query')}"
    return name


class Turn:
    """Folds the CLI's stream-json events into one assistant message: text parts (streamed) and tool parts."""

    def __init__(self, msg):
        self.msg = msg
        self.live = []                           # text parts still being streamed, oldest first

    def feed(self, ev):
        t = ev.get("type")
        m = self.msg
        if t == "system" and ev.get("subtype") == "init":
            return ev.get("session_id")
        if t == "stream_event":
            e = ev.get("event") or {}
            if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") == "text":
                part = {"type": "text", "text": ""}
                m["parts"].append(part)
                self.live.append(part)
            elif e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                if not self.live:
                    part = {"type": "text", "text": ""}
                    m["parts"].append(part)
                    self.live.append(part)
                self.live[-1]["text"] += e["delta"].get("text", "")
        elif t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text":
                    if self.live:
                        self.live.pop(0)["text"] = b.get("text", "")
                    elif b.get("text"):
                        m["parts"].append({"type": "text", "text": b["text"]})
                elif b.get("type") == "tool_use":
                    m["parts"].append({"type": "tool", "id": b.get("id"), "name": b.get("name", "tool"),
                                       "summary": _summary(b.get("name", "tool"), b.get("input"))})
        elif t == "user":
            content = (ev.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if b.get("type") != "tool_result":
                    continue
                out = b.get("content")
                if isinstance(out, list):
                    out = "\n".join(x.get("text", "") for x in out if isinstance(x, dict))
                for p in m["parts"]:
                    if p.get("type") == "tool" and p.get("id") == b.get("tool_use_id"):
                        p["result"] = str(out or "")[:KEEP_RESULT]
                        p["error"] = bool(b.get("is_error"))
        elif t == "result":
            m["cost"] = ev.get("total_cost_usd")
            u = ev.get("usage") or {}
            m["tokens"] = {"in": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                           "out": u.get("output_tokens") or 0}
            m["turns"] = ev.get("num_turns")
            if ev.get("is_error"):
                m["error"] = str(ev.get("result") or ev.get("subtype") or "error")[:600]
            elif not any(p.get("type") == "text" and p.get("text") for p in m["parts"]) and ev.get("result"):
                m["parts"].append({"type": "text", "text": ev["result"]})
            return ev.get("session_id")
        return None


class Chat:
    def __init__(self, ctx, lock=None, exe=None):
        self.x = ctx
        self.lock = lock or threading.RLock()                 # the app's database lock (limits are stored in the database)
        self.mu = threading.RLock()                          # this service's own state
        self.dir = Path(ctx.settings.home) / "chat"
        self.exe = exe
        self.procs = {}                                      # conversation id -> running Popen
        self.cache = {}                                      # conversation id -> loaded conversation

    # ---------- projects ----------
    def _root(self):
        return Path(self.x.settings.dest_root).expanduser()

    def _folders_file(self):
        return self.dir / "folders.json"

    def _extra(self):
        try:
            return [str(p) for p in json.loads(self._folders_file().read_text(encoding="utf-8")) if isinstance(p, str)]
        except (OSError, ValueError):
            return []

    def projects(self):
        """Folders a conversation can work in: every module folder, the study folder itself, and folders you added."""
        from ..domain.years import module_dirs
        root = self._root()
        out = []
        if root.is_dir():
            out += [{"id": str(root / rel), "name": rel, "kind": "module", "path": str(root / rel)} for rel in module_dirs(root)]
            out.append({"id": str(root), "name": f"Study folder ({root.name})", "kind": "root", "path": str(root)})
        for p in self._extra():
            out.append({"id": p, "name": Path(p).name or p, "kind": "folder", "path": p, "missing": not Path(p).is_dir()})
        return out

    def add_folder(self, path):
        p = Path(str(path or "").strip()).expanduser()
        if not str(path or "").strip() or not p.is_absolute():
            raise ChatError("give the full path of a folder, e.g. /Users/you/Projects/my-app")
        try:
            p = p.resolve(strict=True)
        except OSError:
            raise ChatError(f"no such folder: {p}")
        if not p.is_dir():
            raise ChatError(f"not a folder: {p}")
        if p == Path(p.anchor) or p == Path.home().resolve():
            raise ChatError("pick a project folder, not the whole disk or your home folder")
        have = self._extra()
        if str(p) not in have:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._folders_file().write_text(json.dumps(have + [str(p)], indent=1), encoding="utf-8")
        return {"projects": self.projects(), "added": str(p)}

    def remove_folder(self, path):
        have = [p for p in self._extra() if p != path]
        self.dir.mkdir(parents=True, exist_ok=True)
        self._folders_file().write_text(json.dumps(have, indent=1), encoding="utf-8")
        return {"projects": self.projects()}

    def _project(self, pid):
        for p in self.projects():
            if p["id"] == pid:
                if p.get("missing"):
                    raise ChatError(f"the folder is gone: {pid}")
                return p
        raise ChatError("pick a project")

    # ---------- conversations ----------
    def _file(self, cid):
        if not ID_RE.match(cid or ""):
            raise ChatError("unknown conversation")
        return self.dir / "sessions" / f"{cid}.json"

    def _load(self, cid):
        with self.mu:
            if cid in self.cache:
                return self.cache[cid]
            try:
                c = json.loads(self._file(cid).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ChatError("unknown conversation")
            c["running"] = False
            self.cache[cid] = c
            return c

    def _save(self, c):
        f = self._file(c["id"])
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps({k: v for k, v in c.items() if k != "running"}), encoding="utf-8")
        os.replace(tmp, f)

    def list(self, project=None):
        d = self.dir / "sessions"
        out = []
        for f in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                c = self._load(f.stem)
            except ChatError:
                continue
            if project and c["project"] != project:
                continue
            out.append({"id": c["id"], "title": c.get("title") or "New chat", "project": c["project"], "updated": c.get("updated", 0),
                        "running": c["running"], "n": len(c["messages"])})
        return sorted(out, key=lambda c: -c["updated"])

    def create(self, project, model=None, mode="ask"):
        p = self._project(project)
        c = {"id": secrets.token_hex(8), "project": p["id"], "title": "", "model": self._model(model), "mode": self._mode(mode),
             "session": None, "messages": [], "created": time.time(), "updated": time.time(), "v": 0, "running": False}
        with self.mu:
            self.cache[c["id"]] = c
            self._save(c)
        return self.get(c["id"])

    def _model(self, model):
        model = (model or (self.x.settings.llm or {}).get("model") or "sonnet").strip()
        if not re.fullmatch(r"[A-Za-z0-9._\-\[\]]{2,80}", model):
            raise ChatError("unknown model")
        return model

    @staticmethod
    def _mode(mode):
        if mode not in MODES:
            raise ChatError("mode is one of: " + ", ".join(MODES))
        return mode

    def get(self, cid, since_v=None):
        c = self._load(cid)
        with self.mu:
            return {**{k: v for k, v in c.items()}, "modes": {k: v[0] for k, v in MODES.items()}}

    def update(self, cid, model=None, mode=None, title=None):
        c = self._load(cid)
        with self.mu:
            if c["running"]:
                raise ChatError("wait for the answer, or stop it, before changing the settings")
            if model is not None:
                c["model"] = self._model(model)
            if mode is not None:
                c["mode"] = self._mode(mode)
            if title is not None:
                c["title"] = str(title).strip()[:120]
            c["v"] += 1
            self._save(c)
        return self.get(cid)

    def delete(self, cid):
        self.stop(cid)
        with self.mu:
            self.cache.pop(cid, None)
            try:
                self._file(cid).unlink()
            except FileNotFoundError:
                pass
        return {"ok": True}

    # ---------- a turn ----------
    def _exe(self):
        exe = self.exe or (self.x.settings.llm or {}).get("executable") or find_claude_cli()
        if not exe:
            raise ChatError("The chat runs the `claude` command (Claude Code), which was not found. Install Claude Code and sign in "
                            "(run `claude` once in Terminal), or set llm.executable in config.json to its full path.")
        return exe

    def command(self, c):
        tools = MODES[c["mode"]][1]
        cmd = [self._exe(), "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--model", c["model"], "--tools", tools, "--allowedTools", tools, "--permission-mode", "dontAsk",
               "--strict-mcp-config", "--append-system-prompt", SYSTEM]
        if c.get("session"):
            cmd += ["--resume", c["session"]]
        return cmd

    def send(self, cid, text):
        text = str(text or "").strip()
        if not text:
            raise ChatError("write a message first")
        if len(text) > MAX_TEXT:
            raise ChatError(f"the message is too long (more than {MAX_TEXT:,} characters)")
        c = self._load(cid)
        p = self._project(c["project"])
        with self.lock:
            limits = usage_service.load_limits(self.x)
        why = limits_problem(limits, float((self.x.settings.llm or {}).get("stop_at", 0.8)))
        if why:
            raise ChatError(why)
        cmd = self.command(c)
        with self.mu:
            if c["running"]:
                raise ChatError("Claude is still answering; wait or press Stop")
            if sum(1 for x in self.cache.values() if x["running"]) >= MAX_RUNNING:
                raise ChatError(f"{MAX_RUNNING} conversations are already running; wait for one to finish")
            c["messages"].append({"role": "user", "text": text, "at": time.time()})
            reply = {"role": "assistant", "parts": [], "at": time.time(), "model": c["model"], "mode": c["mode"]}
            c["messages"].append(reply)
            if not c["title"]:
                c["title"] = re.sub(r"\s+", " ", text)[:70]
            c["running"], c["updated"] = True, time.time()
            c["v"] += 1
            self._save(c)
        threading.Thread(target=self._run, args=(c, reply, cmd, p["path"], text), daemon=True, name=f"chat-{cid}").start()
        return self.get(cid)

    def _run(self, c, reply, cmd, cwd, text, retried=False):
        turn, err = Turn(reply), []
        env = {**os.environ}
        env.pop("CLAUDECODE", None)                         # a nested run must not think it is inside another session
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                                    start_new_session=os.name != "nt")
        except OSError as e:
            self._finish(c, reply, f"could not run {cmd[0]}: {e}")
            return
        with self.mu:
            self.procs[c["id"]] = proc
        reader = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
        reader.start()
        try:
            proc.stdin.write(text)
            proc.stdin.close()
        except OSError:
            pass
        got_any = False
        for ln in proc.stdout:
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            got_any = True
            if ev.get("type") == "rate_limit_event":
                lim = parse_limits(ev.get("rate_limit_info"))
                with self.lock:
                    try:
                        usage_service.save_limits(self.x, lim)
                    except Exception:
                        pass
                continue
            with self.mu:
                sid = turn.feed(ev)
                if sid:
                    c["session"] = sid
                c["v"] += 1
        proc.wait()
        reader.join(5)
        stderr = "".join(err).strip()
        with self.mu:
            stopped = self.procs.pop(c["id"], None) is None
        if stopped:
            self._finish(c, reply, "stopped" if not reply["parts"] else None, stopped=True)
            return
        if proc.returncode and not got_any and not retried and c.get("session") and LOST_SESSION.search(stderr):
            with self.mu:                                       # the CLI no longer has that session: start a fresh one
                c["session"] = None
                reply["note"] = "The earlier conversation could not be resumed; this answer starts a new one."
            self._run(c, reply, [a for i, a in enumerate(cmd) if a != "--resume" and (i == 0 or cmd[i - 1] != "--resume")], cwd, text, retried=True)
            return
        problem = None
        if proc.returncode and not reply.get("error"):
            problem = (stderr or f"claude exited with code {proc.returncode}")[:600]
            if re.search(r"log ?in|auth|credential", problem, re.I):
                problem += "\nSign in once: run `claude` in Terminal."
        self._finish(c, reply, problem)

    def _finish(self, c, reply, problem, stopped=False):
        with self.mu:
            if problem:
                reply["error"] = problem
            if stopped:
                reply["stopped"] = True
            for p in reply["parts"]:
                p.pop("live", None)
            c["running"], c["updated"] = False, time.time()
            c["v"] += 1
            try:
                self._save(c)
            except OSError:
                pass

    def stop(self, cid):
        with self.mu:
            proc = self.procs.pop(cid, None)
        if proc and proc.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()
            except (OSError, ProcessLookupError):
                pass
            threading.Timer(3, lambda: proc.poll() is None and proc.kill()).start()
        return {"ok": True}

    def stop_all(self):
        for cid in list(self.procs):
            self.stop(cid)

    def state(self):
        exe = ""
        try:
            exe = self._exe()
        except ChatError:
            pass
        llm = self.x.settings.llm or {}
        return {"cli": exe, "models": list(MODELS), "model": llm.get("model") or "sonnet",
                "modes": [{"id": k, "label": v[0], "tools": v[1]} for k, v in MODES.items()]}
