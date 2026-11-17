"""LLM providers (standard library only). The only module that talks to a model.

  ClaudeProvider        Anthropic Messages API (API key)
  ClaudeCLIProvider     the signed-in `claude` command (Claude Code); the only one that knows plan limits
  OpenAICompatProvider  any OpenAI-style /chat/completions endpoint (Ollama, LM Studio, llama.cpp server, vLLM, hosted APIs)
  CLIProvider           any command that takes a prompt and prints the answer (`opencode run`, `llm`, `ollama run`, a script)
All return a Result, with real token counts when the backend reports them and an estimate otherwise. CachedProvider wraps
one and stores answers on disk keyed by (provider, model, system, prompt, max_tokens), so re-running an unchanged job costs nothing.

A model per task: `make_provider(cfg, ..., task=)` reads `llm.tasks.<task>` over the top-level `llm` settings (`task_config`).
Every provider says where text goes (`remote`, `where`), which is what the confirm before a send shows.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class LLMError(RuntimeError):
    pass


class LLMLimit(LLMError):
    """Your Claude plan is at (or near) its usage limit: stop instead of waiting, failing or spending extra."""


def parse_limits(info, now=None):
    """Claude Code's rate_limit_event -> {seen, status, windows:{name:{util,resets}}, overage, using_overage}."""
    info = info or {}
    wins = {k: {"util": float(v.get("utilization") or 0), "resets": v.get("resetsAt") or 0, "status": v.get("status", "allowed")}
            for k, v in (info.get("unifiedWindows") or {}).items()}
    if not wins and info.get("rateLimitType"):
        wins[info["rateLimitType"]] = {"util": 0.0, "resets": info.get("resetsAt") or 0, "status": info.get("status", "allowed")}
    return {"seen": now or time.time(), "status": info.get("status", "allowed"), "windows": wins,
            "overage": info.get("overageStatus"), "using_overage": bool(info.get("isUsingOverage"))}


WINDOW_NAMES = {"five_hour": "5-hour", "seven_day": "7-day"}


def limits_problem(limits, stop_at=0.8, now=None):
    """Why a run should not start (text), or None. Windows that have already reset are ignored."""
    if not limits:
        return None
    now = now or time.time()
    for name, w in (limits.get("windows") or {}).items():
        if w.get("resets") and w["resets"] <= now:
            continue
        when = time.strftime("%a %H:%M", time.localtime(w["resets"])) if w.get("resets") else "later"
        if w.get("status") not in (None, "allowed"):
            return f"Your Claude {WINDOW_NAMES.get(name, name)} limit is reached (resets {when})."
        if w.get("util", 0) >= stop_at:
            return (f"Your Claude {WINDOW_NAMES.get(name, name)} usage is at {w['util'] * 100:.0f}% (the app stops at {stop_at * 100:.0f}%; resets {when}). "
                    f"Change llm.stop_at in config.json to allow more.")
    if limits.get("using_overage"):
        return "Claude would use extra (billed) usage now; the app does not do that."
    return None


def limits_line(limits, now=None):
    if not limits:
        return "Claude plan usage: not known yet (run `moodle-app llm-check`)."
    now = now or time.time()
    parts = []
    for name, w in (limits.get("windows") or {}).items():
        gone = w.get("resets") and w["resets"] <= now
        parts.append(f"{WINDOW_NAMES.get(name, name)} {'0%' if gone else format(w.get('util', 0) * 100, '.0f') + '%'}"
                     + ("" if gone or not w.get("resets") else f" (resets {time.strftime('%a %H:%M', time.localtime(w['resets']))})"))
    ov = {"rejected": "extra usage off", "allowed": "extra usage ON"}.get(limits.get("overage"), "")
    return f"Claude plan usage as of {time.strftime('%H:%M', time.localtime(limits.get('seen', now)))}: " + ", ".join(parts) + (f"; {ov}" if ov else "")


@dataclass
class Result:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: bool = False      # the model stopped because it hit max_tokens
    cached: bool = False


def estimate_tokens(text):
    """Rough size: ~4 characters per token. Good to about +/-30% for English prose; code and maths run higher."""
    return (len(text) + 3) // 4 if text else 0


_sleep = time.sleep              # replaced in tests


