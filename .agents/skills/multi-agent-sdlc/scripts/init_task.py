#!/usr/bin/env python3
"""Create the compact task artifact layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SUBDIRS = ["generator", "evaluator", "evidence"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-root", default="tasks")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    task = Path(args.tasks_root) / args.task_id
    task.mkdir(parents=True, exist_ok=True)
    for sub in SUBDIRS:
        (task / sub).mkdir(parents=True, exist_ok=True)
    state = {
        "task_id": args.task_id,
        "title": args.title,
        "state": "requirements_draft",
        "requirements_approved": False,
        "acceptance_frozen": False,
        "generator_revisions": 0,
        "evaluator_revalidations": 0,
        "blocked_reason": None,
        "latest_summary": None,
    }
    (task / "state.json").write_text(json.dumps(state, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    for name in ("requirements.md", "acceptance.md", "execution-contract.yaml"):
        (task / name).touch(exist_ok=True)
    print(task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
