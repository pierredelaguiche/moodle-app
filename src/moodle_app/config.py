"""Settings and the data directory.

Everything the app owns lives in one directory (override with MOODLE_APP_HOME):
    config.json        what you edit (site, study folder, keywords, llm, notes)
    token              the Moodle token (mode 0600)
    .llm_key           optional LLM key file (mode 0600)
    moodle_app.db      SQLite: everything the app learns (files, deadlines, announcements, history, notes manifest)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CATEGORIES = {
    ".":          ["handbook", "module guide", "module descriptor"],
    "Exams":      ["exam", "exams", "mock", "past paper", "past papers"],
    "Ethics":     ["ethics", "ethical", "consent", "information sheet", "participant"],
    "Workshops":  ["workshop", "lab", "practical", "tutorial", "seminar", "exercise", "worksheet", "problem sheet"],
    "Coursework": ["coursework", "assignment", "assessment", "dissertation", "proposal", "specification", "specs", "cw"],
    "Lectures":   ["lecture", "slides", "lec"],
    "Examples":   ["past project", "previous project", "example project", "exemplar", "past dissertation"],
}
DEFAULT_WEEK_RULE = {"threshold": 3, "categories": ["Workshops", "Lectures"]}
DEFAULT_SYNC = {"files": True, "deadlines": True, "announcements": True, "submission_status_days": 30, "min_interval_minutes": 15}
HIDE_DEFAULT = ["_moodle_sync", "__pycache__", "node_modules"]


class ConfigError(ValueError):
    pass


def default_home() -> Path:
    if os.environ.get("MOODLE_APP_HOME"):
        return Path(os.environ["MOODLE_APP_HOME"]).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "moodle-app"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "moodle-app"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "moodle-app"


@dataclass
class Settings:
    home: Path
    site_url: str = ""
    dest_root: "Path | None" = None
    exclude_course_ids: set = field(default_factory=set)
    keywords: dict = field(default_factory=dict)
    week_folders: dict = field(default_factory=lambda: dict(DEFAULT_WEEK_RULE))
    tree_hide: list = field(default_factory=lambda: list(HIDE_DEFAULT))
    llm: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    sync: dict = field(default_factory=lambda: dict(DEFAULT_SYNC))
    timezone: str = "Europe/Berlin"
    open_on_launch: str = "tab"            # tab | window | none (none: just print the address, for a bookmark)
    port: int = 8765                       # the web app's fixed port, so its address can be bookmarked

    @property
    def db_path(self) -> Path:
        return self.home / "moodle_app.db"

    @property
    def web_token_path(self) -> Path:
        return self.home / "web_token"

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def token_path(self) -> Path:
        return self.home / "token"

    @property
    def categories(self) -> dict:
        return {**DEFAULT_CATEGORIES, **self.keywords}

    def require_root(self) -> Path:
        if not self.dest_root:
            raise ConfigError('Set "dest_root" (the folder that holds one folder per module) in ' + str(self.config_path))
        if not self.dest_root.is_dir():
            raise ConfigError(f"dest_root does not exist: {self.dest_root}")
        return self.dest_root

    def require_site(self) -> str:
        if not self.site_url:
            raise ConfigError('Set "site_url" in ' + str(self.config_path))
        return self.site_url


def load(home: "Path | str | None" = None) -> Settings:
    home = Path(home).expanduser() if home else default_home()
    s = Settings(home=home)
    p = s.config_path
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ConfigError(f"{p} is not valid JSON: {e}")
        s.site_url = (raw.get("site_url") or "").rstrip("/")
        s.dest_root = Path(raw["dest_root"]).expanduser() if raw.get("dest_root") else None
        s.exclude_course_ids = {str(x) for x in raw.get("exclude_course_ids", [])}
        s.keywords = raw.get("keywords") or {}
        s.week_folders = {**DEFAULT_WEEK_RULE, **(raw.get("week_folders") or {})}
        s.tree_hide = raw.get("tree_hide", s.tree_hide)
        s.llm = raw.get("llm") or {}
        s.notes = raw.get("notes") or {}
        s.notes.setdefault("prompts_dir", str(home / "prompts"))          # edit prompts on the AI page; overrides live here
        s.sync = {**DEFAULT_SYNC, **(raw.get("sync") or {})}
        s.timezone = raw.get("timezone", s.timezone)
        s.port = int(raw.get("port", s.port))
        s.open_on_launch = str(raw.get("open_on_launch", s.open_on_launch)).lower()
        if s.open_on_launch not in ("tab", "window", "none"):
            s.open_on_launch = "tab"
    return s


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_token(s: Settings) -> str:
    tok = os.environ.get("MOODLE_TOKEN")
    if tok:
        return tok.strip()
    if s.token_path.exists():
        return s.token_path.read_text().strip()
    raise ConfigError(f"No Moodle token. Run `moodle-app token`, or set MOODLE_TOKEN. (Expected file: {s.token_path})")


def save_token(s: Settings, token: str) -> None:
    write_private(s.token_path, token.strip() + "\n")


# ---------------------------------------------------------------- settings editable from the app
# (key, label, kind, group, help, extra).  Keys are dotted paths into config.json; kinds: text path bool int float choice list
# extra: options / min / max / restart (True = takes effect the next time the app starts)
FIELDS = [
    ("open_on_launch", "When the app starts", "choice", "Launch", "Where the page opens. 'Nothing' only prints the address, for a bookmark.",
     {"options": [["tab", "Open in a new tab"], ["window", "Open in a new window"], ["none", "Open nothing"]]}),
    ("port", "Port", "int", "Launch", "Keeps the address the same, so you can bookmark it.", {"min": 1024, "max": 65535, "restart": True}),
    ("dest_root", "Study folder", "path", "Folders", "The folder that holds one folder per module.", {"restart": True}),
    ("site_url", "Moodle address", "text", "Folders", "For example https://moodle.example.ac.uk", {"restart": True}),
    ("timezone", "Time zone", "text", "Folders", "For example Europe/Zurich. Used for deadlines and the calendar.", {"restart": True}),
    ("exclude_course_ids", "Courses to ignore", "list", "Sync", "Moodle course ids, one per line or comma-separated.", {}),
    ("sync.files", "Download files", "bool", "Sync", "", {}),
    ("sync.deadlines", "Check deadlines", "bool", "Sync", "", {}),
    ("sync.announcements", "Check announcements", "bool", "Sync", "", {}),
    ("sync.min_interval_minutes", "Minimum minutes between syncs", "int", "Sync", "Clicking Sync sooner than this does nothing.", {"min": 0, "max": 1440}),
    ("sync.submission_status_days", "Submission status: days ahead", "int", "Sync", "How far ahead deadlines get their submission status checked.", {"min": 0, "max": 365}),
    ("week_folders.threshold", "Split into week folders from", "int", "Files", "A category with at least this many files gets week folders.", {"min": 1, "max": 50}),
    ("tree_hide", "Hide in Files", "list", "Files", "Folder names the Files page does not show. Names starting with a dot are always hidden.", {}),
    ("llm.provider", "Model provider", "choice", "AI", "'Auto' uses an API key if there is one, else the signed-in claude command.",
     {"options": [["", "Auto"], ["auto", "Auto (explicit)"], ["claude_cli", "Claude command (signed-in account)"], ["claude", "Anthropic API key"], ["openai_compat", "OpenAI-compatible server"]]}),
    ("llm.model", "Model", "text", "AI", "Short name such as haiku (small and cheap) or sonnet.", {}),
    ("llm.stop_at", "Stop at this share of your plan limit", "float", "AI", "0.8 = stop when 80% of the 5-hour or weekly limit is used.", {"min": 0.05, "max": 1}),
    ("notes.output_dir", "Notes folder name", "text", "AI", "Created inside each module.", {}),
]
_FIELD = {f[0]: f for f in FIELDS}


def _get(d, dotted, default=None):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return default
        d = d[part]
    return d


def _set(d, dotted, value):
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(d.get(part), dict):
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value


def _raw(s: Settings) -> dict:
    if not s.config_path.exists():
        return {}
    try:
        raw = json.loads(s.config_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ConfigError(f"{s.config_path} is not valid JSON: {e}")
    return raw if isinstance(raw, dict) else {}


DEFAULTS = {"open_on_launch": "tab", "port": 8765, "timezone": "Europe/Berlin", "sync.files": True, "sync.deadlines": True,
            "sync.announcements": True, "sync.min_interval_minutes": 15, "sync.submission_status_days": 30,
            "week_folders.threshold": DEFAULT_WEEK_RULE["threshold"], "tree_hide": HIDE_DEFAULT, "llm.stop_at": 0.8, "notes.output_dir": "Notes"}


def editable(s: Settings) -> dict:
    """What the Settings page shows: every field with its current value (file value, else the default in use)."""
    raw = _raw(s)
    out = []
    for key, label, kind, group, help_, extra in FIELDS:
        v = _get(raw, key, DEFAULTS.get(key, "" if kind not in ("bool", "list") else ([] if kind == "list" else False)))
        out.append({"key": key, "label": label, "kind": kind, "group": group, "help": help_, "value": v,
                    "options": extra.get("options"), "min": extra.get("min"), "max": extra.get("max"), "restart": bool(extra.get("restart"))})
    return {"fields": out, "config_path": str(s.config_path), "home": str(s.home)}


def _clean(key, kind, extra, v):
    if kind == "bool":
        if not isinstance(v, bool):
            raise ValueError(f"{_FIELD[key][1]}: yes or no")
        return v
    if kind in ("int", "float"):
        try:
            n = int(v) if kind == "int" and float(v) == int(float(v)) else float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{_FIELD[key][1]}: needs a number")
        if (extra.get("min") is not None and n < extra["min"]) or (extra.get("max") is not None and n > extra["max"]):
            raise ValueError(f"{_FIELD[key][1]}: between {extra.get('min')} and {extra.get('max')}")
        return n
    if kind == "choice":
        if v not in [o[0] for o in extra["options"]]:
            raise ValueError(f"{_FIELD[key][1]}: not one of the choices")
        return v
    if kind == "list":
        items = v if isinstance(v, list) else str(v or "").replace(",", "\n").splitlines()
        return [str(x).strip() for x in items if str(x).strip()]
    v = str(v or "").strip()
    if key == "site_url":
        v = v.rstrip("/")
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("Moodle address: must start with https://")
    elif key == "timezone":
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError("Time zone: not a known name (try Europe/Zurich)")
    elif key == "dest_root":
        if v and not Path(v).expanduser().is_dir():
            raise ValueError("Study folder: that folder does not exist")
    elif key == "notes.output_dir":
        if not v or "/" in v or v.startswith("."):
            raise ValueError("Notes folder name: a plain folder name")
    return v


def update(s: Settings, changes: dict) -> list:
    """Validate, merge into config.json (other keys are kept as they are), reload `s` in place. Returns the keys that need a restart."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("nothing to save")
    unknown = [k for k in changes if k not in _FIELD]
    if unknown:
        raise ValueError("not a setting: " + ", ".join(unknown))
    raw = _raw(s)
    errors, restart = [], []
    for key, v in changes.items():
        _, _, kind, _, _, extra = _FIELD[key]
        try:
            clean = _clean(key, kind, extra, v)
        except ValueError as e:
            errors.append(str(e))
            continue
        if key in ("llm.provider", "llm.model", "dest_root", "site_url") and clean == "":
            _unset(raw, key)                       # empty = back to the default
        else:
            _set(raw, key, clean)
        if extra.get("restart"):
            restart.append(key)
    if errors:
        raise ValueError("; ".join(errors))
    write_private(s.config_path, json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    fresh = load(s.home)
    live = {k: getattr(fresh, k) for k in ("exclude_course_ids", "week_folders", "tree_hide", "llm", "notes", "sync", "open_on_launch")}
    for k, val in live.items():
        setattr(s, k, val)
    s.site_url, s.dest_root, s.timezone = fresh.site_url, fresh.dest_root, fresh.timezone
    if changes.get("llm.provider") == "claude" and not s.llm.get("key_file"):          # an API key saved from the app lives in the data folder
        raw = _raw(s)
        _set(raw, "llm.key_file", ".llm_key")
        write_private(s.config_path, json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
        s.llm = load(s.home).llm
    return restart


def _unset(d, dotted):
    parts = dotted.split(".")
    for part in parts[:-1]:
        d = d.get(part)
        if not isinstance(d, dict):
            return
    d.pop(parts[-1], None)


def ensure_defaults(s: Settings) -> None:
    """Make the launch options visible in an existing config.json (added once, never changed afterwards)."""
    if not s.config_path.exists():
        return
    try:
        raw = _raw(s)
    except ConfigError:
        return
    missing = {k: v for k, v in (("open_on_launch", s.open_on_launch), ("port", s.port)) if k not in raw}
    if missing:
        raw.update(missing)
        write_private(s.config_path, json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
