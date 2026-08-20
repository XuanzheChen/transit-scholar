"""Layer2 run-directory layout helpers (FR-001/FR-013).

Layout::

    data_root/layer2/
      parsed/<paper_id>/
        current.json
        runs/<parse_run_id>/
          document.json
          sections.json
          blocks.jsonl
          parser_manifest.json
          paper.md
          markdown_map.jsonl
          retrieval_chunks.jsonl
          assets/figures/
      retrieval/<paper_id>/
        index/
        retrieval_manifest.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from transit_scholar.layer2.config import Layer2Config

CURRENT_FILENAME = "current.json"
DOCUMENT_FILENAME = "document.json"
SECTIONS_FILENAME = "sections.json"
BLOCKS_FILENAME = "blocks.jsonl"
MANIFEST_FILENAME = "parser_manifest.json"
MARKDOWN_FILENAME = "paper.md"
MARKDOWN_MAP_FILENAME = "markdown_map.jsonl"
CHUNKS_FILENAME = "retrieval_chunks.jsonl"
RETRIEVAL_MANIFEST_FILENAME = "retrieval_manifest.json"


@dataclass(frozen=True)
class RunPaths:
    """All artifact paths for a single parse run."""

    paper_dir: Path
    run_dir: Path
    document_path: Path
    sections_path: Path
    blocks_path: Path
    manifest_path: Path
    markdown_path: Path
    markdown_map_path: Path
    chunks_path: Path
    assets_figures_dir: Path


def run_paths(config: Layer2Config, paper_id: str, parse_run_id: str) -> RunPaths:
    paper_dir = config.parsed_paper_dir(paper_id)
    run_dir = paper_dir / "runs" / parse_run_id
    return RunPaths(
        paper_dir=paper_dir,
        run_dir=run_dir,
        document_path=run_dir / DOCUMENT_FILENAME,
        sections_path=run_dir / SECTIONS_FILENAME,
        blocks_path=run_dir / BLOCKS_FILENAME,
        manifest_path=run_dir / MANIFEST_FILENAME,
        markdown_path=run_dir / MARKDOWN_FILENAME,
        markdown_map_path=run_dir / MARKDOWN_MAP_FILENAME,
        chunks_path=run_dir / CHUNKS_FILENAME,
        assets_figures_dir=run_dir / "assets" / "figures",
    )


def load_current(paper_dir: Path) -> str | None:
    """Return the active ``parse_run_id`` from ``current.json`` or None."""
    path = paper_dir / CURRENT_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    run_id = data.get("parse_run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def save_current(paper_dir: Path, parse_run_id: str) -> None:
    """Atomically write ``current.json`` pointing at ``parse_run_id``."""
    paper_dir.mkdir(parents=True, exist_ok=True)
    tmp = paper_dir / f".{CURRENT_FILENAME}.tmp"
    tmp.write_text(
        json.dumps({"parse_run_id": parse_run_id}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, paper_dir / CURRENT_FILENAME)


def retrieval_dir(config: Layer2Config, paper_id: str) -> Path:
    return config.retrieval_paper_dir(paper_id)


def retrieval_index_dir(config: Layer2Config, paper_id: str) -> Path:
    return retrieval_dir(config, paper_id) / "index"


def retrieval_manifest_path(config: Layer2Config, paper_id: str) -> Path:
    return retrieval_dir(config, paper_id) / RETRIEVAL_MANIFEST_FILENAME
