"""L2S2 Package D deterministic tests: persistence layer.

Covers AC-D-01..15, 27, 34..36 at the storage level: layout, round-trip,
run manifest metadata, current pointer safety, versioning (old runs never
overwritten), corruption handling, and atomic-write failure semantics.

Everything is offline and isolated: every test injects ``storage_root`` into a
temporary directory; the repository ``data/`` tree is never touched.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from transit_scholar.layer2.schema_extraction import (
    CurrentPointer,
    ExtractionManifest,
    FieldResult,
    PROMPT_VERSION,
    RunManifest,
    SchemaCorruptRunError,
    SchemaCurrentNotFoundError,
    SchemaFileMissingError,
    SchemaHashMismatchError,
    SchemaInstance,
    SchemaInvalidJsonError,
    SchemaRunIdMismatchError,
    SchemaRunNotFoundError,
    SchemaRunStorage,
    SchemaStorageError,
    ValidationReport,
    compute_extraction_config_hash,
)
from transit_scholar.layer2.schema_extraction import persistence as persistence_module

PAPER_ID = "paper_001"
SCHEMA_ID = "test_schema"
SCHEMA_VERSION = "1.0"
RUN_ID = "run_001"

SCHEMA_HASH = hashlib.sha256(b"deterministic-schema-content").hexdigest()
CREATED_AT = "2026-08-14T00:00:00+00:00"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _instance(paper_id: str = PAPER_ID, schema_id: str = SCHEMA_ID) -> SchemaInstance:
    return SchemaInstance(
        paper_id=paper_id,
        schema_id=schema_id,
        schema_version=SCHEMA_VERSION,
        fields={"f1": FieldResult(value="v", status="explicit")},
    )


def _manifest(run_id: str = RUN_ID) -> ExtractionManifest:
    return ExtractionManifest(
        run_id=run_id,
        paper_id=PAPER_ID,
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        schema_hash=SCHEMA_HASH,
        llm_provider="fake",
        llm_model="fake-v0",
        llm_fake=True,
        created_at=CREATED_AT,
    )


def _report() -> ValidationReport:
    return ValidationReport(
        paper_id=PAPER_ID,
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        schema_hash=SCHEMA_HASH,
        status="passed",
        created_at=CREATED_AT,
    )


def _run_manifest(run_id: str = RUN_ID) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        paper_id=PAPER_ID,
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        schema_hash=SCHEMA_HASH,
        llm_provider="fake",
        llm_model="fake-v0",
        llm_fake=True,
        prompt_version=PROMPT_VERSION,
        extraction_config_hash=compute_extraction_config_hash(PROMPT_VERSION, 8),
        source_parse_run_id=None,
        retrieval_manifest_hash=None,
        created_at=CREATED_AT,
        status="passed",
        run_reason="extract",
        parent_run_id=None,
    )


def _pointer(run_id: str = RUN_ID) -> CurrentPointer:
    return CurrentPointer(
        paper_id=PAPER_ID,
        schema_id=SCHEMA_ID,
        run_id=run_id,
        schema_version=SCHEMA_VERSION,
        schema_hash=SCHEMA_HASH,
        created_at=CREATED_AT,
        status="passed",
    )


def _write_complete_run(storage: SchemaRunStorage, run_id: str = RUN_ID) -> RunManifest:
    return storage.write_run(
        PAPER_ID,
        run_id,
        _instance(),
        _manifest(run_id),
        _report(),
        _run_manifest(run_id),
    )


def _file_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _all_run_files(storage: SchemaRunStorage, run_id: str) -> dict[str, Path]:
    return {
        name: storage.run_dir(PAPER_ID, run_id) / name
        for name in (
            "schema_instance.json",
            "extraction_manifest.json",
            "validation_report.json",
            "run_manifest.json",
        )
    }


# ---------------------------------------------------------------------------
# AC-D-01 storage root resolution
# ---------------------------------------------------------------------------


def test_default_root_is_settings_data_root_layer2_schemas():
    """AC-D-01: the default storage root derives from Settings.data_root."""
    from transit_scholar.config import settings

    storage = SchemaRunStorage()
    assert storage.root == settings.data_root / "layer2" / "schemas"
    assert storage.root == settings.layer2_schema_dir


def test_storage_root_injection_overrides_default(tmp_path):
    """AC-D-01: injecting ``storage_root`` (Path) overrides the default root."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    assert storage.root == Path(tmp_path)


