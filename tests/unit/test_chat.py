"""The Chat page: conversations with Claude Code inside a project folder (services/chat.py, /api/chat/*).
The `claude` command is replaced by tests/fake_claude.py, which prints the same stream-json events."""
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from moodle_app.services.chat import Chat, ChatError, Turn
from moodle_app.web import server
from test_web import req, web  # noqa: F401  (the fixture)

FAKE = Path(__file__).resolve().parent.parent / "fake_claude.py"


@pytest.fixture
def claude(tmp_path, monkeypatch):
    """A `claude` executable that runs the fake; its runs are logged to the returned file."""
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' \"$@\"\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "claude-runs.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    return exe, log


def wait(chat, cid, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        c = chat.get(cid)
        if not c["running"]:
            return c
        time.sleep(0.05)
    raise AssertionError("the turn did not finish")


def runs(log):
    return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []


pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake command is a shell script")


def test_turn_folds_stream_events_into_one_message():
    m = {"parts": []}
    t = Turn(m)
    assert t.feed({"type": "system", "subtype": "init", "session_id": "S1"}) == "S1"
    t.feed({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "text"}}})
    t.feed({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}}})
    assert m["parts"] == [{"type": "text", "text": "Hel"}]                       # streamed text shows before the block is complete
    t.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello"}]}})
    t.feed({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.md"}}]}})
    t.feed({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "body"}]}]}})
    t.feed({"type": "result", "is_error": False, "result": "Hello", "total_cost_usd": 0.5, "usage": {"input_tokens": 3, "output_tokens": 4}})
    assert m["parts"] == [{"type": "text", "text": "Hello"}, {"type": "tool", "id": "t1", "name": "Read", "summary": "Read a.md", "result": "body", "error": False}]
    assert m["cost"] == 0.5 and m["tokens"] == {"in": 3, "out": 4}


def test_conversation_runs_in_the_project_folder_and_continues(web, claude):
    exe, log = claude
    chat = Chat(web.ctx, web.app.lock, exe=str(exe))
    projects = chat.projects()
    mod = next(p for p in projects if p["kind"] == "module")
    assert any(p["kind"] == "root" for p in projects)
    c = chat.create(mod["id"], model="sonnet", mode="ask")
    chat.send(c["id"], "What is in here?")
    c = wait(chat, c["id"])
    reply = c["messages"][-1]
    assert not reply.get("error"), reply
    texts = [p["text"] for p in reply["parts"] if p["type"] == "text"]
    assert "You asked: **What is in here?**" in texts[0] and "The folder has" in texts[1]
    assert [p["name"] for p in reply["parts"] if p["type"] == "tool"] == ["Glob"] and reply["tokens"]["in"] == 1500
    r1 = runs(log)[-1]
    assert r1["cwd"] == mod["path"] and r1["prompt"] == "What is in here?" and "--resume" not in r1["args"]
    a = r1["args"]
    assert a[a.index("--tools") + 1] == "Read,Grep,Glob" and a[a.index("--permission-mode") + 1] == "dontAsk" and a[a.index("--model") + 1] == "sonnet"
    assert c["title"] == "What is in here?" and c["session"]
    # the next message resumes the same Claude session; a new mode applies to it
    chat.update(c["id"], mode="edit")
    chat.send(c["id"], "And now?")
    c2 = wait(chat, c["id"])
    r2 = runs(log)[-1]
    assert r2["args"][r2["args"].index("--resume") + 1] == c["session"] and "Edit,Write" in r2["args"][r2["args"].index("--tools") + 1]
    assert "(continued conversation)" in c2["messages"][-1]["parts"][-1]["text"]
    # saved: a fresh service (a restart) lists and reads it
    again = Chat(web.ctx, web.app.lock, exe=str(exe))
    assert [x["id"] for x in again.list(mod["id"])] == [c["id"]] and len(again.get(c["id"])["messages"]) == 4
    # Claude's plan usage seen in the stream is remembered for the other AI features
    from moodle_app.services import usage
    assert usage.load_limits(web.ctx)["windows"]["five_hour"]["util"] == 0.12


