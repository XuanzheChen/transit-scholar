"""Layer3 Stage2 EvidenceLocator contract tests."""

from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

from transit_scholar.layer3.evidence import EvidenceLocator


def test_paper_locator_round_trips_all_available_provenance():
    locator = EvidenceLocator(
        workspace_id="workspace-1",
        paper_id="paper-1",
        source_kind="paper",
        block_id="block-42",
        pages=[3, 4],
        span={"start": 12, "end": 42},
    )

    restored = EvidenceLocator.model_validate_json(locator.model_dump_json())

    assert restored == locator
    assert restored.workspace_id == "workspace-1"
    assert restored.paper_id == "paper-1"
    assert restored.source_kind == "paper"
    assert restored.block_id == "block-42"
    assert restored.pages == [3, 4]
    assert restored.span is not None
    assert (restored.span.start, restored.span.end) == (12, 42)


def test_paper_locator_requires_identity_and_valid_span_ordering():
    with pytest.raises(ValidationError, match="paper_id"):
        EvidenceLocator(workspace_id="workspace-1", source_kind="paper")

    with pytest.raises(ValidationError, match="span end"):
        EvidenceLocator(
            workspace_id="workspace-1",
            paper_id="paper-1",
            source_kind="paper",
            span={"start": 42, "end": 12},
        )


def test_locator_is_provenance_only_and_does_not_require_claim_fields():
    locator = EvidenceLocator(
        workspace_id="workspace-1", paper_id="paper-1", source_kind="paper"
    )

    assert locator.model_dump(exclude_none=True) == {
        "workspace_id": "workspace-1",
        "source_kind": "paper",
        "paper_id": "paper-1",
    }
    assert {"claim_status", "claim_confidence", "conclusion"}.isdisjoint(
        EvidenceLocator.model_fields
    )


def test_evidence_contract_imports_without_agent_runtime_initialization():
    module = importlib.import_module("transit_scholar.layer3.evidence")

    assert module.EvidenceLocator is EvidenceLocator
