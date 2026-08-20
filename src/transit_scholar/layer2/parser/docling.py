"""Docling primary parser adapter (FR-003, FR-007).

Imports ``docling`` lazily; when the dependency is absent the adapter reports
``dependency_missing`` / ``parser_unavailable`` and never fabricates a
successful parse. The conversion mapping to normalized ``ParserItem`` records
only runs when the dependency is actually installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from transit_scholar.layer2 import util as _util
from transit_scholar.layer2.config import (
    DOCLING_ARTIFACTS_PATH_ENV,
    Layer2Config,
    config_hash,
    resolve_docling_artifacts_path,
)
from transit_scholar.layer2.parser.base import (
    ParserAdapter,
    ParserAvailability,
    ParserInfo,
    ParserItem,
    ParserResult,
    register_adapter,
)


class _DoclingArtifactsPathInvalidError(RuntimeError):
    """``DOCLING_ARTIFACTS_PATH`` is set but does not point to a directory.

    Raised instead of silently falling back to the default model download
    behavior, so the failure is structured and the manifest never claims a
    config that was not actually applied.
    """


class DoclingParserAdapter(ParserAdapter):
    name = "docling"

    def __init__(self, config: Layer2Config | None = None) -> None:
        self._config = config

    @property
    def version(self) -> str:
        # Real installed-package version, not a hardcoded placeholder; stable
        # ``unavailable`` string when the dependency is absent.
        return _util.dependency_version("docling") or "unavailable"

    @property
    def config(self) -> dict[str, Any]:
        return {
            "pipeline": {
                "layout": True,
                "reading_order": True,
                "table_structure": True,
                "provenance": True,
                "picture_description": False,
                "chart_understanding": False,
                "llm_summary": False,
                "formula_enrichment": False,
            },
            # The resolved value of DOCLING_ARTIFACTS_PATH (None when unset).
            # Included in the config hash so a changed artifacts directory
            # invalidates benchmark/resume unit keys instead of silently
            # reusing results produced under different model config.
            "artifacts_path": resolve_docling_artifacts_path(),
        }

    def availability(self) -> ParserAvailability:
        try:
            import docling  # noqa: F401

            return ParserAvailability(available=True, version=self.version)
        except ImportError:
            return ParserAvailability(
                available=False, reason="dependency_missing", version=None
            )

    def parse(self, pdf_path: str) -> ParserResult:
        try:
            import docling  # noqa: F401
        except ImportError:
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
                    "docling is not installed; the primary parser is unavailable "
                    "(reporting truthfully instead of fabricating a parse)"
                ),
            )
        try:
            return self._convert(pdf_path)
        except _DoclingArtifactsPathInvalidError as exc:
            info = ParserInfo(
                name=self.name,
                version=self.version,
                config=self.config,
                config_hash=self.config_hash,
            )
            return ParserResult(
                status="error",
                info=info,
                error_code="DOCLING_ARTIFACTS_PATH_INVALID",
                error_message=str(exc),
            )
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
                error_code="DOCLING_CONVERSION_FAILED",
                error_message=f"docling conversion raised: {exc}",
            )

    def _convert(self, pdf_path: str) -> ParserResult:
        converter, applied_config, warnings = self._make_converter()
        result = converter.convert(str(pdf_path))
        document = result.document
        items: list[ParserItem] = []
        order = 0
        # Docling >= 2.x yields ``(item, level)`` tuples; older docs with a
        # ``dict`` layout are handled defensively.
        iterator = document.iterate_items()
        for entry in iterator:
            if isinstance(entry, tuple):
                element, level = entry
            else:
                element = entry
                level = 1
            label = str(getattr(element, "label", _DummyLabel()).value)
            item_type = _map_docling_label(label)
            text = getattr(element, "text", None) or getattr(element, "markdown", None) or ""
            prov = getattr(element, "prov", None) or []
            page = None
            for p in prov:
                page_no = getattr(p, "page_no", None)
                if page_no is not None:
                    page = page_no
                    break
            bbox = None
            if prov:
                p0 = getattr(prov[0], "bbox", None)
                if p0 is not None:
                    try:
                        bbox = [float(v) for v in p0.as_tuple()]
                    except Exception:  # noqa: BLE001
                        bbox = None
            content: dict[str, Any] = {}
            if item_type == "table":
                markdown = getattr(element, "export_to_markdown", None)
                content["markdown"] = markdown() if callable(markdown) else ""
                content["cells"] = []
            if item_type == "equation":
                content["latex"] = getattr(element, "latex", None) or ""
                if not content["latex"]:
                    content["latex"] = getattr(element, "orig", "") or text
                content["raw_text"] = text
            item_level = int(level) if isinstance(level, int) else 1
            items.append(
                ParserItem(
                    item_id=f"docling_{order}",
                    item_type=item_type,
                    text=text,
                    order=order,
                    page=int(page) if page is not None else 0,
                    bbox=bbox,
                    source_item_id=f"docling_item_{order}",
                    level=item_level,
                    content=content,
                )
            )
            order += 1
        info = ParserInfo(
            name=self.name,
            version=self.version,
            config=applied_config,
            config_hash=config_hash(applied_config),
        )
        return ParserResult(
            status="ok",
            items=items,
            info=info,
            page_count=document.pages and len(document.pages) or None,
            warnings=warnings,
            parser_quality={
                "docling_status": getattr(
                    getattr(result, "status", None), "value", None
                ),
                "docling_error_count": len(getattr(result, "errors", []) or []),
                "applied_pipeline_options": applied_config.get("applied_via"),
            },
        )

    def _make_converter(self) -> tuple[Any, dict[str, Any], list[str]]:
        """Build a ``DocumentConverter`` with the production pipeline options.

        The declared production config (layout/reading-order/table-structure/
        provenance ON, enrichment OFF) is applied through the installed
        Docling version's public options API when possible. When
        ``DOCLING_ARTIFACTS_PATH`` is set, the resolved local model directory
        is applied through the official ``PdfPipelineOptions.artifacts_path``
        field; an invalid directory is a structured failure (never a silent
        fallback to default downloads). The *actually applied* options --
        including a fallback to the default converter with a structured
        warning -- are returned so the manifest records facts, not declared
        intentions.
        """
        from docling.document_converter import DocumentConverter

        artifacts_path = resolve_docling_artifacts_path()
        applied: dict[str, Any] = {
            "pipeline": {
                "layout": True,
                "reading_order": (
                    "builtin ReadingOrderModel (no PdfPipelineOptions field "
                    "in docling 2.119.0; enabled by default)"
                ),
                "table_structure": True,
                "provenance": "docling native prov (always on)",
                "picture_description": False,
                "chart_understanding": False,
                "llm_summary": False,
                "formula_enrichment": False,
            },
            "artifacts": {
                "path": artifacts_path,
                "env": DOCLING_ARTIFACTS_PATH_ENV,
                "applied": False,
                "note": (
                    "DOCLING_ARTIFACTS_PATH unset; default model download "
                    "behavior"
                    if artifacts_path is None
                    else "DOCLING_ARTIFACTS_PATH set; awaiting option application"
                ),
            },
        }
        warnings: list[str] = []
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import PdfFormatOption

            options = PdfPipelineOptions()
            options.do_table_structure = True
            options.do_picture_description = False
            options.do_chart_extraction = False
            options.do_picture_classification = False
            options.do_formula_enrichment = False
            if artifacts_path is not None:
                _validate_docling_artifacts_dir(artifacts_path)
                options.artifacts_path = Path(artifacts_path)
                applied["artifacts"]["applied"] = True
                applied["artifacts"]["note"] = (
                    "local DOCLING_ARTIFACTS_PATH directory in effect "
                    "(PdfPipelineOptions.artifacts_path)"
                )
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=options)
                }
            )
            applied["applied_via"] = "PdfPipelineOptions + PdfFormatOption"
        except _DoclingArtifactsPathInvalidError:
            raise
        except Exception as exc:  # noqa: BLE001 - defensive option application
            warnings.append(
                "docling pipeline options could not be applied "
                f"({type(exc).__name__}: {exc}); using the default converter"
            )
            converter = DocumentConverter()
            applied["applied_via"] = "default DocumentConverter (option application failed)"
            if artifacts_path is not None:
                applied["artifacts"]["applied"] = False
                applied["artifacts"]["note"] = (
                    "DOCLING_ARTIFACTS_PATH set but options application failed; "
                    "default converter used (recorded, not silent)"
                )
        return converter, applied, warnings


def _validate_docling_artifacts_dir(path: str) -> None:
    """Ensure the resolved ``DOCLING_ARTIFACTS_PATH`` is a real directory.

    Raises ``_DoclingArtifactsPathInvalidError`` so the adapter returns a
    structured ``DOCLING_ARTIFACTS_PATH_INVALID`` error instead of silently
    reverting to default model downloads.
    """
    resolved = Path(path).expanduser()
    if not resolved.is_dir():
        raise _DoclingArtifactsPathInvalidError(
            f"{DOCLING_ARTIFACTS_PATH_ENV}={resolved} does not point to a "
            "directory; refusing to silently ignore it (no model download "
            "fallback)"
        )


class _DummyLabel:
    """Fallback when a mock/fake item carries no ``label`` attribute."""

    value = "TEXT"


def _map_docling_label(label: str) -> str:
    lowered = label.lower()
    if "heading" in lowered or lowered in ("section_header", "title"):
        return "heading"
    if "table" in lowered:
        return "table"
    if "picture" in lowered or "figure" in lowered or lowered == "chart":
        return "figure"
    if "caption" in lowered:
        return "caption"
    if "equation" in lowered or "formula" in lowered:
        return "equation"
    if "list" in lowered:
        return "list"
    if "footnote" in lowered or "page_footer" in lowered:
        return "footnote"
    if "reference" in lowered or "bibliography" in lowered:
        return "reference"
    if "paragraph" in lowered or "text" in lowered:
        return "paragraph"
    return "other"


def _factory(config: Layer2Config) -> DoclingParserAdapter:
    return DoclingParserAdapter(config)


register_adapter("docling", _factory)
