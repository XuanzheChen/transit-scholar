#!/usr/bin/env python3
"""Fail when project workflow config and the persisted init record drift."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


ROLES = ("generator", "evaluator")
IDENTITY_FIELDS = (
    "executor",
    "provider",
    "model",
    "reasoning_effort",
    "model_provider",
    "credential_source",
    "model_binding",
    "model_probe_file",
)


def _adapter(command: list[object]) -> str | None:
    for value in command:
        text = str(value).replace("\\", "/")
        if "/adapters/" in text and text.lower().endswith((".ps1", ".py")):
            return text.lower()
    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=".agentic-sdlc/config.yaml")
    parser.add_argument("--init", dest="init_path", default=".agentic-sdlc/init.json")
    args = parser.parse_args()

    config_path = Path(args.config)
    init_path = Path(args.init_path)
    failures: list[str] = []
    try:
        config = _load_yaml(config_path)
        init = _load_json(init_path)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"init-consistency: invalid input: {exc}")
        return 1

    tasks_root = config.get("storage", {}).get("tasks_root")
    if tasks_root != init.get("tasks_root"):
        failures.append(
            f"tasks_root mismatch: config={tasks_root!r}, init={init.get('tasks_root')!r}"
        )

    init_config = config.get("init", {})
    for flag in (
        "require_user_executor_selection",
        "require_user_model_selection",
        "require_user_reasoning_effort_selection",
    ):
        if init_config.get(flag) is not True:
            failures.append(f"config init gate is not enabled: {flag}")

    agents = config.get("agents", {})
    for role in ROLES:
        configured = agents.get(role, {})
        identity = configured.get("identity", {})
        recorded = init.get(role, {})
        for field in IDENTITY_FIELDS:
            if identity.get(field) != recorded.get(field):
                failures.append(
                    f"{role}.{field} mismatch: "
                    f"config={identity.get(field)!r}, init={recorded.get(field)!r}"
                )

        configured_command = configured.get("invocation", {}).get("command", [])
        recorded_command = recorded.get("cli_invocation", [])
        if _adapter(configured_command) != _adapter(recorded_command):
            failures.append(
                f"{role}.adapter mismatch: "
                f"config={_adapter(configured_command)!r}, "
                f"init={_adapter(recorded_command)!r}"
            )
        if "{reasoning_effort}" not in [str(value) for value in configured_command]:
            failures.append(f"{role} invocation does not pass {{reasoning_effort}}")
        if "{reasoning_effort}" not in [str(value) for value in recorded_command]:
            failures.append(f"{role} init invocation does not pass {{reasoning_effort}}")

    if failures:
        for failure in failures:
            print(failure)
        return 1
    print("init-consistency: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
