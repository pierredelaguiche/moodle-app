"""What notes use: the app's own token count, and (the real thing) your Claude plan's usage windows as Claude reports them.

The plan limits come from Claude Code itself (a `rate_limit_event` with the 5-hour and 7-day utilisation), so they are the same
numbers as `/status`. They are refreshed by every call (and by `llm-check` / a stale plan check) and remembered here."""
from __future__ import annotations

import json
import time

from ..llm.providers import limits_line

KEY = "llm_limits"
STALE = 15 * 60


def load_limits(ctx):
    """The plan limits last reported by Claude (see `llm.providers.parse_limits`), or None when never seen."""
    return ctx.settings_repo.get(KEY)


def save_limits(ctx, limits):
    """Remember the limits a provider saw. Empty values are ignored, so a provider without plan limits never erases them."""
    if limits:
        with ctx.conn:
            ctx.settings_repo.set(KEY, limits)


def stale(limits, now=None):
    """True when the limits are unknown or older than STALE seconds: time to ask Claude again before a plan."""
    return not limits or (now or time.time()) - limits.get("seen", 0) > STALE


def app_tokens(ctx, since):
    """Tokens (in + out) of this app's note runs started at or after `since`."""
    tot = 0
    for r in ctx.conn.execute("SELECT summary FROM runs WHERE kind='notes' AND started>=? AND summary IS NOT NULL", (since,)):
        try:
            d = json.loads(r["summary"])
            tot += int(d.get("in", 0)) + int(d.get("out", 0))
        except (ValueError, TypeError):
            pass
    return tot


def _window(limits, name, now):
    w = ((limits or {}).get("windows") or {}).get(name)
    if not w:
        return None
    gone = bool(w.get("resets")) and w["resets"] <= now          # a window that has reset counts as empty, as in limits_line
    return {"util": 0.0 if gone else float(w.get("util", 0)), "resets": None if gone else (w.get("resets") or None)}


def summary(ctx, now=None):
    """Usage as data, for the page to format in one place (the chip on the AI tab).

    Returns {five_hour, seven_day: {util 0..1, resets ts|None} | None, overage: 'allowed'|'rejected'|None,
    seen: ts|None, app_tokens_7d: int}. Windows are None until Claude has reported them (API keys and local
    models never do). Data, not a sentence, so no screen can show the same sentence twice."""
    now = now or ctx.clock()
    limits = load_limits(ctx)
    return {"five_hour": _window(limits, "five_hour", now), "seven_day": _window(limits, "seven_day", now),
            "overage": (limits or {}).get("overage"), "seen": (limits or {}).get("seen"),
            "app_tokens_7d": app_tokens(ctx, now - 7 * 86400)}


def cli_line(ctx, now=None):
    """The same facts as one sentence, for terminal output only (`status`, `llm-check`, `notes`)."""
    now = now or ctx.clock()
    return f"{limits_line(load_limits(ctx), now)}  This app sent {app_tokens(ctx, now - 7 * 86400):,} tokens in the last 7 days."
