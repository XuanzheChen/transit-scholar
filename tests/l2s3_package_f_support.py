"""Deterministic support for the L2S3 Package F acceptance suite.

This module owns:

- loading of the committed, static Package F fixture
  (``tests/fixtures/l2s3_package_f/reviewed_real_papers.json``) and of the
  reviewed L2S2 gold provenance file (``tests/fixtures/**`` only);
- construction of the generic in-memory ``SchemaDefinition`` /
  ``SchemaInstance`` / ``PaperMetadata`` / ``WorkspaceContext`` models;
- the deterministic boundary fakes injected ONLY at the approved proposal
  (``ProposalFake``), resolver-decision (``DecisionFake``), and existing
  injectable embedding-provider (``FakeEmbedding``) boundaries;
- canonical-snapshot / JSON-summary helpers.

The loader reads only committed ``tests/fixtures/**`` files at runtime. It
never reads ``data/``, PDFs, the network, credentials, or an LLM runtime.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from transit_scholar.layer2.retrieval.providers import EmbeddingProvider, ProviderInfo
from transit_scholar.layer2.schema_extraction.models import (
    EvidenceRef,
    FieldDefinition,
    FieldResult,
    SchemaDefinition,
    SchemaInstance,
    SectionDefinition,
)
from transit_scholar.layer2.wiki import (
    EntityResolutionDecision,
    PaperMetadata,
    WorkspaceContext,
    entity_id_for,
)

# ---------------------------------------------------------------------------
# Static fixture paths (committed repository files only).
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "l2s3_package_f"
FIXTURE_PATH = FIXTURE_DIR / "reviewed_real_papers.json"
GOLD_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "l2s2_schema_acceptance"
    / "codex_reviewed_gold.json"
)

SCHEMA_ID = "l2s3_fixture_generic"
SCHEMA_VERSION = "1.0"
FIELD_IDS = ("control_concept", "method_concept", "key_metrics")

#: The canonical per-workspace fixture rosters. ``load_workspace`` enforces
#: these exactly for the two acceptance workspaces, so a paper designated for
#: workspace B can never be loaded into workspace A (or vice versa).
FIXTURE_WORKSPACES: dict[str, list[str]] = {
    "f-wiki-a": ["transit-001", "transit-002", "transit-010"],
    "f-wiki-b": ["transit-006"],
}

#: Fields that are allowed to differ between canonical machine-readable
#: summaries of identical runs (AC-F-009 "explicitly governed timestamps" and
#: the governed per-page build-revision counter).
GOVERNED_FIELDS = ("created_at", "updated_at", "audited_at", "rebuilt_at", "build_revision")

SUMMARY_FILE_NAME = "package-f-summary.json"
FAKE_CALL_LOG_FILE_NAME = "fake-call-log.json"

GOOD_SOURCE_ROLE = "identity_and_field_gold"
BIBLIOGRAPHY_SOURCE_ROLE = "bibliography"
GOLD_BENCHMARK_ID = "l2s2-bus-control-rl-real-papers-codex-reviewed-v1"


# ---------------------------------------------------------------------------
# Fixture loading (runtime reads only committed fixture/gold JSON).
# ---------------------------------------------------------------------------


def load_fixture() -> dict[str, Any]:
    """Load and return the committed static Package F fixture (deterministic)."""
    with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_gold() -> dict[str, Any]:
    """Load the reviewed L2S2 gold for provenance assertions (read-only)."""
    with GOLD_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def papers_by_id(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {record["paper_id"]: record for record in fixture["papers"]}


# ---------------------------------------------------------------------------
# Generic schema / instance / metadata / context construction.
# ---------------------------------------------------------------------------


def build_generic_schema_definition() -> SchemaDefinition:
    """Return the reusable generic research schema used by all fixture workspaces."""
    return SchemaDefinition(
        schema_id=SCHEMA_ID,
        version=SCHEMA_VERSION,
        name="L2S3 Package F Generic Research Schema",
        sections=[
            SectionDefinition(
                id="overview",
                label="Overview",
                fields=[
                    FieldDefinition(
                        id="control_concept",
                        label="Control Concept",
                        question="What control approach does the paper study?",
                        description=(
                            "The control approach a transit-operation paper studies; "
                            "values are derived from the reviewed L2S2 gold field."
                        ),
                        type="string",
                        evidence_required=True,
                        allow_inference=True,
                    ),
                    FieldDefinition(
                        id="method_concept",
                        label="Method Concept",
                        question="Which concrete method does the paper use?",
                        description=(
                            "The concrete reinforcement-learning method the paper "
                            "proposes or applies."
                        ),
                        type="string",
                        evidence_required=True,
                        allow_inference=True,
                    ),
                    FieldDefinition(
                        id="key_metrics",
                        label="Key Metrics",
                        question="Which metrics does the paper report?",
                        description="The evaluation metrics reported by the paper.",
                        type="list",
                        evidence_required=True,
                        allow_inference=True,
                    ),
                ],
            )
        ],
    )


def build_instance(record: dict[str, Any]) -> SchemaInstance:
    """Build the generic ``SchemaInstance`` for one fixture paper record."""
    fields: dict[str, FieldResult] = {}
    for field_id, payload in record["instance_fields"].items():
        evidence = [
            EvidenceRef(
                block_id=item["block_id"],
                char_start=item["char_start"],
                char_end=item["char_end"],
                pages=list(item.get("pages", [])),
                quote=item.get("quote", ""),
            )
            for item in payload.get("evidence", [])
        ]
        fields[field_id] = FieldResult(
            value=payload["value"],
            status=payload["status"],
            evidence=evidence,
            confidence=payload["confidence"],
        )
    return SchemaInstance(
        paper_id=record["paper_id"],
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        fields=fields,
    )


def build_paper_metadata(record: dict[str, Any]) -> PaperMetadata:
    return PaperMetadata(
        paper_id=record["paper_id"],
        title=record["title"],
        authors=list(record["authors"]),
        year=record["year"],
    )


def build_workspace_context(workspace_id: str, paper_ids: list[str]) -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=workspace_id,
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        paper_ids=list(paper_ids),
    )


def load_workspace(
    workspace_id: str,
    paper_ids: list[str],
    fixture: dict[str, Any] | None = None,
) -> tuple[WorkspaceContext, SchemaDefinition, dict[str, SchemaInstance], dict[str, PaperMetadata]]:
    """Load one deterministic fixture workspace.

    Rejects unknown paper ids and, for the two fixture acceptance workspaces,
    any paper set that is out of the workspace's reviewed roster, with
    ``ValueError`` before any model is built. Ordering of ``paper_ids`` is
    preserved.
    """
    fixture = load_fixture() if fixture is None else fixture
    records = papers_by_id(fixture)
    unknown = [pid for pid in paper_ids if pid not in records]
    if unknown:
        raise ValueError(
            f"unknown fixture paper ids for workspace {workspace_id!r}: {sorted(unknown)}"
        )
    roster = FIXTURE_WORKSPACES.get(workspace_id)
    if roster is not None and list(paper_ids) != list(roster):
        raise ValueError(
            f"paper set {list(paper_ids)!r} is out of the fixture roster for workspace "
            f"{workspace_id!r}: {roster!r}"
        )
    context = build_workspace_context(workspace_id, paper_ids)
    definition = build_generic_schema_definition()
    instances = {pid: build_instance(records[pid]) for pid in paper_ids}
    metadata = {pid: build_paper_metadata(records[pid]) for pid in paper_ids}
    return context, definition, instances, metadata


# ---------------------------------------------------------------------------
# Canonical snapshots and governed-field stripping (AC-F-009).
# ---------------------------------------------------------------------------


def strip_governed(value: Any) -> Any:
    """Recursively remove explicitly governed fields from a JSON structure."""
    if isinstance(value, dict):
        return {key: strip_governed(item) for key, item in value.items() if key not in GOVERNED_FIELDS}
    if isinstance(value, list):
        return [strip_governed(item) for item in value]
    return value


def snapshot_sha256s(store: Any) -> dict[str, str]:
    """Return deterministic name -> sha256 map of the raw snapshot files."""
    raw = store.read_raw_snapshot()
    return {name: (asset["sha256"] or "missing") for name, asset in sorted(raw.items())}


def object_id_sets(store: Any) -> dict[str, set[str]]:
    return {
        "pages": {page.page_id for page in store.list_pages()},
        "entities": {entity.entity_id for entity in store.list_entities()},
        "links": {link.link_id for link in store.list_links()},
    }


# ---------------------------------------------------------------------------
# Deterministic boundary fakes (proposal, resolver decision, embedding).
# ---------------------------------------------------------------------------


def _values_equal(left: Any, right: Any) -> bool:
    """Deep equality that treats tuples and lists as interchangeable.

    Field Card values for ``list`` fields are frozen into tuples by the real
    ``build_field_cards`` machinery, while the fixture authors plain lists;
    this comparison keeps the boundary fake exact while being container-agnostic.
    """
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_values_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_values_equal(left[key], right[key]) for key in left)
    return left == right


class ProposalFake:
    """Deterministic structured-output fake for ``EntityProposalRunner``.

    Validates that the request was built from the real ``build_field_cards``
    output (paper / schema binding, exact field-id set, authored field
    values), records every call, and returns exactly the fixture-authored
    proposal list for that paper. It never writes store state.
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        self._papers = papers_by_id(fixture)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: Any) -> dict[str, Any]:
        paper_id = getattr(request, "paper_id", None)
        record = self._papers.get(paper_id)
        if record is None:
            raise AssertionError(f"proposal request for unknown fixture paper: {paper_id!r}")
        if getattr(request, "schema_id", None) != SCHEMA_ID:
            raise AssertionError("proposal request schema identity does not match the fixture")
        if getattr(request, "schema_version", None) != SCHEMA_VERSION:
            raise AssertionError("proposal request schema version does not match the fixture")
        cards = tuple(getattr(request, "cards", ()))
        expected_fields = set(record["instance_fields"])
        card_fields = {card.field_id for card in cards}
        if card_fields != expected_fields:
            raise AssertionError(
                f"proposal request field set {sorted(card_fields)!r} does not match the "
                f"fixture {sorted(expected_fields)!r}; cards were not built by build_field_cards"
            )
        for card in cards:
            authored = record["instance_fields"][card.field_id]
            if not _values_equal(card.value, authored["value"]):
                raise AssertionError(
                    f"card {card.field_id!r} value does not match the fixture-authored instance"
                )
            if card.status != authored["status"]:
                raise AssertionError(f"card {card.field_id!r} status does not match the fixture")
        proposals = list(record["proposals"])
        self.calls.append({"paper_id": paper_id, "proposal_count": len(proposals)})
        return {"proposals": proposals}

    __call__ = __call__  # type: ignore[assignment]