def test_errors_stop_and_a_lost_session(web, claude, monkeypatch):
    exe, log = claude
    chat = Chat(web.ctx, web.app.lock, exe=str(exe))
    pid = chat.projects()[0]["id"]
    c = chat.create(pid)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    chat.send(c["id"], "hi")
    err = wait(chat, c["id"])["messages"][-1]["error"]
    assert "Invalid API key" in err and "Sign in once" in err
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "slow")
    chat.send(c["id"], "long one")
    time.sleep(0.6)
    with pytest.raises(ChatError):
        chat.send(c["id"], "while running")                       # one turn at a time
    chat.stop(c["id"])
    done = wait(chat, c["id"])["messages"][-1]
    assert done.get("stopped") and done["parts"]                  # what streamed before Stop is kept
    # Claude no longer has the session: the message is answered in a new one, and the reply says so
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    chat.send(c["id"], "first")
    sid = wait(chat, c["id"])["session"]
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "lostsession")
    chat.send(c["id"], "again")
    c = wait(chat, c["id"])
    assert c["session"] != sid and "could not be resumed" in c["messages"][-1]["note"] and not c["messages"][-1].get("error")


def test_projects_folders_and_validation(web, tmp_path, claude):
    chat = Chat(web.ctx, web.app.lock, exe=str(claude[0]))
    proj = tmp_path / "my-app"
    proj.mkdir()
    r = chat.add_folder(str(proj))
    assert r["added"] == str(proj.resolve()) and any(p["kind"] == "folder" and p["path"] == str(proj.resolve()) for p in r["projects"])
    for bad in ("", "relative/path", str(tmp_path / "nope"), "/", str(Path.home())):
        with pytest.raises(ChatError):
            chat.add_folder(bad)
    with pytest.raises(ChatError):
        chat.create("/somewhere/else")                             # only listed projects
    with pytest.raises(ChatError):
        chat.create(str(proj.resolve()), mode="everything")
    with pytest.raises(ChatError):
        chat.get("../../etc/passwd")
    c = chat.create(str(proj.resolve()), mode="run")
    assert chat.command(c)[chat.command(c).index("--tools") + 1].endswith(",Bash")
    with pytest.raises(ChatError):
        chat.send(c["id"], "   ")
    assert chat.remove_folder(str(proj.resolve()))["projects"] and not any(p["kind"] == "folder" for p in chat.projects())
    chat.delete(c["id"])
    assert chat.list() == []


def test_missing_cli_is_explained(web, monkeypatch):
    monkeypatch.setattr("moodle_app.services.chat.find_claude_cli", lambda: "")
    chat = Chat(web.ctx, web.app.lock)
    assert chat.state()["cli"] == ""
    c = chat.create(chat.projects()[0]["id"])
    with pytest.raises(ChatError, match="claude"):
        chat.send(c["id"], "hi")


def test_chat_endpoints_only_answer_to_the_chat_page(web, claude):
    web.app._chat = Chat(web.ctx, web.app.lock, exe=str(claude[0]))
    for op in ("state", "list", "create", "send", "folder/add"):
        assert req(web, "/api/chat/" + op, {})[0] == 403
        assert req(web, "/api/chat/" + op, {}, page="ai")[0] == 403 and req(web, "/api/chat/" + op, {}, page="study")[0] == 403
    s, st = req(web, "/api/chat/state", {}, page="chat")
    assert s == 200 and st["cli"] and st["projects"]
    s, c = req(web, "/api/chat/create", {"project": st["projects"][0]["id"]}, page="chat")
    s, c = req(web, "/api/chat/send", {"id": c["id"], "text": "hello"}, page="chat")
    assert s == 200 and c["running"]
    wait(web.app.chat, c["id"])
    s, c = req(web, "/api/chat/get", {"id": c["id"]}, page="chat")
    assert c["messages"][-1]["parts"]
    assert req(web, "/api/chat/create", {"project": "/etc"}, page="chat")[0] == 400
    # only the Chat page's script names these paths
    for f in server.STATIC.glob("*.js"):
        assert ("/api/chat/" in f.read_text()) == (f.name == "chat.js"), f.name
