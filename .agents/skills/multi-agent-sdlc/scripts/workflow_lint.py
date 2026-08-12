#!/usr/bin/env python3
"""Check multi-agent-sdlc workflow files for known drift patterns."""

from __future__ import annotations

import sys
from pathlib import Path


REQUIRED = {
    "SKILL.md": [
        "generator_plan",
        "lightweight closure check",
        "does not run a second technical acceptance review",
    ],
    "references/workflow-protocol.md": [
        "Generator plan",
        "User plan review",
        "lightweight closure check",
    ],
    "references/state-machine.md": [
        "generator_plan",
        "generator_plan_planner_review",
        "generator_plan_user_review",
    ],
    "references/role-contracts.md": [
        "review of G's implementation plan",
        "does not perform a second technical review",
    ],
    "assets/roles/planner.md": [
        "planning-only implementation plan",
        "Perform a second technical acceptance review after E accepts",
    ],
    "assets/roles/generator.md": [
        "planning round",
        "Wait for Planner approval and user approval",
    ],
    "assets/roles/evaluator.md": [
        "Hand acceptance criteria to Planner",
        "return a final acceptance decision directly",
    ],
    "assets/default-config.yaml": [
        "generator_plan",
        "generator_plan_planner_review",
        "generator_plan_user_review",
    ],
}

FORBIDDEN = [
    "acceptance_planner_review -> implementation",
    "P checks product drift once",
    "one final product-drift review",
    "perform one final product-drift review",
]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for rel, needles in REQUIRED.items():
        path = root / rel
        if not path.exists():
            failures.append(f"missing file: {rel}")
            continue
        text = path.read_text(encoding="utf-8-sig")
        for needle in needles:
            if needle not in text:
                failures.append(f"{rel}: missing required phrase: {needle}")
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "workflow_lint.py":
            continue
        if path.suffix.lower() not in {".md", ".yaml", ".json", ".py"}:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        for phrase in FORBIDDEN:
            if phrase in text:
                failures.append(f"{path.relative_to(root)}: forbidden stale phrase: {phrase}")
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print("workflow-lint: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
