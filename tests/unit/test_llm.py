import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from moodle_app.llm import providers as llm


class Srv:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                code, obj = outer.replies.pop(0)
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.s.server_address[1]}"
        threading.Thread(target=self.s.serve_forever, daemon=True).start()

    def close(self):
        self.s.shutdown()
        self.s.server_close()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: None)


def test_claude_request_shape_and_usage():
    s = Srv([(200, {"content": [{"type": "text", "text": "hi"}], "usage": {"input_tokens": 12, "output_tokens": 3}, "stop_reason": "end_turn"})])
    try:
        r = llm.ClaudeProvider("m1", "KEY", s.url).complete("SYS", "PROMPT", 99)
    finally:
        s.close()
    path, headers, body = s.seen[0]
    assert path == "/v1/messages" and headers["x-api-key"] == "KEY" and headers["anthropic-version"] == "2023-06-01"
    assert body == {"model": "m1", "max_tokens": 99, "system": "SYS", "messages": [{"role": "user", "content": "PROMPT"}]}
    assert (r.text, r.input_tokens, r.output_tokens, r.truncated) == ("hi", 12, 3, False)


def test_claude_truncation_flag():
    s = Srv([(200, {"content": [{"type": "text", "text": "cut"}], "stop_reason": "max_tokens"})])
    try:
        assert llm.ClaudeProvider("m", "k", s.url).complete("s", "p", 5).truncated
    finally:
        s.close()


def test_openai_compat_shape_and_local_detection():
    s = Srv([(200, {"choices": [{"message": {"content": "yo"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}})])
    try:
        p = llm.OpenAICompatProvider("qwen", s.url + "/v1")
        r = p.complete("S", "P", 10)
    finally:
        s.close()
    path, headers, body = s.seen[0]
    assert path == "/v1/chat/completions" and "authorization" not in {k.lower() for k in headers}
    assert body["messages"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "P"}]
    assert r.truncated and r.input_tokens == 4 and not p.remote
    assert llm.OpenAICompatProvider("m", "https://api.example.com/v1", "k").remote


def test_retry_on_429_then_success_and_hard_failure():
    s = Srv([(429, {"error": "slow"}), (200, {"content": [{"type": "text", "text": "ok"}]})])
    try:
        assert llm.ClaudeProvider("m", "k", s.url).complete("s", "p", 5).text == "ok"
    finally:
        s.close()
    s = Srv([(401, {"error": "bad key"})])
    try:
        with pytest.raises(llm.LLMError, match="HTTP 401"):
            llm.ClaudeProvider("m", "k", s.url).complete("s", "p", 5)
    finally:
        s.close()


def test_cache_hits_skip_the_network_and_truncated_is_not_cached(tmp_path):
    s = Srv([(200, {"content": [{"type": "text", "text": "one"}], "usage": {"input_tokens": 1, "output_tokens": 1}}),
             (200, {"content": [{"type": "text", "text": "cut"}], "stop_reason": "max_tokens"}),
             (200, {"content": [{"type": "text", "text": "full"}]})])
    try:
        c = llm.CachedProvider(llm.ClaudeProvider("m", "k", s.url), tmp_path / "c")
        assert c.complete("s", "p", 5).text == "one"
        again = c.complete("s", "p", 5)
        assert again.text == "one" and again.cached and len(s.seen) == 1
        assert c.complete("s", "other", 5).truncated
        assert c.complete("s", "other", 5).text == "full"      # the cut-off answer was not cached
    finally:
        s.close()


def test_make_provider_key_sources(tmp_path, monkeypatch):
    for n in ("ANTHROPIC_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(llm, "find_claude_cli", lambda: "")
    with pytest.raises(llm.LLMError, match="claude. command"):
        llm.make_provider({"provider": "claude"}, tmp_path)
    (tmp_path / ".llm_key").write_text("filekey\n")
    assert llm.make_provider({"provider": "claude", "key_file": ".llm_key"}, tmp_path).api_key == "filekey"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "envkey")
    assert llm.make_provider({"provider": "claude", "key_file": ".llm_key"}, tmp_path).api_key == "envkey"
    with pytest.raises(llm.LLMError, match="provider"):
        llm.make_provider({"provider": "nope"}, tmp_path)
    assert "local" in llm.make_provider({"provider": "openai_compat", "model": "m", "base_url": "http://localhost:11434/v1"}, tmp_path).destination


def _fake_claude(tmp_path, body):
    f = tmp_path / "claude"
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)
    return str(f)


