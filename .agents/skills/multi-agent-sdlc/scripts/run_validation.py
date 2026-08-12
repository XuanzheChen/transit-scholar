#!/usr/bin/env python3
"""Run validation commands for E and save command evidence.

E should judge evidence, not depend on an interactive shell permission loop.
Runner executes approved commands with one stable environment and writes
stdout/stderr/exit code for each command.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_commands(path: str) -> list[dict[str, object]]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, list):
        raise ValueError("commands file must contain a JSON array")
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("cmd"), list):
            raise ValueError("each command must be an object with cmd list")
        if not all(isinstance(part, str) for part in item["cmd"]):
            raise ValueError("cmd must be a list of strings")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commands-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()

    commands = load_commands(args.commands_file)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    overall = 0
    for index, spec in enumerate(commands, start=1):
        name = str(spec.get("name") or f"command-{index}")
        cmd = [str(part) for part in spec["cmd"]]
        timeout = int(spec.get("timeout_seconds") or args.timeout_seconds)
        started = utc()
        proc = subprocess.run(cmd, cwd=args.cwd, capture_output=True, text=True, timeout=timeout, check=False)
        completed = utc()
        stem = f"{index:02d}-{name.replace(' ', '_')}"
        stdout_file = f"{stem}.stdout.log"
        stderr_file = f"{stem}.stderr.log"
        (out / stdout_file).write_text(proc.stdout or "", encoding="utf-8")
        (out / stderr_file).write_text(proc.stderr or "", encoding="utf-8")
        result = {
            "name": name,
            "cmd": cmd,
            "exit_code": proc.returncode,
            "started_at": started,
            "completed_at": completed,
            "stdout": stdout_file,
            "stderr": stderr_file,
        }
        results.append(result)
        if proc.returncode != 0:
            overall = proc.returncode if overall == 0 else overall
    summary = {
        "output_protocol": "valid",
        "overall_exit_code": overall,
        "commands": results,
    }
    (out / "validation-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(out / "validation-summary.json")
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
