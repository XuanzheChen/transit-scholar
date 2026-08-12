#!/usr/bin/env python3
"""Minimal Git worktree guard helpers."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def ensure_git(cwd: str) -> int:
    proc = git(["rev-parse", "--is-inside-work-tree"], cwd)
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        print("invalid-git-worktree")
        return 1
    print("ok")
    return 0


def diff(cwd: str, output: str) -> int:
    proc = git(["diff", "--binary"], cwd)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(proc.stdout, encoding="utf-8")
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--repo-root", default=".")
    p_diff = sub.add_parser("diff")
    p_diff.add_argument("--repo-root", default=".")
    p_diff.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.cmd == "check":
        return ensure_git(args.repo_root)
    if args.cmd == "diff":
        return diff(args.repo_root, args.output)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