class DecisionFake:
    """Deterministic ``ResolutionDecisionProvider`` for the real resolver.

    The real resolver reaches the decider only for genuinely distinct
    concepts (exact canonical/alias reuse never consults it); ``create`` is
    therefore always the correct deterministic answer. Logs every call. Never
    creates entities or links itself.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, proposal: Any, candidates: tuple[Any, ...]) -> EntityResolutionDecision:
        self.calls.append(
            {"canonical_name": proposal.canonical_name, "candidate_count": len(candidates)}
        )
        return EntityResolutionDecision(
            action="create",
            reason="deterministic fixture decision",
            target_entity_id=None,
            confidence=1.0,
        )


class FakeEmbedding(EmbeddingProvider):
    """Fixed vector embedding provider bound to the existing injection boundary.

    Keeps ``search_entities`` semantic mode offline, deterministic, and with
    ``status="ok"`` so the real resolver's semantic-candidate path is
    exercised without any network or credential access.
    """

    available = True
    reason = None
    info = ProviderInfo(provider="l2s3-fixture", model="deterministic", dimension=2)

    def dimension(self) -> int | None:
        return 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


# ---------------------------------------------------------------------------
# Output-dir injection (AC-F-010: summary written ONLY to an injected root).
# ---------------------------------------------------------------------------


def resolve_output_dir(fallback: Path) -> Path:
    """Return ``L2S3_PACKAGE_F_OUTPUT_DIR`` when set, else the pytest temp root."""
    env_value = os.environ.get("L2S3_PACKAGE_F_OUTPUT_DIR")
    if env_value:
        path = Path(env_value)
        path.mkdir(parents=True, exist_ok=True)
        return path
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def expected_entity_id(workspace_id: str, canonical_name: str) -> str:
    """Deterministic workspace-scoped entity id (mirrors the real models API)."""
    return entity_id_for(workspace_id, canonical_name)