def test_claude_cli_provider(tmp_path, monkeypatch):
    for n in ("ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    exe = _fake_claude(tmp_path, 'cat >/dev/null; echo "$@" > %s/args; echo \'{"result":"hi","is_error":false,"stop_reason":"end_turn",'
                                 '"usage":{"input_tokens":3,"cache_read_input_tokens":10,"output_tokens":2}}\'' % tmp_path)
    monkeypatch.setattr(llm, "find_claude_cli", lambda: exe)
    p = llm.make_provider({}, tmp_path)                       # default: auto, no key -> CLI, haiku
    assert p.name == "claude_cli" and p.model == "haiku" and "claude command" in p.destination
    r = p.complete("sys", "prompt", 100)
    assert (r.text, r.input_tokens, r.output_tokens) == ("hi", 13, 2)
    args = (tmp_path / "args").read_text()
    assert "--model haiku" in args and "--tools" in args and "--setting-sources" in args


def test_claude_cli_errors_and_key_preferred(tmp_path, monkeypatch):
    exe = _fake_claude(tmp_path, 'cat >/dev/null; echo \'{"is_error":true,"result":"Please login"}\'; exit 1')
    with pytest.raises(llm.LLMError, match="log in"):
        llm.ClaudeCLIProvider("haiku", exe).complete("s", "p", 10)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    p = llm.make_provider({"provider": "auto", "model": "haiku"}, tmp_path)
    assert p.name == "claude" and p.model == "claude-haiku-5-5"


def test_pdf_text_is_extracted_once(tmp_path, monkeypatch):
    from moodle_app.notes import extract as ex
    calls = []
    monkeypatch.setattr(ex, "_extract_pdf", lambda p: calls.append(p) or "<!-- p.1 -->\nhello")
    ex.configure_cache(tmp_path / "cache")
    try:
        pdf = tmp_path / "a.pdf"
        pdf.write_bytes(b"%PDF-1")
        assert ex.extract_info(pdf) == ("<!-- p.1 -->\nhello", False)
        assert ex.extract_info(pdf) == ("<!-- p.1 -->\nhello", True)         # second ask: from the shared cache
        assert len(calls) == 1
        pdf.write_bytes(b"%PDF-1 changed")                                   # file changed: read again
        assert ex.extract_info(pdf)[1] is False and len(calls) == 2
    finally:
        ex.configure_cache(None)


def test_claude_cli_retries_without_token_overrides_and_diagnose(tmp_path, monkeypatch):
    ok = json.dumps({"result": "ready", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})
    bad = json.dumps({"is_error": True, "result": "API Error: 400 thinking.enabled.budget_tokens: Input should be >= 1024"})
    (tmp_path / "ok.json").write_text(ok)
    (tmp_path / "bad.json").write_text(bad)
    exe = _fake_claude(tmp_path, f'cat >/dev/null\nif [ -n "$CLAUDE_CODE_MAX_OUTPUT_TOKENS" ]; then cat {tmp_path}/bad.json; exit 1; fi\ncat {tmp_path}/ok.json')
    assert llm.ClaudeCLIProvider("haiku", exe).complete("s", "p", 50).text == "ready"
    for n in ("ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    lines = llm.diagnose({"executable": exe, "key_file": ".llm_key"}, tmp_path)
    assert "API key: none" in lines[0] and exe in lines[1]
    (tmp_path / ".llm_key").write_text("k")
    assert "found in" in llm.diagnose({"executable": exe, "key_file": ".llm_key"}, tmp_path)[0]


def test_limits_parse_and_guard():
    now = 1_800_000_000
    info = {"status": "allowed", "overageStatus": "rejected", "isUsingOverage": False,
            "unifiedWindows": {"five_hour": {"utilization": 0.85, "resetsAt": now + 600, "status": "allowed"},
                               "seven_day": {"utilization": 0.1, "resetsAt": now + 9999, "status": "allowed"}}}
    lim = llm.parse_limits(info, now)
    assert "85%" in llm.limits_problem(lim, 0.8, now)
    assert llm.limits_problem(lim, 0.9, now) is None
    assert llm.limits_problem(lim, 0.8, now + 700) is None                   # that window has reset since
    lim["using_overage"] = True
    assert "extra" in llm.limits_problem(lim, 0.9, now)
    lim["using_overage"] = False
    lim["windows"]["seven_day"]["status"] = "rejected"
    assert "7-day limit is reached" in llm.limits_problem(lim, 0.9, now)


def test_claude_cli_stream_learns_limits_and_refuses_when_high(tmp_path):
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.9, "resetsAt": 4_000_000_000, "status": "allowed"}}}}
    res = {"type": "result", "result": "hi", "is_error": False, "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}
    (tmp_path / "o.txt").write_text(json.dumps(ev) + "\n" + json.dumps(res) + "\n")
    exe = _fake_claude(tmp_path, f"cat >/dev/null\ncat {tmp_path}/o.txt")
    p = llm.ClaudeCLIProvider("haiku", exe)
    assert p.complete("s", "p", 100).text == "hi" and p.limits["windows"]["five_hour"]["util"] == 0.9
    with pytest.raises(llm.LLMLimit, match="90%"):
        p.complete("s", "p", 100)                                           # next call is refused before anything is sent
    assert p.complete("s", "p", 100, check=False).text == "hi"
