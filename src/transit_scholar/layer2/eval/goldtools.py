"""Gold annotation tools for P (FR-GOLD-001).

Only browsing / searching / exporting candidate drafts exist here. There is
deliberately NO code path that generates gold questions, auto-selects gold
blocks, or rewrites existing gold -- P (Codex) is the sole annotator.

Commands::

    python -m transit_scholar.layer2.eval.goldtools
      browse --data-root <root> <paper_id> [--offset N] [--limit N]
      search --data-root <root> <pattern> [--paper <paper_id>] [--limit N]
      export --data-root <root> <paper_id> --block-ids b1,b2
             --query "..." --type <query_type> [--language zh|en]
             [--span block_id:start:end ...] [--out <file.jsonl>]
      template [--out <file.json>]

Exit codes: 0 = success, 2 = usage/input error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from transit_scholar.layer2.eval.gold import QUERY_TYPES
from transit_scholar.layer2.schema import GoldQuery

EXIT_OK = 0
EXIT_USAGE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transit_scholar.layer2.eval.goldtools",
        description=(
            "P-side tools to browse canonical blocks, search keywords and "
            "export gold candidate drafts. No gold is generated automatically."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    browse = sub.add_parser("browse", help="page through a paper's canonical blocks")
    browse.add_argument("--data-root", required=True)
    browse.add_argument("paper_id")
    browse.add_argument("--offset", type=int, default=0)
    browse.add_argument("--limit", type=int, default=40)

    search = sub.add_parser("search", help="locate blocks by regex/keyword")
    search.add_argument("--data-root", required=True)
    search.add_argument("pattern")
    search.add_argument("--paper", default=None)
    search.add_argument("--limit", type=int, default=20)

    export = sub.add_parser("export", help="append a GoldQuery draft to a scratch file")
    export.add_argument("--data-root", required=True)
    export.add_argument("paper_id")
    export.add_argument("--block-ids", required=True, help="comma-separated gold block ids")
    export.add_argument("--query", required=True)
    export.add_argument("--type", required=True, choices=QUERY_TYPES)
    export.add_argument("--language", default=None, choices=("zh", "en"))
    export.add_argument("--span", action="append", default=[], help="block_id:start:end")
    export.add_argument("--out", default=None, help="JSONL scratch file (appended)")

    template = sub.add_parser("template", help="write empty gold + annotator templates")
    template.add_argument("--out", default=None, help="JSON file base path (default: gold.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "browse":
        return _browse(args)
    if args.command == "search":
        return _search(args)
    if args.command == "export":
        return _export(args)
    return _template(args)


def load_run_blocks(data_root: str | Path, paper_id: str) -> list[dict[str, Any]]:
    """Load the accepted run's canonical blocks for ``paper_id``."""
    from transit_scholar.config import Settings
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.paths import load_current, run_paths

    config = Layer2Config.from_settings(Settings(data_root=Path(data_root)))
    current_run = load_current(config.parsed_paper_dir(paper_id))
    if current_run is None:
        raise FileNotFoundError(f"paper {paper_id!r} has no active parse run")
    rp = run_paths(config, paper_id, current_run)
    if not rp.blocks_path.is_file():
        raise FileNotFoundError(f"paper {paper_id!r} has no blocks.jsonl")
    blocks: list[dict[str, Any]] = []
    for line in rp.blocks_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            blocks.append(json.loads(line))
    return blocks


def _browse(args: argparse.Namespace) -> int:
    try:
        blocks = load_run_blocks(args.data_root, args.paper_id)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"browse failed: {exc}", file=sys.stderr)
        return EXIT_USAGE
    offset = max(0, args.offset)
    limit = args.limit
    page = blocks[offset:offset + limit]
    for block in page:
        text = (block.get("text") or "").replace("\n", " ")
        print(
            f"{block.get('block_id')}\t{block.get('block_type')}\t"
            f"order={block.get('order')}\tpage={block.get('pages')}\t{text[:160]}"
        )
    print(
        f"-- {len(page)} of {len(blocks)} blocks "
        f"(offset={offset} limit={limit})",
        file=sys.stderr,
    )
    return EXIT_OK


