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
    return ctx.settings_repo.get(KEY)


def save_limits(ctx, limits):
    if limits:
        with ctx.conn:
            ctx.settings_repo.set(KEY, limits)


def stale(limits, now=None):
    return not limits or (now or time.time()) - limits.get("seen", 0) > STALE


def app_tokens(ctx, since):
    tot = 0
    for r in ctx.conn.execute("SELECT summary FROM runs WHERE kind='notes' AND started>=? AND summary IS NOT NULL", (since,)):
        try:
            d = json.loads(r["summary"])
            tot += int(d.get("in", 0)) + int(d.get("out", 0))
        except (ValueError, TypeError):
            pass
    return tot


def line(ctx, now=None):
    now = now or ctx.clock()
    return f"{limits_line(load_limits(ctx), now)}  This app sent {app_tokens(ctx, now - 7 * 86400):,} tokens in the last 7 days."
