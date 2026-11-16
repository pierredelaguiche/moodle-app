#!/usr/bin/env python3
"""Start moodle-app on macOS, Windows or Linux.

    python3 start.py [serve options]      macOS / Linux
    py start.py [serve options]           Windows (PowerShell or cmd)

Creates a private virtual environment (.venv) on the first run, installs the app into it, then runs `moodle-app serve`.
It calls `python -m moodle_app`, so it does not depend on the `moodle-app` command being on your PATH
(on Windows the pip scripts folder usually is not). Any arguments are passed to `serve`, e.g. `--port 9000 --open none`.
"""
import hashlib
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
STAMP = VENV / ".installed"


def fail(msg):
    sys.exit(f"Error: {msg}")


def wanted_stamp():
    return hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()


def ensure_installed():
    if sys.version_info < (3, 9):
        fail("Python 3.9 or newer is required.")
    if not PY.exists():
        print("→ Creating virtual environment (.venv)")
        venv.EnvBuilder(with_pip=True).create(VENV)
    if STAMP.exists() and STAMP.read_text() == wanted_stamp():
        return
    print("→ Installing moodle-app")
    r = subprocess.run([str(PY), "-m", "pip", "install", "-e", f"{ROOT}[pdf]"])
    if r.returncode:                                  # pymupdf has no wheel everywhere: the app works without it, minus PDF reading
        print("→ Retrying without PDF support (pymupdf could not be installed)")
        r = subprocess.run([str(PY), "-m", "pip", "install", "-e", str(ROOT)])
        if r.returncode:
            fail("installation failed.")
    STAMP.write_text(wanted_stamp())


def main():
    ensure_installed()
    try:
        sys.exit(subprocess.call([str(PY), "-m", "moodle_app", "serve", *sys.argv[1:]]))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
