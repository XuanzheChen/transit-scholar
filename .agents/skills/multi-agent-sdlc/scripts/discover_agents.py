#!/usr/bin/env python3
"""Discover locally callable agent executors."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone

DEFAULT_CANDIDATES = ["zcode", "opencode", "opencode-cli", "codex", "claude", "gemini", "aider"]

SUGGESTED = {
    "zcode": ["zcode", "{prompt_file}"],
    "opencode": ["opencode", "run", "--", "{prompt_file}"],
    "opencode-cli": ["opencode", "run", "--", "{prompt_file}"],
    "codex": ["codex", "run", "--prompt-file", "{prompt_file}"],
    "claude": ["claude", "--print", "--file", "{prompt_file}"],
    "gemini": ["gemini", "--prompt-file", "{prompt_file}"],
    "aider": ["aider", "--message-file", "{prompt_file}"],
}


def version_for(command: str) -> str | None:
    for flag in ("--version", "version", "-v"):
        try:
            proc = subprocess.run([command, flag], capture_output=True, text=True, timeout=5, check=False)
        except Exception:
            continue
        out = (proc.stdout or proc.stderr or "").strip()
        if out:
            return out.splitlines()[0][:200]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", nargs="*", default=DEFAULT_CANDIDATES)
    args = parser.parse_args()
    agents = []
    for name in args.candidates:
        path = shutil.which(name)
        agents.append({
            "name": name,
            "available": path is not None,
            "path": path,
            "version": version_for(name) if path else None,
            "suggested_cli_invocation": SUGGESTED.get(name, [name, "{prompt_file}"]),
        })
    print(json.dumps({"discovered_at": datetime.now(timezone.utc).isoformat(), "agents": agents}, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
