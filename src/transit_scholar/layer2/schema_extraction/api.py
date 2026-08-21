"""L2S2 Package D public API.

Stable, storage-backed entry points for Layer3:

- ``extract_schema``  -> in-memory extraction (Package B) + validation
  (Package C) + complete persistence + atomic current update;
- ``get_schema``      -> read the current or a historical run's instance;
- ``get_field``       -> read one field result;
- ``validate_schema`` -> read-only re-validation of a stored run;
- ``recheck_fields``  -> targeted recheck of selected fields producing a new
  complete run (old runs are never overwritten).

Every function supports deterministic injection (llm_client, retrieval,
canonical_reader, verifier, cross_field_validators, recheck_callable,
storage / storage_root, top_k). The normal runtime path resolves one shared
LLM client from the unified config (project-root ``.env`` via the config
bootstrap -> ``LLMConfig.from_env`` -> ``resolve_runtime_llm_client``) and
hands the **same** client object to the Extractor, the real
``StructuredSemanticVerifier`` and the default Targeted Recheck. FakeLLM /
offline defaults are used only when explicitly injected or when the resolved
provider is ``fake`` (explicit fake mode). Unconfigured / network-blocked
runtimes fail with an explicit ``LLMUnavailableError`` — never a silent fake.

Failures are explicit: run-level extraction failures raise
``SchemaExtractionRunError`` (never masqueraded as ``not_found``); storage
failures raise ``SchemaStorageError`` subclasses and never touch
``current.json``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel

from .engine import build_runtime_recheck_callable, extract_schema_instance_in_memory
from .hashing import compute_schema_hash
from .llm import resolve_runtime_llm_client
from .loader import get_schema_definition
from .models import FieldResult, SchemaInstance
from .persistence import (
    PROMPT_VERSION,
    CurrentPointer,
    RunManifest,
    SchemaRunStorage,
    compute_extraction_config_hash,
)
from .recheck import RecheckTrace, run_targeted_recheck
from .retrieval import FakeRetrieval
from .semantic import FakeSemanticVerifier, StructuredSemanticVerifier
from .trace import ExtractionManifest
from .validation_pipeline import run_validation_pipeline_in_memory
from .validation_report import (
    ValidationReport,
    derive_report_status,
)

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class SchemaExtractionRunError(Exception):
    """Extraction failed at run level (e.g. ``schema_load_failed``).

    Carries the stable ``error_code`` from the Package B manifest. Never
    masqueraded as a ``not_found`` field result; no run files are written and
    the current pointer is untouched.
    """

    def __init__(self, message: str = "", *, error_code: str = "extraction_failed"):
        super().__init__(message or error_code)
        self.error_code = error_code


class SchemaIdMismatchError(Exception):
    """The stored schema id does not match the requested schema id."""


class SchemaFieldNotFoundError(Exception):
    """The requested field id does not exist in the schema definition."""


class SchemaFieldMissingError(Exception):
    """The field exists in the definition but is absent from the instance
    (e.g. the field-level extraction failed)."""


class SchemaRecheckError(Exception):
    """Targeted recheck failed for at least one target field.

    Carries the full ``RecheckTrace``; no new run files are written and the
    old current pointer stays untouched.
    """

    def __init__(self, trace: RecheckTrace, message: str = ""):
        super().__init__(message or "targeted recheck failed")
        self.trace = trace
        self.error_code = "recheck_failed"


# ---------------------------------------------------------------------------
# explicit schema switch (FR-007)
# ---------------------------------------------------------------------------

#: Environment variable controlling the L2S2 schema feature switch.
_SCHEMA_ENABLED_ENV = "TRANSIT_SCHOLAR_SCHEMA_ENABLED"

_SCHEMA_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})


def schema_enabled() -> bool:
    """Read the explicit ``TRANSIT_SCHOLAR_SCHEMA_ENABLED`` switch (FR-007).

    Default is enabled (unset / blank / unknown values stay enabled).
    ``0`` / ``false`` / ``no`` / ``off`` (case-insensitive) disable it;
    ``1`` / ``true`` / ``yes`` / ``on`` enable it. This is a read-only
    configuration boundary: it never turns off persistence or the public API
    by itself, and it does not affect L2S1.
    """
    value = os.environ.get(_SCHEMA_ENABLED_ENV)
    if value is None:
        return True
    return value.strip().lower() not in _SCHEMA_DISABLED_VALUES


# ---------------------------------------------------------------------------
# result object
# ---------------------------------------------------------------------------


class SchemaRunResult(BaseModel):
    """Stable return object for ``extract_schema`` / ``recheck_fields``.

    Callers never need to know the storage layout: everything is either in
    this object or reachable through the read APIs.
    """

    run_id: str
    paper_id: str
    schema_id: str
    instance: SchemaInstance
    manifest: ExtractionManifest
    report: ValidationReport
    run_manifest: RunManifest
    is_current: bool = True


# ---------------------------------------------------------------------------
# injection handling and offline defaults
# ---------------------------------------------------------------------------

#: Injection keys accepted by the public API (requirements 4.2 / AC-D-21).
_INJECTION_KEYS = frozenset(
    {
        "llm_client",
        "retrieval",
        "canonical_reader",
        "verifier",
        "cross_field_validators",
        "recheck_callable",
        "storage",
        "storage_root",
        "top_k",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_injections(kwargs: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(kwargs) - _INJECTION_KEYS)
    if unknown:
        raise TypeError(
            f"unexpected injection option(s): {', '.join(unknown)}; "
            f"supported: {', '.join(sorted(_INJECTION_KEYS))}"
        )
    return dict(kwargs)


def _resolve_storage(injections: dict[str, Any]) -> SchemaRunStorage:
    storage = injections.get("storage")
    storage_root = injections.get("storage_root")
    if storage is not None:
        return storage
    if storage_root is not None:
        return SchemaRunStorage(storage_root=Path(storage_root))
    return SchemaRunStorage()


def _default_retrieval() -> FakeRetrieval:
    return FakeRetrieval()


def _offline_canonical_reader(paper_id: str, block_ids: Iterable[str]) -> dict:
    """Offline default canonical reader: an empty canonical view.

    Deterministic and network-free, matching the offline FakeRetrieval
    default (which produces no evidence refs, so no blocks are ever
    requested).
    """
    return {}


def _resolve_runtime_context(
    inj: dict[str, Any], *, need_recheck: bool
) -> tuple[Any, Any, Any]:
    """Resolve the shared real-runtime LLM context once per public call.

    Returns ``(client, verifier, recheck_callable)``:

    - ``client`` = injected ``llm_client`` or ``resolve_runtime_llm_client()``
      (the only place the resolver runs when nothing is injected; a resolution
      failure raises ``LLMUnavailableError`` up — nothing is written);
    - ``verifier`` = injected verifier, else ``StructuredSemanticVerifier``
      on the **same** client for a real client, else the offline
      ``FakeSemanticVerifier`` for explicit fake mode;
    - ``recheck_callable`` = injected callable, else (only for
      ``need_recheck``) the offline default for fake mode, else
      ``build_runtime_recheck_callable`` on the **same** client for real mode.

    Explicit injection of ``llm_client`` / ``verifier`` / ``recheck_callable``
    always wins (AC-RW-06/07); the resolver is never called when a client is
    injected. ``extract_schema`` / ``validate_schema`` call with
    ``need_recheck=False`` so they install no recheck default (recheck only
    goes through ``recheck_fields``).
    """
    client = inj.get("llm_client") or resolve_runtime_llm_client()
    fake_mode = bool(getattr(client, "is_fake", False))

    verifier = inj.get("verifier")
    if verifier is None:
        if fake_mode:
            verifier = FakeSemanticVerifier(
                default_response={
                    "decision": "supported",
                    "confidence": None,
                    "notes": "offline default fake verifier",
                }
            )
        else:
            verifier = StructuredSemanticVerifier(client)

    recheck_callable = inj.get("recheck_callable")
    if recheck_callable is None and need_recheck:
        if fake_mode:
            recheck_callable = _default_recheck_callable
        else:
            retrieval = inj.get("retrieval") or _default_retrieval()
            top_k = int(inj.get("top_k", 8))
            recheck_callable = build_runtime_recheck_callable(
                client, retrieval=retrieval, top_k=top_k
            )
    return client, verifier, recheck_callable


def _default_recheck_callable(definition, field, paper_id) -> FieldResult:
    """Offline default recheck (explicit fake mode): an honest ``unclear``.

    Without a real re-extraction backend the offline default cannot confirm
    a value, so it records ``unclear`` (a legal conclusion per AC-D-30)
    instead of inventing data. Real mode installs
    ``build_runtime_recheck_callable`` instead; an explicitly injected
    ``recheck_callable`` always wins.
    """
    return FieldResult(
        value=None,
        status="unclear",
        confidence=None,
        notes="offline default recheck: no real re-extraction available",
    )


def _build_run_manifest(
    *,
    run_id: str,
    paper_id: str,
    schema_id: str,
    schema_version: str,
    schema_hash: str,
    llm_provider: str,
    llm_model: str,
    llm_fake: bool,
    status: str,
    created_at: str,
    run_reason: str,
    parent_run_id: str | None,
    top_k: int,
) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        paper_id=paper_id,
        schema_id=schema_id,
        schema_version=schema_version,
        schema_hash=schema_hash,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_fake=llm_fake,
        prompt_version=PROMPT_VERSION,
        extraction_config_hash=compute_extraction_config_hash(PROMPT_VERSION, top_k),
        source_parse_run_id=None,
        retrieval_manifest_hash=None,
        created_at=created_at,
        status=status,
        run_reason=run_reason,
        parent_run_id=parent_run_id,
    )


def _read_instance(
    storage: SchemaRunStorage,
    paper_id: str,
    schema_id: str,
    run_id: str | None,
) -> SchemaInstance:
    """Resolve a stored instance by current pointer or explicit run id."""
    if run_id is None:
        pointer = storage.read_current(paper_id)
        if pointer.schema_id != schema_id:
            raise SchemaIdMismatchError(
                f"current run {pointer.run_id!r} for paper {paper_id!r} uses "
                f"schema {pointer.schema_id!r}, not the requested "
                f"{schema_id!r}"
            )
        run_id = pointer.run_id
    stored = storage.read_run(paper_id, run_id)
    if stored.instance.schema_id != schema_id:
        raise SchemaIdMismatchError(
            f"run {run_id!r} for paper {paper_id!r} uses schema "
            f"{stored.instance.schema_id!r}, not the requested {schema_id!r}"
        )
    return stored.instance


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def extract_schema(
    paper_id: str,
    schema_id: str = "bus_control_rl",
    **injection_options: Any,
) -> SchemaRunResult:
    """Extract + validate + persist one schema run and update the current pointer.

    Order (AC-D-23): Package B in-memory extraction -> Package C validation
    pipeline -> write the four run files -> read-back verification -> atomic
    current.json update.
    """
    inj = _split_injections(injection_options)
    storage = _resolve_storage(inj)
    top_k = int(inj.get("top_k", 8))
    retrieval = inj.get("retrieval") or _default_retrieval()
    canonical_reader = inj.get("canonical_reader") or _offline_canonical_reader
    llm_client, verifier, recheck_callable = _resolve_runtime_context(
        inj, need_recheck=False
    )
    cross_field_validators = inj.get("cross_field_validators")

    run = extract_schema_instance_in_memory(
        paper_id,
        schema_id,
        llm_client=llm_client,
        retrieval=retrieval,
        top_k=top_k,
        canonical_reader=canonical_reader,
    )
    if run.instance is None or run.manifest.run_error_code:
        raise SchemaExtractionRunError(
            run.manifest.run_error_message or "schema extraction failed",
            error_code=run.manifest.run_error_code or "extraction_failed",
        )

    instance = run.instance
    manifest = run.manifest
    definition = get_schema_definition(schema_id)

    report = run_validation_pipeline_in_memory(
        definition,
        instance,
        canonical_reader=canonical_reader,
        verifier=verifier,
        cross_field_validators=cross_field_validators,
        recheck_callable=recheck_callable,
    )

    run_manifest = _build_run_manifest(
        run_id=manifest.run_id,
        paper_id=paper_id,
        schema_id=instance.schema_id,
        schema_version=instance.schema_version,
        schema_hash=compute_schema_hash(definition),
        llm_provider=manifest.llm_provider,
        llm_model=manifest.llm_model,
        llm_fake=manifest.llm_fake,
        status=report.status,
        created_at=manifest.created_at or _utc_now_iso(),
        run_reason="extract",
        parent_run_id=None,
        top_k=top_k,
    )
    finalized_manifest = storage.write_run(
        paper_id, manifest.run_id, instance, manifest, report, run_manifest
    )
    storage.verify_run_readable(
        paper_id, manifest.run_id, expected_schema_id=schema_id
    )
    pointer = CurrentPointer(
        paper_id=paper_id,
        schema_id=instance.schema_id,
        run_id=manifest.run_id,
        schema_version=instance.schema_version,
        schema_hash=compute_schema_hash(definition),
        created_at=finalized_manifest.created_at,
        status=report.status,
    )
    storage.write_current(paper_id, pointer)
    return SchemaRunResult(
        run_id=manifest.run_id,
        paper_id=paper_id,
        schema_id=instance.schema_id,
        instance=instance,
        manifest=manifest,
        report=report,
        run_manifest=finalized_manifest,
        is_current=True,
    )


def get_schema(
    paper_id: str,
    schema_id: str = "bus_control_rl",
    run_id: str | None = None,
    *,
    storage: Any = None,
    storage_root: Path | str | None = None,
) -> SchemaInstance:
    """Read the current (or a historical) run's SchemaInstance."""
    storage_obj = _resolve_storage({"storage": storage, "storage_root": storage_root})
    return _read_instance(storage_obj, paper_id, schema_id, run_id)


