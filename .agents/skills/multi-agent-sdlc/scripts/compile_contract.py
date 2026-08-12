#!/usr/bin/env python3
"""Compile and precheck a simple execution contract.

Write paths come from approved scope plus deterministic impact expansion:
acceptance-mentioned tests, G-plan-mentioned files, and tests that reference
changed public symbols. If G needs a file outside this contract, it must stop
and report CONTRACT_UPDATE_REQUIRED instead of editing first.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
from pathlib import Path


def intersects(write_paths: list[str], forbidden_paths: list[str]) -> list[str]:
    conflicts = []
    for write in write_paths:
        for forbidden in forbidden_paths:
            if fnmatch.fnmatch(write, forbidden) or fnmatch.fnmatch(forbidden, write):
                conflicts.append(f"{write} <-> {forbidden}")
    return conflicts


def yaml_list(items: list[str], indent: int = 2) -> str:
    pad = " " * indent
    return "\n".join(f'{pad}- "{item}"' for item in items) if items else f"{pad}[]"


def load_list(raw: str | None, path: str | None) -> list[str]:
    if path:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    elif raw:
        value = json.loads(raw)
    else:
        value = []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("paths must be a JSON array of strings")
    return value


def normalize_path(raw: str) -> str:
    return raw.replace("\\", "/").strip().strip("`'\"").rstrip(".,;:)")


def mentioned_paths(text: str) -> list[str]:
    patterns = [
        r"(?:src|tests|scripts|doc|alembic|\.agentic-sdlc|\.agents)/[^\s,;:)，。；）]+",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            path = normalize_path(match)
            if path and path not in found:
                found.append(path)
    return found


def read_optional(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8-sig")


def rg_files(pattern: str, root: str) -> list[str]:
    proc = subprocess.run(["rg", "-l", pattern, "tests"], cwd=root, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        return []
    return [normalize_path(line) for line in proc.stdout.splitlines() if line.strip()]


def basename_symbols(paths: list[str]) -> list[str]:
    symbols: list[str] = []
    for path in paths:
        name = Path(path).stem
        if name and name not in symbols:
            symbols.append(name)
    return symbols


def expand_test_paths(write_paths: list[str], acceptance_text: str, plan_text: str, repo_root: str) -> list[str]:
    expanded = list(dict.fromkeys(normalize_path(p) for p in write_paths))
    for path in mentioned_paths(acceptance_text + "\n" + plan_text):
        if path.startswith("tests/") and path not in expanded:
            expanded.append(path)
    lower_text = (acceptance_text + "\n" + plan_text).lower()
    cluster_patterns = []
    if any(word in lower_text for word in ("gate", "blocker", "second-layer", "second_layer")):
        cluster_patterns.extend(["tests/test_*gate*.py", "tests/test_*workflow*.py"])
    if "pipeline" in lower_text or "workflow" in lower_text:
        cluster_patterns.append("tests/test_*workflow*.py")
    if "trace" in lower_text:
        cluster_patterns.append("tests/test_*trace*.py")
    for pattern in cluster_patterns:
        for path in sorted(Path(repo_root).glob(pattern)):
            rel = normalize_path(str(path.relative_to(repo_root)))
            if rel not in expanded:
                expanded.append(rel)
    for symbol in basename_symbols(expanded):
        for rel in rg_files(symbol, repo_root):
            if rel.startswith("tests/") and rel not in expanded:
                expanded.append(rel)
    return expanded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--write-paths-json")
    parser.add_argument("--write-paths-file")
    parser.add_argument("--forbidden-paths-json")
    parser.add_argument("--forbidden-paths-file")
    parser.add_argument("--acceptance-file")
    parser.add_argument("--generator-plan-file")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--expand-tests", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_paths = load_list(args.write_paths_json, args.write_paths_file)
    forbidden_paths = load_list(args.forbidden_paths_json, args.forbidden_paths_file)
    if args.expand_tests:
        write_paths = expand_test_paths(
            write_paths,
            read_optional(args.acceptance_file),
            read_optional(args.generator_plan_file),
            args.repo_root,
        )
    conflicts = intersects(write_paths, forbidden_paths)
    if conflicts:
        for conflict in conflicts:
            print(f"conflict: {conflict}")
        return 1
    text = (
        f"version: 1\nrole: {args.role}\ntask_id: {args.task_id}\n"
        f"write_paths:\n{yaml_list(write_paths)}\n"
        f"forbidden_paths:\n{yaml_list(forbidden_paths)}\n"
    )
    Path(args.output).write_text(text, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
