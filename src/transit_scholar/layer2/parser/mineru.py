"""MinerU whole-document fallback parser adapter (FR-003/FR-007).

MinerU >= 3 no longer exports ``from mineru import MinerU``. This adapter uses
the public, maintainable local pipeline entry of the installed version
(``mineru.cli.client.run_orchestrated_cli`` -- the same code path behind the
``mineru`` CLI) restricted to the local ``pipeline`` backend. Remote / VLM
HTTP backends, LLM-aided post-processing and paid APIs are never used.

Structured output first: the local pipeline writes ``{stem}_middle.json``
(per-page ``pdf_info`` with ``page_idx``/``page_size`` and typed ``para_blocks``
carrying bbox, heading levels, table HTML, equation LaTeX and captions) and
``{stem}_content_list.json``. The adapter builds ``ParserItem`` records from
these structured artifacts so real page numbers, bboxes and structure are
preserved. Only when no structured artifact exists does it fall back to the
plain markdown, emitting ``page=0`` items and a clear warning (readable body,
missing provenance -> ``degraded``, never a fabricated hard failure).

``availability()`` checks the real callable entry for the installed version
instead of only ``import mineru``: it requires a version >= 3 AND the CLI
client module to be present. Automation tests mock the module-level seam
``_invoke_local_pipeline`` so no model is ever run.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from transit_scholar.layer2 import util as _util
from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.parser.base import (
    ParserAdapter,
    ParserAvailability,
    ParserInfo,
    ParserItem,
    ParserResult,
    register_adapter,
)

#: The public, maintainable local pipeline entry for MinerU 3.x (also used by
#: the ``mineru`` console script). Never a remote/VLM HTTP or paid backend.
_MINERU_CLI_MODULE = "mineru.cli.client"
_MINERU_MIN_MAJOR = 3
_MINERU_BACKEND = "pipeline"
#: MinerU OCR language (``en`` normalizes to ``ch`` which covers English +
#: CJK text layers; the pipeline backend only uses OCR for image-based pages).
_MINERU_OCR_LANG = "ch"

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

#: MinerU middle-json block types that carry usable structure.
_MINERU_TITLE_TYPES = frozenset({"title", "doc_title", "paragraph_title"})
_MINERU_CAPTION_TYPES = frozenset(
    {
        "caption",
        "image_caption",
        "table_caption",
        "chart_caption",
        "algorithm_caption",
        "code_caption",
    }
)
_MINERU_FOOTNOTE_TYPES = frozenset(
    {"footnote", "page_footnote", "image_footnote", "table_footnote", "chart_footnote", "code_footnote"}
)
#: Noise blocks dropped from the reading order (headers/footers/page numbers).
_MINERU_NOISE_TYPES = frozenset(
    {"header", "footer", "page_number", "discarded", "phonetic", "vertical_text"}
)
#: Table/equation/visual spans inside sub-blocks.
_MINERU_TABLE_SPAN = "table"
_MINERU_TABLE_BODY = "table_body"
_MINERU_IMAGE_BODY = "image_body"
_MINERU_CHART_BODY = "chart_body"


def _mineru_version() -> str | None:
    """Installed MinerU version from package metadata (``None`` if absent)."""
    return _util.dependency_version("mineru")


def _mineru_entry_available() -> bool:
    """Whether the public CLI module (the real callable entry) exists.

    ``find_spec`` only imports the empty parent packages ``mineru`` /
    ``mineru.cli`` and never executes the heavy ``client`` module body.
    """
    try:
        return importlib.util.find_spec(_MINERU_CLI_MODULE) is not None
    except (ImportError, ModuleNotFoundError):
        return False


class MinerUParserAdapter(ParserAdapter):
    name = "mineru"

    def __init__(self, config: Layer2Config | None = None) -> None:
        self._config = config

    @property
    def version(self) -> str:
        return _mineru_version() or "unavailable"

    @property
    def config(self) -> dict[str, Any]:
        return {
            "engine": "mineru",
            "entry": _MINERU_CLI_MODULE,
            "backend": _MINERU_BACKEND,
            "whole_document_fallback": True,
            "remote_http": False,
            "vlm": False,
            "llm_aided": False,
            #: Structured local artifacts are preferred over the plain
            #: markdown so real page numbers/bboxes are preserved.
            "output_mode": "structured_local_artifacts_preferred",
            "structured_artifacts": ["*_middle.json", "*_content_list.json"],
        }

    def availability(self) -> ParserAvailability:
        version = _mineru_version()
        if version is None:
            return ParserAvailability(
                available=False, reason="dependency_missing", version=None
            )
        major = _parse_major(version)
        if major is None or major < _MINERU_MIN_MAJOR:
            return ParserAvailability(
                available=False, reason="unsupported_version", version=version
            )
        if not _mineru_entry_available():
            return ParserAvailability(
                available=False, reason="parser_unavailable", version=version
            )
        return ParserAvailability(available=True, version=version)

    def parse(self, pdf_path: str) -> ParserResult:
        version = _mineru_version()
        if version is None:
            info = ParserInfo(
                name=self.name,
                version=self.version,
                config=self.config,
                config_hash=self.config_hash,
            )
            return ParserResult(
                status="dependency_missing",
                info=info,
                error_code="DEPENDENCY_MISSING",
                error_message=(
                    "mineru is not installed; the fallback parser is unavailable "
                    "(reporting truthfully instead of fabricating a parse)"
                ),
            )
        try:
            return self._convert(pdf_path)
        except Exception as exc:  # noqa: BLE001 - structured parser failure
            info = ParserInfo(
                name=self.name,
                version=self.version,
                config=self.config,
                config_hash=self.config_hash,
            )
            return ParserResult(
                status="error",
                info=info,
                error_code="MINERU_CONVERSION_FAILED",
                error_message=f"mineru conversion raised: {exc}",
            )

    def _convert(self, pdf_path: str) -> ParserResult:
        # The local pipeline writes structured artifacts + markdown into a
        # fresh temp dir (whole document replacement). ``_invoke_local_pipeline``
        # is the test seam: automation mocks it and never runs the heavy models.
        output_dir = Path(tempfile.mkdtemp(prefix="mineru_l2_"))
        try:
            _invoke_local_pipeline(pdf_path, output_dir)
            structured = _read_mineru_structured(output_dir)
            fallback_markdown = None
            if structured is None:
                fallback_markdown = _read_mineru_markdown(output_dir)
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

        if structured is not None:
            items, page_count, used_artifact = structured
            applied = dict(self.config)
            applied["output_mode"] = used_artifact
            applied["page_count_source"] = "mineru structured artifact"
            warnings: list[str] = []
            if used_artifact == "content_list":
                warnings.append(
                    "mineru middle json unavailable; used *_content_list.json "
                    "(page numbers preserved, per-block bbox not present)"
                )
            info = ParserInfo(
                name=self.name,
                version=self.version,
                config=applied,
                config_hash=self.config_hash,
            )
            return ParserResult(
                status="ok",
                items=items,
                info=info,
                page_count=page_count,
                warnings=warnings,
                parser_quality={
                    "structured_artifact": used_artifact,
                    "page_count_source": "mineru structured artifact",
                },
            )

        # Structured artifacts genuinely unavailable -> markdown fallback.
        if fallback_markdown is None:
            info = ParserInfo(
                name=self.name,
                version=self.version,
                config=self.config,
                config_hash=self.config_hash,
            )
            return ParserResult(
                status="error",
                info=info,
                error_code="MINERU_NO_OUTPUT",
                error_message="mineru local pipeline produced no output artifacts",
            )

        items = _markdown_to_items(fallback_markdown)
        applied = dict(self.config)
        applied["output_mode"] = "markdown_fallback"
        applied["page_count_source"] = "pdf"
        info = ParserInfo(
            name=self.name,
            version=self.version,
            config=applied,
            config_hash=self.config_hash,
        )
        return ParserResult(
            status="ok",
            items=items,
            info=info,
            page_count=_pdf_page_count(pdf_path),
            warnings=[
                "mineru structured artifacts (*_middle.json / "
                "*_content_list.json) unavailable; fell back to markdown. "
                "Items carry no page/bbox provenance (page=0); validation "
                "will report degraded, not a fabricated hard failure"
            ],
            parser_quality={
                "structured_artifact": None,
                "page_count_source": "pdf",
            },
        )


def _invoke_local_pipeline(pdf_path: str | Path, output_dir: Path) -> None:
    """Run the MinerU local ``pipeline`` backend over one PDF (public CLI entry).

    ``api_url`` stays ``None`` so MinerU starts a temporary *local* API server;
    ``backend="pipeline"`` uses the free local models. No remote server, no VLM
    HTTP client, no LLM-aided post-processing, no paid API is ever configured.
    """
    from mineru.cli.client import run_orchestrated_cli

    asyncio.run(
        run_orchestrated_cli(
            input_path=Path(pdf_path),
            output_dir=output_dir,
            method="auto",
            backend=_MINERU_BACKEND,
            effort="medium",
            lang=_MINERU_OCR_LANG,
            server_url=None,
            api_url=None,
            start_page_id=0,
            end_page_id=None,
            formula_enable=True,
            table_enable=True,
            image_analysis=False,
            client_side_output_generation=True,
            extra_cli_args=(),
        )
    )


def _read_mineru_structured(
    output_dir: Path,
) -> tuple[list[ParserItem], int, str] | None:
    """Read the richest available MinerU structured artifact.

    Prefers ``*_middle.json`` (per-page ``pdf_info`` with ``page_idx`` and
    per-block ``bbox``), then ``*_content_list.json`` (page numbers preserved,
    bbox absent), and returns ``None`` only when no structured artifact can be
    parsed -- the caller then falls back to markdown.
    """
    middle_candidates = sorted(output_dir.rglob("*_middle.json"))
    for path in middle_candidates:
        try:
            return _middle_json_to_items(path)
        except Exception:  # noqa: BLE001 - try the next artifact
            continue
    content_candidates = sorted(output_dir.rglob("*_content_list.json"))
    for path in content_candidates:
        try:
            return _content_list_to_items(path)
        except Exception:  # noqa: BLE001 - try the next artifact
            continue
    return None


def _middle_json_to_items(path: Path) -> tuple[list[ParserItem], int, str]:
    """Convert a MinerU ``*_middle.json`` into page-true ``ParserItem``s.

    The middle json ``pdf_info`` entries expose ``page_idx`` (0-based),
    ``page_size`` and typed ``para_blocks`` (bbox, heading level, table HTML,
    equation LaTeX, caption sub-blocks). Page numbers are 1-based canonical
    pages; bboxes are the parser's own pixel coordinates (never fabricated).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    pdf_info = data.get("pdf_info") or []
    if not isinstance(pdf_info, list) or not pdf_info:
        raise ValueError("mineru middle json has no pdf_info list")
    page_count = len(pdf_info)
    items: list[ParserItem] = []
    order = 0
    for page_info in sorted(
        pdf_info, key=lambda entry: int(entry.get("page_idx", 0) or 0)
    ):
        page_no_1 = int(page_info.get("page_idx", 0) or 0) + 1
        blocks = page_info.get("para_blocks") or []
        for _index, block in sorted(
            enumerate(blocks),
            key=lambda pair: (pair[1].get("index", pair[0]) or pair[0], pair[0]),
        ):
            emitted = _map_middle_block(block, page_no_1, order)
            for item in emitted:
                items.append(item)
                order += 1
    if not items:
        raise ValueError("mineru middle json contains no usable blocks")
    return items, page_count, "middle_json"