def _post_json(url, headers, body, timeout=300, retries=3):
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 504, 529) and attempt < retries:
                _sleep(2 ** attempt)
                continue
            raise LLMError(f"HTTP {e.code} from {urlparse(url).netloc}: {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                _sleep(2 ** attempt)
                continue
            raise LLMError(f"could not reach {urlparse(url).netloc}: {e}")


class Provider:
    """What every provider offers. `remote` False means the text stays on this computer; `who` names the receiver otherwise."""
    name = "base"
    model = ""
    remote = True
    base_url = ""
    who = ""

    @property
    def destination(self):
        """One line for logs and Settings: provider, host and model."""
        host = urlparse(self.base_url).netloc or self.base_url
        return f"{self.name} ({host}), model {self.model}" + ("" if self.remote else " [local, text stays on this machine]")

    @property
    def where(self):
        """The privacy half of every confirm: 'stays on this computer' or 'sent to <who>'."""
        return f"sent to {self.who or urlparse(self.base_url).hostname or self.name}" if self.remote else "stays on this computer"

    limits = None

    def complete(self, system, prompt, max_tokens):
        """Send one prompt; return a Result or raise LLMError (LLMLimit when a plan limit stops it)."""
        raise NotImplementedError

    def probe(self):
        """Refresh `limits` with one tiny call (only Claude's own login knows them). Returns the limits or None."""
        return None

    def limit_problem(self):
        return None

    def runnable_probe(self):
        return False


class ClaudeProvider(Provider):
    """Anthropic's Messages API with an API key (billed per use)."""
    name = "claude"
    who = "Anthropic"

    def __init__(self, model, api_key, base_url="https://api.anthropic.com"):
        if not api_key:
            raise LLMError("No API key for Claude. Set ANTHROPIC_API_KEY or put the key in the file named by llm.key_file.")
        self.model, self.api_key, self.base_url = model, api_key, base_url.rstrip("/")

    def complete(self, system, prompt, max_tokens):
        out = _post_json(f"{self.base_url}/v1/messages",
                         {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                         {"model": self.model, "max_tokens": max_tokens, "system": system,
                          "messages": [{"role": "user", "content": prompt}]})
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        if not text.strip():
            raise LLMError(f"empty answer (stop_reason={out.get('stop_reason')})")
        u = out.get("usage") or {}
        return Result(text, u.get("input_tokens", 0), u.get("output_tokens", 0), out.get("stop_reason") == "max_tokens")


ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
CLI_DIRS = ("~/.claude/local", "~/.local/bin", "~/.npm-global/bin", "~/.bun/bin", "/opt/homebrew/bin", "/usr/local/bin")


def find_claude_cli():
    """Path of the `claude` executable (Claude Code), or ''. Checks PATH, then the usual install places (a GUI-launched app has a short PATH)."""
    hit = shutil.which("claude")
    if hit:
        return hit
    dirs = [Path(d).expanduser() for d in CLI_DIRS]
    nvm = Path("~/.nvm/versions/node").expanduser()
    if nvm.is_dir():
        dirs += [v / "bin" for v in sorted(nvm.iterdir(), reverse=True)]
    for d in dirs:
        f = d / "claude"
        if f.is_file() and os.access(f, os.X_OK):
            return str(f)
    return ""


class ClaudeCLIProvider(Provider):
    """Claude through the `claude` command (Claude Code) and its login: no API key, no extra billing set-up.
    Runs headless with no tools, no MCP, no project settings and an empty working directory, so only the prompt is sent.
    Each call carries a fixed ~14k-token Claude Code preamble (cached after the first call), so notes are sent in big batches."""
    name = "claude_cli"
    base_url = "claude CLI"
    who = "Anthropic"

    def __init__(self, model="haiku", exe=None, timeout=600, stop_at=0.8, limits=None):
        self.limits, self.stop_at = limits, stop_at
        self.exe = exe or find_claude_cli()
        if not self.exe:
            raise LLMError("The `claude` command was not found. Install Claude Code and log in (`claude`), or set an API key.")
        self.model, self.timeout = model or "haiku", timeout

    @property
    def destination(self):
        return f"Claude via the claude command (signed-in account), model {self.model}"

    def _run(self, cmd, prompt, env):
        with tempfile.TemporaryDirectory() as cwd:
            try:
                return subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout, cwd=cwd, env=env)
            except subprocess.TimeoutExpired:
                raise LLMError(f"claude CLI timed out after {self.timeout}s")
            except OSError as e:
                raise LLMError(f"could not run {self.exe}: {e}")

    def limit_problem(self):
        return limits_problem(self.limits, self.stop_at)

    def runnable_probe(self):
        return True

    def probe(self):
        try:
            self.complete("Answer with one word.", "ok", 50, check=False)
        except LLMError:
            pass
        return self.limits

    def complete(self, system, prompt, max_tokens, check=True):
        if check and self.limit_problem():
            raise LLMLimit(self.limit_problem())
        cmd = [self.exe, "-p", "--model", self.model, "--output-format", "stream-json", "--verbose", "--tools", "",
               "--system-prompt", system, "--no-session-persistence", "--disable-slash-commands",
               "--setting-sources", "", "--strict-mcp-config"]
        # thinking is switched off (cheaper, and its budget must be >= 1024 which a small output cap would violate)
        env = {**os.environ, "MAX_THINKING_TOKENS": "0", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max(max_tokens, 2048))}
        p = self._run(cmd, prompt, env)
        if "budget_tokens" in (p.stdout + p.stderr):       # an older/newer CLI that still wants thinking: let it use its defaults
            env.pop("CLAUDE_CODE_MAX_OUTPUT_TOKENS"), env.pop("MAX_THINKING_TOKENS")
            p = self._run(cmd, prompt, env)
        out = {}
        for ln in p.stdout.splitlines():
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if ev.get("type") == "rate_limit_event":
                self.limits = parse_limits(ev.get("rate_limit_info"))
            elif ev.get("type") == "result" or (not out and "result" in ev):
                out = ev
        if not out:
            raise LLMError(f"claude CLI gave no result (exit {p.returncode}): {(p.stderr or p.stdout)[:300].strip()}")
        if out.get("is_error") or p.returncode:
            msg = str(out.get("result") or p.stderr)[:300]
            if "limit" in msg.lower() and ("reached" in msg.lower() or "exceeded" in msg.lower()):
                raise LLMLimit(f"Claude says: {msg}")
            raise LLMError(f"claude CLI error: {msg}" + (" - run `claude` once and log in" if "login" in msg.lower() or "auth" in msg.lower() else ""))
        text = out.get("result") or ""
        if not text.strip():
            raise LLMError(f"empty answer (stop_reason={out.get('stop_reason')})")
        u = out.get("usage") or {}
        used_in = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        return Result(text, used_in, u.get("output_tokens", 0), out.get("stop_reason") == "max_tokens")


