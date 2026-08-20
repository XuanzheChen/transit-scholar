"""Shared Layer2 Step1 test fixtures (task-2026-08-12-002).

Deterministic, offline helpers: fake parsers, fake embedding/reranker
providers, ready-paper builders and end-to-end parse helpers. Nothing here
touches the real ``data/`` tree or the network.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import fitz
import pytest

from transit_scholar import config as _config
from transit_scholar.db.engine import SessionLocal as _RealSessionLocal
from transit_scholar.db.models import IngestionJob, Paper, PaperAuthor, PaperFile
from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.parser.base import ParserAdapter
from transit_scholar.layer2.parser.fake import FakeParserAdapter, make_item
from transit_scholar.layer2.retrieval.providers import (
    EmbeddingProvider,
    ProviderInfo,
    RerankerProvider,
    UnavailableError,
)
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
)


# ---------------------------------------------------------------------------
# Fake providers
# ---------------------------------------------------------------------------


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic embedding provider with a fixed dimension."""

    def __init__(
        self,
        *,
        dimension: int = 8,
        model: str = "fake-embedding",
        revision: str = "test-v1",
        available: bool = True,
        reason: str | None = None,
    ) -> None:
        self._dimension = dimension
        self._model = model
        self._revision = revision
        self._available = available
        self._reason = reason

    @property
    def available(self) -> bool:
        return self._available

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def info(self) -> ProviderInfo | None:
        return (
            ProviderInfo(
                provider="fake",
                model=self._model,
                dimension=self._dimension,
                revision=self._revision,
            )
            if self._available
            else None
        )

    def dimension(self) -> int | None:
        return self._dimension

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = [digest[i % len(digest)] for i in range(self._dimension)]
        norm = sum(v * v for v in values) ** 0.5 or 1.0
        return [v / norm for v in values]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not self._available:
            raise UnavailableError(self._reason or "unavailable", error_code="unavailable")
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        if not self._available:
            raise UnavailableError(self._reason or "unavailable", error_code="unavailable")
        return self._vector(text)


