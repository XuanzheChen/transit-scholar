#!/usr/bin/env python3
"""Add a repository-local task directory to .gitignore."""

from __future__ import annotations

import argparse
from pathlib import Path

BEGIN = "# BEGIN multi-agent-sdlc"
END = "# END multi-agent-sdlc"


def normalize_entry(tasks_root: str) -> str:
    entry = tasks_root.replace("\\", "/").strip()
    if not entry:
        raise ValueError("tasks_root cannot be empty")
    if ":" in entry or entry.startswith("/"):
        raise ValueError("absolute paths should not be added to .gitignore")
    return f"/{entry.strip('/')}/"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--tasks-root", required=True)
    args = parser.parse_args()
    entry = normalize_entry(args.tasks_root)
    path = Path(args.repo_root) / ".gitignore"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if entry in lines:
        print("already-present")
        return 0
    try:
        end = lines.index(END)
    except ValueError:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend([BEGIN, entry, END])
    else:
        lines.insert(end, entry)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print("updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
