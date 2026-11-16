#!/usr/bin/env bash
# Double-click launcher for macOS (Linux: run ./start.command). Windows: double-click start.bat.
cd "$(dirname "$0")" && exec python3 start.py "$@"
