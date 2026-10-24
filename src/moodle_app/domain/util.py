from __future__ import annotations

import hashlib
import re
from pathlib import Path


def safe_name(name, fallback="untitled"):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
    return (name or fallback)[:120]


def norm(text):
    return re.sub(r"[_\-.\s]+", " ", (text or "").lower()).strip()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def rel_posix(path: Path, root: Path) -> str:
    return Path(path).relative_to(root).as_posix()
