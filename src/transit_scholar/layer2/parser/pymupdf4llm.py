"""PyMuPDF4LLM diagnostic parser adapter (FR-003).

Used as a lightweight diagnostic / benchmark baseline. The conversion goes
through ``pymupdf4llm.to_markdown(..., page_chunks=True)`` so real page
numbers are preserved; the real PDF page count is recorded when readable.
The adapter tolerates legacy return shapes (plain string / single dict) with
an explicit warning: items then carry ``page=0`` (parser provided no page-level
provenance) and validation reports degraded instead of a fabricated hard
failure. bbox stays ``None`` when the tool provides none -- never fabricated.
Never participates in multi-parser voting.

The conversion call goes through the module-level seam ``_call_pymupdf4llm``
so automated tests can mock it and never trigger OCR or model downloads.
"""

from __future__ import annotations

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


class Pymupdf4LLMParserAdapter(ParserAdapter):
    name = "pymupdf4llm"

    def __init__(self, config: Layer2Config | None = None) -> None:
        self._config = config

    @property
    def version(self) -> str:
        return _util.dependency_version("pymupdf4llm") or "unavailable"

    @property
    def config(self) -> dict[str, Any]:
        return {
            "engine": "pymupdf4llm",
            "role": "diagnostic",
            #: Page-level output is requested so items carry real page numbers.
            "page_chunks": True,
            "page_count_source": "pdf",
        }

    def availability(self) -> ParserAvailability:
        try:
            import pymupdf4llm  # noqa: F401

            return ParserAvailability(available=True, version=self.version)
        except ImportError:
            return ParserAvailability(
                available=False, reason="dependency_missing", version=None
            )

    def parse(self, pdf_path: str) -> ParserResult:
        availability = self.availability()
        if not availability.available:
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
                    "pymupdf4llm is not installed; the diagnostic parser is "
                    "unavailable (reporting truthfully instead of fabricating a parse)"
                ),
            )
        try:
            markdown = _call_pymupdf4llm(pdf_path)
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
                error_code="PYMUPDF4LLM_CONVERSION_FAILED",
                error_message=f"pymupdf4llm conversion raised: {exc}",
            )
        items, page_mapped, shape_note = _chunked_to_items(markdown)
        applied = dict(self.config)
        applied["output_shape"] = shape_note
        info = ParserInfo(
            name=self.name,
            version=self.version,
            config=applied,
            config_hash=self.config_hash,
        )
        warnings: list[str] = []
        if not page_mapped:
            warnings.append(
                "pymupdf4llm returned a legacy non-page-chunked output; "
                "items carry no page-level provenance (page=0). Validation "
                "will report degraded, not a fabricated hard failure"
            )
        return ParserResult(
            status="ok",
            items=items,
            info=info,
            page_count=_pdf_page_count(pdf_path),
            warnings=warnings,
            parser_quality={
                "page_chunks_applied": page_mapped,
                "output_shape": shape_note,
                "page_count_source": "pdf",
            },
        )


def _call_pymupdf4llm(pdf_path: str, *, page_chunks: bool = True) -> Any:
    """Convert a PDF to markdown with page-level output (default path).

    ``page_chunks=True`` keeps real page numbers; the returned shape may be a
    list of per-page dicts, a single dict, or (legacy) a plain string.
    """
    import pymupdf4llm

    return pymupdf4llm.to_markdown(str(pdf_path), page_chunks=page_chunks)


def _chunked_to_items(chunks: Any) -> tuple[list[ParserItem], bool, str]:
    """Convert pymupdf4llm output into items, preserving real page numbers.

    Handles three public shapes:
    - a list of per-page chunk dicts (``metadata["page_number"]`` 1-based or
      ``metadata["page"]`` 0-based);
    - a single chunk dict (one page);
    - a plain markdown string (legacy shape -> ``page=0`` items, no
      provenance; the caller adds the degraded warning).

    Returns ``(items, page_mapped, shape_note)``.
    """
    if isinstance(chunks, str):
        inner_items, _mapped, _shape = _markdown_to_items(chunks)
        return inner_items, False, "legacy_string"
    if isinstance(chunks, dict):
        page = _chunk_page_number(chunks, 0)
        inner_items, _mapped, _shape = _markdown_to_items(
            str(chunks.get("text") or ""), page=page
        )
        return inner_items, True, "single_chunk_dict"
    if isinstance(chunks, list):
        items: list[ParserItem] = []
        order = 0
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, dict):
                continue
            page = _chunk_page_number(chunk, index)
            chunk_items, _mapped, _shape = _markdown_to_items(
                str(chunk.get("text") or ""), page=page
            )
            for item in chunk_items:
                item.order = order
                item.item_id = f"pymupdf4llm_{order}"
                item.source_item_id = f"pymupdf4llm_item_{order}"
                order += 1
                items.append(item)
        return items, True, "page_chunks_list"
    inner_items, _mapped, _shape = _markdown_to_items(str(chunks))
    return inner_items, False, "unknown_shape"


def _chunk_page_number(chunk: dict[str, Any], index: int) -> int:
    """Real 1-based page number from a pymupdf4llm page chunk.

    The layout path stores 1-based ``metadata["page_number"]``; the rag path
    stores 0-based ``metadata["page"]``. Falling back to the list position is
    only used when neither key exists.
    """
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        page_number = metadata.get("page_number")
        if isinstance(page_number, int) and page_number > 0:
            return page_number
        page = metadata.get("page")
        if isinstance(page, int) and page >= 0:
            return page + 1
    return index + 1


def _markdown_to_items(markdown: str, *, page: int = 0) -> tuple[list[ParserItem], bool, str]:
    """Parse markdown text into items on a single page (or ``page=0``)."""
    items: list[ParserItem] = []
    order = 0
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        item_type = "heading" if line.startswith("#") else "paragraph"
        if item_type == "heading":
            level = min(line.count("#"), 6)
            text = line.lstrip("#").strip()
            content = {"level": level}
        else:
            level = 1
            text = line
            content = {}
        items.append(
            ParserItem(
                item_id=f"pymupdf4llm_{order}",
                item_type=item_type,
                text=text,
                order=order,
                page=page,
                bbox=None,
                source_item_id=f"pymupdf4llm_item_{order}",
                level=level,
                content=content,
            )
        )
        order += 1
    return items, page > 0, "parsed_lines"


def _pdf_page_count(pdf_path: str) -> int | None:
    """Real PDF page count via PyMuPDF (``None`` when unavailable)."""
    try:
        import fitz

        document = fitz.open(pdf_path)
        try:
            return document.page_count
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - optional page-count hint only
        return None


def _factory(config: Layer2Config) -> Pymupdf4LLMParserAdapter:
    return Pymupdf4LLMParserAdapter(config)


register_adapter("pymupdf4llm", _factory)