def test_storage_root_accepts_str(tmp_path):
    storage = SchemaRunStorage(storage_root=str(tmp_path))
    assert storage.root == Path(tmp_path)


# ---------------------------------------------------------------------------
# AC-D-02/03 write layout and round-trip
# ---------------------------------------------------------------------------


def test_write_run_creates_four_valid_json_files(tmp_path):
    """AC-D-02: one successful run writes exactly the four expected files,
    each valid JSON, under ``runs/<run_id>/``."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage)

    run_dir = storage.run_dir(PAPER_ID, RUN_ID)
    names = sorted(p.name for p in run_dir.iterdir() if p.is_file())
    assert names == [
        "extraction_manifest.json",
        "run_manifest.json",
        "schema_instance.json",
        "validation_report.json",
    ]
    for name in names:
        json.loads((run_dir / name).read_text(encoding="utf-8"))


def test_run_directory_name_equals_manifest_run_id(tmp_path):
    """AC-D-02: the run directory name equals ``run_manifest.run_id``."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage)
    stored = storage.read_run(PAPER_ID, RUN_ID)
    assert stored.run_manifest.run_id == RUN_ID
    assert storage.run_dir(PAPER_ID, RUN_ID).name == stored.run_manifest.run_id


def test_roundtrip_models_equivalent(tmp_path):
    """AC-D-03: all three persisted models round-trip through
    ``model_validate`` and remain equivalent to the in-memory objects."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    instance, manifest, report = _instance(), _manifest(), _report()
    storage.write_run(PAPER_ID, RUN_ID, instance, manifest, report, _run_manifest())

    run_dir = storage.run_dir(PAPER_ID, RUN_ID)
    rebuilt_instance = SchemaInstance.model_validate_json(
        (run_dir / "schema_instance.json").read_text(encoding="utf-8")
    )
    rebuilt_manifest = ExtractionManifest.model_validate_json(
        (run_dir / "extraction_manifest.json").read_text(encoding="utf-8")
    )
    rebuilt_report = ValidationReport.model_validate_json(
        (run_dir / "validation_report.json").read_text(encoding="utf-8")
    )
    assert rebuilt_instance.model_dump() == instance.model_dump()
    assert rebuilt_manifest.model_dump() == manifest.model_dump()
    assert rebuilt_report.model_dump() == report.model_dump()


# ---------------------------------------------------------------------------
# AC-D-04/05/06 run manifest metadata
# ---------------------------------------------------------------------------


def test_run_manifest_answers_all_rebuild_questions(tmp_path):
    """AC-D-04/05: run_manifest carries every rebuild key with correct values
    and digests that match the bytes on disk."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    finalized = _write_complete_run(storage)

    required_keys = {
        "schema_id",
        "schema_version",
        "schema_hash",
        "llm_provider",
        "llm_model",
        "llm_fake",
        "prompt_version",
        "source_parse_run_id",
        "retrieval_manifest_hash",
        "extraction_config_hash",
        "created_at",
        "status",
        "run_id",
        "paper_id",
        "run_reason",
        "file_digests",
    }
    assert required_keys <= set(finalized.model_dump().keys())

    assert finalized.schema_id == SCHEMA_ID
    assert finalized.schema_version == SCHEMA_VERSION
    assert finalized.schema_hash == SCHEMA_HASH
    assert finalized.llm_provider == "fake"
    assert finalized.llm_model == "fake-v0"
    assert finalized.llm_fake is True
    assert finalized.status == "passed"

    digested_names = {digest.path for digest in finalized.file_digests}
    assert digested_names == {
        "schema_instance.json",
        "extraction_manifest.json",
        "validation_report.json",
    }
    run_dir = storage.run_dir(PAPER_ID, RUN_ID)
    for digest in finalized.file_digests:
        target = run_dir / digest.path
        raw = target.read_bytes()
        assert digest.sha256 == hashlib.sha256(raw).hexdigest()
        assert digest.size == len(raw)