class FakeRerankerProvider(RerankerProvider):
    """Deterministic reranker scoring by token overlap with the query."""

    def __init__(
        self,
        *,
        model: str = "fake-reranker",
        revision: str = "test-v1",
        available: bool = True,
        reason: str | None = None,
        bias: dict[str, int] | None = None,
    ) -> None:
        self._model = model
        self._revision = revision
        self._available = available
        self._reason = reason
        self._bias = bias or {}

    @property
    def available(self) -> bool:
        return self._available

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def info(self) -> ProviderInfo | None:
        return (
            ProviderInfo(provider="fake", model=self._model, dimension=None, revision=self._revision)
            if self._available
            else None
        )

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        if not self._available:
            raise UnavailableError(self._reason or "unavailable", error_code="unavailable")
        query_tokens = set(_tokens(query))
        scored: list[tuple[int, float]] = []
        for index, document in enumerate(documents):
            overlap = len(query_tokens & set(_tokens(document)))
            score = float(overlap) + float(self._bias.get(str(index), 0))
            scored.append((index, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


def _tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[A-Za-z0-9]+", text.lower())


# ---------------------------------------------------------------------------
# Ready paper / PDF helpers
# ---------------------------------------------------------------------------


def make_pdf(path: str | Path, *, text: str = "", pages: int = 1, title: str = "Fake Paper") -> Path:
    """Generate a real minimal PDF with optional text (offline)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        if text:
            page.insert_textbox(fitz.Rect(50, 50, 550, 750), text, fontsize=11)
    document.set_metadata({"title": title})
    document.save(str(path))
    document.close()
    return path


def fake_pdf_bytes() -> bytes:
    return b"%PDF-1.4 fake content for test\n"


def make_ready_paper(
    project_tmp_path: Path,
    *,
    pdf_path: Path | None = None,
    title: str = "Layer2 Test Paper",
    authors: str | None = "Test Author",
    doi: str | None = "10.0000/layer2",
) -> tuple[str, str, Path]:
    """Create a Layer1-ready paper (paper_id, file_id, source_pdf_path).

    Sets ``_config.settings.data_root`` to ``project_tmp_path`` so the gate
    resolves the primary file. Returns the three identifiers used throughout
    the Layer2 tests.
    """
    _config.settings.data_root = project_tmp_path
    if pdf_path is None:
        pdf_path = project_tmp_path / f"source_{uuid.uuid4().hex}.pdf"
        pdf_path.write_bytes(fake_pdf_bytes())
    pdf_path = Path(pdf_path)
    if pdf_path.parent.resolve() != project_tmp_path.resolve():
        target = project_tmp_path / pdf_path.name
        if target.resolve() != pdf_path.resolve():
            raise ValueError("pdf_path must live inside project_tmp_path")
    relative_path = str(pdf_path.relative_to(project_tmp_path))

    with _RealSessionLocal() as session:
        paper = Paper(title=title, status="active", doi=doi)
        session.add(paper)
        session.flush()
        if authors:
            session.add(PaperAuthor(
                paper_id=paper.id,
                author_order=1,
                full_name=authors,
                normalized_name=authors.lower(),
            ))
        pf = PaperFile(
            paper_id=paper.id,
            is_primary=True,
            relative_path=relative_path,
            mime_type="application/pdf",
        )
        session.add(pf)
        session.flush()
        session.add(IngestionJob(
            uploaded_filename=pdf_path.name,
            file_id=pf.id,
            paper_id=paper.id,
            status="accepted",
            current_stage="completed",
        ))
        session.commit()
        paper_id = paper.id
        file_id = pf.id
    return paper_id, file_id, pdf_path


def patch_parsers(monkeypatch: pytest.MonkeyPatch, adapters: list[ParserAdapter]) -> None:
    """Force ``parse_paper`` to use the given adapters (test seam)."""
    monkeypatch.setattr(
        "transit_scholar.layer2.pipeline.resolve_parsers",
        lambda config: adapters,
    )


def run_parse(
    project_tmp_path: Path,
    items: list,
    *,
    monkeypatch: pytest.MonkeyPatch,
    title: str = "Layer2 Test Paper",
    parser_kwargs: dict[str, Any] | None = None,
    page_count: int | None = None,
    l2_config: Layer2Config | None = None,
):
    """End-to-end helper: ready paper + fake parser + ``parse_paper``.

    Returns ``(paper_id, file_id, source_pdf_path, parse_result)``.
    """
    paper_id, file_id, pdf_path = make_ready_paper(project_tmp_path, title=title)
    parser_kwargs = dict(parser_kwargs or {})
    if page_count is not None:
        parser_kwargs["page_count"] = page_count
    adapter = FakeParserAdapter(items=items, **parser_kwargs)
    patch_parsers(monkeypatch, [adapter])

    from transit_scholar.config import Settings
    from transit_scholar.layer2.pipeline import parse_paper

    config = l2_config or _local_default_config(project_tmp_path)
    result = parse_paper(paper_id, config=config)
    return paper_id, file_id, pdf_path, result


def _local_default_config(project_tmp_path: Path) -> Layer2Config:
    """A Layer2Config pinned to the deterministic local store.

    Parse tests must not depend on whether LanceDB is installed, so any
    helper that builds its own config defaults to ``store="local"`` (the V1
    *default* ``Layer2Config.store`` stays ``"lancedb"``).
    """
    from transit_scholar.config import Settings

    config = Layer2Config.from_settings(Settings(data_root=project_tmp_path))
    object.__setattr__(config, "store", "local")
    return config


def canonical_fixture_items() -> list:
    """A multi-type deterministic parser item stream."""
    return [
        make_item(
            item_id="h_intro", item_type="heading", text="Introduction", order=0,
            page=1, level=1, bbox=[70.0, 100.0, 530.0, 120.0], font_size=14.0,
        ),
        make_item(
            item_id="p1", item_type="paragraph",
            text="Bus systems suffer from bunching and irregular headways.",
            order=1, page=1, bbox=[70.0, 130.0, 530.0, 150.0], font_size=10.0,
        ),
        make_item(
            item_id="h_method", item_type="heading", text="Method", order=2,
            page=1, level=1, bbox=[70.0, 160.0, 530.0, 180.0], font_size=14.0,
        ),
        make_item(
            item_id="p2", item_type="paragraph",
            text="We use deep reinforcement learning for holding control.",
            order=3, page=1, bbox=[70.0, 190.0, 530.0, 210.0], font_size=10.0,
        ),
        make_item(
            item_id="p3", item_type="paragraph",
            text="The reward function balances passenger waiting time and regularity.",
            order=4, page=1, bbox=[70.0, 220.0, 530.0, 240.0], font_size=10.0,
        ),
        make_item(
            item_id="eq1", item_type="equation", text="r_t = -w * wait_t",
            order=5, page=1, bbox=[70.0, 250.0, 530.0, 265.0],
            content={"latex": "r_t = -w \\cdot wait_t", "label": "1", "raw_text": "r_t = -w * wait_t"},
        ),
    ]


def table_caption_items() -> list:
    """A table + caption + figure + caption stream (STRUCT fixtures)."""
    cells = [
        {"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "Method", "is_header": True},
        {"row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "Mean Wait", "is_header": True},
        {"row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "DRL", "is_header": False},
        {"row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "5.1", "is_header": False},
    ]
    markdown = "| Method | Mean Wait |\n| --- | --- |\n| DRL | 5.1 |"
    return [
        make_item(
            item_id="tbl1", item_type="table", text=markdown, order=0, page=2,
            bbox=[70.0, 60.0, 530.0, 120.0],
            content={
                "label": "Table 1",
                "n_rows": 2,
                "n_cols": 2,
                "cells": cells,
                "markdown": markdown,
            },
        ),
        make_item(
            item_id="cap1", item_type="caption",
            text="Table 1. Mean waiting time under each control method.",
            order=1, page=2, bbox=[70.0, 130.0, 530.0, 145.0], font_size=9.0,
        ),
        make_item(
            item_id="fig1", item_type="figure", text="", order=2, page=3,
            bbox=[70.0, 60.0, 400.0, 200.0],
            content={
                "label": "Figure 1",
                "asset_path": "assets/figures/fig_0003.png",
                "_image_bytes": b"\x89PNG fake image bytes",
                "_image_ext": "png",
            },
        ),
        make_item(
            item_id="cap2", item_type="caption",
            text="Figure 1. Average waiting time over the simulation horizon.",
            order=3, page=3, bbox=[70.0, 210.0, 530.0, 225.0], font_size=9.0,
        ),
    ]


def equation_items() -> list:
    return [
        make_item(
            item_id="eq2", item_type="equation",
            text="J = E[sum gamma^t r_t]", order=0, page=1,
            content={
                "latex": "J = \\mathbb{E}[\\sum_t \\gamma^t r_t]",
                "label": "(7)",
                "raw_text": "J = E[sum gamma^t r_t]",
            },
            bbox=[70.0, 60.0, 530.0, 80.0],
        )
    ]


def cross_page_paragraph_items() -> list:
    """A paragraph split across page 1 -> page 2 (strong continuation)."""
    return [
        make_item(
            item_id="p_a", item_type="paragraph",
            text="The reward function is designed to balance passenger",
            order=0, page=1, bbox=[70.0, 650.0, 530.0, 665.0], font_size=10.0,
        ),
        make_item(
            item_id="p_b", item_type="paragraph",
            text="waiting time and bus regularity.",
            order=1, page=2, bbox=[70.0, 60.0, 530.0, 75.0], font_size=10.0,
        ),
    ]


def big_table_items(
    *,
    data_rows: int = 25,
    columns: int = 3,
    tokens_per_cell: int = 14,
    caption_after: bool = True,
) -> list:
    """A >900-token table with >= 20 data rows and a caption.

    Caption ordering follows real papers by default (caption AFTER its
    parent), so the chunker's relation-based binding is what keeps them
    together.
    """
    header_cells = [
        {
            "row": 0, "col": col, "row_span": 1, "col_span": 1,
            "text": f"Col {col}", "is_header": True,
        }
        for col in range(columns)
    ]
    header_text = " | ".join(f"Col {col}" for col in range(columns))
    cells = list(header_cells)
    rows_markdown: list[str] = []
    for row in range(1, data_rows + 1):
        cell_texts = [
            " ".join(f"t{row}c{col}_{k}" for k in range(tokens_per_cell))
            for col in range(columns)
        ]
        rows_markdown.append(" | ".join(cell_texts))
        for col in range(columns):
            cells.append(
                {
                    "row": row, "col": col, "row_span": 1, "col_span": 1,
                    "text": cell_texts[col], "is_header": False,
                }
            )
    markdown = (
        f"| {header_text} |\n"
        + "| " + " | ".join("---" for _ in range(columns)) + " |\n"
        + "\n".join(f"| {line} |" for line in rows_markdown)
    )
    items = [
        make_item(
            item_id="tbl_big", item_type="table", text=markdown, order=0,
            page=2, bbox=[70.0, 60.0, 530.0, 120.0],
            content={
                "label": "Table Big",
                "n_rows": data_rows + 1,
                "n_cols": columns,
                "cells": cells,
                "markdown": markdown,
            },
        ),
    ]
    caption = make_item(
        item_id="cap_big", item_type="caption",
        text=(
            "Table Big. Detailed results of the large evaluation table with "
            "many rows and columns of measured performance values."
        ),
        order=1, page=2, bbox=[70.0, 130.0, 530.0, 145.0], font_size=9.0,
    )
    if caption_after:
        items.append(caption)
    else:
        items.insert(0, caption)
    return items


def deep_section_items(long_title_tokens: int = 60) -> list:
    """A >= 3-level section path with long titles, then a long paragraph."""
    title = " ".join(f"w{i}" for i in range(long_title_tokens))
    return [
        make_item(
            item_id="h1", item_type="heading",
            text=f"First Level {title}", order=0, page=1, level=1,
            bbox=[70.0, 60.0, 530.0, 80.0], font_size=14.0,
        ),
        make_item(
            item_id="h2", item_type="heading",
            text=f"Second Level {title}", order=1, page=1, level=2,
            bbox=[70.0, 90.0, 530.0, 110.0], font_size=12.0,
        ),
        make_item(
            item_id="h3", item_type="heading",
            text=f"Third Level {title}", order=2, page=1, level=3,
            bbox=[70.0, 120.0, 530.0, 140.0], font_size=11.0,
        ),
        make_item(
            item_id="p_deep", item_type="paragraph",
            text="Deeply nested section body paragraph with meaningful text.",
            order=3, page=1, bbox=[70.0, 150.0, 530.0, 170.0], font_size=10.0,
        ),
    ]


def long_caption_items() -> list:
    """A table + an extremely long caption + an equation (token pressure)."""
    markdown = "| Method | Value |\n| --- | --- |\n| DRL | 5.1 |"
    long_caption_text = "Table 9. " + " ".join(
        f"cap{i}" for i in range(120)
    ) + "."
    return [
        make_item(
            item_id="tbl9", item_type="table", text=markdown, order=0, page=1,
            bbox=[70.0, 60.0, 530.0, 100.0],
            content={
                "label": "Table 9", "n_rows": 2, "n_cols": 2,
                "cells": [
                    {"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "Method", "is_header": True},
                    {"row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "Value", "is_header": True},
                    {"row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "DRL", "is_header": False},
                    {"row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "5.1", "is_header": False},
                ],
                "markdown": markdown,
            },
        ),
        make_item(
            item_id="cap9", item_type="caption", text=long_caption_text,
            order=1, page=1, bbox=[70.0, 110.0, 530.0, 125.0], font_size=9.0,
        ),
        make_item(
            item_id="eq9", item_type="equation", text="r = 1", order=2, page=1,
            bbox=[70.0, 135.0, 530.0, 155.0],
            content={"latex": "r = 1", "label": "(9)", "raw_text": "r = 1"},
        ),
    ]


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def read_artifacts(config: Layer2Config, paper_id: str, parse_run_id: str) -> dict[str, Any]:
    """Read every artifact of a parse run into a dict of parsed objects."""
    import json

    from transit_scholar.layer2.paths import run_paths

    rp = run_paths(config, paper_id, parse_run_id)
    return {
        "document": CanonicalDocument.from_dict(
            json.loads(rp.document_path.read_text(encoding="utf-8"))
        ),
        "sections": [
            CanonicalSection.from_dict(record)
            for record in json.loads(rp.sections_path.read_text(encoding="utf-8"))
        ],
        "blocks": [
            CanonicalBlock.from_dict(json.loads(line))
            for line in rp.blocks_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ],
        "manifest": json.loads(rp.manifest_path.read_text(encoding="utf-8")),
        "markdown": rp.markdown_path.read_text(encoding="utf-8"),
        "markdown_map": rp.markdown_map_path.read_text(encoding="utf-8"),
        "chunks": rp.chunks_path.read_text(encoding="utf-8"),
        "run_dir": rp.run_dir,
    }