def _map_middle_block(
    block: dict[str, Any], page_no_1: int, order: int
) -> list[ParserItem]:
    """Map one middle-json para block (and its caption sub-blocks) to items."""
    block_type = str(block.get("type") or "other")
    if block_type in _MINERU_NOISE_TYPES:
        return []
    bbox = _block_bbox(block)
    if block_type in _MINERU_TITLE_TYPES:
        level = max(1, int(block.get("level", 1) or 1))
        return [
            ParserItem(
                item_id=f"mineru_{order}",
                item_type="heading",
                text=_block_text(block),
                order=order,
                page=page_no_1,
                bbox=bbox,
                source_item_id=f"mineru_item_{order}",
                level=level,
                content={"level": level},
            )
        ]
    if block_type in _MINERU_CAPTION_TYPES:
        text = _block_text(block)
        if not text.strip():
            return []
        return [
            ParserItem(
                item_id=f"mineru_{order}",
                item_type="caption",
                text=text,
                order=order,
                page=page_no_1,
                bbox=bbox,
                source_item_id=f"mineru_item_{order}",
                level=1,
                content={},
            )
        ]
    if block_type in _MINERU_FOOTNOTE_TYPES:
        text = _block_text(block)
        if not text.strip():
            return []
        return [
            ParserItem(
                item_id=f"mineru_{order}",
                item_type="footnote",
                text=text,
                order=order,
                page=page_no_1,
                bbox=bbox,
                source_item_id=f"mineru_item_{order}",
                level=1,
                content={},
            )
        ]
    if block_type == "table":
        return _table_para_items(block, page_no_1, order)
    if block_type in ("image", "chart", "algorithm"):
        return _visual_para_items(block, page_no_1, order, block_type)
    if block_type in ("interline_equation", "equation"):
        latex = _block_text(block)
        return [
            ParserItem(
                item_id=f"mineru_{order}",
                item_type="equation",
                text=latex,
                order=order,
                page=page_no_1,
                bbox=bbox,
                source_item_id=f"mineru_item_{order}",
                level=1,
                content={"latex": latex, "raw_text": latex},
            )
        ]
    if block_type in ("list", "index"):
        return [
            ParserItem(
                item_id=f"mineru_{order}",
                item_type="list",
                text=_block_text(block),
                order=order,
                page=page_no_1,
                bbox=bbox,
                source_item_id=f"mineru_item_{order}",
                level=1,
                content={},
            )
        ]
    if block_type == "ref_text":
        return [
            ParserItem(
                item_id=f"mineru_{order}",
                item_type="reference",
                text=_block_text(block),
                order=order,
                page=page_no_1,
                bbox=bbox,
                source_item_id=f"mineru_item_{order}",
                level=1,
                content={},
            )
        ]
    # text / abstract / aside_text / code / unknown -> paragraph / other
    item_type = "paragraph" if block_type in ("text", "abstract", "aside_text", "code") else "other"
    return [
        ParserItem(
            item_id=f"mineru_{order}",
            item_type=item_type,
            text=_block_text(block),
            order=order,
            page=page_no_1,
            bbox=bbox,
            source_item_id=f"mineru_item_{order}",
            level=1,
            content={},
        )
    ]


