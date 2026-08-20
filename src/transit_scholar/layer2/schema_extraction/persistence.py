"""L2S2 Package D persistence layer.

Stores complete schema runs under ``<storage_root>/<paper_id>/runs/<run_id>/``
with a single per-paper ``current.json`` pointer. Every JSON file is written
with a same-directory temporary file followed by ``os.replace`` so readers
never observe half-written content.

Layout::

    <storage_root>/<paper_id>/
        current.json
        runs/
            <run_id>/
                schema_instance.json
                extraction_manifest.json
                validation_report.json
                run_manifest.json

Integrity contract:

- ``run_manifest.json`` records sha256 digests (plus sizes) of the other three
  files; ``read_run`` verifies those digests against the bytes on disk.
- ``run_manifest.json`` itself is protected by atomic replacement plus a
  read-back parse plus ``run_manifest.run_id == directory name``.
- Cross-file consistency (paper id / schema id across instance, manifest,
  report, run manifest) is verified on every read.
- Every failure path raises an explicit ``SchemaStorageError`` subclass;
  nothing is ever silently treated as an empty result.

This module imports only stdlib, pydantic, and sibling Package A/B/C models.
The project ``config`` module is imported lazily inside ``_default_schema_root``
so the deterministic import path stays free of the L2S1 stack.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .models import SchemaInstance
from .trace import ExtractionManifest
from .validation_report import ReportStatus, ValidationReport

# ---------------------------------------------------------------------------
# file names
# ---------------------------------------------------------------------------

SCHEMA_INSTANCE_FILE = "schema_instance.json"
EXTRACTION_MANIFEST_FILE = "extraction_manifest.json"
VALIDATION_REPORT_FILE = "validation_report.json"
RUN_MANIFEST_FILE = "run_manifest.json"
CURRENT_POINTER_FILE = "current.json"

#: The four files a complete run must contain (fixed order used by writers).
RUN_FILES = (
    SCHEMA_INSTANCE_FILE,
    EXTRACTION_MANIFEST_FILE,
    VALIDATION_REPORT_FILE,
    RUN_MANIFEST_FILE,
)

#: Files covered by the run manifest digest list (run_manifest.json itself is
#: protected by atomic write + read-back parse + run id equality).
DIGESTED_FILES = (
    SCHEMA_INSTANCE_FILE,
    EXTRACTION_MANIFEST_FILE,
    VALIDATION_REPORT_FILE,
)

#: Stable prompt revision for Package D. Deliberately a constant for this
#: installation: it never changes between runs on the same checkout and is
#: bumped by hand when the deterministic prompt construction changes.
PROMPT_VERSION = "l2s2-extraction-prompt-v1"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class SchemaStorageError(Exception):
    """Base error for L2S2 Package D storage failures."""


class SchemaRunNotFoundError(SchemaStorageError):
    """No run directory exists for the requested run id."""


class SchemaCurrentNotFoundError(SchemaStorageError):
    """No ``current.json`` exists for the requested paper."""


class SchemaFileMissingError(SchemaStorageError):
    """A run file that must exist is missing."""


class SchemaInvalidJsonError(SchemaStorageError):
    """A persisted file is not valid JSON or does not validate against its model."""


class SchemaHashMismatchError(SchemaStorageError):
    """A recorded file digest does not match the bytes on disk."""


class SchemaRunIdMismatchError(SchemaStorageError):
    """``run_manifest.run_id`` does not match the run directory name."""


class SchemaCorruptRunError(SchemaStorageError):
    """A run fails cross-file or requested-identity consistency checks."""


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


class FileDigest(BaseModel):
    """Integrity summary of one persisted run file."""

    path: str
    sha256: str
    size: int


class RunManifest(BaseModel):
    """Package D run metadata (requirements 4.5 / AC-D-04..06)."""

    run_id: str
    paper_id: str
    schema_id: str
    schema_version: str
    schema_hash: str
    llm_provider: str
    llm_model: str
    llm_fake: bool
    prompt_version: str
    extraction_config_hash: str
    source_parse_run_id: str | None = None
    retrieval_manifest_hash: str | None = None
    created_at: str
    status: ReportStatus
    run_reason: Literal["extract", "recheck"]
    parent_run_id: str | None = None
    file_digests: list[FileDigest] = Field(default_factory=list)


class CurrentPointer(BaseModel):
    """Per-paper ``current.json`` pointer (requirements 4.4 / AC-D-07)."""

    paper_id: str
    schema_id: str
    run_id: str
    schema_version: str
    schema_hash: str
    created_at: str
    status: ReportStatus


class StoredRun(BaseModel):
    """Aggregate of everything read back for one run."""

    instance: SchemaInstance
    manifest: ExtractionManifest
    report: ValidationReport
    run_manifest: RunManifest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _default_schema_root() -> Path:
    """Default storage root: ``Settings.data_root / layer2 / schemas``.

    Imports ``transit_scholar.config`` lazily so importing this module (and
    therefore ``transit_scholar.layer2.schema_extraction``) never pulls the
    L2S1 config/DB stack into memory.
    """
    from transit_scholar.config import settings  # noqa: PLC0415 - lazy import

    return settings.layer2_schema_dir


def compute_extraction_config_hash(prompt_version: str, top_k: int) -> str:
    """Deterministic hash of the extraction configuration actually used.

    Only stable inputs (``prompt_version`` and ``top_k``) enter the payload;
    no timestamps or randomness (AC-D-06).
    """
    payload = json.dumps(
        {"prompt_version": prompt_version, "top_k": int(top_k)},
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, model: BaseModel) -> None:
    """Serialize ``model`` and atomically replace ``path``.

    The temporary file lives in the same directory as the target so
    ``os.replace`` never crosses a volume boundary. On any failure the
    temporary file is removed and the original target (if any) is untouched.
    """
    data = model.model_dump_json(indent=2)
    tmp_path = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex[:12]}"
    try:
        tmp_path.write_text(data + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _read_json_file(
    path: Path,
    model_cls: type[BaseModel],
    *,
    description: str,
) -> BaseModel:
    """Read and validate one persisted JSON file; failures are explicit."""
    if not path.is_file():
        raise SchemaFileMissingError(f"{description}: file missing: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaFileMissingError(
            f"{description}: could not read {path}: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaInvalidJsonError(
            f"{description}: {path} is not valid JSON: {exc}"
        ) from exc
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise SchemaInvalidJsonError(
            f"{description}: {path} does not validate as "
            f"{model_cls.__name__}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


class SchemaRunStorage:
    """JSON-file storage for L2S2 schema runs.

    Pure persistence: no extraction, validation, or recheck logic. A custom
    storage root can be injected (``storage_root``) for tests and isolation.
    """

    def __init__(self, storage_root: Path | str | None = None):
        self.root = Path(storage_root) if storage_root is not None else _default_schema_root()

    # -- paths ---------------------------------------------------------------

    def paper_dir(self, paper_id: str) -> Path:
        return self.root / paper_id

    def runs_dir(self, paper_id: str) -> Path:
        return self.paper_dir(paper_id) / "runs"

    def run_dir(self, paper_id: str, run_id: str) -> Path:
        return self.runs_dir(paper_id) / run_id

    def current_path(self, paper_id: str) -> Path:
        return self.paper_dir(paper_id) / CURRENT_POINTER_FILE

    # -- write ----------------------------------------------------------------

    def write_run(
        self,
        paper_id: str,
        run_id: str,
        instance: SchemaInstance,
        manifest: ExtractionManifest,
        report: ValidationReport,
        run_manifest: RunManifest,
    ) -> RunManifest:
        """Write a complete run (four files) atomically per file.

        Returns the finalized run manifest including the computed file
        digests. Historical run files are only ever written once by their
        creator; this method never rewrites an existing run.
        """
        run_dir = self.run_dir(paper_id, run_id)
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            digests: list[FileDigest] = []
            for name, model in (
                (SCHEMA_INSTANCE_FILE, instance),
                (EXTRACTION_MANIFEST_FILE, manifest),
                (VALIDATION_REPORT_FILE, report),
            ):
                target = run_dir / name
                _atomic_write_json(target, model)
                digests.append(
                    FileDigest(
                        path=name,
                        sha256=_sha256_file(target),
                        size=target.stat().st_size,
                    )
                )
            finalized = run_manifest.model_copy(update={"file_digests": digests})
            _atomic_write_json(run_dir / RUN_MANIFEST_FILE, finalized)
        except SchemaStorageError:
            raise
        except Exception as exc:
            raise SchemaStorageError(
                f"failed to persist schema run {run_id!r} for paper "
                f"{paper_id!r}: {type(exc).__name__}: {exc}"
            ) from exc
        return finalized

    def write_current(self, paper_id: str, pointer: CurrentPointer) -> None:
        """Atomically update the per-paper current pointer."""
        paper_dir = self.paper_dir(paper_id)
        try:
            paper_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(self.current_path(paper_id), pointer)
        except SchemaStorageError:
            raise
        except Exception as exc:
            raise SchemaStorageError(
                f"failed to update current.json for paper {paper_id!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # -- read -----------------------------------------------------------------

    def read_current(self, paper_id: str) -> CurrentPointer:
        path = self.current_path(paper_id)
        if not path.is_file():
            raise SchemaCurrentNotFoundError(
                f"no current schema run pointer for paper {paper_id!r} "
                f"(missing {path})"
            )
        pointer = _read_json_file(
            path, CurrentPointer, description="current pointer"
        )
        assert isinstance(pointer, CurrentPointer)  # noqa: S101 - model typed
        return pointer

    def read_run(self, paper_id: str, run_id: str) -> StoredRun:
        """Read one complete run back with full integrity verification."""
        run_dir = self.run_dir(paper_id, run_id)
        if not run_dir.is_dir():
            raise SchemaRunNotFoundError(
                f"schema run {run_id!r} for paper {paper_id!r} not found "
                f"(missing {run_dir})"
            )
        instance = _read_json_file(
            run_dir / SCHEMA_INSTANCE_FILE, SchemaInstance,
            description=f"run {run_id!r} schema instance",
        )
        manifest = _read_json_file(
            run_dir / EXTRACTION_MANIFEST_FILE, ExtractionManifest,
            description=f"run {run_id!r} extraction manifest",
        )
        report = _read_json_file(
            run_dir / VALIDATION_REPORT_FILE, ValidationReport,
            description=f"run {run_id!r} validation report",
        )
        run_manifest = _read_json_file(
            run_dir / RUN_MANIFEST_FILE, RunManifest,
            description=f"run {run_id!r} run manifest",
        )
        assert isinstance(instance, SchemaInstance)  # noqa: S101 - typed
        assert isinstance(manifest, ExtractionManifest)  # noqa: S101 - typed
        assert isinstance(report, ValidationReport)  # noqa: S101 - typed
        assert isinstance(run_manifest, RunManifest)  # noqa: S101 - typed

        if run_manifest.run_id != run_id:
            raise SchemaRunIdMismatchError(
                f"run manifest run_id {run_manifest.run_id!r} does not match "
                f"run directory name {run_id!r} for paper {paper_id!r}"
            )

        for label, value in (
            ("instance", instance.paper_id),
            ("extraction manifest", manifest.paper_id),
            ("validation report", report.paper_id),
            ("run manifest", run_manifest.paper_id),
        ):
            if value != paper_id:
                raise SchemaCorruptRunError(
                    f"run {run_id!r} for paper {paper_id!r}: {label} belongs "
                    f"to paper {value!r}"
                )
        schema_ids = {
            instance.schema_id,
            manifest.schema_id,
            report.schema_id,
            run_manifest.schema_id,
        }
        if len(schema_ids) != 1:
            raise SchemaCorruptRunError(
                f"run {run_id!r} for paper {paper_id!r}: schema ids disagree "
                f"across files: {sorted(schema_ids)}"
            )

        for digest in run_manifest.file_digests:
            if digest.path not in DIGESTED_FILES:
                raise SchemaCorruptRunError(
                    f"run {run_id!r}: unknown digest path {digest.path!r}"
                )
            target = run_dir / digest.path
            if not target.is_file():
                raise SchemaFileMissingError(
                    f"run {run_id!r}: digested file missing: {target}"
                )
            actual_sha = _sha256_file(target)
            if actual_sha != digest.sha256:
                raise SchemaHashMismatchError(
                    f"run {run_id!r}: {digest.path} digest mismatch: recorded "
                    f"{digest.sha256[:16]}..., actual {actual_sha[:16]}..."
                )
            actual_size = target.stat().st_size
            if actual_size != digest.size:
                raise SchemaHashMismatchError(
                    f"run {run_id!r}: {digest.path} size mismatch: recorded "
                    f"{digest.size}, actual {actual_size}"
                )

        return StoredRun(
            instance=instance,
            manifest=manifest,
            report=report,
            run_manifest=run_manifest,
        )

    def verify_run_readable(
        self,
        paper_id: str,
        run_id: str,
        *,
        expected_schema_id: str | None = None,
    ) -> StoredRun:
        """Read-back verification before a run may become current (AC-D-08).

        Performs the full ``read_run`` integrity checks and additionally
        verifies the stored instance schema id against the requested one.
        """
        stored = self.read_run(paper_id, run_id)
        if (
            expected_schema_id is not None
            and stored.instance.schema_id != expected_schema_id
        ):
            raise SchemaCorruptRunError(
                f"run {run_id!r} for paper {paper_id!r}: instance schema id "
                f"{stored.instance.schema_id!r} does not match requested "
                f"schema id {expected_schema_id!r}"
            )
        return stored