def get_field(
    paper_id: str,
    schema_id: str,
    field_id: str,
    run_id: str | None = None,
    *,
    storage: Any = None,
    storage_root: Path | str | None = None,
) -> FieldResult:
    """Read one field's result from the current (or a historical) run."""
    storage_obj = _resolve_storage({"storage": storage, "storage_root": storage_root})
    definition = get_schema_definition(schema_id)
    definition_field_ids = {
        field.id for section in definition.sections for field in section.fields
    }
    if field_id not in definition_field_ids:
        raise SchemaFieldNotFoundError(
            f"field {field_id!r} does not exist in schema {schema_id!r}"
        )
    instance = _read_instance(storage_obj, paper_id, schema_id, run_id)
    if field_id not in instance.fields:
        raise SchemaFieldMissingError(
            f"field {field_id!r} exists in schema {schema_id!r} but is absent "
            f"from the stored instance (field-level extraction failed)"
        )
    return instance.fields[field_id]


def validate_schema(
    paper_id: str,
    schema_id: str = "bus_control_rl",
    run_id: str | None = None,
    **injection_options: Any,
) -> ValidationReport:
    """Re-validate a stored run's instance and return a fresh report.

    Strictly read-only (AC-D-20): the stored run files and ``current.json``
    are never modified, and an injected ``recheck_callable`` is deliberately
    not triggered (recheck only goes through ``recheck_fields``).
    """
    inj = _split_injections(injection_options)
    storage = _resolve_storage(inj)
    canonical_reader = inj.get("canonical_reader") or _offline_canonical_reader
    _, verifier, _ = _resolve_runtime_context(inj, need_recheck=False)
    cross_field_validators = inj.get("cross_field_validators")

    instance = _read_instance(storage, paper_id, schema_id, run_id)
    definition = get_schema_definition(schema_id)
    working_instance = instance.model_copy(deep=True)
    return run_validation_pipeline_in_memory(
        definition,
        working_instance,
        canonical_reader=canonical_reader,
        verifier=verifier,
        cross_field_validators=cross_field_validators,
        enable_recheck=False,
    )