def test_run_manifest_created_at_is_iso8601_utc(tmp_path):
    """AC-D-05: ``created_at`` is an ISO8601 UTC timestamp."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    finalized = _write_complete_run(storage)
    parsed = datetime.fromisoformat(finalized.created_at)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_prompt_version_is_stable_constant():
    """AC-D-06: ``prompt_version`` is a stable installation-level constant."""
    assert PROMPT_VERSION == "l2s2-extraction-prompt-v1"
    assert PROMPT_VERSION == "l2s2-extraction-prompt-v1"


def test_extraction_config_hash_is_deterministic():
    """AC-D-06: the config hash derives only from stable inputs (prompt
    version + top_k); no timestamps or randomness."""
    first = compute_extraction_config_hash(PROMPT_VERSION, 8)
    second = compute_extraction_config_hash(PROMPT_VERSION, 8)
    assert first == second
    assert len(first) == 64
    assert compute_extraction_config_hash(PROMPT_VERSION, 4) != first
    assert compute_extraction_config_hash("other-prompt-v2", 8) != first


def test_source_and_retrieval_keys_exist_and_are_null_offline(tmp_path):
    """AC-D-06: ``source_parse_run_id`` / ``retrieval_manifest_hash`` keys
    exist and are explicitly null in offline fake mode."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    finalized = _write_complete_run(storage)
    dumped = finalized.model_dump()
    assert "source_parse_run_id" in dumped and finalized.source_parse_run_id is None
    assert (
        "retrieval_manifest_hash" in dumped
        and finalized.retrieval_manifest_hash is None
    )


# ---------------------------------------------------------------------------
# AC-D-07/08/09 current pointer
# ---------------------------------------------------------------------------


def test_current_pointer_minimum_keys_and_status_consistency(tmp_path):
    """AC-D-07: current.json carries the minimum key set and its status equals
    the pointed run's manifest status."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    finalized = _write_complete_run(storage)
    storage.verify_run_readable(PAPER_ID, RUN_ID, expected_schema_id=SCHEMA_ID)
    storage.write_current(PAPER_ID, _pointer())

    pointer = storage.read_current(PAPER_ID)
    assert pointer.model_dump().keys() >= {
        "paper_id",
        "schema_id",
        "run_id",
        "schema_version",
        "schema_hash",
        "created_at",
        "status",
    }
    assert pointer.status == finalized.status == "passed"
    assert pointer.run_id == RUN_ID
    assert pointer.schema_hash == SCHEMA_HASH


def test_verify_run_readable_checks_requested_schema_id(tmp_path):
    """AC-D-08: read-back verification fails when the stored instance schema
    id does not match the requested schema id."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage)
    stored = storage.verify_run_readable(
        PAPER_ID, RUN_ID, expected_schema_id=SCHEMA_ID
    )
    assert stored.instance.schema_id == SCHEMA_ID
    assert stored.instance.paper_id == PAPER_ID
    assert stored.run_manifest.run_id == RUN_ID
    with pytest.raises(SchemaCorruptRunError):
        storage.verify_run_readable(PAPER_ID, RUN_ID, expected_schema_id="other_schema")


