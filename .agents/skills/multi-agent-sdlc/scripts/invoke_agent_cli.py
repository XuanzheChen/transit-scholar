#!/usr/bin/env python3
"""Invoke a configured external agent CLI and save raw output evidence.

The adapter is intentionally deterministic: it saves raw streams, extracts the
final Markdown response when the CLI exposes one, writes result.md verbatim, and
parses the stable status header into status.json. If parsing fails, it records
invalid_output and returns a non-zero exit code instead of asking Planner to
repair or reinterpret the worker output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from parse_status_header import build_status


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_command(raw: str | None, path: str | None) -> list[str]:
    if path:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    elif raw:
        value = json.loads(raw)
    else:
        raise ValueError("command JSON or command file required")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("command must be a JSON array of strings")
    return value


def substitute(command: list[str], values: dict[str, str]) -> list[str]:
    rendered = []
    for part in command:
        for key, value in values.items():
            part = part.replace("{" + key + "}", value)
        rendered.append(part)
    return rendered


def extract_response(stdout: str) -> tuple[str, str]:
    """Return (response_text, method) from raw stdout.

    Preferred format is a JSON object with a string ``response`` field, matching
    ZCode's ``--json`` output. If stdout is not JSON or has no response text,
    use raw stdout as the fallback result so status parsing remains mechanical.
    """
    text = stdout.strip()
    if not text:
        return "", "empty_stdout"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return stdout, "raw_stdout"
    if isinstance(payload, dict) and isinstance(payload.get("response"), str):
        response = payload["response"]
        if response.strip():
            return response, "json_response"
    return stdout, "json_without_response"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--role", required=True, choices=["generator", "evaluator"])
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--command-json")
    parser.add_argument("--command-file")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    template = load_command(args.command_json, args.command_file)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    command = substitute(template, {
        "prompt_file": str(Path(args.prompt_file)),
        "output_dir": str(out_dir),
        "task_id": args.task_id,
        "role": args.role,
    })
    started = utc()
    proc = subprocess.run(command, cwd=args.cwd, capture_output=True, text=True, timeout=args.timeout_seconds, check=False)
    completed = utc()
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    (out_dir / "stdout.log").write_text(stdout, encoding="utf-8")
    (out_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    (out_dir / "raw_stdout.json").write_text(stdout, encoding="utf-8")
    (out_dir / "raw_stderr.log").write_text(stderr, encoding="utf-8")
    result_text, extraction_method = extract_response(stdout)
    (out_dir / "result.md").write_text(result_text, encoding="utf-8")
    status = build_status(args.role, result_text)
    status["cli_exit_code"] = proc.returncode
    status["response_extraction_method"] = extraction_method
    if proc.returncode != 0:
        status["invocation_status"] = "cli_error"
    elif status["output_protocol"] != "valid":
        status["invocation_status"] = "invalid_output"
    else:
        status["invocation_status"] = "completed"
    (out_dir / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    evidence = {
        "provider": args.provider,
        "role": args.role,
        "command_template": template,
        "concrete_command": command,
        "exit_code": proc.returncode,
        "started_at": started,
        "completed_at": completed,
        "stdout_log": "stdout.log",
        "stderr_log": "stderr.log",
        "raw_stdout": "raw_stdout.json",
        "raw_stderr": "raw_stderr.log",
        "result_file": "result.md",
        "status_file": "status.json",
        "output_protocol": status["output_protocol"],
        "response_extraction_method": extraction_method,
        "simulated": False,
        "local_subagent_used": False,
    }
    (out_dir / "invocation.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(out_dir / "invocation.json")
    if proc.returncode != 0:
        return proc.returncode
    return 0 if status["output_protocol"] == "valid" else 44


if __name__ == "__main__":
    raise SystemExit(main())