def _table_para_items(
    block: dict[str, Any], page_no_1: int, order: int
) -> list[ParserItem]:
    """Emit a table item from a ``table`` para block plus caption sub-blocks."""
    html = _extract_table_html(block)
    text = html or _block_text(block)
    items = [
        ParserItem(
            item_id=f"mineru_{order}",
            item_type="table",
            text=text,
            order=order,
            page=page_no_1,
            bbox=_block_bbox(block),
            source_item_id=f"mineru_item_{order}",
            level=1,
            content={
                "markdown": html or "",
                "cells": [],
                "n_rows": _count_table_rows(html),
                "n_cols": 0,
            },
        )
    ]
    items.extend(_visual_sub_captions(block, page_no_1, order + 1))
    return items


def _visual_para_items(
    block: dict[str, Any], page_no_1: int, order: int, block_type: str
) -> list[ParserItem]:
    """Emit a figure item for image/chart/algorithm para blocks + captions."""
    items = [
        ParserItem(
            item_id=f"mineru_{order}",
            item_type="figure",
            text="",
            order=order,
            page=page_no_1,
            bbox=_block_bbox(block),
            source_item_id=f"mineru_item_{order}",
            level=1,
            content={
                "label": f"MinerU {block_type}",
                "asset_path": "",
                "raw_block_type": block_type,
            },
        )
    ]
    items.extend(_visual_sub_captions(block, page_no_1, order + 1))
    return items


