"""L2S3 Package F — end-to-end acceptance test module.

Runs the committed, reviewed real-paper fixture through the real Package A
Store, Package B Service, Package C Field Card/proposal path, Package D
resolver, and Package E builder, with deterministic fakes injected only at
the approved proposal, resolver-decision, and existing injectable
embedding-provider boundaries. All storage and output is confined to pytest
temporary roots and the injected ``L2S3_PACKAGE_F_OUTPUT_DIR``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from transit_scholar.layer2.wiki import (
    EntityProposalRunner,
    EntityResolver,
    PageEntityLink,
    PaperMetadata,
    WikiCorruptionError,
    WikiManifest,
    WikiNotFoundError,
    WikiService,
    WikiStore,
    WikiWorkspaceMismatchError,
    build_field_cards,
    build_wiki_for_paper,
    build_wiki_for_workspace,
    link_id_for,
    normalize_entity_name,
)

from tests.l2s3_package_f_support import (
    BIBLIOGRAPHY_SOURCE_ROLE,
    FAKE_CALL_LOG_FILE_NAME,
    FIELD_IDS,
    GOLD_BENCHMARK_ID,
    GOLD_PATH,
    GOOD_SOURCE_ROLE,
    GOVERNED_FIELDS,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SUMMARY_FILE_NAME,
    DecisionFake,
    FakeEmbedding,
    ProposalFake,
    build_workspace_context,
    load_fixture,
    load_gold,
    load_workspace,
    object_id_sets,
    resolve_output_dir,
    snapshot_sha256s,
    strip_governed,
    write_json,
)

WORKSPACE_A_ID = "f-wiki-a"
WORKSPACE_A_PAPERS = ["transit-001", "transit-002", "transit-010"]
WORKSPACE_B_ID = "f-wiki-b"
WORKSPACE_B_PAPERS = ["transit-006"]
SHARED_CONCEPT = "Bus Holding Control"
EXPECTED_ENTITY_CANONICALS_A = {
    "Bus Holding Control",
    "Distributional MARL",
    "Multi-line Bus Scheduling",
    "PPO",
    "DQN",
}
#: Field -> reviewed fixture confidence values for link trace assertions.
PROPOSAL_CONFIDENCE = {"control_concept": 0.92, "method_concept": 0.9}

REQUIRED_SUMMARY_KEYS = [
    "fixture_id",
    "workspace",
    "counts",
    "manifest",
    "audit",
    "isolation",
    "idempotence",
    "network_blocked",
    "command",
    "exit_code",
    "provenance",
    "trace_report",
    "related_pages",
]


def _canonical_run_state(store: WikiStore) -> dict[str, object]:
    """Canonical, governed-field-stripped projection of a persisted snapshot."""
    return strip_governed(
        {
            "manifest": store.get_manifest().model_dump(mode="json"),
            "pages": [page.model_dump(mode="json") for page in store.list_pages()],
            "entities": [entity.model_dump(mode="json") for entity in store.list_entities()],
            "links": [link.model_dump(mode="json") for link in store.list_links()],
        }
    )


def _compose_summary(**payload: object) -> dict[str, object]:
    summary = {
        "fixture_id": None,
        "workspace": None,
        "counts": None,
        "manifest": None,
        "audit": None,
        "isolation": None,
        "idempotence": None,
        "network_blocked": None,
        "command": None,
        "exit_code": None,
        "provenance": None,
        "trace_report": None,
        "related_pages": [],
    }
    summary.update(payload)
    return summary


def _assert_provenance_against_gold(fixture: dict[str, object], gold: dict[str, object]) -> None:
    """AC-F-001 provenance: identity/title/schema equality with the reviewed gold."""
    gold_papers = {record["paper_id"]: record for record in gold["papers"]}
    for record in fixture["papers"]:
        paper_id = record["paper_id"]
        gold_record = gold_papers[paper_id]
        provenance = record["provenance"]["gold"]
        assert gold_record["gold_status"] == "evaluated"
        assert provenance["gold_status"] == gold_record["gold_status"]
        assert provenance["review_status"] == "codex_reviewed"
        assert gold_record["schema_id"] == "bus_control_rl"
        assert gold_record["schema_version"] == "1.0"
        assert provenance["schema_id"] == gold_record["schema_id"]
        assert provenance["schema_version"] == gold_record["schema_version"]
        if paper_id == "transit-010":
            # The gold stores the title en dash as an ASCII question mark; the
            # reviewed bibliography manifest records the en dash explicitly.
            assert record["title"] == gold_record["title"].replace("?", "\u2013")
        else:
            assert record["title"] == gold_record["title"]


def _build_workspace(
    context: object,
    definition: object,
    instances: dict[str, object],
    metadata: dict[str, object],
    store: WikiStore,
    fixture: dict[str, object],
) -> tuple[object, ProposalFake, DecisionFake]:
    service = WikiService(context, store, FakeEmbedding())
    proposal_fake = ProposalFake(fixture)
    decision_fake = DecisionFake()
    runner = EntityProposalRunner(proposal_fake)
    resolver = EntityResolver(context, service, decision_fake)
    result = build_wiki_for_workspace(
        context, definition, instances, metadata, service, runner, resolver
    )
    return result, proposal_fake, decision_fake


# ---------------------------------------------------------------------------
# AC-F-001 — fixture provenance and shape
# ---------------------------------------------------------------------------


def test_fixture_provenance_and_shape():
    fixture = load_fixture()
    gold = load_gold()

    assert fixture["fixture_id"] == "l2s3-package-f-real-papers-v1"
    assert fixture["schema"]["schema_id"] == SCHEMA_ID
    assert fixture["schema"]["schema_version"] == SCHEMA_VERSION
    assert gold["benchmark_id"] == GOLD_BENCHMARK_ID
    roles = {source["role"] for source in fixture["sources"]}
    assert roles == {GOOD_SOURCE_ROLE, BIBLIOGRAPHY_SOURCE_ROLE}
    for source in fixture["sources"]:
        assert source["runtime_read"] is False

    records = fixture["papers"]
    assert 3 <= len(records) <= 5
    assert len({record["paper_id"] for record in records}) == len(records)

    all_canonical: list[str] = []
    shared_counts: dict[str, int] = {}
    for record in records:
        assert record["paper_id"]
        assert record["title"].strip()
        assert record["authors"] and all(author.strip() for author in record["authors"])
        year = record["year"]
        assert isinstance(year, int) and 1000 <= year <= 3000
        assert set(record["instance_fields"]) == set(FIELD_IDS)
        for field_id, payload in record["instance_fields"].items():
            assert payload["status"] == "explicit"
            assert isinstance(payload["confidence"], float) and 0.0 <= payload["confidence"] <= 1.0
            for evidence in payload.get("evidence", []):
                assert evidence["char_end"] >= evidence["char_start"]
                assert evidence["quote"]
        for proposal in record["proposals"]:
            assert proposal["canonical_name"]
            assert proposal["source_field_id"] in FIELD_IDS
            all_canonical.append(proposal["canonical_name"])
            shared_counts[proposal["canonical_name"]] = shared_counts.get(proposal["canonical_name"], 0) + 1

    assert len(set(all_canonical)) >= 2
    shared_by_multiple = [name for name, count in shared_counts.items() if count >= 2]
    assert len(shared_by_multiple) >= 1
    assert SHARED_CONCEPT in shared_by_multiple

    # Determinism and local-only loading.
    assert load_fixture() == fixture

    _assert_provenance_against_gold(fixture, gold)

    fixture_text = fixture_path_text()
    for token in (
        "sk-",
        "Bearer ",
        "api.jina.ai",
        "dashscope",
        "https://",
        "http://",
        "TRANSIT_SCHOLAR_",
        "JINA_API_KEY",
    ):
        assert token not in fixture_text, f"credential-like token present in fixture: {token!r}"


def fixture_path_text() -> str:
    from tests.l2s3_package_f_support import FIXTURE_PATH

    return FIXTURE_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-F-001 loader — generic schema models
# ---------------------------------------------------------------------------


def test_loader_constructs_valid_generic_schema_models():
    context, definition, instances, metadata = load_workspace(WORKSPACE_A_ID, WORKSPACE_A_PAPERS)
    assert context.workspace_id == WORKSPACE_A_ID
    assert context.schema_id == SCHEMA_ID and context.schema_version == SCHEMA_VERSION
    assert context.paper_ids == WORKSPACE_A_PAPERS

    field_ids = [field.id for section in definition.sections for field in section.fields]
    assert field_ids == list(FIELD_IDS)
    for instance in instances.values():
        assert set(instance.fields) == set(FIELD_IDS)
        for result in instance.fields.values():
            assert result.status == "explicit"
    for paper_id, record in metadata.items():
        assert record.paper_id == paper_id
        assert record.title and record.authors and record.year

    # Real Package C boundary for one paper (also exercised by the runbook).
    cards = build_field_cards(definition, instances["transit-001"])
    assert [card.field_id for card in cards] == list(FIELD_IDS)
    assert all(card.status == "explicit" for card in cards)
    assert all(card.evidence_required for card in cards)

    # Loader guard rails: unknown/foreign paper ids and empty workspaces fail.
    with pytest.raises(ValueError):
        load_workspace(WORKSPACE_A_ID, ["transit-001", "transit-999"])
    with pytest.raises(ValueError):
        load_workspace(WORKSPACE_A_ID, ["transit-006"])  # not part of workspace A
    with pytest.raises(ValueError):
        load_workspace(WORKSPACE_A_ID, [])  # type: ignore[arg-type]

    # Deterministic entity ids follow the real workspace-scoped hashing.
    from tests.l2s3_package_f_support import expected_entity_id

    assert expected_entity_id("f-wiki-a", SHARED_CONCEPT).startswith("entity_")
    assert expected_entity_id("f-wiki-a", SHARED_CONCEPT) != expected_entity_id(
        "f-wiki-b", SHARED_CONCEPT
    )


# ---------------------------------------------------------------------------
# AC-F-002..010 — the full multi-paper acceptance runbook
# ---------------------------------------------------------------------------


def test_multi_paper_real_papers_acceptance(project_tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSIT_SCHOLAR_BLOCK_NETWORK", "1")
    output_dir = resolve_output_dir(project_tmp_path / "package-f-output")

    fixture = load_fixture()
    gold = load_gold()
    _assert_provenance_against_gold(fixture, gold)

    # ---------------- Workspace A: real A-E chain from an empty root --------
    root_a = project_tmp_path / "ws-a"
    context, definition, instances, metadata = load_workspace(WORKSPACE_A_ID, WORKSPACE_A_PAPERS)
    store_a = WikiStore(context, root_a)
    service_a = WikiService(context, store_a, FakeEmbedding())
    result, proposal_fake, decision_fake = _build_workspace(
        context, definition, instances, metadata, store_a, fixture
    )

    # AC-F-002/003: all three papers complete, one page each, revision 2.
    assert result.status == "complete", result.model_dump()
    assert result.complete_count == 3 and result.incomplete_count == 0 and result.failed_count == 0
    pages = store_a.list_pages()
    assert len(pages) == 3
    assert {page.paper_id for page in pages} == set(WORKSPACE_A_PAPERS)
    assert len({page.page_id for page in pages}) == 3
    per_paper = {paper.paper_id: paper for paper in result.papers}
    for paper_id in WORKSPACE_A_PAPERS:
        paper_result = per_paper[paper_id]
        assert paper_result.status == "complete"
        assert paper_result.page.build_status == "complete"
        assert paper_result.page.build_revision == 2
        page = store_a.get_page(paper_result.page.page_id)
        assert page.paper_id == paper_id
        expected_metadata = metadata[paper_id]
        assert page.title == expected_metadata.title
        assert page.summary.startswith(expected_metadata.title)
        assert "Authors:" in page.summary and "Year:" in page.summary

    # AC-F-004: shared concept across two papers and distinct concepts distinct.
    entities = store_a.list_entities()
    assert len(entities) == 5
    canonical_names = {entity.canonical_name for entity in entities}
    assert canonical_names == EXPECTED_ENTITY_CANONICALS_A
    normalized_names = {normalize_entity_name(entity.canonical_name) for entity in entities}
    assert len(normalized_names) == len(entities)  # no normalized duplicate entities
    holding = service_a.find_entities_by_canonical_name(SHARED_CONCEPT)
    assert len(holding) == 1

    # AC-F-004 evidence: resolver traces and deterministic fake boundary logs.
    assert proposal_fake.calls == [
        {"paper_id": "transit-001", "proposal_count": 2},
        {"paper_id": "transit-002", "proposal_count": 2},
        {"paper_id": "transit-010", "proposal_count": 2},
    ]
    # The real resolver consults the decision boundary for every genuinely
    # distinct concept (create), with a deterministic, explainable candidate
    # count: the real service's semantic mode scores all existing entities, so
    # candidates grow one-by-one with each newly created concept. The shared
    # concept never reaches the decider (exact-canonical reuse).
    assert decision_fake.calls == [
        {"canonical_name": "Distributional MARL", "candidate_count": 1},
        {"canonical_name": "Multi-line Bus Scheduling", "candidate_count": 2},
        {"canonical_name": "PPO", "candidate_count": 3},
        {"canonical_name": "DQN", "candidate_count": 4},
    ]
    for paper_id in ("transit-001", "transit-002"):
        for trace in per_paper[paper_id].proposals:
            assert trace.resolution == "create" and trace.resolution_reason == "created"
            assert trace.entity_id and trace.link_id
    assert per_paper["transit-010"].proposals[0].resolution == "reuse"
    # Exact-canonical reuse is reported distinctly from semantic reuse; the
    # shared concept is never recreated.
    assert per_paper["transit-010"].proposals[0].resolution_reason == "exact_match"
    assert per_paper["transit-010"].proposals[1].resolution == "create"
    assert per_paper["transit-010"].proposals[1].resolution_reason == "created"

    # AC-F-005: bidirectional links exactly once.
    page_by_paper = {page.paper_id: page for page in pages}
    linked_pages = {page.paper_id for page in service_a.find_pages_by_entity(holding[0].entity_id)}
    assert linked_pages == {"transit-001", "transit-010"}
    page_result_001 = service_a.list_page_entities(page_by_paper["transit-001"].page_id)
    assert [entity.entity_id for entity in page_result_001.entities].count(holding[0].entity_id) == 1
    page_result_010 = service_a.list_page_entities(page_by_paper["transit-010"].page_id)
    assert [entity.entity_id for entity in page_result_010.entities].count(holding[0].entity_id) == 1

    # AC-F-005: related pages are derived only from shared entities.
    related = service_a.find_related_pages(page_by_paper["transit-001"].page_id)
    assert len(related) == 1
    assert related[0].page.page_id == page_by_paper["transit-010"].page_id
    assert related[0].shared_entity_ids == [holding[0].entity_id]
    assert related[0].shared_entity_count == 1
    related_010 = service_a.find_related_pages(page_by_paper["transit-010"].page_id)
    assert [item.page.page_id for item in related_010] == [page_by_paper["transit-001"].page_id]
    assert service_a.find_related_pages(page_by_paper["transit-002"].page_id) == []

    # AC-F-003: manifest completion through the existing store boundary.
    manifest = WikiManifest(
        workspace_id=context.workspace_id,
        schema_id=context.schema_id,
        schema_version=context.schema_version,
        paper_ids=list(context.paper_ids),
        builder_version="wiki-core-v1",
        build_status="complete",
    )
    persisted_manifest = store_a.upsert_manifest(manifest)
    assert persisted_manifest.build_status == "complete"
    assert persisted_manifest.paper_ids == WORKSPACE_A_PAPERS
    assert store_a.get_manifest().paper_ids == WORKSPACE_A_PAPERS
    service_a.rebuild_indexes()

    # AC-F-007: every persisted link preserves the full trace.
    links = store_a.list_links()
    assert len(links) == 6
    trace_report: dict[str, object] = {"links": [], "fields": {}}
    definition_fields = {
        field.id: {"label": field.label, "question": field.question}
        for section in definition.sections
        for field in section.fields
    }
    trace_report["fields"] = definition_fields
    for link in links:
        page = store_a.get_page(link.page_id)
        entity = store_a.get_entity(link.entity_id)
        assert link.paper_id == page.paper_id
        assert link.workspace_id == context.workspace_id
        assert link.schema_id == SCHEMA_ID and link.schema_version == SCHEMA_VERSION
        assert link.source_field_id in FIELD_IDS[:-1]
        assert link.source_status == "explicit"
        assert link.confidence == PROPOSAL_CONFIDENCE[link.source_field_id]
        assert link.link_id == link_id_for(
            link.workspace_id,
            link.page_id,
            link.entity_id,
            link.source_field_id,
            link.source_status,
            link.schema_id,
            link.schema_version,
        )
        assert entity.workspace_id == context.workspace_id
        trace_report["links"].append(
            {
                "link_id": link.link_id,
                "paper_id": link.paper_id,
                "page_id": link.page_id,
                "entity_id": link.entity_id,
                "schema_id": link.schema_id,
                "schema_version": link.schema_version,
                "source_field_id": link.source_field_id,
                "source_status": link.source_status,
                "confidence": link.confidence,
            }
        )
    trace_report["link_count"] = len(links)
    trace_report["all_link_ids_match"] = True
    trace_report["dangling_or_foreign_targets"] = 0

    # AC-F-006: search remains workspace scoped (A terms hit only A objects;
    # B-unique terms are empty in A).
    a_id_sets = object_id_sets(store_a)
    all_a_ids = a_id_sets["pages"] | a_id_sets["entities"]
    search_hits = service_a.search_wiki("holding")
    assert search_hits.status == "ok"
    assert {hit.object_id for hit in search_hits.hits} <= all_a_ids
    assert holding[0].entity_id in {hit.object_id for hit in search_hits.hits}
    page_hits = {hit.object_id for hit in service_a.search_pages("holding").hits}
    assert page_hits and page_hits <= a_id_sets["pages"]
    entity_hits = {hit.object_id for hit in service_a.search_entities("holding").hits}
    assert holding[0].entity_id in entity_hits and entity_hits <= a_id_sets["entities"]
    assert service_a.search_wiki("timetable").hits == []
    assert service_a.search_wiki("stranding").hits == []

    # AC-F-008: read-only audits with zero issues after manifest + index.
    hashes_before_audit = snapshot_sha256s(store_a)
    for page in pages:
        report = service_a.audit_page(page.page_id)
        assert report.ok and report.issues == []
    wiki_report = service_a.audit_wiki()
    assert wiki_report.ok and wiki_report.issues == []
    assert snapshot_sha256s(store_a) == hashes_before_audit

    # AC-F-009: reload from disk preserves every id, count, ordering, and query.
    store_a2 = WikiStore(context, root_a)
    service_a2 = WikiService(context, store_a2, FakeEmbedding())
    assert snapshot_sha256s(store_a2) == snapshot_sha256s(store_a)
    assert [page.page_id for page in store_a2.list_pages()] == [page.page_id for page in pages]
    assert [entity.entity_id for entity in store_a2.list_entities()] == [
        entity.entity_id for entity in entities
    ]
    assert [link.link_id for link in store_a2.list_links()] == [link.link_id for link in links]
    assert [page.page_id for page in service_a2.find_pages_by_entity(holding[0].entity_id)] == [
        page.page_id for page in service_a.find_pages_by_entity(holding[0].entity_id)
    ]
    assert service_a2.find_related_pages(page_by_paper["transit-001"].page_id) == related
    reloaded_hits = service_a2.search_wiki("holding")
    assert [(hit.object_id, hit.score) for hit in reloaded_hits.hits] == [
        (hit.object_id, hit.score) for hit in search_hits.hits
    ]

    # AC-F-009: rerunning the identical fixture is idempotent.
    first_state = _canonical_run_state(store_a)
    result2, proposal_fake2, decision_fake2 = _build_workspace(
        context, definition, instances, metadata, store_a, fixture
    )
    assert result2.status == "complete"
    assert proposal_fake2.calls == proposal_fake.calls
    # On the rerun every concept resolves through the exact-canonical reuse
    # path, so the deterministic decision boundary is not consulted again and
    # no new entity is created (idempotence).
    assert decision_fake2.calls == []
    store_a.upsert_manifest(manifest)
    service_a.rebuild_indexes()
    second_state = _canonical_run_state(store_a)
    assert second_state == first_state
    rerun_ids = object_id_sets(store_a)
    for kind in ("pages", "entities", "links"):
        assert rerun_ids[kind] == a_id_sets[kind]
    rerun_duplicates = {
        kind: len(rerun_ids[kind] - a_id_sets[kind]) for kind in ("pages", "entities", "links")
    }
    assert rerun_duplicates == {"pages": 0, "entities": 0, "links": 0}

    # ---------------- Workspace B + isolation (AC-F-006) --------------------
    root_b = project_tmp_path / "ws-b"
    context_b, definition_b, instances_b, metadata_b = load_workspace(
        WORKSPACE_B_ID, WORKSPACE_B_PAPERS
    )
    store_b = WikiStore(context_b, root_b)
    service_b = WikiService(context_b, store_b, FakeEmbedding())
    result_b, proposal_fake_b, decision_fake_b = _build_workspace(
        context_b, definition_b, instances_b, metadata_b, store_b, fixture
    )
    assert result_b.status == "complete"
    manifest_b = WikiManifest(
        workspace_id=context_b.workspace_id,
        schema_id=context_b.schema_id,
        schema_version=context_b.schema_version,
        paper_ids=list(context_b.paper_ids),
        builder_version="wiki-core-v1",
        build_status="complete",
    )
    store_b.upsert_manifest(manifest_b)
    service_b.rebuild_indexes()

    b_id_sets = object_id_sets(store_b)
    b_all_ids = b_id_sets["pages"] | b_id_sets["entities"]
    for kind in ("pages", "entities", "links"):
        assert a_id_sets[kind].isdisjoint(b_id_sets[kind])

    b_timetable = service_b.search_wiki("timetable")
    assert b_timetable.status == "ok" and b_timetable.hits
    assert {hit.object_id for hit in b_timetable.hits} <= b_all_ids
    b_stranding = service_b.search_entities("stranding")
    assert b_stranding.status == "ok" and b_stranding.hits
    assert {hit.object_id for hit in b_stranding.hits} <= b_id_sets["entities"]
    assert service_b.search_wiki("holding").hits == []  # A-unique term

    b_pages = store_b.list_pages()
    b_entities = store_b.list_entities()
    b_links = store_b.list_links()
    a_hashes_before_foreign = snapshot_sha256s(store_a)

    with pytest.raises(WikiNotFoundError):
        service_a.get_page(b_pages[0].page_id)
    with pytest.raises(WikiNotFoundError):
        service_a.get_entity(b_entities[0].entity_id)
    with pytest.raises(WikiNotFoundError):
        store_a.get_link(b_links[0].link_id)  # the service has no link getter; store lookup fails
    with pytest.raises(WikiNotFoundError):
        service_b.get_page(page_by_paper["transit-001"].page_id)

    with pytest.raises(WikiWorkspaceMismatchError):
        service_a.link_page_entity(
            page_by_paper["transit-001"],
            b_entities[0],
            source_field_id="method_concept",
            source_status="explicit",
            confidence=0.9,
        )
    with pytest.raises(WikiWorkspaceMismatchError):
        service_a.update_entity(b_entities[0])
    with pytest.raises(WikiWorkspaceMismatchError):
        service_a.create_entity(b_entities[0])
    with pytest.raises(WikiWorkspaceMismatchError):
        WikiService(context, store_b)

    assert snapshot_sha256s(store_a) == a_hashes_before_foreign

    # ---------------- Machine-readable summary (AC-F-010) -------------------
    network_blocked = os.environ.get("TRANSIT_SCHOLAR_BLOCK_NETWORK", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    repo_root = Path(__file__).resolve().parents[1]
    command = "python -m pytest " + " ".join(sys.argv[1:])
    summary = _compose_summary(
        fixture_id=fixture["fixture_id"],
        workspace={
            "workspace_id": WORKSPACE_A_ID,
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
        },
        counts={
            "papers": 3,
            "pages": len(pages),
            "entities": len(entities),
            "links": len(links),
        },
        manifest={
            "status": store_a.get_manifest().build_status,
            "paper_ids": store_a.get_manifest().paper_ids,
        },
        audit={
            "audit_wiki_ok": True,
            "audit_pages_ok": True,
            "issue_count": 0,
        },
        isolation={
            "workspace_a_searches_empty_for_b_terms": True,
            "workspace_b_searches_empty_for_a_terms": True,
            "foreign_reads_rejected": True,
            "foreign_mutations_rejected": True,
            "a_hashes_unchanged": True,
        },
        idempotence={
            "reload_identical": True,
            **{f"rerun_duplicate_{kind}": count for kind, count in rerun_duplicates.items()},
            "canonical_identical": True,
            "governed_fields": list(GOVERNED_FIELDS),
        },
        network_blocked=network_blocked,
        command=command,
        exit_code=0,
        provenance={
            "gold": str(Path(GOLD_PATH).resolve().relative_to(repo_root)),
            "bibliography": "data/stage7_acceptance/real_papers/manifest.json (documented, not runtime-read)",
            "gold_benchmark_id": gold["benchmark_id"],
        },
        trace_report=trace_report,
        related_pages=[
            {
                "page_id": page_by_paper["transit-001"].page_id,
                "related_page_ids": [item.page.page_id for item in related],
                "shared_entity_ids": [holding[0].entity_id],
                "shared_entity_count": 1,
                "unrelated_page_ids": [page_by_paper["transit-002"].page_id],
            }
        ],
    )
    summary_path = output_dir / SUMMARY_FILE_NAME
    write_json(summary_path, summary)
    write_json(
        output_dir / FAKE_CALL_LOG_FILE_NAME,
        {
            "proposal_calls": proposal_fake.calls,
            "decision_calls": decision_fake.calls,
            "workspace_b_proposal_calls": proposal_fake_b.calls,
            "workspace_b_decision_calls": decision_fake_b.calls,
        },
    )
    reloaded_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert reloaded_summary == json.loads(json.dumps(summary))
    for key in REQUIRED_SUMMARY_KEYS:
        assert key in reloaded_summary, f"summary missing required key: {key}"

    print(f"PACKAGE F SUMMARY -> {summary_path}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


# ---------------------------------------------------------------------------
# AC-F-003 — manifest completion guards (missing/extra/duplicate)
# ---------------------------------------------------------------------------


def test_manifest_completion_guards_missing_extra_duplicate(project_tmp_path):
    context = build_workspace_context("f-neg", ["p1", "p2"])
    store = WikiStore(context, project_tmp_path / "neg-store")
    service = WikiService(context, store, FakeEmbedding())
    for paper_id in ("p1", "p2"):
        service.ensure_paper_page(
            PaperMetadata(paper_id=paper_id, title=f"Paper {paper_id}", authors=["A"], year=2024)
        )
    complete = WikiManifest(
        workspace_id=context.workspace_id,
        schema_id=context.schema_id,
        schema_version=context.schema_version,
        paper_ids=["p1", "p2"],
        builder_version="wiki-core-v1",
        build_status="complete",
    )
    assert store.upsert_manifest(complete).build_status == "complete"

    missing = complete.model_copy(update={"paper_ids": ["p1"]})
    with pytest.raises(WikiCorruptionError):
        store.upsert_manifest(missing)
    extra = complete.model_copy(update={"paper_ids": ["p1", "p2", "p3"]})
    with pytest.raises(WikiCorruptionError):
        store.upsert_manifest(extra)

    # Duplicate paper identity: re-creating the page returns the existing one.
    duplicate_page = service.ensure_paper_page(
        PaperMetadata(paper_id="p1", title="Paper p1", authors=["A"], year=2024)
    )
    assert duplicate_page.page_id in {page.page_id for page in store.list_pages()}
    assert len(store.list_pages()) == 2

    # Duplicate page records in the raw snapshot fail store validation.
    pages_path = store.pages_path
    first_line = pages_path.read_text(encoding="utf-8").splitlines()[0]
    pages_path.write_text(first_line + "\n" + first_line + "\n", encoding="utf-8")
    with pytest.raises(WikiCorruptionError):
        WikiStore(context, project_tmp_path / "neg-store").list_pages()


# ---------------------------------------------------------------------------
# AC-F-008 — injected audit failures cannot produce a false complete result
# ---------------------------------------------------------------------------


def test_injected_audit_failure_prevents_false_complete(project_tmp_path):
    fixture = load_fixture()
    context, definition, instances, metadata = load_workspace("f-audit", ["transit-001"])
    store = WikiStore(context, project_tmp_path / "audit-store")
    service = WikiService(context, store, FakeEmbedding())
    result = build_wiki_for_paper(
        context,
        definition,
        instances["transit-001"],
        metadata["transit-001"],
        service,
        EntityProposalRunner(ProposalFake(fixture)),
        EntityResolver(context, service, DecisionFake()),
    )
    assert result.status == "complete"

    # Inject a dangling link directly into the raw snapshot; audits detect it
    # as an error while remaining strictly read-only.
    dangling = PageEntityLink(
        link_id="link_" + "d" * 64,
        workspace_id=context.workspace_id,
        page_id="page_" + "d" * 64,
        entity_id="entity_" + "e" * 64,
        paper_id="transit-001",
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        source_field_id="method_concept",
        source_status="explicit",
        confidence=0.5,
    )
    with store.links_path.open("a", encoding="utf-8") as handle:
        handle.write(dangling.model_dump_json() + "\n")
    hashes_before = snapshot_sha256s(store)
    report = service.audit_wiki()
    assert not report.ok
    codes = {issue.code for issue in report.issues}
    assert {"dangling_page_link", "dangling_entity_link"} <= codes
    assert snapshot_sha256s(store) == hashes_before  # audits never repair data

    # A corrupted derived index fails the builder's own audit phase, so the
    # injected failure prevents a false "complete" status.
    context2, definition2, instances2, metadata2 = load_workspace("f-audit2", ["transit-001"])
    store2 = WikiStore(context2, project_tmp_path / "audit2-store")
    service2 = WikiService(context2, store2, FakeEmbedding())
    first = build_wiki_for_paper(
        context2,
        definition2,
        instances2["transit-001"],
        metadata2["transit-001"],
        service2,
        EntityProposalRunner(ProposalFake(fixture)),
        EntityResolver(context2, service2, DecisionFake()),
    )
    assert first.status == "complete"
    index = store2.index_path / "package_b_index.json"
    index.write_text(
        json.dumps({"index_version": 999, "source_fingerprint": "corrupt", "pages": [], "entities": [], "links": []}),
        encoding="utf-8",
    )
    rerun = build_wiki_for_paper(
        context2,
        definition2,
        instances2["transit-001"],
        metadata2["transit-001"],
        service2,
        EntityProposalRunner(ProposalFake(fixture)),
        EntityResolver(context2, service2, DecisionFake()),
    )
    assert rerun.status == "incomplete"
    assert rerun.audit.attempted and not rerun.audit.ok
    assert "index_corrupt" in rerun.audit.issue_codes


# ---------------------------------------------------------------------------
# AC-F-009/010 — summary schema and governed-field canonical projections
# ---------------------------------------------------------------------------


def test_summary_json_schema_and_governed_fields(project_tmp_path):
    for key in REQUIRED_SUMMARY_KEYS:
        summary = _compose_summary()
        assert key in summary

    sample = {
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "audited_at": "2026-01-03T00:00:00Z",
        "rebuilt_at": "2026-01-04T00:00:00Z",
        "build_revision": 4,
        "stable": {"page_id": "page_x", "count": 3},
    }
    other = {
        "created_at": "2026-02-01T00:00:00Z",
        "updated_at": "2026-02-02T00:00:00Z",
        "audited_at": "2026-02-03T00:00:00Z",
        "rebuilt_at": "2026-02-04T00:00:00Z",
        "build_revision": 8,
        "stable": {"page_id": "page_x", "count": 3},
    }
    assert strip_governed(sample) == strip_governed(other)
    assert "created_at" not in strip_governed(sample)
    assert list(GOVERNED_FIELDS) == [
        "created_at",
        "updated_at",
        "audited_at",
        "rebuilt_at",
        "build_revision",
    ]

    # Reload keeps raw snapshots byte-identical.
    context = build_workspace_context("f-hash", ["p1"])
    root = project_tmp_path / "hash-store"
    store = WikiStore(context, root)
    service = WikiService(context, store, FakeEmbedding())
    service.ensure_paper_page(
        PaperMetadata(paper_id="p1", title="Hash Paper", authors=["A"], year=2024)
    )
    before = snapshot_sha256s(store)
    after = snapshot_sha256s(WikiStore(context, root))
    assert after == before


# ---------------------------------------------------------------------------
# AC-F-013 — safety scan over the Package F footprint
# ---------------------------------------------------------------------------


def test_package_f_safety_scan():
    import re

    from tests.l2s3_package_f_support import FIXTURE_PATH

    sources = {
        "fixture": FIXTURE_PATH.read_text(encoding="utf-8"),
        "support": Path(__file__).resolve().parent.joinpath("l2s3_package_f_support.py").read_text(encoding="utf-8"),
        "test": Path(__file__).resolve().read_text(encoding="utf-8"),
    }

    # Credential-shaped content (real secrets would look like these patterns).
    secret_pattern = re.compile(r"(sk-[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9._-]{8,})")
    # The deleted smoke script token is spelled via concatenation so that even
    # the scanner itself contains no contiguous reference to the script path.
    deleted_script_token = "l2s2" + "_runtime_smoke"
    for name, text in sources.items():
        assert secret_pattern.search(text) is None, f"{name}: secret-shaped token present"
        assert deleted_script_token not in text, f"{name}: references the deleted smoke script"

    # The committed fixture and the support module must not contain credential
    # env names, provider URLs, bare credential prefixes, or any reference to
    # the deleted smoke script. The test module legitimately spells those
    # tokens out inside this scan function, so it is only subject to the
    # secret-shaped regex and the script token check above.
    for name in ("fixture", "support"):
        text = sources[name]
        for env_token in (
            "TRANSIT_SCHOLAR_LLM_API_KEY",
            "TRANSIT_SCHOLAR_EMBEDDING_API_KEY",
            "TRANSIT_SCHOLAR_RERANKER_API_KEY",
            "JINA_API_KEY",
        ):
            assert env_token not in text, f"{name}: credential env name present: {env_token!r}"
        for url_token in ("api.jina.ai", "dashscope", "https://", "http://"):
            assert url_token not in text, f"{name}: provider URL present: {url_token!r}"
        assert "sk-" not in text, f"{name}: credential prefix present"
        assert "Bearer " not in text, f"{name}: authorization prefix present"

    for name in ("support", "test"):
        text = sources[name]
        # The support module (runtime boundary) may not import network or
        # process-launch machinery anywhere; the test module is checked on its
        # module header (its scanner legitimately spells tokens out later).
        scanned = text if name == "support" else text.split("def ", 1)[0]
        for token in (
            "import socket",
            "from socket",
            "import requests",
            "from requests",
            "import urllib",
            "from urllib",
            "import httpx",
            "from httpx",
            "subprocess",
        ):
            assert token not in scanned, f"{name}: network/subprocess import present: {token!r}"
        # No file-access call may ever target the repository data/ tree.
        assert re.search(r"(open|read_text|read_bytes)\([^)]*data/", text) is None, (
            f"{name}: file access targeting data/"
        )
        assert "storage_root=None" not in scanned and "settings.layer2_dir" not in scanned

    assert '"data/' not in sources["support"] and "'data/" not in sources["support"]
    assert "remove_link(" not in sources["support"] and "unlink_page_entity(" not in sources["support"]

    # The fixture may document provenance paths, but every source entry must be
    # marked as documentation-only (runtime_read false).
    fixture = json.loads(sources["fixture"])
    bibliography_refs = json.dumps(fixture)
    assert "data/stage7_acceptance/real_papers/manifest.json" in bibliography_refs
    assert all(source["runtime_read"] is False for source in fixture["sources"])

    # No destructive or out-of-scope API usage anywhere in the runtime
    # boundary code (the support module). The acceptance test module itself is
    # reviewed code that binds only the governed public boundaries.
    support = sources["support"]
    for token in ("remove_link", "unlink_page_entity", "drop_all", "reset_database", "alembic", "migrate"):
        assert token not in support, f"destructive/out-of-scope token present: {token!r}"
