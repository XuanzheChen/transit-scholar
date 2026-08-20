"""Layer2 Step1 parse pipeline: gate -> parse -> validate -> fallback (FR-004).

``parse_paper`` is the single-paper entry point. It always consults
``get_second_layer_input(paper_id)`` first and never reads the primary PDF
unless the gate is ``ready``. Parse runs are versioned: the same trigger inputs
reuse the current run; force or any trigger change creates a new run that only
promotes to ``current.json`` when passed or degraded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transit_scholar.config import settings as global_settings
from transit_scholar.layer2.chunker import ChunkBuilder
from transit_scholar.layer2.config import Layer2Config
from transit_scholar.layer2.markdown import MarkdownRenderer
from transit_scholar.layer2.normalizer import NormalizationOutput, Normalizer
from transit_scholar.layer2.parser.base import ParserAdapter, ParserResult
from transit_scholar.layer2.parser.registry import resolve_parsers
from transit_scholar.layer2.paths import (
    load_current,
    run_paths,
    save_current,
)
from transit_scholar.layer2.retrieval.api import build_retrieval
from transit_scholar.layer2.schema import (
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
    ParsePaperResult,
)
from transit_scholar.layer2.util import new_parse_run_id, now_utc_iso, sha256_file
from transit_scholar.layer2.validation import ParseValidation, ParseValidator
from transit_scholar.workflow.service import get_second_layer_input

MANIFEST_FORMAT = "transit-scholar-layer2-parser-manifest-v1"


@dataclass
class _Attempt:
    adapter: ParserAdapter
    result: ParserResult
    normalized: NormalizationOutput | None = None
    validation: ParseValidation | None = None


@dataclass
class _Acceptance:
    parser_used: str
    status: str
    attempt: _Attempt
    warnings: list[str]
    error_code: str | None
    error_message: str | None


def parse_paper(
    paper_id: str,
    *,
    force: bool = False,
    config: Layer2Config | None = None,
) -> ParsePaperResult:
    """Parse a single paper into Layer2 artifacts (gate-gated)."""
    config = config or Layer2Config.from_settings(global_settings)

    gate = get_second_layer_input(paper_id)
    if gate.status != "ready":
        blockers = list(gate.blockers)
        return ParsePaperResult(
            paper_id=paper_id,
            file_id=None,
            parse_run_id=None,
            status="blocked",
            parser_used=None,
            output_dir=None,
            warnings=[],
            blockers=blockers,
            error_code=gate.error_code or (blockers[0] if blockers else "gate_blocked"),
            error_message="Layer1 second-layer gate is not ready",
        )

    pdf_path = Path(gate.source_pdf_path)
    if not pdf_path.is_file():
        return ParsePaperResult(
            paper_id=paper_id,
            file_id=gate.primary_file_id,
            parse_run_id=None,
            status="blocked",
            parser_used=None,
            output_dir=None,
            warnings=[],
            blockers=["source_file_missing"],
            error_code="source_file_missing",
            error_message="primary PDF is missing on disk",
        )

    source_sha256 = sha256_file(pdf_path)
    adapters = resolve_parsers(config)
    if not adapters:
        return ParsePaperResult(
            paper_id=paper_id,
            file_id=gate.primary_file_id,
            parse_run_id=None,
            status="needs_review",
            parser_used=None,
            output_dir=None,
            warnings=[],
            blockers=[],
            error_code="PARSER_UNAVAILABLE",
            error_message="no parser adapter is available",
        )

    primary = adapters[0]
    triggers = _trigger_tuple(source_sha256, adapters, config)

    paper_dir = config.parsed_paper_dir(paper_id)
    current_run = load_current(paper_dir)
    if current_run and not force:
        rp = run_paths(config, paper_id, current_run)
        if rp.manifest_path.is_file():
            manifest = _read_json(rp.manifest_path)
            if _manifest_matches_triggers(manifest, triggers):
                extra_warnings: list[str] = []
                changed = _derived_differences(manifest, config)
                if changed:
                    rebuilt = rebuild_derived(paper_id, config=config)
                    if rebuilt.get("status") == "ok":
                        manifest = _read_json(rp.manifest_path)
                        extra_warnings.append(
                            "derived artifacts rebuilt: " + ",".join(changed)
                        )
                return _reuse_result(
                    gate, current_run, rp, manifest, extra_warnings=extra_warnings
                )

    run_id = new_parse_run_id()
    run_created_at = now_utc_iso()
    rp = run_paths(config, paper_id, run_id)
    rp.run_dir.mkdir(parents=True, exist_ok=True)
    rp.assets_figures_dir.mkdir(parents=True, exist_ok=True)

    acceptance = _run_parse_chain(
        adapters, pdf_path, gate, config, source_sha256, run_id, run_created_at
    )

    manifest = _build_manifest(
        config, gate, source_sha256, run_id, acceptance, adapters, rp, run_created_at
    )
    rp.manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    attempt = acceptance.attempt
    normalized = attempt.normalized
    if normalized is not None:
        _materialize_figures(normalized.blocks, rp.assets_figures_dir, config)
        _write_canonical(rp, normalized, acceptance.status)
        _write_derived(config, paper_id, run_id, normalized)

    if acceptance.status in ("passed", "degraded"):
        save_current(paper_dir, run_id)

    return ParsePaperResult(
        paper_id=paper_id,
        file_id=gate.primary_file_id,
        parse_run_id=run_id,
        status=acceptance.status,
        parser_used=acceptance.parser_used,
        output_dir=str(rp.run_dir),
        warnings=list(acceptance.warnings),
        blockers=[],
        error_code=acceptance.error_code,
        error_message=acceptance.error_message,
    )


def rebuild_derived(
    paper_id: str, *, config: Layer2Config | None = None
) -> dict[str, Any]:
    """Regenerate derived views without reparsing the PDF (FR-013).

    Only renderer/chunker/retrieval artifacts change; canonical files stay
    byte-identical and the parse run id is unchanged.
    """
    config = config or Layer2Config.from_settings(global_settings)
    current_run = load_current(config.parsed_paper_dir(paper_id))
    if current_run is None:
        return {"status": "unavailable", "error_code": "no_current_run"}
    rp = run_paths(config, paper_id, current_run)
    if not rp.document_path.is_file() or not rp.sections_path.is_file() or not rp.blocks_path.is_file():
        return {"status": "unavailable", "error_code": "canonical_missing"}

    document = CanonicalDocument.from_dict(_read_json(rp.document_path))
    sections = _read_sections(rp.sections_path)
    blocks = _read_blocks(rp.blocks_path)
    normalized = NormalizationOutput(document=document, sections=sections, blocks=blocks)
    _write_derived(config, paper_id, current_run, normalized)

    if rp.manifest_path.is_file():
        manifest = _read_json(rp.manifest_path)
        manifest["renderer_version"] = config.renderer_version
        manifest["chunker_version"] = config.chunker_version
        manifest["embedding_model"] = config.resolved_embedding_model
        manifest["embedding_model_revision"] = "provider-declared"
        manifest["reranker_model"] = config.resolved_reranker_model
        manifest["reranker_model_revision"] = "provider-declared"
        manifest["config"] = config.as_manifest_section()
        rp.manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    return {"status": "ok", "parse_run_id": current_run}


# ---------------------------------------------------------------------------
# Parse chain
# ---------------------------------------------------------------------------


def _run_parse_chain(
    adapters: list[ParserAdapter],
    pdf_path: Path,
    gate,
    config: Layer2Config,
    source_sha256: str,
    run_id: str,
    run_created_at: str,
) -> _Acceptance:
    attempts: list[_Attempt] = []
    for index, adapter in enumerate(adapters):
        result = adapter.parse(str(pdf_path))
        attempt = _Attempt(adapter=adapter, result=result)
        attempts.append(attempt)
        if result.status != "ok":
            continue
        normalized = Normalizer(config).normalize(
            result,
            paper_id=gate.paper_id,
            file_id=gate.primary_file_id,
            source_sha256=source_sha256,
            parse_run_id=run_id,
            page_heights=_page_heights(pdf_path),
            created_at=run_created_at,
        )
        validation = ParseValidator(config).validate(
            result, normalized.document, normalized.sections, normalized.blocks
        )
        attempt.normalized = normalized
        attempt.validation = validation

        if validation.status == "passed":
            return _Acceptance(
                parser_used=adapter.name,
                status="passed",
                attempt=attempt,
                warnings=_attempt_warnings(attempt),
                error_code=None,
                error_message=None,
            )
        if index > 0:
            # A fallback result that is degraded or failed -> needs_review
            return _Acceptance(
                parser_used=adapter.name,
                status="needs_review",
                attempt=attempt,
                warnings=_attempt_warnings(attempt),
                error_code=validation.error_code or "PARSE_VALIDATION_FAILED",
                error_message=(
                    f"fallback parser {adapter.name} did not pass validation: "
                    f"{validation.status}"
                ),
            )
        # Primary degraded/failed -> whole-document fallback continues.

    last = attempts[-1]
    if last.validation is None:
        return _Acceptance(
            parser_used=last.adapter.name,
            status="needs_review",
            attempt=last,
            warnings=list(last.result.warnings),
            error_code=last.result.error_code or "PARSER_FAILED",
            error_message=f"no usable parse from {last.adapter.name}",
        )
    if last.validation.status == "degraded":
        return _Acceptance(
            parser_used=last.adapter.name,
            status="degraded",
            attempt=last,
            warnings=_attempt_warnings(last),
            error_code=None,
            error_message=None,
        )
    return _Acceptance(
        parser_used=last.adapter.name,
        status="needs_review",
        attempt=last,
        warnings=_attempt_warnings(last),
        error_code=last.validation.error_code or "PARSE_VALIDATION_FAILED",
        error_message="no parser produced an acceptable canonical document",
    )


def _attempt_warnings(attempt: _Attempt) -> list[str]:
    warnings = list(attempt.result.warnings)
    if attempt.validation is not None:
        warnings.extend(attempt.validation.warnings)
    return warnings


def _requested_chain_hash(adapters: list[ParserAdapter]) -> str:
    """Stable hash over the full requested primary->fallback adapter chain.

    The chain hash covers every adapter's name, version and config hash, so a
    fallback-accepted run is reused on identical input+chain (zero reparse)
    while any chain-member config change correctly invalidates the cache.
    """
    from transit_scholar.layer2.util import stable_json_hash

    chain = [
        {
            "name": adapter.name,
            "version": adapter.version,
            "config_hash": adapter.config_hash,
        }
        for adapter in adapters
    ]
    return stable_json_hash({"chain": chain})


def _requested_chain(adapters: list[ParserAdapter]) -> list[dict[str, str]]:
    return [
        {
            "name": adapter.name,
            "version": adapter.version,
            "config_hash": adapter.config_hash,
        }
        for adapter in adapters
    ]


def _trigger_tuple(
    source_sha256: str, adapters: list[ParserAdapter], config: Layer2Config
) -> tuple[str, ...]:
    return (
        source_sha256,
        _requested_chain_hash(adapters),
        config.canonical_schema_version,
        config.normalizer_version,
    )


def _manifest_matches_triggers(manifest: dict[str, Any], triggers: tuple[str, ...]) -> bool:
    source_sha256, chain_hash, schema_version, normalizer_version = triggers
    # Old manifests without the chain hash never match (safe one-time reparse).
    return (
        manifest.get("source_sha256") == source_sha256
        and manifest.get("requested_parser_chain_hash") == chain_hash
        and manifest.get("canonical_schema_version") == schema_version
        and manifest.get("normalizer_version") == normalizer_version
    )


#: Derived fingerprints whose changes must NOT reparse the PDF but must rebuild
#: the corresponding derived artifacts automatically.
_DERIVED_FINGERPRINT_KEYS = (
    "renderer_version",
    "chunker_version",
    "chunking",
    "retrieval",
    "embedding_model",
    "reranker_model",
)


def _derived_fingerprint(config: Layer2Config) -> dict[str, Any]:
    section = config.as_manifest_section()
    return {
        "renderer_version": config.renderer_version,
        "chunker_version": config.chunker_version,
        "chunking": section["chunking"],
        "retrieval": section["retrieval"],
        "embedding_model": config.resolved_embedding_model,
        "reranker_model": config.resolved_reranker_model,
    }


def _manifest_derived_fingerprint(manifest: dict[str, Any]) -> dict[str, Any]:
    config_section = manifest.get("config") or {}
    return {
        "renderer_version": manifest.get("renderer_version"),
        "chunker_version": manifest.get("chunker_version"),
        "chunking": config_section.get("chunking"),
        "retrieval": config_section.get("retrieval"),
        "embedding_model": manifest.get("embedding_model"),
        "reranker_model": manifest.get("reranker_model"),
    }


def _derived_differences(manifest: dict[str, Any], config: Layer2Config) -> list[str]:
    """Compare the manifest's derived fingerprint with the current config.

    Returns the list of changed derived keys. ``parse_paper`` calls this on
    the reuse path so derived rebuilds happen automatically without any manual
    ``rebuild_derived()`` invocation.
    """
    current = _derived_fingerprint(config)
    stored = _manifest_derived_fingerprint(manifest)
    return [
        key for key in _DERIVED_FINGERPRINT_KEYS if stored.get(key) != current[key]
    ]


def _reuse_result(
    gate,
    current_run: str,
    rp,
    manifest: dict[str, Any],
    *,
    extra_warnings: list[str] | None = None,
) -> ParsePaperResult:
    status = manifest.get("parse_status", "needs_review")
    warnings = list(manifest.get("warnings", [])) + ["reused existing parse run"]
    if extra_warnings:
        warnings.extend(extra_warnings)
    return ParsePaperResult(
        paper_id=gate.paper_id,
        file_id=gate.primary_file_id,
        parse_run_id=current_run,
        status=status,
        parser_used=manifest.get("parser_name"),
        output_dir=str(rp.run_dir),
        warnings=warnings,
        blockers=[],
        error_code=manifest.get("error_code"),
        error_message=manifest.get("error_message"),
    )


def _build_manifest(
    config: Layer2Config,
    gate,
    source_sha256: str,
    run_id: str,
    acceptance: _Acceptance,
    adapters: list[ParserAdapter],
    rp,
    created_at: str,
) -> dict[str, Any]:
    result = acceptance.attempt.result
    warnings = list(acceptance.warnings)
    warnings.extend(result.warnings)
    parser_version = result.info.version if result.info else "unknown"
    parser_config = result.info.config if result.info else {}
    parser_config_hash = result.info.config_hash if result.info else ""
    page_count = (
        acceptance.attempt.normalized.document.page_count
        if acceptance.attempt.normalized
        else result.page_count
    )
    chain = _requested_chain(adapters)
    return {
        "format_version": MANIFEST_FORMAT,
        "paper_id": gate.paper_id,
        "file_id": gate.primary_file_id,
        "parse_run_id": run_id,
        "source_sha256": source_sha256,
        "requested_parser_chain": chain,
        "requested_parser_chain_hash": _requested_chain_hash(adapters),
        "parser_name": acceptance.parser_used,
        "parser_version": parser_version,
        "parser_config": parser_config,
        "parser_config_hash": parser_config_hash,
        "canonical_schema_version": config.canonical_schema_version,
        "normalizer_version": config.normalizer_version,
        "renderer_version": config.renderer_version,
        "chunker_version": config.chunker_version,
        "embedding_model": config.resolved_embedding_model,
        "embedding_model_revision": "provider-declared",
        "reranker_model": config.resolved_reranker_model,
        "reranker_model_revision": "provider-declared",
        "page_count": page_count,
        "parse_status": acceptance.status,
        "warnings": warnings,
        "error_code": acceptance.error_code,
        "error_message": acceptance.error_message,
        "created_at": created_at,
        "config": config.as_manifest_section(),
    }


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------


def _write_canonical(rp, normalized: NormalizationOutput, parse_status: str) -> None:
    document = normalized.document
    document.parse_status = parse_status
    document.section_count = len(normalized.sections)
    document.block_count = len(normalized.blocks)
    rp.document_path.write_text(
        json.dumps(document.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rp.sections_path.write_text(
        json.dumps(
            [s.to_dict() for s in normalized.sections], indent=2, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    rp.blocks_path.write_text(
        "\n".join(
            json.dumps(b.to_dict(), ensure_ascii=False) for b in normalized.blocks
        )
        + ("\n" if normalized.blocks else ""),
        encoding="utf-8",
    )


def _materialize_figures(
    blocks: list[CanonicalBlock], assets_dir: Path, config: Layer2Config
) -> None:
    """Save figure image bytes and set ``content.asset_path``."""
    for block in blocks:
        if block.block_type != "figure":
            continue
        image_bytes = block.content.pop("_image_bytes", None)
        ext = block.content.pop("_image_ext", None) or "png"
        if image_bytes is None or not config.save_figure_assets:
            continue
        filename = f"fig_{block.order:04d}.{ext}"
        assets_dir.mkdir(parents=True, exist_ok=True)
        (assets_dir / filename).write_bytes(image_bytes)
        block.content["asset_path"] = f"assets/figures/{filename}"


def _write_derived(
    config: Layer2Config, paper_id: str, parse_run_id: str, normalized: NormalizationOutput
) -> None:
    rp = run_paths(config, paper_id, parse_run_id)
    markdown = MarkdownRenderer(config).render(
        normalized.blocks, normalized.sections, parse_run_id
    )
    rp.markdown_path.write_text(markdown.text, encoding="utf-8")
    rp.markdown_map_path.write_text(
        "\n".join(
            json.dumps(e.to_dict(), ensure_ascii=False) for e in markdown.entries
        )
        + ("\n" if markdown.entries else ""),
        encoding="utf-8",
    )
    chunks = ChunkBuilder(config).build(
        normalized.blocks,
        normalized.sections,
        paper_id=paper_id,
        parse_run_id=parse_run_id,
    )
    rp.chunks_path.write_text(
        "\n".join(
            json.dumps(c.to_dict(), ensure_ascii=False) for c in chunks
        )
        + ("\n" if chunks else ""),
        encoding="utf-8",
    )
    build_retrieval(paper_id, config=config)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_sections(path: Path) -> list[CanonicalSection]:
    return [
        CanonicalSection.from_dict(record)
        for record in json.loads(path.read_text(encoding="utf-8"))
    ]


def _read_blocks(path: Path) -> list[CanonicalBlock]:
    return [
        CanonicalBlock.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _page_heights(pdf_path: Path) -> dict[int, float]:
    try:
        import fitz

        document = fitz.open(pdf_path)
        try:
            return {
                index + 1: document[index].rect.height
                for index in range(document.page_count)
            }
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - optional geometry hint only
        return {}
