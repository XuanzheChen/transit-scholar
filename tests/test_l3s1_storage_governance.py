"""Layer3 Stage1 Workspace-owned storage governance tests (T-002).

Covers the derived storage-layout machinery: Workspace-specific Schema/Wiki
roots (REQ-004/REQ-005/REQ-006 layout), scoped Schema-storage deletion
isolation between Workspaces (AC-006), deterministic Base Wiki input
fingerprinting over Workspace/Schema/membership/current-run identities
(REQ-007/AC-010), and durable build provenance (AC-011).
"""

from __future__ import annotations

import pytest

from transit_scholar.layer2.schema_extraction.persistence import (
    CurrentPointer,
    SchemaCurrentNotFoundError,
    SchemaRunStorage,
)
from transit_scholar.layer3.storage import (
    BuildProvenanceError,
    WorkspaceStorageLayout,
    compute_wiki_input_fingerprint,
    current_schema_run_identities,
    read_build_provenance,
    record_build_provenance,
    workspace_layout,
)
from transit_scholar.layer3.workspace.errors import InvalidWorkspaceInputError

SCHEMA = {
    "schema_id": "bus_control_rl",
    "schema_version": "1.0",
    "schema_hash": "abc123",
}


def _ab_layouts(project_tmp_path):
    return (
        workspace_layout("aaaa1111", data_root=project_tmp_path),
        workspace_layout("bbbb2222", data_root=project_tmp_path),
    )


def _identity(run_id: str, schema_hash: str = "abc123", status: str = "passed") -> dict[str, str]:
    return {"run_id": run_id, "schema_hash": schema_hash, "status": status}


# ---------------------------------------------------------------------------
# derived roots (REQ-004/REQ-005/REQ-006)
# ---------------------------------------------------------------------------


def test_workspace_layout_derives_distinct_roots_per_workspace(project_tmp_path):
    layout_a, layout_b = _ab_layouts(project_tmp_path)
    assert layout_a.workspace_id != layout_b.workspace_id
    assert layout_a.schemas_dir != layout_b.schemas_dir
    assert layout_a.wiki_dir != layout_b.wiki_dir
    assert layout_a.derived_dir.parent == project_tmp_path / "layer3" / "workspaces"
    # Recommended layout: <root>/<workspace_id>/schemas and .../wiki
    assert layout_a.schemas_dir == (
        project_tmp_path / "layer3" / "workspaces" / "aaaa1111" / "schemas"
    )
    assert layout_a.wiki_dir == (
        project_tmp_path / "layer3" / "workspaces" / "aaaa1111" / "wiki"
    )


def test_workspace_layout_accepts_explicit_base_dir(project_tmp_path):
    layout = workspace_layout("ws-1", base_dir=project_tmp_path)
    assert layout.derived_dir == project_tmp_path / "ws-1"
    assert layout.schemas_dir == project_tmp_path / "ws-1" / "schemas"
    assert layout.wiki_dir == project_tmp_path / "ws-1" / "wiki"


def test_workspace_layout_rejects_conflicting_root_injection(project_tmp_path):
    with pytest.raises(InvalidWorkspaceInputError):
        workspace_layout(
            "ws-1", data_root=project_tmp_path, base_dir=project_tmp_path
        )


@pytest.mark.parametrize(
    "unsafe",
    ["", "  ", ".", "..", "a/b", "a\\b", "c:evil", "trail "],
)
def test_workspace_layout_rejects_unsafe_workspace_ids(project_tmp_path, unsafe):
    with pytest.raises(InvalidWorkspaceInputError):
        workspace_layout(unsafe, data_root=project_tmp_path)