def test_new_run_updates_current_and_old_run_untouched(tmp_path):
    """AC-D-09/11/14: a second run gets a new directory; the first run's files
    stay byte-identical; the pointer is atomically switched to the new run."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage, "run_001")
    storage.verify_run_readable(PAPER_ID, "run_001", expected_schema_id=SCHEMA_ID)
    storage.write_current(PAPER_ID, _pointer("run_001"))

    old_bytes = {
        name: _file_bytes(path) for name, path in _all_run_files(storage, "run_001").items()
    }
    old_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in old_bytes.items()}

    _write_complete_run(storage, "run_002")
    storage.verify_run_readable(PAPER_ID, "run_002", expected_schema_id=SCHEMA_ID)
    storage.write_current(PAPER_ID, _pointer("run_002"))

    assert storage.read_current(PAPER_ID).run_id == "run_002"
    for name, path in _all_run_files(storage, "run_001").items():
        raw = _file_bytes(path)
        assert hashlib.sha256(raw).hexdigest() == old_hashes[name]
    assert (storage.run_dir(PAPER_ID, "run_002")).is_dir()


def test_is_current_derivable_from_current_json(tmp_path):
    """AC-D-15: whether a run is current is uniquely derivable from
    current.json (no in-place rewriting of historical run files)."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage, "run_001")
    storage.write_current(PAPER_ID, _pointer("run_001"))
    assert storage.read_current(PAPER_ID).run_id == "run_001"
    _write_complete_run(storage, "run_002")
    storage.write_current(PAPER_ID, _pointer("run_002"))
    assert storage.read_current(PAPER_ID).run_id == "run_002"
    assert storage.read_run(PAPER_ID, "run_001").run_manifest.run_id == "run_001"


# ---------------------------------------------------------------------------
# AC-D-10/36 corruption handling
# ---------------------------------------------------------------------------


def test_missing_current_raises_explicit_error(tmp_path):
    """AC-D-10: no current.json -> explicit SchemaCurrentNotFoundError."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    with pytest.raises(SchemaCurrentNotFoundError):
        storage.read_current(PAPER_ID)


def test_current_pointing_to_missing_run_raises(tmp_path):
    """AC-D-10: current pointing at a non-existent run -> explicit
    SchemaRunNotFoundError on read."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    storage.write_current(PAPER_ID, _pointer("ghost_run"))
    pointer = storage.read_current(PAPER_ID)
    assert pointer.run_id == "ghost_run"
    with pytest.raises(SchemaRunNotFoundError):
        storage.read_run(PAPER_ID, pointer.run_id)


def test_corrupt_current_invalid_json_raises(tmp_path):
    """AC-D-10: invalid current.json -> SchemaInvalidJsonError, never an
    empty result."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    storage.current_path(PAPER_ID).parent.mkdir(parents=True, exist_ok=True)
    storage.current_path(PAPER_ID).write_text("{not json", encoding="utf-8")
    with pytest.raises(SchemaInvalidJsonError):
        storage.read_current(PAPER_ID)


def test_read_run_missing_file_raises(tmp_path):
    """AC-D-36: a run with a missing file raises SchemaFileMissingError."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage)
    (storage.run_dir(PAPER_ID, RUN_ID) / "schema_instance.json").unlink()
    with pytest.raises(SchemaFileMissingError):
        storage.read_run(PAPER_ID, RUN_ID)


def test_read_run_invalid_json_raises(tmp_path):
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage)
    (storage.run_dir(PAPER_ID, RUN_ID) / "validation_report.json").write_text(
        "[1,2,3", encoding="utf-8"
    )
    with pytest.raises(SchemaInvalidJsonError):
        storage.read_run(PAPER_ID, RUN_ID)


def test_read_run_digest_mismatch_raises(tmp_path):
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage)
    target = storage.run_dir(PAPER_ID, RUN_ID) / "schema_instance.json"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SchemaHashMismatchError):
        storage.read_run(PAPER_ID, RUN_ID)


def test_read_run_run_id_mismatch_raises(tmp_path):
    storage = SchemaRunStorage(storage_root=tmp_path)
    storage.write_run(
        PAPER_ID,
        RUN_ID,
        _instance(),
        _manifest(RUN_ID),
        _report(),
        _run_manifest("different_run_id"),
    )
    with pytest.raises(SchemaRunIdMismatchError):
        storage.read_run(PAPER_ID, RUN_ID)


def test_read_run_cross_paper_mismatch_raises(tmp_path):
    storage = SchemaRunStorage(storage_root=tmp_path)
    storage.write_run(
        PAPER_ID,
        RUN_ID,
        _instance(paper_id="other_paper"),
        _manifest(RUN_ID),
        _report(),
        _run_manifest(RUN_ID),
    )
    with pytest.raises(SchemaCorruptRunError):
        storage.read_run(PAPER_ID, RUN_ID)


