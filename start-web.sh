#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"

if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/start_web.py "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python scripts/start_web.py "$@"
fi

echo "[TransitScholar] ERROR: Python 3.11+ was not found." >&2
echo "Install Python 3.11 or newer, then run start-web.sh again." >&2
exit 1
