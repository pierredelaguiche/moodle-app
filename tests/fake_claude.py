"""A stand-in for the `claude` command in -p stream-json mode: prints the same kinds of events, slowly enough to stream.

Writes its arguments, working directory and prompt to $FAKE_CLAUDE_LOG (one JSON line per run) when that is set.
$FAKE_CLAUDE_MODE: "ok" (default), "fail" (exits 1 with a login message on stderr), "slow" (keeps streaming for ~10 s),
"lostsession" (fails on --resume, as the real command does for a session it no longer has)."""
import json
import os
import sys
import time
import uuid

args = sys.argv[1:]
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if os.environ.get("FAKE_CLAUDE_LOG"):
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        f.write(json.dumps({"args": args, "cwd": os.getcwd(), "prompt": prompt}) + "\n")
if mode == "fail":
    sys.stderr.write("Invalid API key · Please run /login\n")
    sys.exit(1)
if mode == "lostsession" and "--resume" in args:
    sys.stderr.write("No conversation found with session ID: " + args[args.index("--resume") + 1] + "\n")
    sys.exit(1)
sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())


def out(ev):
    ev.setdefault("session_id", sid)
    print(json.dumps(ev), flush=True)


def stream_text(text, index=0):
    out({"type": "stream_event", "event": {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}})
    for i in range(0, len(text), 6):
        out({"type": "stream_event", "event": {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text[i:i + 6]}}})
        time.sleep(0.01 if mode != "slow" else 0.2)
    out({"type": "stream_event", "event": {"type": "content_block_stop", "index": index}})
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})


tools = args[args.index("--tools") + 1] if "--tools" in args else ""
out({"type": "system", "subtype": "init", "cwd": os.getcwd(), "model": args[args.index("--model") + 1] if "--model" in args else "?", "tools": tools.split(",")})
out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {"five_hour": {"utilization": 0.12, "resetsAt": int(time.time()) + 3600}}}})
first = f"I will look at the folder. You asked: **{prompt.strip()[:80]}**"
if mode == "slow":
    first = first + " " + "and more " * 50
stream_text(first)
names = sorted(n for n in os.listdir(".") if not n.startswith("."))[:5]
tid = "toolu_" + uuid.uuid4().hex[:12]
out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": "Glob", "input": {"pattern": "*"}}]}})
time.sleep(0.05)
out({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "\n".join(names) or "(empty)", "is_error": False}]}})
second = "The folder has: " + ", ".join(f"`{n}`" for n in names) + ".\n\nMaths works too: $e^{i\\pi}+1=0$." + (" (continued conversation)" if "--resume" in args else "")
stream_text(second, 1)
out({"type": "result", "subtype": "success", "is_error": False, "result": second, "num_turns": 2, "total_cost_usd": 0.0123,
     "usage": {"input_tokens": 1200, "cache_read_input_tokens": 300, "output_tokens": 85}})