def test_read_run_schema_id_disagreement_raises(tmp_path):
    storage = SchemaRunStorage(storage_root=tmp_path)
    storage.write_run(
        PAPER_ID,
        RUN_ID,
        _instance(schema_id="another_schema"),
        _manifest(RUN_ID),
        _report(),
        _run_manifest(RUN_ID),
    )
    with pytest.raises(SchemaCorruptRunError):
        storage.read_run(PAPER_ID, RUN_ID)


def test_partial_run_dir_is_never_readable_as_complete(tmp_path):
    """AC-D-36: a residual half-written run directory can never be read as a
    complete run."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    run_dir = storage.run_dir(PAPER_ID, "partial_run")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "schema_instance.json").write_text(
        _instance().model_dump_json(indent=2), encoding="utf-8"
    )
    with pytest.raises(SchemaFileMissingError):
        storage.read_run(PAPER_ID, "partial_run")


# ---------------------------------------------------------------------------
# AC-D-34/35 atomicity
# ---------------------------------------------------------------------------


def test_run_write_failure_leaves_existing_current_untouched(tmp_path, monkeypatch):
    """AC-D-34: a failure while writing run files raises SchemaStorageError
    and leaves the existing current pointer byte-identical."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage, "run_001")
    storage.verify_run_readable(PAPER_ID, "run_001", expected_schema_id=SCHEMA_ID)
    storage.write_current(PAPER_ID, _pointer("run_001"))
    current_before = _file_bytes(storage.current_path(PAPER_ID))

    real_write = persistence_module._atomic_write_json

    def failing_write(path, model):
        if path.name == "validation_report.json":
            raise OSError("disk full (injected)")
        real_write(path, model)

    monkeypatch.setattr(persistence_module, "_atomic_write_json", failing_write)
    with pytest.raises(SchemaStorageError):
        _write_complete_run(storage, "run_002")
    assert _file_bytes(storage.current_path(PAPER_ID)) == current_before
    assert storage.read_current(PAPER_ID).run_id == "run_001"


def test_current_write_failure_keeps_old_pointer(tmp_path, monkeypatch):
    """AC-D-34: a failure while writing current.json leaves the old pointer
    byte-identical."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage, "run_001")
    storage.verify_run_readable(PAPER_ID, "run_001", expected_schema_id=SCHEMA_ID)
    storage.write_current(PAPER_ID, _pointer("run_001"))
    current_before = _file_bytes(storage.current_path(PAPER_ID))

    real_write = persistence_module._atomic_write_json

    def failing_write(path, model):
        if path.name == "current.json":
            raise OSError("permission denied (injected)")
        real_write(path, model)

    monkeypatch.setattr(persistence_module, "_atomic_write_json", failing_write)
    with pytest.raises(SchemaStorageError):
        storage.write_current(PAPER_ID, _pointer("run_002"))
    assert _file_bytes(storage.current_path(PAPER_ID)) == current_before
    assert storage.read_current(PAPER_ID).run_id == "run_001"


def test_atomic_write_leaves_no_temp_files(tmp_path):
    storage = SchemaRunStorage(storage_root=tmp_path)
    _write_complete_run(storage)
    leftovers = [
        p
        for p in storage.run_dir(PAPER_ID, RUN_ID).iterdir()
        if ".tmp-" in p.name
    ]
    assert leftovers == []


def test_atomic_write_failure_cleans_temp_file(tmp_path, monkeypatch):
    """A failing atomic write removes its own temporary file."""
    storage = SchemaRunStorage(storage_root=tmp_path)
    real_replace = persistence_module.os.replace

    def failing_replace(src, dst):
        raise OSError("replace failed (injected)")

    monkeypatch.setattr(persistence_module.os, "replace", failing_replace)
    try:
        with pytest.raises(SchemaStorageError):
            _write_complete_run(storage)
    finally:
        monkeypatch.setattr(persistence_module.os, "replace", real_replace)
    run_dir = storage.run_dir(PAPER_ID, RUN_ID)
    leftovers = [p.name for p in run_dir.iterdir() if ".tmp-" in p.name]
    assert leftovers == []