class OpenAICompatProvider(Provider):
    """Any server with an OpenAI-style /chat/completions endpoint.

    `local` says whether the server runs on this computer. None = decide from the address (localhost is local);
    set it in config when the address cannot tell, e.g. a container name such as http://ollama:11434/v1."""
    name = "openai_compat"

    def __init__(self, model, base_url, api_key="", local=None):
        if not model or not base_url:
            raise LLMError("openai_compat needs a model and a base_url (e.g. http://localhost:11434/v1 for Ollama).")
        self.model, self.base_url, self.api_key = model, base_url.rstrip("/"), api_key
        self.remote = urlparse(self.base_url).hostname not in ("localhost", "127.0.0.1", "::1") if local is None else not local

    def complete(self, system, prompt, max_tokens):
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        out = _post_json(f"{self.base_url}/chat/completions", headers,
                         {"model": self.model, "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]})
        try:
            choice = out["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"unexpected response shape: {str(out)[:200]}")
        if not text.strip():
            raise LLMError("empty answer")
        u = out.get("usage") or {}
        return Result(text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), choice.get("finish_reason") == "length")


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ARG_MAX = 30_000 if os.name == "nt" else 120_000          # one argument: Windows caps a command line near 32k, Linux an argument at 128k


def split_command(command):
    """A command from config as an argument list. A list is used as it is; a string is split into arguments by
    `config.split_args` (quotes group words; Windows paths keep their backslashes). No shell ever runs it."""
    if isinstance(command, str):
        from ..config import split_args
        try:
            return split_args(command)
        except ValueError as e:
            raise LLMError(f"cli command: {e}")
    return [str(a) for a in (command or [])]


class CLIProvider(Provider):
    """Any command-line program as a model: the prompt goes in, the answer is whatever it prints.

    command   argument list (or one string). Placeholders, replaced inside any argument: {model}, {system}, {prompt}.
              Without {prompt} the prompt is written to stdin; without {system} the system text is put in front of the prompt.
              Examples: ["opencode", "run", "-m", "{model}", "{prompt}"]   ["ollama", "run", "{model}"]   ["llm", "-m", "{model}", "-s", "{system}"]
    local     True when the program answers on this computer (e.g. `ollama run`). The app cannot know what a command
              does with the text, so the default is False: confirms then say the text is sent to that command.
    env       extra environment variables for the command; timeout in seconds.
    No shell is involved and the command runs in an empty temporary folder, so it gets the prompt and nothing else.
    Token counts are estimates: a plain command does not report them."""
    name = "cli"

    def __init__(self, command, model="", local=False, timeout=600, env=None):
        self.command = split_command(command)
        if not self.command:
            raise LLMError('The cli provider needs a command, e.g. "command": ["opencode", "run", "-m", "{model}", "{prompt}"].')
        self.exe = shutil.which(self.command[0]) or (self.command[0] if Path(self.command[0]).is_file() else "")
        if not self.exe:
            raise LLMError(f"The command `{self.command[0]}` was not found. Install it or give its full path.")
        self.program = Path(self.command[0]).name
        self.model, self.remote, self.timeout = model or "", not local, float(timeout or 600)
        self.env = {str(k): str(v) for k, v in (env or {}).items()}
        self.base_url = self.program
        self.who = f"the {self.program} command"

    @property
    def destination(self):
        return f"the {self.program} command" + (f", model {self.model}" if self.model else "") + ("" if self.remote else " [local, text stays on this machine]")

    def complete(self, system, prompt, max_tokens):
        as_arg = any("{prompt}" in a for a in self.command[1:])
        if not any("{system}" in a for a in self.command[1:]) and system:
            prompt = f"{system}\n\n{prompt}"
        if as_arg and len(prompt.encode("utf-8")) > ARG_MAX:
            raise LLMError(f"The prompt is too long to pass to {self.program} as an argument. Remove {{prompt}} from the command so it is sent on stdin.")
        fill = {"{model}": self.model, "{system}": system or "", "{prompt}": prompt}
        cmd = [self.exe] + [re.sub(r"\{(?:model|system|prompt)\}", lambda m: fill[m.group()], a) for a in self.command[1:]]
        with tempfile.TemporaryDirectory() as cwd:
            try:
                p = subprocess.run(cmd, input=None if as_arg else prompt, stdin=subprocess.DEVNULL if as_arg else None, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=self.timeout, cwd=cwd, env={**os.environ, **self.env})
            except subprocess.TimeoutExpired:
                raise LLMError(f"{self.program} timed out after {self.timeout:.0f}s")
            except OSError as e:
                raise LLMError(f"could not run {self.program}: {e}")
        text = ANSI.sub("", p.stdout or "").strip()
        if p.returncode:
            raise LLMError(f"{self.program} failed (exit {p.returncode}): {ANSI.sub('', p.stderr or p.stdout or '').strip()[-300:]}")
        if not text:
            raise LLMError(f"{self.program} printed no answer" + (f": {p.stderr.strip()[-200:]}" if (p.stderr or "").strip() else ""))
        return Result(text, estimate_tokens(prompt), estimate_tokens(text))


class CachedProvider(Provider):
    """Wraps a provider with an on-disk answer cache; everything else is passed through."""

    def __init__(self, inner, cache_dir):
        self.inner, self.cache_dir = inner, Path(cache_dir)
        self.name, self.model, self.remote, self.base_url, self.who = inner.name, inner.model, inner.remote, inner.base_url, inner.who

    @property
    def where(self):
        return self.inner.where

    @property
    def destination(self):
        return self.inner.destination

    @property
    def limits(self):
        return self.inner.limits

    def probe(self):
        return self.inner.probe()

    def limit_problem(self):
        return self.inner.limit_problem()

    def runnable_probe(self):
        return self.inner.runnable_probe()

    def complete(self, system, prompt, max_tokens):
        h = hashlib.sha256(json.dumps([self.name, self.model, system, prompt, max_tokens]).encode()).hexdigest()
        f = self.cache_dir / f"{h}.json"
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                return Result(d["text"], d.get("input_tokens", 0), d.get("output_tokens", 0), False, True)
            except (OSError, ValueError, KeyError):
                pass
        res = self.inner.complete(system, prompt, max_tokens)
        if not res.truncated:                       # never cache a cut-off answer
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps({"text": res.text, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens}),
                         encoding="utf-8")
        return res


def _read_key(llm_cfg, key_dir, env_names):
    for n in env_names:
        if os.environ.get(n):
            return os.environ[n].strip()
    kf = llm_cfg.get("key_file")
    if kf:
        p = Path(kf).expanduser()
        p = p if p.is_absolute() else Path(key_dir) / p
        if p.exists():
            return p.read_text().strip()
    return ""


def diagnose(llm_cfg, key_dir):
    """Lines saying what the LLM set-up can find: API key (where from), the claude command and whether it is logged in."""
    llm_cfg = llm_cfg or {}
    lines = []
    env = next((n for n in ("ANTHROPIC_API_KEY", "LLM_API_KEY") if os.environ.get(n)), None)
    kf = llm_cfg.get("key_file")
    kp = (Path(kf).expanduser() if Path(kf or ".").is_absolute() else Path(key_dir) / kf) if kf else None
    if env:
        lines.append(f"API key: found in ${env} (used before the Claude login)")
    elif kp and kp.is_file() and kp.read_text().strip():
        lines.append(f"API key: found in {kp} (used before the Claude login)")
    else:
        lines.append("API key: none (fine: the Claude login is used instead)" + (f"; looked in ${'/$'.join(('ANTHROPIC_API_KEY', 'LLM_API_KEY'))} and {kp}" if kp else ""))
    exe = llm_cfg.get("executable") or find_claude_cli()
    if not exe:
        lines.append("claude command: NOT FOUND. Install Claude Code, or set llm.executable to its full path (`which claude` in Terminal).")
        return lines
    lines.append(f"claude command: {exe}")
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        a = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=20)
        info = json.loads(a.stdout) if a.stdout.strip().startswith("{") else {}
        lines.append(f"  version: {v or '?'}; logged in: {'yes (' + str(info.get('authMethod', '')) + ')' if info.get('loggedIn') else 'NO: run `claude` in Terminal and log in' if info else 'unknown'}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        lines.append(f"  could not query it: {e}")
    return lines


TASKS = ("notes", "guide", "audio_script", "chat", "ask")
PROVIDERS = ("auto", "claude", "claude_cli", "openai_compat", "cli")
OWN_KEYS = ("model", "base_url", "command", "local", "key_file", "key_env", "executable", "env", "timeout")     # belong to one provider


def task_config(llm_cfg, task=None):
    """The settings one task runs with: `llm.tasks.<task>` laid over the top-level `llm` keys.

    An entry that names a provider replaces the provider-specific keys (model, base_url, command, ...) as a set, so a
    local model for one task never inherits the default's Claude model name; an entry without a provider (just another
    model, say) changes only the keys it has. A task without an entry, or an unknown task, gets the default."""
    llm_cfg = llm_cfg or {}
    base = {k: v for k, v in llm_cfg.items() if k != "tasks"}
    over = (llm_cfg.get("tasks") or {}).get(task) if task else None
    over = {k: v for k, v in (over or {}).items() if v not in (None, "", [])} if isinstance(over, dict) else {}
    if over.get("provider"):
        base = {k: v for k, v in base.items() if k not in OWN_KEYS}
    return {**base, **over}


def make_provider(llm_cfg, key_dir, cache_dir=None, limits=None, task=None):
    """Build the provider for a task (None = the default) from config key `llm`. Keys come from the environment or a
    git-ignored key file, never from config (`key_env` names another environment variable, `key_file` another file).
    provider: "auto" (default) = API key if there is one, else the signed-in `claude` command.
    "claude" with no key falls back to the CLI too. Model defaults to Haiku (cheap); "haiku"/"sonnet"/"opus" are aliases."""
    cfg = task_config(llm_cfg, task)
    kind = cfg.get("provider") or "auto"
    model = cfg.get("model") or "haiku"
    own = (cfg["key_env"],) if cfg.get("key_env") else ()
    if kind in ("auto", "claude"):
        key = _read_key(cfg, key_dir, own + ("ANTHROPIC_API_KEY", "LLM_API_KEY"))
        if key:
            p = ClaudeProvider(ALIASES.get(model, model), key, cfg.get("base_url") or "https://api.anthropic.com")
        else:
            p = ClaudeCLIProvider(model, cfg.get("executable"), stop_at=float(cfg.get("stop_at", 0.8)), limits=limits)
    elif kind == "claude_cli":
        p = ClaudeCLIProvider(model, cfg.get("executable"), stop_at=float(cfg.get("stop_at", 0.8)), limits=limits)
    elif kind == "openai_compat":
        p = OpenAICompatProvider(cfg.get("model"), cfg.get("base_url"), _read_key(cfg, key_dir, own + ("OPENAI_API_KEY", "LLM_API_KEY")),
                                 local=cfg.get("local") if isinstance(cfg.get("local"), bool) else None)
    elif kind == "cli":
        p = CLIProvider(cfg.get("command"), cfg.get("model") or "", local=bool(cfg.get("local")), timeout=cfg.get("timeout") or 600, env=cfg.get("env"))
    else:
        raise LLMError(f'Unknown provider "{kind}". Use one of: {", ".join(PROVIDERS)} (see README, AI notes).')
    return CachedProvider(p, cache_dir) if cache_dir else p