def _visual_sub_captions(
    block: dict[str, Any], page_no_1: int, order: int
) -> list[ParserItem]:
    """Emit caption items from the visual sub-blocks (stable parent binding)."""
    out: list[ParserItem] = []
    for sub in block.get("blocks") or []:
        sub_type = str(sub.get("type") or "")
        if sub_type not in _MINERU_CAPTION_TYPES:
            continue
        text = _block_text(sub)
        if not text.strip():
            continue
        out.append(
            ParserItem(
                item_id=f"mineru_{order}",
                item_type="caption",
                text=text,
                order=order,
                page=page_no_1,
                bbox=_block_bbox(sub),
                source_item_id=f"mineru_item_{order}",
                level=1,
                content={"raw_block_type": sub_type},
            )
        )
        order += 1
    return out


def _extract_table_html(block: dict[str, Any]) -> str:
    """Collect the rendered table HTML from ``table_body`` sub-block spans."""
    htmls: list[str] = []
    for sub in block.get("blocks") or []:
        if str(sub.get("type") or "") != _MINERU_TABLE_BODY:
            continue
        for line in sub.get("lines") or []:
            for span in line.get("spans") or []:
                if str(span.get("type") or "") == _MINERU_TABLE_SPAN and span.get("html"):
                    htmls.append(str(span["html"]))
    return "\n".join(htmls)


def _count_table_rows(html: str) -> int:
    if not html:
        return 0
    return html.count("<tr")


def _block_text(block: dict[str, Any]) -> str:
    """Join span contents of a middle-json block (deterministic order)."""
    parts: list[str] = []
    for line in block.get("lines") or []:
        for span in line.get("spans") or []:
            content = span.get("content")
            if content:
                parts.append(str(content))
    return " ".join(parts)


