"""Open, edit, create and save text files inside the study folder, safely.

Rules: paths are resolved and must stay inside dest_root; symlinks are refused; only known text suffixes are editable and
they must be valid UTF-8 (we never guess an encoding and corrupt a file); a save is refused when the file changed on disk
since it was opened (the client sends the hash it opened); the previous version is copied to <home>/backups first (last 10
per file kept, outside your study folder); the write is atomic (temp file in the same folder, fsync, rename).
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".py", ".tex", ".bib", ".c", ".h", ".cpp", ".hpp", ".java", ".js", ".ts", ".json", ".csv",
                 ".sh", ".sql", ".html", ".css", ".yml", ".yaml", ".toml", ".ini", ".r", ".rs", ".go", ".m", ".hs", ".log"}
RAW_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp"}
MAX_BYTES = 2_000_000
KEEP_BACKUPS = 10
LANG = {".md": "markdown", ".markdown": "markdown", ".py": "python", ".tex": "latex", ".c": "c", ".h": "c", ".json": "json", ".sh": "shell"}


class EditorError(ValueError):
    pass


class Conflict(EditorError):
    """The file changed on disk since it was opened."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Editor:
    def __init__(self, root: Path, home: Path):
        self.root, self.home = Path(root).resolve(), Path(home)

    # ---------- paths
    def resolve(self, rel, must_exist=True) -> Path:
        if not rel or "\0" in rel:
            raise EditorError("no file given")
        p = (self.root / rel)
        # refuse symlinks anywhere below the root (a link inside the folder could point out of it)
        cur = self.root
        for part in Path(rel).parts:
            cur = cur / part
            if cur.is_symlink():
                raise EditorError("symbolic links are not opened")
        p = p.resolve()
        if self.root not in p.parents:
            raise EditorError("path is outside the study folder")
        if must_exist and not p.is_file():
            raise EditorError("not a file")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    # ---------- read
    def read(self, rel):
        p = self.resolve(rel)
        suf = p.suffix.lower()
        st = p.stat()
        base = {"rel": self.rel(p), "name": p.name, "size": st.st_size, "mtime": st.st_mtime, "suffix": suf}
        if suf in RAW_TYPES:
            return {**base, "kind": "pdf" if suf == ".pdf" else "image", "editable": False}
        if suf not in TEXT_SUFFIXES:
            return {**base, "kind": "binary", "editable": False}
        if st.st_size > MAX_BYTES:
            return {**base, "kind": "toolarge", "editable": False}
        data = p.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {**base, "kind": "binary", "editable": False, "note": "not valid UTF-8, so it is not opened for editing"}
        eol = "crlf" if "\r\n" in text else "lf"
        return {**base, "kind": "text", "editable": True, "text": text.replace("\r\n", "\n"), "eol": eol, "sha": sha(data),
                "lang": LANG.get(suf, "text")}

    def raw(self, rel):
        p = self.resolve(rel)
        ctype = RAW_TYPES.get(p.suffix.lower())
        if not ctype:
            raise EditorError("this file type is not previewed")
        if p.stat().st_size > 60_000_000:
            raise EditorError("file too large to preview")
        return p.read_bytes(), ctype

    # ---------- write
    def _backup(self, p: Path, data: bytes):
        d = self.home / "backups" / sha(self.rel(p).encode())[:16]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{int(time.time())}-{time.monotonic_ns() % 10**6:06d}-{p.name}").write_bytes(data)
        old = sorted(d.iterdir())
        for f in old[:-KEEP_BACKUPS]:
            f.unlink(missing_ok=True)

    def _atomic_write(self, p: Path, data: bytes):
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if p.exists():
                os.chmod(tmp, p.stat().st_mode & 0o777)
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)

    def save(self, rel, text, base_sha, eol="lf", force=False):
        p = self.resolve(rel)
        if p.suffix.lower() not in TEXT_SUFFIXES:
            raise EditorError("this file type is not editable here")
        if not isinstance(text, str):
            raise EditorError("text must be a string")
        cur = p.read_bytes()
        if not force and sha(cur) != base_sha:
            raise Conflict("The file changed on disk since you opened it. Reload it, or save a copy under another name.")
        out = text.replace("\r\n", "\n")
        if eol == "crlf":
            out = out.replace("\n", "\r\n")
        data = out.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise EditorError("file would be larger than 2 MB")
        if data != cur:
            self._backup(p, cur)
            self._atomic_write(p, data)
        return {"sha": sha(data), "size": len(data), "changed": data != cur}

    def create(self, rel, text=""):
        if not rel or rel.endswith("/") or "\0" in rel:
            raise EditorError("give a file name")
        parts = Path(rel).parts
        if any(x in ("..", "") or x.startswith(".") for x in parts):
            raise EditorError("invalid path")
        if Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            raise EditorError("choose a text file name such as notes.md, .txt, .py or .tex")
        p = (self.root / rel)
        parent = p.parent
        # create missing folders one by one, never through a symlink
        cur = self.root
        for part in parts[:-1]:
            cur = cur / part
            if cur.is_symlink():
                raise EditorError("symbolic links are not used")
            if not cur.exists():
                cur.mkdir()
        parent = parent.resolve()
        if self.root != parent and self.root not in parent.parents:
            raise EditorError("path is outside the study folder")
        target = parent / p.name
        if target.exists() or target.is_symlink():
            raise EditorError("a file with that name already exists")
        data = text.encode("utf-8")
        self._atomic_write(target, data)
        return {"rel": self.rel(target), "sha": sha(data)}