def test_schema_storage_injection_uses_workspace_specific_root(project_tmp_path):
    layout_a, layout_b = _ab_layouts(project_tmp_path)
    storage_a = layout_a.schema_storage()
    storage_b = layout_b.schema_storage()
    assert isinstance(storage_a, SchemaRunStorage)
    assert storage_a.root == layout_a.schemas_dir
    assert storage_b.root == layout_b.schemas_dir
    # Writing through A's storage never appears under B's root.
    storage_a.write_current(
        "paper_1",
        CurrentPointer(
            paper_id="paper_1",
            schema_id=SCHEMA["schema_id"],
            run_id="run-a",
            schema_version=SCHEMA["schema_version"],
            schema_hash=SCHEMA["schema_hash"],
            created_at="2025-01-01T00:00:00+00:00",
            status="passed",
        ),
    )
    assert storage_a.read_current("paper_1").run_id == "run-a"
    assert not (layout_b.schemas_dir / "paper_1" / "current.json").exists()
    with pytest.raises(SchemaCurrentNotFoundError):
        storage_b.read_current("paper_1")


# ---------------------------------------------------------------------------
# scoped deletion isolation (AC-006)
# ---------------------------------------------------------------------------


def test_delete_one_workspace_schema_storage_leaves_other_workspace_intact(
    project_tmp_path,
):
    layout_a, layout_b = _ab_layouts(project_tmp_path)
    for layout, run_id in ((layout_a, "run-a"), (layout_b, "run-b")):
        storage = layout.schema_storage()
        storage.write_current(
            "paper_1",
            CurrentPointer(
                paper_id="paper_1",
                schema_id=SCHEMA["schema_id"],
                run_id=run_id,
                schema_version=SCHEMA["schema_version"],
                schema_hash=SCHEMA["schema_hash"],
                created_at="2025-01-01T00:00:00+00:00",
                status="passed",
            ),
        )

    layout_a.delete_schema_storage()

    with pytest.raises(SchemaCurrentNotFoundError):
        layout_a.schema_storage().read_current("paper_1")
    # Workspace B's Schema storage is physically separate and untouched.
    assert layout_b.schema_storage().read_current("paper_1").run_id == "run-b"
    assert (layout_b.schemas_dir / "paper_1" / "current.json").is_file()


def test_delete_workspace_derived_boundary_removes_only_that_workspace(
    project_tmp_path,
):
    layout_a, layout_b = _ab_layouts(project_tmp_path)
    layout_a.schema_storage().write_current(
        "paper_1",
        CurrentPointer(
            paper_id="paper_1",
            schema_id=SCHEMA["schema_id"],
            run_id="run-a",
            schema_version=SCHEMA["schema_version"],
            schema_hash=SCHEMA["schema_hash"],
            created_at="2025-01-01T00:00:00+00:00",
            status="passed",
        ),
    )
    layout_b.wiki_dir.mkdir(parents=True, exist_ok=True)
    (layout_b.wiki_dir / "placeholder.txt").write_text("b", encoding="utf-8")

    layout_a.delete()

    assert not layout_a.derived_dir.exists()
    assert layout_b.wiki_dir.exists()
    assert (layout_b.wiki_dir / "placeholder.txt").is_file()


# ---------------------------------------------------------------------------
# deterministic fingerprint (REQ-007 / AC-010)
# ---------------------------------------------------------------------------


def test_fingerprint_is_deterministic_and_permutation_stable():
    base = dict(
        workspace_id="ws-1",
        schema_id=SCHEMA["schema_id"],
        schema_version=SCHEMA["schema_version"],
        schema_hash=SCHEMA["schema_hash"],
        paper_ids=["p1", "p2", "p3"],
        schema_run_identities={
            "p1": _identity("r1"),
            "p2": _identity("r2"),
            "p3": None,
        },
    )
    first = compute_wiki_input_fingerprint(**base)
    assert first == compute_wiki_input_fingerprint(**base)
    # Deterministic ordering: permutation of inputs must not change the hash.
    shuffled = dict(base)
    shuffled["paper_ids"] = ["p3", "p1", "p2"]
    shuffled["schema_run_identities"] = {
        "p3": None,
        "p1": _identity("r1"),
        "p2": _identity("r2"),
    }
    assert compute_wiki_input_fingerprint(**shuffled) == first


