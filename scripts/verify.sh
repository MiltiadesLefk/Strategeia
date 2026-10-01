#!/usr/bin/env sh
# Thin wrapper: run scripts/verify.py with the backend venv's Python.
# Same flags as verify.py (e.g. `scripts/verify.sh --build --ui`).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$ROOT/backend/.venv/Scripts/python.exe" ]; then
    PY="$ROOT/backend/.venv/Scripts/python.exe"
elif [ -x "$ROOT/backend/.venv/bin/python" ]; then
    PY="$ROOT/backend/.venv/bin/python"
else
    PY="$(command -v python3 || command -v python)"
fi
exec "$PY" "$ROOT/scripts/verify.py" "$@"
