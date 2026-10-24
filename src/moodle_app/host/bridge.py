from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


class HostBridge:
    """Default behaviour: move to a trash folder we control, so nothing is ever unlinked outright."""
    name = "folder"

    def __init__(self, trash_dir: Path):
        self.trash_dir = Path(trash_dir)

    def trash(self, p: Path) -> str:
        """Move ONE regular file to the trash. Returns a description of where it went. Raises RuntimeError, deleting nothing."""
        return self._to_folder(p, self.trash_dir)

    def open(self, p: Path) -> None:
        """Open a document with the program your computer uses for it. Raises RuntimeError when that is not possible."""
        cmd = {"darwin": ["open", "--", str(p)]}.get(sys.platform) or (["xdg-open", str(p)] if sys.platform.startswith("linux") else None)
        if cmd is None:
            raise RuntimeError("opening files with another program is not supported on this system")
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise RuntimeError(f"could not open it: {e}")

    @staticmethod
    def _to_folder(p: Path, trash: Path, err: str = "") -> str:
        try:
            trash.mkdir(parents=True, exist_ok=True)
            dest, n = trash / p.name, 0
            while dest.exists():
                n += 1
                dest = trash / f"{p.stem} {n}{p.suffix}"
            shutil.move(str(p), str(dest))
            return str(trash)
        except Exception as e:
            raise RuntimeError(f"could not move to Trash ({err or e}). Nothing was deleted.")


class MacBridge(HostBridge):
    name = "mac"

    def trash(self, p: Path) -> str:
        script = 'tell application "Finder" to delete (POSIX file %s)' % json.dumps(str(p), ensure_ascii=False)
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and not p.exists():
                return "the Trash"                                # Finder keeps 'Put Back' working
            err = (r.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            err = str(e)
        return self._to_folder(p, Path.home() / ".Trash", err)


class LinuxBridge(HostBridge):
    name = "linux"

    def trash(self, p: Path) -> str:
        return self._to_folder(p, Path.home() / ".local" / "share" / "Trash" / "files")


def get_bridge(home: Path) -> HostBridge:
    if sys.platform == "darwin":
        return MacBridge(home / "trash")
    if sys.platform.startswith("linux"):
        return LinuxBridge(home / "trash")
    return HostBridge(home / "trash")