def _block_bbox(block: dict[str, Any]) -> list[float] | None:
    raw = block.get("bbox")
    if not raw:
        return None
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError):
        return None


def _content_list_to_items(path: Path) -> tuple[list[ParserItem], int, str]:
    """Convert a MinerU ``*_content_list.json`` into ``ParserItem``s.

    The content-list entries are flat ``{type, text, ...}`` records with no
    per-block bbox; page numbers come from the per-page layout of the list.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    pages = data.get("pdf_info") or data.get("pages") or []
    if not isinstance(pages, list) or not pages:
        raise ValueError("mineru content list has no page records")
    page_count = len(pages)
    items: list[ParserItem] = []
    order = 0
    for page_index, page in enumerate(pages):
        page_no_1 = int(page.get("page_idx", page_index) or page_index) + 1
        for entry in page.get("content_list") or []:
            entry_type = str(entry.get("type") or "text")
            mapped = _map_content_list_entry(entry, entry_type)
            if mapped is None:
                continue
            item_type, text, content = mapped
            items.append(
                ParserItem(
                    item_id=f"mineru_{order}",
                    item_type=item_type,
                    text=text,
                    order=order,
                    page=page_no_1,
                    bbox=None,
                    source_item_id=f"mineru_item_{order}",
                    level=max(1, int(content.get("level", 1) or 1)),
                    content=content,
                )
            )
            order += 1
    if not items:
        raise ValueError("mineru content list contains no usable entries")
    return items, page_count, "content_list"


def _map_content_list_entry(
    entry: dict[str, Any], entry_type: str
) -> tuple[str, str, dict[str, Any]] | None:
    """Map one content-list entry to (item_type, text, content) or None."""
    if entry_type in ("header", "footer", "page_number", "discarded"):
        return None
    text = str(entry.get("text") or "")
    if entry_type == "equation":
        return ("equation", text, {"latex": text, "raw_text": text})
    if entry_type == "image":
        caption = entry.get("image_caption") or []
        caption_text = (
            caption[0].get("text")
            if isinstance(caption, list) and caption and isinstance(caption[0], dict)
            else ""
        )
        return ("figure", str(caption_text or ""), {})
    if entry_type == "table":
        caption = entry.get("table_caption") or []
        caption_text = (
            caption[0].get("text")
            if isinstance(caption, list) and caption and isinstance(caption[0], dict)
            else ""
        )
        table_text = str(entry.get("table_ocr", "") or text or "")
        return ("table", table_text, {"markdown": table_text, "cells": [], "n_rows": 0, "n_cols": 0, "caption_text": str(caption_text or "")})
    if entry_type == "text" and entry.get("text_level"):
        return ("heading", text, {"level": max(1, int(entry["text_level"]))})
    if entry_type == "list":
        return ("list", text, {})
    if entry_type == "title":
        return ("heading", text, {"level": max(1, int(entry.get("text_level", 1) or 1))})
    return ("paragraph", text, {})


def _pdf_page_count(pdf_path: str | Path) -> int | None:
    """Real PDF page count via PyMuPDF (``None`` when unavailable)."""
    try:
        import fitz

        document = fitz.open(str(pdf_path))
        try:
            return document.page_count
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - optional page-count hint only
        return None


def _read_mineru_markdown(output_dir: Path) -> str | None:
    """Return the first ``.md`` file written by the local pipeline."""
    for path in sorted(output_dir.rglob("*.md")):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _markdown_to_items(markdown: str) -> list[ParserItem]:
    items: list[ParserItem] = []
    order = 0
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _MD_HEADING_RE.match(line)
        if match:
            item_type = "heading"
            level = min(len(match.group(1)), 6)
            text = match.group(2).strip()
            content = {"level": level}
        else:
            item_type = "paragraph"
            level = 1
            text = line
            content = {}
        items.append(
            ParserItem(
                item_id=f"mineru_{order}",
                item_type=item_type,
                text=text,
                order=order,
                page=0,
                bbox=None,
                source_item_id=f"mineru_item_{order}",
                level=level,
                content=content,
            )
        )
        order += 1
    return items


def _parse_major(version: str) -> int | None:
    try:
        return int(version.split(".")[0])
    except (ValueError, IndexError):
        return None


def _factory(config: Layer2Config) -> MinerUParserAdapter:
    return MinerUParserAdapter(config)


register_adapter("mineru", _factory)
