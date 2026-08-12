#!/usr/bin/env python3
"""Parse minimal G/E status headers into status.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED = {
    "generator": ["STATUS", "NEXT", "BLOCKING"],
    "evaluator": ["DECISION", "BLOCKING_COUNT", "REQUIRES_USER_DECISION"],
}

ALLOWED = {
    "generator": {
        "STATUS": {"completed", "blocked"},
        "NEXT": {"evaluator", "planner"},
        "BLOCKING": {"true", "false"},
    },
    "evaluator": {
        "DECISION": {"accept", "needs_revision", "blocked"},
        "REQUIRES_USER_DECISION": {"true", "false"},
    },
}


def parse_header(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines()[:20]:
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().upper()
        if key and key.replace("_", "").isalpha():
            fields[key] = value.strip()
    return fields


def build_status(role: str, text: str) -> dict[str, object]:
    fields = parse_header(text)
    missing = [key for key in REQUIRED[role] if key not in fields]
    invalid_values: dict[str, str] = {}
    for key, allowed_values in ALLOWED.get(role, {}).items():
        value = fields.get(key)
        if value is not None and value.lower() not in allowed_values:
            invalid_values[key] = value
    if role == "evaluator" and "BLOCKING_COUNT" in fields:
        try:
            int(fields["BLOCKING_COUNT"])
        except ValueError:
            invalid_values["BLOCKING_COUNT"] = fields["BLOCKING_COUNT"]
    output_protocol = "invalid_output" if missing or invalid_values else "valid"
    status: dict[str, object] = {
        "role": role,
        "output_protocol": output_protocol,
        "missing_fields": missing,
        "invalid_values": invalid_values,
        "fields": fields,
    }
    if role == "generator":
        status["status"] = fields.get("STATUS")
        status["next"] = fields.get("NEXT")
        status["blocking"] = fields.get("BLOCKING", "").lower() == "true"
    if role == "evaluator":
        status["decision"] = fields.get("DECISION", "blocked" if output_protocol != "valid" else None)
        status["blocking_count"] = int(fields["BLOCKING_COUNT"]) if fields.get("BLOCKING_COUNT", "").isdigit() else None
        status["requires_user_decision"] = fields.get("REQUIRES_USER_DECISION", "").lower() == "true"
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True, choices=["generator", "evaluator"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    text = Path(args.input).read_text(encoding="utf-8-sig")
    status = build_status(args.role, text)
    Path(args.output).write_text(json.dumps(status, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(status["output_protocol"])
    return 1 if status["output_protocol"] != "valid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
