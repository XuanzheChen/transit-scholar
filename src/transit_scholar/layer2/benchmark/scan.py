"""API-key leak scanner for benchmark output directories (NFR-002 / AC-JINA-001).

Scans every file under the given roots for the *actual* ``JINA_API_KEY`` value
(resolved from the project environment) and for common key-shaped secrets that
must never appear in committed artifacts. A match is a hard failure.

Command::

    python -m transit_scholar.layer2.benchmark.scan <root> [<root> ...]
      [--out report.json] [--key-env JINA_API_KEY]

Exit codes: 0 = no matches, 2 = usage/input error, 3 = key value(s) found.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_KEY_FOUND = 3

#: Built from parts so the source never contains a literal key-shaped prefix.
_SECRET_PREFIX = "s" + "k-"
_KEY_SHAPED_RE = re.compile(
    rf"\b({_SECRET_PREFIX}[A-Za-z0-9_\-]{{12,}}|jina_[A-Za-z0-9]{{20,}})\b"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.benchmark.scan",
        description=(
            "Scan directories for the actual JINA_API_KEY value (from the "
            "environment) and key-shaped secrets. Exit 0 only when no value "
            "matches."
        ),
    )
    parser.add_argument("roots", nargs="+", help="directories to scan recursively")
    parser.add_argument("--out", default=None, help="write report JSON to this path")
    parser.add_argument("--key-env", default="JINA_API_KEY", help="env var holding the key value")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = [Path(root) for root in args.roots]
    for root in roots:
        if not root.is_dir():
            print(f"scan root missing: {root}", file=sys.stderr)
            return EXIT_USAGE
    report = scan_roots(roots, key_env=args.key_env)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if report["matched_count"]:
        print(
            f"KEY VALUE FOUND: {report['matched_count']} file(s) contain the "
            f"{args.key_env} value or key-shaped secrets",
            file=sys.stderr,
        )
        for match in report["matched"]:
            print(f"  {match['path']} ({match['match']})", file=sys.stderr)
        return EXIT_KEY_FOUND
    print(
        f"scan clean: {report['files_scanned']} file(s) under "
        f"{', '.join(str(r) for r in roots)}; no key value matched",
        file=sys.stderr,
    )
    return EXIT_OK


def scan_roots(roots: list[Path], *, key_env: str = "JINA_API_KEY") -> dict[str, Any]:
    """Recursively scan ``roots`` for the actual key value and key shapes."""
    key_value = os.environ.get(key_env) or None
    matched: list[dict[str, str]] = []
    files_scanned = 0
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            files_scanned += 1
            try:
                blob = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                blob = ""
            if key_value and key_value in blob:
                matched.append({"path": str(path), "match": "exact_key_value"})
                continue
            if _KEY_SHAPED_RE.search(blob):
                matched.append({"path": str(path), "match": "key_shaped_secret"})
    return {
        "files_scanned": files_scanned,
        "roots": [str(r) for r in roots],
        "key_env": key_env,
        "key_value_set": bool(key_value),
        "matched": matched,
        "matched_count": len(matched),
        "clean": len(matched) == 0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