@pytest.mark.parametrize(
    "mutate",
    [
        (lambda kw: kw.update(workspace_id="ws-2")),
        (lambda kw: kw.update(schema_id="other_schema")),
        (lambda kw: kw.update(schema_version="2.0")),
        (lambda kw: kw.update(schema_hash="def456")),
        (lambda kw: kw.update(paper_ids=["p1", "p2"])),
        (lambda kw: kw.update(paper_ids=["p1", "p4", "p3"])),
        (lambda kw: kw["schema_run_identities"].update({"p1": _identity("r-new")})),
        (lambda kw: kw["schema_run_identities"].update({"p2": _identity("r2", schema_hash="other")})),
        (lambda kw: kw["schema_run_identities"].update({"p3": _identity("r3")})),
    ],
)
def test_fingerprint_changes_when_any_authoritative_input_changes(mutate):
    base = dict(
        workspace_id="ws-1",
        schema_id=SCHEMA["schema_id"],
        schema_version=SCHEMA["schema_version"],
        schema_hash=SCHEMA["schema_hash"],
        paper_ids=["p1", "p2", "p3"],
        schema_run_identities={
            "p1": _identity("r1"),
            "p2": _identity("r2"),
            "p3": None,
        },
    )
    original = compute_wiki_input_fingerprint(**base)
    changed = dict(base)
    changed["schema_run_identities"] = dict(base["schema_run_identities"])
    mutate(changed)
    recomputed = compute_wiki_input_fingerprint(**changed)
    assert recomputed != original


def test_current_schema_run_identities_reads_only_workspace_pointers(project_tmp_path):
    layout = workspace_layout("ws-1", data_root=project_tmp_path)
    storage = layout.schema_storage()
    storage.write_current(
        "p2",
        CurrentPointer(
            paper_id="p2",
            schema_id=SCHEMA["schema_id"],
            run_id="run-b",
            schema_version=SCHEMA["schema_version"],
            schema_hash=SCHEMA["schema_hash"],
            created_at="2025-01-01T00:00:00+00:00",
            status="passed",
        ),
    )
    identities = current_schema_run_identities(storage, ["p1", "p2"])
    assert identities == {
        "p1": None,
        "p2": {
            "run_id": "run-b",
            "schema_hash": SCHEMA["schema_hash"],
            "status": "passed",
        },
    }


# ---------------------------------------------------------------------------
# build provenance (AC-011)
# ---------------------------------------------------------------------------


def test_provenance_roundtrip_and_missing(project_tmp_path):
    layout = workspace_layout("ws-1", data_root=project_tmp_path)
    assert read_build_provenance(layout.wiki_dir) is None

    recorded = record_build_provenance(
        layout.wiki_dir,
        workspace_id="ws-1",
        input_fingerprint="fp-1",
        schema_runs={"p1": _identity("r1")},
        build_status="complete",
        build_revision=1,
    )
    assert recorded.build_revision == 1
    assert (layout.wiki_dir / "provenance.json").is_file()

    loaded = read_build_provenance(layout.wiki_dir)
    assert loaded is not None
    assert loaded.input_fingerprint == "fp-1"
    assert loaded.schema_runs == {"p1": _identity("r1")}
    assert loaded.build_status == "complete"
    assert loaded.build_revision == 1
    assert loaded.workspace_id == "ws-1"

    second = record_build_provenance(
        layout.wiki_dir,
        workspace_id="ws-1",
        input_fingerprint="fp-2",
        schema_runs={"p1": _identity("r1")},
        build_status="partial",
        build_revision=2,
    )
    assert second.build_revision == 2
    assert read_build_provenance(layout.wiki_dir).input_fingerprint == "fp-2"


def test_provenance_unreadable_raises_explicit_error(project_tmp_path):
    layout = workspace_layout("ws-1", data_root=project_tmp_path)
    layout.wiki_dir.mkdir(parents=True)
    (layout.wiki_dir / "provenance.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BuildProvenanceError):
        read_build_provenance(layout.wiki_dir)


def test_layout_helpers_expose_paths(project_tmp_path):
    layout = workspace_layout("ws-1", data_root=project_tmp_path)
    assert not layout.exists()
    assert isinstance(layout, WorkspaceStorageLayout)
    assert layout.wiki_store_base == project_tmp_path / "layer3" / "workspaces"
    layout.wiki_dir.mkdir(parents=True)
    assert layout.exists()