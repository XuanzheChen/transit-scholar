"""L2S2 Package E deterministic tests: report generation (AC-E-59),
AC-E-37..41 keys and wording, freeze rules and byte determinism.

Fully offline: storage roots point at ``tmp_path`` and all outputs are
written under ``tmp_path``. No network, no LLM, no PDF, no ``data/**`` IO.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from transit_scholar.layer2.schema_extraction import (
    PROMPT_VERSION,
    CurrentPointer,
    EvidenceRef,
    ExtractionManifest,
    FieldResult,
    RunManifest,
    SchemaInstance,
    SchemaRunStorage,
    ValidationReport,
    compute_extraction_config_hash,
)
from transit_scholar.layer2.schema_acceptance import (
    AcceptanceReport,
    GoldBenchmark,
    GoldField,
    GoldPaper,
    load_schema_gold,
    write_acceptance_report,
)
from transit_scholar.layer2.schema_acceptance.evaluate import evaluate_schema_gold
from transit_scholar.layer2.schema_acceptance.report import FREEZE_MESSAGE

FIXTURES = Path(__file__).parent / "fixtures" / "l2s2_schema_acceptance"
SEED_GOLD = FIXTURES / "seed_gold.json"
SEED_GOLD_CLEAN = FIXTURES / "seed_gold_clean.json"
DRAFT_GOLD = FIXTURES / "draft_gold.json"

CREATED_AT = "2026-08-14T00:00:00+00:00"
SCHEMA_HASH = "0" * 64

REQUIRED_JSON_KEYS = {
    "benchmark_id",
    "gold_version",
    "report_schema_version",
    "gold_path",
    "schema_id",
    "schema_version",
    "papers",
    "metrics",
    "issues",
    "traceability",
    "freeze",
    "generated_at",
}

MARKDOWN_SECTIONS = [
    "冻结建议",
    "总体指标",
    "每篇论文简表",
    "Top Failure Fields",
    "Failure Analysis",
    "后续建议",
]

REQUIRED_METRIC_KEYS = {
    "paper_count",
    "field_count",
    "evaluated_field_count",
    "value_correct_count",
    "value_partial_count",
    "value_incorrect_count",
    "value_not_evaluated_count",
    "value_accuracy",
    "status_accuracy",
    "evidence_required_count",
    "evidence_present_rate",
    "weak_traceability_rate",
    "strict_traceability_rate",
    "not_found_correctness",
    "issue_count",
}

FIELD_RESULT_KEYS = {
    "field_id",
    "expected_status",
    "predicted_status",
    "value_judgement",
    "effective_judgement",
    "value_match",
    "evidence_present",
    "evidence_support",
    "weak_ok_refs",
    "weak_fail_refs",
    "strict_mode",
    "issues",
}


# ---------------------------------------------------------------------------
# helpers (mirror the evaluate-test storage builder)
# ---------------------------------------------------------------------------


def _good_ref(block_id="blk_1", quote="real paper text") -> EvidenceRef:
    return EvidenceRef(
        block_id=block_id,
        char_start=0,
        char_end=len(quote),
        pages=[2],
        section_path=["Method"],
        quote=quote,
    )


def _seed_instance_stored() -> SchemaInstance:
    return SchemaInstance(
        paper_id="seed-eval-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_type": FieldResult(
                value="holding",
                status="explicit",
                evidence=[_good_ref("blk_ctrl", "holding control applied")],
            ),
            "research_problem.control_objective": FieldResult(
                value="minimize passenger waiting time", status="explicit"
            ),
            "rl_formulation.rl_paradigm": FieldResult(value="SARL", status="explicit"),
            "rl_formulation.algorithm": FieldResult(value="DQN", status="explicit"),
            "decision_model.state": FieldResult(
                value={"positions": ["p1"], "delays": ["d1"]}, status="inferred"
            ),
            "decision_model.reward": FieldResult(
                value={"wait_time": -1.0}, status="explicit"
            ),
            "baselines.baselines": FieldResult(
                value=["no control", "schedule based"], status="explicit"
            ),
            "control_constraints.holding_upper_bound": FieldResult(
                value=None, status="not_found"
            ),
            "results.main_results": FieldResult(value=None, status="not_found"),
            "experimental_setup.simulator": FieldResult(
                value="SUMO",
                status="explicit",
                evidence=[_good_ref("blk_sim", "simulations in SUMO")],
            ),
            "rl_formulation.agent_definition": FieldResult(
                value="each bus",
                status="explicit",
                evidence=[
                    EvidenceRef(block_id="blk_q1", char_start=0, char_end=4, quote=""),
                    EvidenceRef(block_id="blk_q2", char_start=0, char_end=4, quote=""),
                    EvidenceRef(block_id="blk_q3", char_start=0, char_end=4, quote=""),
                ],
            ),
            "research_problem.line_scope": FieldResult(
                value="Line 5", status="explicit"
            ),
            "metrics.metrics": FieldResult(
                value=["waiting time", "headway regularity"], status="explicit"
            ),
        },
    )


def _clean_instance() -> SchemaInstance:
    return SchemaInstance(
        paper_id="seed-clean-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_type": FieldResult(
                value="holding",
                status="explicit",
                evidence=[_good_ref("blk_ctrl", "holding control applied")],
            ),
            "research_problem.control_objective": FieldResult(
                value="minimize passenger waiting time", status="explicit"
            ),
            "rl_formulation.rl_paradigm": FieldResult(
                value="MARL",
                status="explicit",
                evidence=[_good_ref("blk_para", "multi-agent reinforcement learning")],
            ),
        },
    )


def _write_run(
    storage_root: Path,
    instance: SchemaInstance,
    *,
    run_id: str = "run_001",
) -> None:
    storage = SchemaRunStorage(storage_root=storage_root)
    paper_id = instance.paper_id
    manifest = ExtractionManifest(
        run_id=run_id,
        paper_id=paper_id,
        schema_id=instance.schema_id,
        schema_version=instance.schema_version,
        schema_hash=SCHEMA_HASH,
        llm_provider="fake",
        llm_model="fake-v0",
        llm_fake=True,
        created_at=CREATED_AT,
    )
    report = ValidationReport(
        paper_id=paper_id,
        schema_id=instance.schema_id,
        schema_version=instance.schema_version,
        schema_hash=SCHEMA_HASH,
        status="passed",
        created_at=CREATED_AT,
    )
    run_manifest = RunManifest(
        run_id=run_id,
        paper_id=paper_id,
        schema_id=instance.schema_id,
        schema_version=instance.schema_version,
        schema_hash=SCHEMA_HASH,
        llm_provider="fake",
        llm_model="fake-v0",
        llm_fake=True,
        prompt_version=PROMPT_VERSION,
        extraction_config_hash=compute_extraction_config_hash(PROMPT_VERSION, 8),
        created_at=CREATED_AT,
        status="passed",
        run_reason="extract",
    )
    storage.write_run(paper_id, run_id, instance, manifest, report, run_manifest)
    storage.write_current(
        paper_id,
        CurrentPointer(
            paper_id=paper_id,
            schema_id=instance.schema_id,
            run_id=run_id,
            schema_version=instance.schema_version,
            schema_hash=SCHEMA_HASH,
            created_at=CREATED_AT,
            status="passed",
        ),
    )


def _report_for(gold_path: Path, storage_root: Path, **kwargs):
    gold = load_schema_gold(gold_path)
    report = evaluate_schema_gold(gold, storage_root=storage_root, **kwargs)
    report.gold_path = str(gold_path)
    return report


# ---------------------------------------------------------------------------
# AC-E-59.1/2: files written, JSON round-trip, key coverage
# ---------------------------------------------------------------------------


def test_write_acceptance_report_writes_both_files(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    report = _report_for(SEED_GOLD_CLEAN, storage_root)
    out = tmp_path / "out"
    json_path, md_path = write_acceptance_report(report, out)
    assert json_path == out / "acceptance_report.json"
    assert md_path == out / "acceptance_summary.md"
    assert json_path.is_file()
    assert md_path.is_file()


def test_json_report_round_trips(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    out = tmp_path / "out"
    json_path, _ = write_acceptance_report(report, out)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    restored = AcceptanceReport.model_validate(data)
    assert restored.model_dump() == report.model_dump()


def test_json_report_contains_required_keys(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    out = tmp_path / "out"
    json_path, _ = write_acceptance_report(report, out)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert REQUIRED_JSON_KEYS <= set(data)
    assert data["report_schema_version"] == "1.0"
    assert data["freeze"]["declared_frozen"] is False
    # three metric layers (AC-E-34)
    assert set(data["metrics"]) >= {"overall", "per_paper", "per_field"}
    assert REQUIRED_METRIC_KEYS <= set(data["metrics"]["overall"])
    for paper in data["papers"]:
        assert set(paper) >= {"paper_id", "run_kind", "run_id"}
        for field in paper["fields"]:
            assert FIELD_RESULT_KEYS <= set(field)
    assert {"mode", "weak_traceability_rate", "strict_traceability_rate"} <= set(
        data["traceability"]
    )
    # generated_at is ISO 8601 UTC (AC-E-37)
    parsed = datetime.fromisoformat(data["generated_at"])
    assert parsed.utcoffset() is not None


def test_json_report_contains_no_secrets(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    out = tmp_path / "out"
    json_path, md_path = write_acceptance_report(report, out)
    for text in (
        json_path.read_text(encoding="utf-8"),
        md_path.read_text(encoding="utf-8"),
    ):
        assert not re.search(r"(?i)api[_-]?key|bearer|secret|token|password", text)


# ---------------------------------------------------------------------------
# AC-E-59.3/4: markdown sections and freeze wording
# ---------------------------------------------------------------------------


def test_markdown_contains_six_sections_and_freeze_wording(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    out = tmp_path / "out"
    _, md_path = write_acceptance_report(report, out)
    text = md_path.read_text(encoding="utf-8")
    for section in MARKDOWN_SECTIONS:
        assert section in text
    assert "declared_frozen: false" in text
    assert "冻结由用户/Planner 决定" in text
    assert "已冻结" not in text
    assert "freeze_suggestion: **insufficient_data**" in text
    # template notice (AC-E-51)
    assert "模板条目" in text
    assert "指标不代表真实论文质量" in text


def test_markdown_paper_table_marks_run_kind_and_run_id(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    out = tmp_path / "out"
    _, md_path = write_acceptance_report(report, out)
    text = md_path.read_text(encoding="utf-8")
    assert "seed-eval-001" in text
    assert "current" in text
    assert "run_001" in text
    assert "historical" in text or "in_memory" in text


# ---------------------------------------------------------------------------
# AC-E-59.4/5: freeze suggestion rules
# ---------------------------------------------------------------------------


def test_freeze_insufficient_data_with_template_entry(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    assert report.freeze.declared_frozen is False
    assert report.freeze.freeze_suggestion == "insufficient_data"
    assert report.freeze.message == FREEZE_MESSAGE
    template_papers = [p for p in report.papers if p.template]
    assert template_papers
    assert any(
        i.type == "template_entry_skipped" for i in report.issues
    )


def test_freeze_ready_with_clean_data(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    report = _report_for(SEED_GOLD_CLEAN, storage_root)
    assert report.freeze.freeze_suggestion == "ready"
    assert not any(i.severity == "error" for i in report.issues)
    assert report.metrics.overall.issue_count == 0
    assert report.metrics.overall.value_accuracy == 1.0


def test_freeze_not_ready_with_error_issue(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    gold = load_schema_gold(SEED_GOLD)
    # keep only the evaluated paper (no template, no paper_error) so the
    # error-level issues force "not_ready"
    gold.papers = [p for p in gold.papers if p.paper_id == "seed-eval-001"]
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    assert report.freeze.freeze_suggestion == "not_ready"
    assert any(i.severity == "error" for i in report.issues)


# ---------------------------------------------------------------------------
# AC-E-59.6: byte determinism
# ---------------------------------------------------------------------------


def test_report_deterministic_except_generated_at(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    first = _report_for(SEED_GOLD, storage_root)
    second = _report_for(SEED_GOLD, storage_root)
    first_dump = json.loads(first.model_dump_json())
    second_dump = json.loads(second.model_dump_json())
    assert first_dump.pop("generated_at") != second_dump.pop("generated_at")
    assert first_dump == second_dump


def test_report_files_byte_identical_for_same_report(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    report = _report_for(SEED_GOLD_CLEAN, storage_root)
    json_a, md_a = write_acceptance_report(report, tmp_path / "a")
    json_b, md_b = write_acceptance_report(report, tmp_path / "b")
    assert json_a.read_bytes() == json_b.read_bytes()
    assert md_a.read_bytes() == md_b.read_bytes()


# ---------------------------------------------------------------------------
# AC-E-33: invariants on the written report
# ---------------------------------------------------------------------------


def test_metrics_invariants_hold_on_report(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    for metrics in [report.metrics.overall, *report.metrics.per_paper.values()]:
        assert (
            metrics.value_correct_count
            + metrics.value_partial_count
            + metrics.value_incorrect_count
            + metrics.value_not_evaluated_count
            == metrics.field_count - metrics.missing_prediction_count
        )
        for rate in (
            metrics.value_accuracy,
            metrics.status_accuracy,
            metrics.evidence_present_rate,
            metrics.weak_traceability_rate,
            metrics.strict_traceability_rate,
            metrics.not_found_correctness,
        ):
            assert rate is None or 0.0 <= rate <= 1.0
    successful = [p for p in report.papers if p.paper_error is None]
    assert report.metrics.overall.paper_count >= len(successful)


def test_traceability_weak_only_without_reader(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    report = _report_for(SEED_GOLD, storage_root)
    assert report.traceability.mode == "weak_only"
    assert report.traceability.strict_traceability_rate is None
    assert report.traceability.weak_traceability_rate == pytest.approx(0.4)


def test_traceability_strict_with_reader(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    blocks = {
        "blk_ctrl": {
            "block_id": "blk_ctrl",
            "paper_id": "seed-eval-001",
            "text": "holding control applied",
            "pages": [2],
            "section_path": ["Method"],
        },
        "blk_sim": {
            "block_id": "blk_sim",
            "paper_id": "seed-eval-001",
            "text": "simulations in SUMO",
            "pages": [2],
            "section_path": ["Method"],
        },
    }
    gold = load_schema_gold(SEED_GOLD)
    gold.papers = [p for p in gold.papers if p.paper_id == "seed-eval-001"]
    report = evaluate_schema_gold(
        gold,
        storage_root=storage_root,
        canonical_reader=lambda paper_id, block_ids: dict(blocks),
    )
    assert report.traceability.mode == "strict"
    assert report.traceability.strict_traceability_rate == pytest.approx(0.4)


def test_traceability_strict_failed_with_raising_reader(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    report = _report_for(
        SEED_GOLD_CLEAN,
        storage_root,
        canonical_reader=lambda paper_id, block_ids: (_ for _ in ()).throw(
            RuntimeError("canonical down")
        ),
    )
    assert report.traceability.mode == "strict_failed"
    assert report.traceability.strict_traceability_rate is None
    assert any(
        i.type == "canonical_read_failed" for i in report.issues
    )


def test_write_to_missing_directory_creates_it(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    report = _report_for(SEED_GOLD_CLEAN, storage_root)
    nested = tmp_path / "a" / "b" / "out"
    json_path, md_path = write_acceptance_report(report, nested)
    assert json_path.is_file() and md_path.is_file()


# ---------------------------------------------------------------------------
# FR-006 (task-2026-08-15-001): draft gold in reports and freeze rules
# ---------------------------------------------------------------------------


def test_freeze_insufficient_data_with_draft_entries(tmp_path):
    """A benchmark containing draft papers must yield
    freeze_suggestion == insufficient_data with declared_frozen False, even
    when no storage runs exist (draft papers are skipped, not errors)."""
    report = _report_for(DRAFT_GOLD, tmp_path / "empty_storage")
    assert report.freeze.declared_frozen is False
    assert report.freeze.freeze_suggestion == "insufficient_data"
    assert report.metrics.overall.evaluated_field_count == 0
    assert report.metrics.overall.value_accuracy is None
    assert report.metrics.overall.value_correct_count == 0
    assert all(p.draft for p in report.papers)
    assert all(not p.template for p in report.papers)
    assert any(i.type == "draft_entry_skipped" for i in report.issues)


def test_markdown_surfaces_draft_and_never_claims_frozen(tmp_path):
    report = _report_for(DRAFT_GOLD, tmp_path / "empty_storage")
    out = tmp_path / "out"
    _, md_path = write_acceptance_report(report, out)
    text = md_path.read_text(encoding="utf-8")
    assert "draft" in text
    assert "(draft)" in text
    assert "非人工 gold" in text
    assert "已冻结" not in text
    assert "freeze_suggestion: **insufficient_data**" in text


def test_json_report_surfaces_draft_flag(tmp_path):
    report = _report_for(DRAFT_GOLD, tmp_path / "empty_storage")
    out = tmp_path / "out"
    json_path, _ = write_acceptance_report(report, out)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["freeze"]["declared_frozen"] is False
    assert data["freeze"]["freeze_suggestion"] == "insufficient_data"
    assert data["metrics"]["overall"]["evaluated_field_count"] == 0
    assert data["metrics"]["overall"]["value_accuracy"] is None
    assert all(p["draft"] for p in data["papers"])


# ---------------------------------------------------------------------------
# FR-005 / AC-T001-F21..F24: freeze classification, blockers, gold review
# ---------------------------------------------------------------------------


def _single_paper_gold(
    fields,
    paper_id: str = "seed-clean-001",
    selected_for_v1: bool = True,
) -> GoldBenchmark:
    return GoldBenchmark.model_validate(
        {
            "papers": [
                {
                    "paper_id": paper_id,
                    "title": "t",
                    "pdf_relative_path": "tests/fixtures/l2s2_schema_acceptance/seed_paper.pdf",
                    "schema_id": "bus_control_rl",
                    "schema_version": "1.0",
                    "selected_for_v1": selected_for_v1,
                    "gold_status": "evaluated",
                    "notes": "",
                    "fields": fields,
                }
            ]
        }
    )


def _single_field_gold(
    field_id,
    *,
    expected_status="explicit",
    acceptable=None,
    judgement="correct",
    expectation="optional",
) -> GoldBenchmark:
    return _single_paper_gold(
        [
            {
                "field_id": field_id,
                "expected_status": expected_status,
                "acceptable_values": acceptable,
                "value_judgement": judgement,
                "evidence_expectation": expectation,
                "notes": "n",
            }
        ]
    )


def test_freeze_ready_with_only_human_correct_exact_string_mismatch(tmp_path):
    """AC-T001-F18/F21: value_judgement=correct with only an exact-string
    mismatch never blocks freeze (the mismatch is a warning diagnostic)."""
    storage_root = tmp_path / "storage"
    instance = SchemaInstance(
        paper_id="seed-clean-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_type": FieldResult(
                value="holding control applied",
                status="explicit",
                evidence=[_good_ref("blk_ctrl", "holding control applied")],
            ),
        },
    )
    _write_run(storage_root, instance)
    gold = _single_field_gold(
        "research_problem.control_type", acceptable=["holding"], expectation="required"
    )
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    assert report.freeze.freeze_suggestion == "ready"
    assert report.freeze.declared_frozen is False
    assert any(i.type == "value_mismatch" for i in report.issues)
    assert all(
        i.type != "value_mismatch" or i.severity == "warning" for i in report.issues
    )
    assert report.freeze.blocking_error_count == 0
    assert report.freeze.exact_match_diagnostic_count >= 1
    assert report.freeze.remaining_freezing_blockers == []


def test_freeze_not_ready_caused_by_gold_review(tmp_path):
    """AC-T001-S6/F21/F22: gold-status-review candidates force not_ready; the
    blocker list excludes them (reported separately) and is empty here."""
    storage_root = tmp_path / "storage"
    instance = SchemaInstance(
        paper_id="seed-clean-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "results.main_results": FieldResult(value=None, status="not_found"),
        },
    )
    _write_run(storage_root, instance)
    gold = _single_field_gold(
        "results.main_results",
        expected_status="not_found",
        acceptable=["result A"],
        expectation="not_required",
    )
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    assert report.freeze.freeze_suggestion == "not_ready"
    assert report.freeze.gold_review_count == 1
    assert len(report.gold_status_review) == 1
    assert report.gold_status_review[0].field_id == "results.main_results"
    assert report.freeze.remaining_freezing_blockers == []
    assert report.freeze.blocking_error_count == 0


def test_freeze_not_ready_with_real_blocking_error(tmp_path):
    """AC-T001-F21/F22: real blocking errors list remaining_freezing_blockers."""
    storage_root = tmp_path / "storage"
    instance = SchemaInstance(
        paper_id="seed-clean-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.algorithm": FieldResult(value=None, status="not_found"),
        },
    )
    _write_run(storage_root, instance)
    gold = _single_field_gold(
        "rl_formulation.algorithm", acceptable=["PPO"], expectation="required"
    )
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    assert report.freeze.freeze_suggestion == "not_ready"
    assert report.freeze.gold_review_count == 0
    assert report.freeze.blocking_error_count >= 1
    blockers = report.freeze.remaining_freezing_blockers
    assert any(b["type"] == "status_mismatch" for b in blockers)
    assert not any(
        b["type"] in ("value_mismatch", "judgement_conflict") for b in blockers
    )
    assert not any(b["type"] == "gold_status_review" for b in blockers)


def test_gold_status_review_json_artifact_written_when_nonempty(tmp_path):
    """AC-T001-S6/F23: the acceptance run writes gold_status_review.json when
    non-empty."""
    storage_root = tmp_path / "storage"
    instance = SchemaInstance(
        paper_id="seed-clean-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "results.main_results": FieldResult(value=None, status="not_found"),
        },
    )
    _write_run(storage_root, instance)
    gold = _single_field_gold(
        "results.main_results",
        expected_status="not_found",
        acceptable=["result A"],
        expectation="not_required",
    )
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    out = tmp_path / "out"
    write_acceptance_report(report, out)
    review_path = out / "gold_status_review.json"
    assert review_path.is_file()
    data = json.loads(review_path.read_text(encoding="utf-8"))
    assert data[0]["field_id"] == "results.main_results"
    assert data[0]["expected_status"] == "not_found"


def test_json_report_surfaces_freeze_breakdown_and_gold_review(tmp_path):
    storage_root = tmp_path / "storage"
    instance = SchemaInstance(
        paper_id="seed-clean-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "results.main_results": FieldResult(value=None, status="not_found"),
        },
    )
    _write_run(storage_root, instance)
    gold = _single_field_gold(
        "results.main_results",
        expected_status="not_found",
        acceptable=["result A"],
        expectation="not_required",
    )
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    out = tmp_path / "out"
    json_path, _ = write_acceptance_report(report, out)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    freeze = data["freeze"]
    assert freeze["blocking_error_count"] == 0
    assert freeze["gold_review_count"] == 1
    assert freeze["remaining_freezing_blockers"] == []
    assert "gold_status_review" in data
    assert data["gold_status_review"][0]["field_id"] == "results.main_results"
    assert freeze["declared_frozen"] is False
