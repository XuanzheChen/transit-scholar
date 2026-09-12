"""Run the formal, fail-closed AC-016 real-model product acceptance gate."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPOSITORY = Path(__file__).resolve().parents[1]
GATE = (
    "tests/ui/test_product_e2e_smoke.py::"
    "test_real_product_smoke_runs_end_to_end_through_the_built_ui"
)


def main() -> int:
    environment = os.environ.copy()
    environment["TRANSIT_SCHOLAR_AC016_FORMAL"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", GATE, "-q", "-rs"],
        cwd=REPOSITORY,
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