def recheck_fields(
    paper_id: str,
    schema_id: str,
    field_ids: Iterable[str],
    **injection_options: Any,
) -> SchemaRunResult:
    """Recheck the given fields of the current run and persist a new run.

    Only the requested fields are re-extracted (through the injected
    recheck callable, at most once per field); every other field stays
    byte-identical to the old instance. Success produces a brand-new complete
    run and atomically updates the current pointer; failure writes nothing and
    leaves the old pointer untouched (AC-D-29..33).
    """
    inj = _split_injections(injection_options)
    storage = _resolve_storage(inj)
    top_k = int(inj.get("top_k", 8))
    canonical_reader = inj.get("canonical_reader") or _offline_canonical_reader
    _, verifier, recheck_callable = _resolve_runtime_context(inj, need_recheck=True)
    cross_field_validators = inj.get("cross_field_validators")

    pointer = storage.read_current(paper_id)
    if pointer.schema_id != schema_id:
        raise SchemaIdMismatchError(
            f"current run {pointer.run_id!r} for paper {paper_id!r} uses "
            f"schema {pointer.schema_id!r}, not the requested {schema_id!r}"
        )
    old_run_id = pointer.run_id
    stored = storage.read_run(paper_id, old_run_id)
    if stored.instance.schema_id != schema_id:
        raise SchemaIdMismatchError(
            f"current run {old_run_id!r} for paper {paper_id!r} uses schema "
            f"{stored.instance.schema_id!r}, not the requested {schema_id!r}"
        )

    definition = get_schema_definition(schema_id)
    target_fields = list(dict.fromkeys(field_ids))
    new_instance = stored.instance.model_copy(deep=True)

    trace = run_targeted_recheck(
        definition, new_instance, target_fields, recheck_callable
    )
    failed_entries = [
        entry
        for entry in trace.entries
        if entry.error_code
        in ("recheck_failed", "recheck_invalid_result", "recheck_field_missing")
    ]
    if failed_entries:
        raise SchemaRecheckError(
            trace,
            message=(
                "targeted recheck failed for fields: "
                + ", ".join(sorted({entry.field_id for entry in failed_entries}))
            ),
        )

    report = run_validation_pipeline_in_memory(
        definition,
        new_instance,
        canonical_reader=canonical_reader,
        verifier=verifier,
        cross_field_validators=cross_field_validators,
        enable_recheck=False,
    )
    report.recheck_trace = trace
    report.status = derive_report_status(report.issues, trace)
    report.created_at = _utc_now_iso()

    new_run_id = uuid.uuid4().hex
    new_manifest = stored.manifest.model_copy(
        update={"run_id": new_run_id, "created_at": _utc_now_iso()}
    )
    run_manifest = _build_run_manifest(
        run_id=new_run_id,
        paper_id=paper_id,
        schema_id=definition.schema_id,
        schema_version=definition.version,
        schema_hash=compute_schema_hash(definition),
        llm_provider=stored.manifest.llm_provider,
        llm_model=stored.manifest.llm_model,
        llm_fake=stored.manifest.llm_fake,
        status=report.status,
        created_at=_utc_now_iso(),
        run_reason="recheck",
        parent_run_id=old_run_id,
        top_k=top_k,
    )
    finalized_manifest = storage.write_run(
        paper_id, new_run_id, new_instance, new_manifest, report, run_manifest
    )
    storage.verify_run_readable(
        paper_id, new_run_id, expected_schema_id=schema_id
    )
    pointer_new = CurrentPointer(
        paper_id=paper_id,
        schema_id=definition.schema_id,
        run_id=new_run_id,
        schema_version=definition.version,
        schema_hash=compute_schema_hash(definition),
        created_at=finalized_manifest.created_at,
        status=report.status,
    )
    storage.write_current(paper_id, pointer_new)
    return SchemaRunResult(
        run_id=new_run_id,
        paper_id=paper_id,
        schema_id=definition.schema_id,
        instance=new_instance,
        manifest=new_manifest,
        report=report,
        run_manifest=finalized_manifest,
        is_current=True,
    )