def _search(args: argparse.Namespace) -> int:
    try:
        regex = re.compile(args.pattern, re.IGNORECASE)
    except re.error as exc:
        print(f"invalid pattern: {exc}", file=sys.stderr)
        return EXIT_USAGE
    from transit_scholar.config import Settings
    from transit_scholar.layer2.config import Layer2Config
    from transit_scholar.layer2.paths import load_current, run_paths

    config = Layer2Config.from_settings(Settings(data_root=Path(args.data_root)))
    paper_ids = [args.paper] if args.paper else _active_paper_ids(config)
    hits: list[tuple[str, dict[str, Any]]] = []
    for paper_id in paper_ids:
        current_run = load_current(config.parsed_paper_dir(paper_id))
        if current_run is None:
            continue
        rp = run_paths(config, paper_id, current_run)
        if not rp.blocks_path.is_file():
            continue
        for line in rp.blocks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            block = json.loads(line)
            if regex.search(block.get("text") or ""):
                hits.append((paper_id, block))
    for paper_id, block in hits[: args.limit]:
        text = (block.get("text") or "").replace("\n", " ")
        print(
            f"{paper_id}\t{block.get('block_id')}\t{block.get('block_type')}\t"
            f"order={block.get('order')}\t{text[:160]}"
        )
    print(f"-- {len(hits)} hit(s)", file=sys.stderr)
    return EXIT_OK


def _active_paper_ids(config) -> list[str]:
    parsed_dir = config.layer2_parsed_dir
    if not parsed_dir.is_dir():
        return []
    return sorted(p.name for p in parsed_dir.iterdir() if p.is_dir())


def _export(args: argparse.Namespace) -> int:
    try:
        blocks = {b["block_id"]: b for b in load_run_blocks(args.data_root, args.paper_id)}
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return EXIT_USAGE
    block_ids = [bid.strip() for bid in args.block_ids.split(",") if bid.strip()]
    missing = [bid for bid in block_ids if bid not in blocks]
    if missing:
        print(f"block ids not found in the accepted run: {missing}", file=sys.stderr)
        return EXIT_USAGE
    spans: list[dict[str, Any]] = []
    for spec in args.span:
        parts = spec.split(":")
        if len(parts) != 3 or parts[0] not in blocks:
            print(f"invalid span spec (block_id:start:end): {spec}", file=sys.stderr)
            return EXIT_USAGE
        char_start, char_end = int(parts[1]), int(parts[2])
        block_text = blocks[parts[0]].get("text") or ""
        if not (0 <= char_start < char_end <= len(block_text)):
            print(f"span out of range for block {parts[0]}: {spec}", file=sys.stderr)
            return EXIT_USAGE
        spans.append({"block_id": parts[0], "char_start": char_start, "char_end": char_end})
    gold = GoldQuery(
        paper_id=args.paper_id,
        query=args.query,
        query_type=args.type,
        gold_block_ids=block_ids,
        gold_source_spans=spans or None,
    ).to_dict()
    if args.language:
        gold["_language_hint"] = args.language
    out = Path(args.out) if args.out else Path("gold_scratch.jsonl")
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(gold, ensure_ascii=False) + "\n")
    print(f"draft appended to {out}", file=sys.stderr)
    return EXIT_OK


def _template(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else Path("gold.json")
    annotator_path = out.with_name("gold_annotator.json")
    out.write_text("[]\n", encoding="utf-8")
    annotator_path.write_text(
        json.dumps(
            {
                "annotator": "Planner(Codex) 人工标定",
                "date": "",
                "note": "P 是唯一标注者；G/E 不生成或改写 gold。",
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"templates written: {out}, {annotator_path}", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
