"""Lightweight PDF reading via PyMuPDF.

Only reads document metadata, file facts, and the first few pages of text.
Does NOT do OCR, full-text extraction, or structural analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from transit_scholar.config import settings


@dataclass
class PdfReadResult:
    """Result of reading a PDF for metadata extraction."""

    metadata: dict[str, str] = field(default_factory=dict)
    page_count: int | None = None
    pdf_version: str | None = None
    is_encrypted: bool = False
    first_pages_text: str = ""
    is_scanned_candidate: bool = False
    partial: bool = False
    partial_messages: list[str] = field(default_factory=list)


# Character threshold below which a page is considered "low text".
_LOW_TEXT_THRESHOLD = 50


def read_pdf(path: str | Path) -> PdfReadResult:
    """Open a PDF and extract metadata, file facts, and first-pages text.

    Reads at most ``settings.light_parse_page_count`` pages. If the PDF
    opens but some pages fail to yield text, the result is marked partial
    rather than raising.
    """
    result = PdfReadResult()
    path = Path(path)

    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        result.partial = True
        result.partial_messages.append(f"Failed to open PDF: {exc}")
        return result

    try:
        # --- Document metadata ------------------------------------------------
        raw_meta = doc.metadata or {}
        result.metadata = {k: str(v) for k, v in raw_meta.items() if v}

        # --- File facts -------------------------------------------------------
        result.page_count = doc.page_count
        result.pdf_version = result.metadata.get("format") or None
        result.is_encrypted = bool(doc.needs_pass)

        # --- First pages text -------------------------------------------------
        max_pages = settings.light_parse_page_count
        page_texts: list[str] = []
        total_chars = 0
        for page_idx in range(min(max_pages, doc.page_count)):
            try:
                page = doc.load_page(page_idx)
                text = page.get_text("text")
                page_texts.append(text)
                total_chars += len(text)
            except Exception as exc:  # noqa: BLE001
                result.partial = True
                result.partial_messages.append(
                    f"Failed to read page {page_idx}: {exc}"
                )

        result.first_pages_text = "\n".join(page_texts)

        # --- Heuristic: scanned candidate ------------------------------------
        # If the first few pages yield very little text, flag as scanned.
        if result.page_count and result.page_count > 0:
            avg_chars = total_chars / min(max_pages, result.page_count)
            if avg_chars < _LOW_TEXT_THRESHOLD:
                result.is_scanned_candidate = True
    finally:
        doc.close()

    return result
