"""First-run setup from the app: where the Moodle site and study folder are, getting the Moodle token, and an optional model API key."""
from __future__ import annotations

import os
from pathlib import Path

from .. import config
from ..llm import providers
from ..moodle import auth
from ..moodle.client import Moodle


class Setup:
    KEY_FILE = ".llm_key"

    def __init__(self, ctx, lock):
        self.x, self.lock = ctx, lock
        self._pending = None                     # (site, passport) of the login link that was handed out

    def _has_token(self):
        s = self.x.settings
        return bool(os.environ.get("MOODLE_TOKEN")) or s.token_path.is_file()

    def status(self):
        s = self.x.settings
        site, root_ok = bool(s.site_url), bool(s.dest_root and Path(s.dest_root).is_dir())
        key = providers._read_key(s.llm, s.home, ("ANTHROPIC_API_KEY", "LLM_API_KEY"))
        return {"site_url": s.site_url, "dest_root": str(s.dest_root or ""), "root_ok": root_ok, "site": site, "token": self._has_token(),
                "connected": self.x.moodle is not None, "claude_login": bool(providers.find_claude_cli()), "api_key": bool(key),
                "provider": s.llm.get("provider") or "auto", "complete": site and root_ok and self._has_token(), "home": str(s.home)}

    def _live(self):
        s = self.x.settings
        self.x.site_url = s.site_url
        if s.site_url and self._has_token():
            try:
                self.x.moodle = Moodle(s.site_url, config.read_token(s))
            except config.ConfigError:
                pass

    def save_places(self, site_url=None, dest_root=None):
        changes = {}
        if site_url is not None:
            changes["site_url"] = site_url
        if dest_root is not None:
            changes["dest_root"] = dest_root
        if changes:
            config.update(self.x.settings, changes)
            self._live()
        return self.status()

    def begin(self):
        """The Moodle sign-in link. After signing in, Moodle redirects to an address starting moodlemobile://token=…, which is pasted back."""
        base = self.x.settings.require_site()
        url, passport = auth.begin(base)
        self._pending = (base, passport)
        return {"url": url}

    def finish(self, pasted):
        if not self._pending:
            raise ValueError("Press 'Open Moodle sign-in' first, then paste the address Moodle sends you to.")
        base, passport = self._pending
        try:
            token, _ = auth.parse(base, passport, pasted or "")
        except auth.HandshakeError as e:
            raise ValueError(str(e))
        m = Moodle(base, token)
        try:
            info = m.call("core_webservice_get_site_info")
        except Exception as e:
            raise ValueError(f"Moodle did not accept that token: {e}")
        config.save_token(self.x.settings, token)
        self.x.moodle = m
        self._pending = None
        return {**self.status(), "user": (info or {}).get("fullname", "")}

    def save_api_key(self, key):
        key = (key or "").strip()
        if len(key) < 20 or any(c.isspace() for c in key):
            raise ValueError("That does not look like an API key.")
        config.write_private(self.x.settings.home / self.KEY_FILE, key + "\n")
        config.update(self.x.settings, {"llm.provider": "claude"})
        return self.status()

    def remove_api_key(self):
        p = self.x.settings.home / self.KEY_FILE
        if p.exists():
            p.unlink()
        config.update(self.x.settings, {"llm.provider": ""})
        return self.status()
