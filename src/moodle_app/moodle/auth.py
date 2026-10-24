"""Token handshake through Moodle's browser launch flow (works with SSO). The paste step is manual by design."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import urllib.parse


class HandshakeError(Exception):
    pass


def begin(base):
    """Returns (url_to_open_in_browser, passport)."""
    base = base.rstrip("/")
    passport = secrets.token_hex(6)
    return f"{base}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport={passport}", passport


def parse(base, passport, pasted):
    """From the pasted moodlemobile://token=... address. Returns (token, handshake_matched)."""
    m = re.search(r"token=([^\s&]+)", pasted.strip())
    b = urllib.parse.unquote(m.group(1) if m else pasted.strip())
    try:
        decoded = base64.b64decode(b + "=" * (-len(b) % 4)).decode()
    except Exception:
        raise HandshakeError("Could not decode that. Paste the full moodlemobile://token=... address.")
    parts = decoded.split(":::")
    if len(parts) < 2 or not parts[1]:
        raise HandshakeError("Unexpected format: no token found in that string.")
    return parts[1], parts[0] == hashlib.md5((base.rstrip("/") + passport).encode()).hexdigest()
