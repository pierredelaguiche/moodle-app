"""A stand-in for any command-line model (`opencode run`, `ollama run`, ...), for the generic `cli` provider.

Reads the prompt from stdin when it is piped, takes its arguments as given, and prints an answer wrapped in a colour
code (real tools colour their output; the provider must strip it). With $FAKE_CLI_LOG set it appends one JSON line per
run: {"args", "stdin", "cwd"}. $FAKE_CLI_MODE: "ok" (default), "fail" (exit 3, message on stderr), "empty" (prints
nothing). $FAKE_CLI_ANSWER replaces the answer text."""
import json
import os
import sys

args = sys.argv[1:]
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
if os.environ.get("FAKE_CLI_LOG"):
    with open(os.environ["FAKE_CLI_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "stdin": stdin, "cwd": os.getcwd()}) + "\n")
mode = os.environ.get("FAKE_CLI_MODE", "ok")
if mode == "fail":
    sys.stderr.write("fake-cli: not signed in\n")
    sys.exit(3)
if mode != "empty":
    print("\x1b[32m" + os.environ.get("FAKE_CLI_ANSWER", "ready") + "\x1b[0m")
