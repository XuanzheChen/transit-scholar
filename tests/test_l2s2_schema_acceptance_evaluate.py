"""L2S2 Package E deterministic tests: evaluation engine (AC-E-57, AC-E-58)
and the AC-E-33 metric invariants.

Fully offline: every schema instance is built in memory and every storage
root points at ``tmp_path``. No network, no LLM, no PDF, no ``data/**`` IO.
"""

from __future__ import annotations

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
    ValidationIssue,
    ValidationReport,
    compute_extraction_config_hash,
)
from transit_scholar.layer2.schema_acceptance import (
    AcceptanceReport,
    FieldEvaluation,
    GoldBenchmark,
    GoldField,
    GoldPaper,
    PaperEvaluation,
    evaluate_schema_gold,
    evaluate_schema_instance,
    load_schema_gold,
)
from transit_scholar.layer2.schema_acceptance.metrics import compute_paper_metrics

FIXTURES = Path(__file__).parent / "fixtures" / "l2s2_schema_acceptance"
SEED_GOLD = FIXTURES / "seed_gold.json"

CREATED_AT = "2026-08-14T00:00:00+00:00"
SCHEMA_HASH = "0" * 64


# ---------------------------------------------------------------------------
# gold / instance helpers
# ---------------------------------------------------------------------------


def _seed_gold() -> GoldBenchmark:
    return load_schema_gold(SEED_GOLD)


def _entry(index: int = 0, **overrides) -> GoldPaper:
    entry = _seed_gold().papers[index].model_copy(deep=True)
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def _entry_with_fields(fields: list[GoldField], **overrides) -> GoldPaper:
    base = {
        "paper_id": "paper-x",
        "title": "paper x",
        "pdf_relative_path": "data/stage7_acceptance/real_papers/example.pdf",
        "schema_id": "bus_control_rl",
        "schema_version": "1.0",
        "selected_for_v1": True,
        "notes": "",
        "fields": fields,
    }
    base.update(overrides)
    return GoldPaper.model_validate(base)


def _gold_field(field_id: str, *, expected_status="explicit", acceptable=None,
                judgement="correct", expectation="optional", **overrides) -> GoldField:
    data = {
        "field_id": field_id,
        "expected_status": expected_status,
        "acceptable_values": acceptable,
        "value_judgement": judgement,
        "evidence_expectation": expectation,
        "notes": "n",
    }
    data.update(overrides)
    return GoldField.model_validate(data)


def _good_ref(block_id="blk_1", quote="real paper text") -> EvidenceRef:
    return EvidenceRef(
        block_id=block_id,
        char_start=0,
        char_end=len(quote),
        pages=[2],
        section_path=["Method"],
        quote=quote,
    )


def _seed_instance() -> SchemaInstance:
    """In-memory prediction matching seed-eval-001 (may contain model-level
    invalid refs on purpose: weak traceability checks run on raw values)."""
    ref_empty_block = EvidenceRef.model_construct(
        block_id="", char_start=0, char_end=4, pages=[], section_path=[], quote="q"
    )
    ref_bad_range = EvidenceRef.model_construct(
        block_id="blk_b", char_start=5, char_end=2, pages=[], section_path=[], quote="q"
    )
    ref_empty_quote = EvidenceRef.model_construct(
        block_id="blk_c", char_start=0, char_end=4, pages=[], section_path=[], quote=""
    )
    agent_field = FieldResult.model_construct(
        value="each bus",
        status="explicit",
        evidence=[ref_empty_block, ref_bad_range, ref_empty_quote],
    )
    return SchemaInstance.model_construct(
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
            "rl_formulation.agent_definition": agent_field,
            "research_problem.line_scope": FieldResult(
                value="Line 5", status="explicit"
            ),
            "metrics.metrics": FieldResult(
                value=["waiting time", "headway regularity"], status="explicit"
            ),
            # research_problem.network_scope intentionally absent
        },
    )


def _seed_instance_stored() -> SchemaInstance:
    """Same predictions but with model-valid refs (storage round-trip safe);
    the three agent_definition refs fail weak checks via empty quotes."""
    instance = _seed_instance()
    agent_field = instance.fields["rl_formulation.agent_definition"]
    agent_field.evidence = [
        EvidenceRef(block_id="blk_q1", char_start=0, char_end=4, quote=""),
        EvidenceRef(block_id="blk_q2", char_start=0, char_end=4, quote=""),
        EvidenceRef(block_id="blk_q3", char_start=0, char_end=4, quote=""),
    ]
    return instance


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
    report: ValidationReport | None = None,
    make_current: bool = True,
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
    rep = report or ValidationReport(
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
        status=rep.status,
        run_reason="extract",
    )
    storage.write_run(paper_id, run_id, instance, manifest, rep, run_manifest)
    if make_current:
        storage.write_current(
            paper_id,
            CurrentPointer(
                paper_id=paper_id,
                schema_id=instance.schema_id,
                run_id=run_id,
                schema_version=instance.schema_version,
                schema_hash=SCHEMA_HASH,
                created_at=CREATED_AT,
                status=rep.status,
            ),
        )


def _field_issues(field_eval: FieldEvaluation) -> list[str]:
    return [issue.type for issue in field_eval.issues]


# ---------------------------------------------------------------------------
# AC-E-57.1: simple fields, automatic value_match
# ---------------------------------------------------------------------------


def test_simple_enum_value_match_true():
    entry = _entry_with_fields(
        [_gold_field("research_problem.control_type", acceptable=["holding"],
                     judgement="correct", expectation="required")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_type": FieldResult(
                value="holding", status="explicit", evidence=[_good_ref()]
            )
        },
    )
    result = evaluate_schema_instance(instance, entry)
    field = result.fields[0]
    assert field.value_match is True
    assert field.effective_judgement == "correct"
    assert field.status_match is True
    assert field.evidence_present is True
    assert field.evidence_support is True
    assert field.issues == []


def test_simple_string_value_match_true_and_false():
    entry = _entry_with_fields(
        [
            _gold_field("research_problem.control_objective",
                        acceptable="minimize passenger waiting time"),
            _gold_field("rl_formulation.algorithm", acceptable=["PPO"]),
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_objective": FieldResult(
                value="minimize passenger waiting time", status="explicit"
            ),
            "rl_formulation.algorithm": FieldResult(value="DQN", status="explicit"),
        },
    )
    result = evaluate_schema_instance(instance, entry)
    assert result.fields[0].value_match is True
    assert _field_issues(result.fields[0]) == []
    assert result.fields[1].value_match is False
    assert "value_mismatch" in _field_issues(result.fields[1])
    mismatch = next(i for i in result.fields[1].issues if i.type == "value_mismatch")
    # AC-T001-F18: exact-string mismatch is a diagnostic (warning), never a
    # freeze-blocking error
    assert mismatch.severity == "warning"
    assert mismatch.fields == ["rl_formulation.algorithm"]


def test_scalar_array_membership_match():
    entry = _entry_with_fields(
        [_gold_field("research_problem.network_scope", acceptable=["single line", "multi-line corridor"])]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.network_scope": FieldResult(
                value="multi-line corridor", status="explicit"
            )
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.value_match is True


# ---------------------------------------------------------------------------
# AC-E-57.2: complex fields walk on gold judgement only
# ---------------------------------------------------------------------------


def test_complex_fields_value_match_null_and_judgement_buckets():
    entry = _entry_with_fields(
        [
            _gold_field("decision_model.state", acceptable=None,
                        judgement="partially_correct"),
            _gold_field("baselines.baselines", acceptable=None,
                        judgement="incorrect"),
            _gold_field("metrics.metrics", acceptable=None,
                        judgement="not_evaluated"),
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "decision_model.state": FieldResult(
                value={"positions": []}, status="explicit"
            ),
            "baselines.baselines": FieldResult(value=[], status="explicit"),
            "metrics.metrics": FieldResult(value=["x"], status="explicit"),
        },
    )
    result = evaluate_schema_instance(instance, entry)
    assert [f.value_match for f in result.fields] == [None, None, None]
    assert [f.effective_judgement for f in result.fields] == [
        "partially_correct",
        "incorrect",
        "not_evaluated",
    ]
    metrics = compute_paper_metrics(result)
    assert metrics.value_partial_count == 1
    assert metrics.value_incorrect_count == 1
    assert metrics.value_not_evaluated_count == 1
    assert metrics.value_correct_count == 0
    assert metrics.evaluated_field_count == 2
    assert metrics.value_accuracy == 0.0


def test_judgement_conflict_warning_recorded():
    entry = _entry_with_fields(
        [_gold_field("rl_formulation.algorithm", acceptable=["PPO"], judgement="correct")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={"rl_formulation.algorithm": FieldResult(value="DQN", status="explicit")},
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    issues = {i.type: i for i in field.issues}
    assert "value_mismatch" in issues
    assert "judgement_conflict" in issues
    assert issues["judgement_conflict"].severity == "warning"
    assert field.effective_judgement == "correct"


# ---------------------------------------------------------------------------
# AC-E-57.3: missing prediction
# ---------------------------------------------------------------------------


def test_missing_prediction_issue_and_exclusion_from_buckets():
    entry = _entry_with_fields(
        [_gold_field("research_problem.network_scope", acceptable=["single line"])]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={},
    )
    result = evaluate_schema_instance(instance, entry)
    field = result.fields[0]
    assert field.prediction_missing is True
    assert field.predicted_status is None
    assert field.effective_judgement is None
    assert "missing_prediction" in _field_issues(field)
    assert next(i for i in field.issues if i.type == "missing_prediction").severity == "error"
    metrics = compute_paper_metrics(result)
    assert metrics.field_count == 1
    assert metrics.missing_prediction_count == 1
    assert metrics.value_not_evaluated_count == 0
    assert metrics.evaluated_field_count == 0
    assert metrics.value_accuracy is None
    assert metrics.status_accuracy is None


# ---------------------------------------------------------------------------
# AC-E-57.4: status mismatch
# ---------------------------------------------------------------------------


def test_status_mismatch_issue():
    entry = _entry_with_fields(
        [_gold_field("rl_formulation.algorithm", acceptable=["PPO"],
                     expected_status="explicit")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={"rl_formulation.algorithm": FieldResult(value="PPO", status="inferred")},
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.status_match is False
    assert "status_mismatch" in _field_issues(field)
    # AC-T001-S3: explicit<->inferred swap is an explainable non-blocking
    # warning (both assertive), carrying a machine-readable explanation
    issue = next(i for i in field.issues if i.type == "status_mismatch")
    assert issue.severity == "warning"
    assert issue.explanation
    assert "explicit" in issue.message


# ---------------------------------------------------------------------------
# AC-E-57.5: evidence structure
# ---------------------------------------------------------------------------


def test_evidence_required_missing_reports_evidence_missing():
    entry = _entry_with_fields(
        [_gold_field("rl_formulation.algorithm", acceptable=["PPO"],
                     expectation="required")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={"rl_formulation.algorithm": FieldResult(value="PPO", status="explicit")},
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.evidence_present is False
    assert field.evidence_support is False
    assert "evidence_missing" in _field_issues(field)


def test_evidence_optional_and_not_required_do_not_report():
    entry = _entry_with_fields(
        [
            _gold_field("rl_formulation.algorithm", acceptable=["PPO"],
                        expectation="optional"),
            _gold_field("research_problem.control_objective",
                        acceptable="x", expectation="not_required"),
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.algorithm": FieldResult(value="PPO", status="explicit"),
            "research_problem.control_objective": FieldResult(
                value="x",
                status="explicit",
                evidence=[_good_ref("blk_extra")],
            ),
        },
    )
    result = evaluate_schema_instance(instance, entry)
    assert "evidence_missing" not in _field_issues(result.fields[0])
    assert result.fields[0].evidence_support is None
    assert result.fields[1].evidence_present is True
    assert result.fields[1].evidence_support is None
    assert _field_issues(result.fields[1]) == []


# ---------------------------------------------------------------------------
# AC-E-57.6/7: weak traceability
# ---------------------------------------------------------------------------


def test_weak_traceability_bad_refs_reported_individually():
    entry = _entry_with_fields(
        [_gold_field("rl_formulation.agent_definition", acceptable=["each bus"],
                     expectation="required")]
    )
    refs = [
        EvidenceRef.model_construct(
            block_id="", char_start=0, char_end=4, pages=[], section_path=[], quote="q"
        ),
        EvidenceRef.model_construct(
            block_id="blk_b", char_start=5, char_end=2, pages=[], section_path=[], quote="q"
        ),
        EvidenceRef.model_construct(
            block_id="blk_c", char_start=0, char_end=4, pages=[], section_path=[], quote=""
        ),
    ]
    instance = SchemaInstance.model_construct(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.agent_definition": FieldResult.model_construct(
                value="each bus", status="explicit", evidence=refs
            )
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.weak_ok_refs == 0
    assert field.weak_fail_refs == 3
    invalid = [i for i in field.issues if i.type == "evidence_ref_invalid"]
    assert len(invalid) == 3
    assert all(i.severity == "error" for i in invalid)
    assert "block_id" in invalid[0].message
    assert "char range" in invalid[1].message
    assert "quote" in invalid[2].message


def test_weak_traceability_good_ref_passes():
    entry = _entry_with_fields(
        [_gold_field("research_problem.control_type", acceptable=["holding"])]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_type": FieldResult(
                value="holding",
                status="explicit",
                evidence=[_good_ref("blk_ok")],
            )
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.weak_ok_refs == 1
    assert field.weak_fail_refs == 0
    assert "evidence_ref_invalid" not in _field_issues(field)


def test_weak_only_mode_without_canonical_reader():
    entry = _entry_with_fields(
        [_gold_field("research_problem.control_type", acceptable=["holding"])]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_type": FieldResult(
                value="holding", status="explicit", evidence=[_good_ref()]
            )
        },
    )
    result = evaluate_schema_instance(instance, entry, canonical_reader=None)
    field = result.fields[0]
    assert field.strict_mode == "weak_only"
    assert field.strict_ok_refs is None
    assert field.strict_fail_refs is None
    all_issues = result.issues + field.issues
    assert not any(i.type == "canonical_read_failed" for i in all_issues)


# ---------------------------------------------------------------------------
# AC-E-57.8/9: strict traceability
# ---------------------------------------------------------------------------


def _strict_scenario():
    entry = _entry_with_fields(
        [_gold_field("research_problem.control_type", acceptable=["holding"],
                     expectation="required")],
        paper_id="strict-001",
    )
    instance = SchemaInstance(
        paper_id="strict-001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.control_type": FieldResult(
                value="holding",
                status="explicit",
                evidence=[
                    EvidenceRef(
                        block_id="blk_ok",
                        char_start=0,
                        char_end=9,
                        pages=[1],
                        section_path=["Method"],
                        quote="the quick",
                    ),
                    EvidenceRef(
                        block_id="blk_gone",
                        char_start=0,
                        char_end=4,
                        pages=[],
                        section_path=[],
                        quote="gone",
                    ),
                ],
            )
        },
    )
    blocks = {
        "blk_ok": {
            "block_id": "blk_ok",
            "paper_id": "strict-001",
            "text": "the quick brown fox",
            "pages": [1],
            "section_path": ["Method"],
        }
    }
    return entry, instance, blocks


def test_strict_mode_with_fake_reader():
    entry, instance, blocks = _strict_scenario()
    reader = lambda paper_id, block_ids: dict(blocks)  # noqa: E731
    result = evaluate_schema_instance(instance, entry, canonical_reader=reader)
    field = result.fields[0]
    assert field.strict_mode == "strict"
    assert field.strict_ok_refs == 1
    assert field.strict_fail_refs == 1
    assert field.weak_ok_refs == 2
    assert field.weak_fail_refs == 0
    integrity = [i for i in field.issues if i.source == "evidence_integrity"]
    assert any(i.type == "evidence_block_missing" for i in integrity)
    assert all(i.severity == "error" for i in integrity)


def test_strict_failed_when_reader_raises():
    entry, instance, _ = _strict_scenario()

    def bad_reader(paper_id, block_ids):
        raise RuntimeError("canonical down")

    result = evaluate_schema_instance(instance, entry, canonical_reader=bad_reader)
    assert result.fields[0].strict_mode == "strict_failed"
    assert result.fields[0].strict_ok_refs is None
    paper_issues = {i.type: i for i in result.issues}
    assert "canonical_read_failed" in paper_issues
    assert paper_issues["canonical_read_failed"].severity == "error"
    assert paper_issues["canonical_read_failed"].source == "evidence_integrity"


# ---------------------------------------------------------------------------
# AC-E-57.10: absent-class correctness
# ---------------------------------------------------------------------------


def test_absent_same_status_empty_value_is_correct():
    entry = _entry_with_fields(
        [_gold_field("control_constraints.holding_upper_bound",
                     expected_status="not_found", acceptable=None,
                     expectation="not_required")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "control_constraints.holding_upper_bound": FieldResult(
                value=None, status="not_found"
            )
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.absent_correct is True
    assert field.status_match is True
    assert _field_issues(field) == []


def test_absent_same_status_nonempty_value_hallucinated():
    entry = _entry_with_fields(
        [_gold_field("control_constraints.holding_upper_bound",
                     expected_status="not_found", acceptable=None,
                     judgement="correct", expectation="not_required")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "control_constraints.holding_upper_bound": FieldResult(
                value="30 seconds", status="not_found"
            )
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.absent_correct is False
    assert "hallucinated_value_for_absent_field" in _field_issues(field)
    issue = next(
        i for i in field.issues if i.type == "hallucinated_value_for_absent_field"
    )
    assert issue.severity == "error"


def test_absent_not_found_vs_not_applicable_confused():
    entry = _entry_with_fields(
        [_gold_field("results.main_results",
                     expected_status="not_applicable", acceptable=None,
                     judgement="correct", expectation="not_required")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={"results.main_results": FieldResult(value=None, status="not_found")},
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.absent_correct is False
    assert "not_found_not_applicable_confused" in _field_issues(field)
    issue = next(
        i for i in field.issues if i.type == "not_found_not_applicable_confused"
    )
    assert issue.severity == "error"


def test_absent_vs_nonabsent_status_mismatch_type():
    entry = _entry_with_fields(
        [_gold_field("results.main_results",
                     expected_status="not_found", acceptable=None,
                     judgement="correct", expectation="not_required")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "results.main_results": FieldResult(
                value=["result"], status="explicit"
            )
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert "not_found_status_mismatch" in _field_issues(field)
    assert "not_found_not_applicable_confused" not in _field_issues(field)


def test_absent_missing_prediction_only_counts_missing():
    entry = _entry_with_fields(
        [_gold_field("control_constraints.holding_upper_bound",
                     expected_status="not_found", acceptable=None,
                     expectation="not_required")]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={},
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.prediction_missing is True
    assert _field_issues(field) == ["missing_prediction"]
    metrics = compute_paper_metrics(
        evaluate_schema_instance(instance, entry)
    )
    assert metrics.missing_prediction_count == 1
    assert metrics.not_found_correctness == 0.0


# ---------------------------------------------------------------------------
# AC-E-57.11: validation_report merging (no LLM / verifier calls)
# ---------------------------------------------------------------------------


def test_validation_report_issues_merged_with_source():
    entry = _entry_with_fields(
        [_gold_field("rl_formulation.algorithm", acceptable=["PPO"])]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={"rl_formulation.algorithm": FieldResult(value="PPO", status="explicit")},
    )
    report = ValidationReport(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        status="failed",
        issues=[
            ValidationIssue(
                type="semantic_unsupported",
                severity="error",
                message="semantic verifier could not support the claim",
                fields=["rl_formulation.algorithm"],
            )
        ],
    )
    result = evaluate_schema_instance(instance, entry, validation_report=report)
    merged = [i for i in result.issues if i.source == "validation_report"]
    assert len(merged) == 1
    assert merged[0].type == "semantic_unsupported"
    assert merged[0].fields == ["rl_formulation.algorithm"]
    # the field itself keeps its own evaluation issues only
    assert _field_issues(result.fields[0]) == []


# ---------------------------------------------------------------------------
# AC-E-57.12: schema mismatch paper error
# ---------------------------------------------------------------------------


def test_schema_id_mismatch_produces_paper_error():
    entry = _entry_with_fields(
        [_gold_field("rl_formulation.algorithm", acceptable=["PPO"])],
        schema_id="generic_research_paper",
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={"rl_formulation.algorithm": FieldResult(value="PPO", status="explicit")},
    )
    result = evaluate_schema_instance(instance, entry)
    assert result.paper_error == "schema_mismatch"
    assert result.fields == []
    assert any(i.type == "schema_mismatch" and i.severity == "error"
               for i in result.issues)


def test_paper_id_mismatch_produces_paper_error():
    entry = _entry_with_fields(
        [_gold_field("rl_formulation.algorithm", acceptable=["PPO"])],
        paper_id="paper-x",
    )
    instance = SchemaInstance(
        paper_id="paper-y",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={"rl_formulation.algorithm": FieldResult(value="PPO", status="explicit")},
    )
    result = evaluate_schema_instance(instance, entry)
    assert result.paper_error == "schema_mismatch"


# ---------------------------------------------------------------------------
# AC-E-51: template entry skipping
# ---------------------------------------------------------------------------


def test_template_entry_skipped_with_warning():
    entry = _entry_with_fields(
        [
            _gold_field("rl_formulation.algorithm", expected_status=None,
                        acceptable=None, judgement="not_evaluated")
        ],
        gold_status="template",
    )
    result = evaluate_schema_instance(SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={},
    ), entry)
    assert result.template is True
    assert result.paper_error is None
    assert result.fields == []
    assert any(i.type == "template_entry_skipped" and i.severity == "warning"
               for i in result.issues)


# ---------------------------------------------------------------------------
# AC-E-58: input paths (direct object / storage current / storage historical)
# ---------------------------------------------------------------------------


def test_evaluate_schema_instance_direct_input():
    entry = _entry(0)
    result = evaluate_schema_instance(_seed_instance(), entry)
    assert isinstance(result, PaperEvaluation)
    assert result.run_kind == "in_memory"
    assert result.run_id is None
    assert len(result.fields) == 14


def test_evaluate_schema_gold_current_run(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    gold = GoldBenchmark.model_validate({"papers": [_entry(0)]})
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    paper = report.papers[0]
    assert paper.run_kind == "current"
    assert paper.run_id == "run_001"
    assert paper.paper_error is None
    assert len(paper.fields) == 14
    assert report.metrics.overall.paper_count == 1


def test_evaluate_schema_gold_historical_run(tmp_path):
    storage_root = tmp_path / "storage"
    old_instance = _seed_instance_stored().model_copy(deep=True)
    old_instance.fields["research_problem.control_type"] = FieldResult(
        value="scheduling",
        status="explicit",
        evidence=[_good_ref("blk_ctrl", "holding control applied")],
    )
    new_instance = _seed_instance_stored()
    _write_run(storage_root, old_instance, run_id="run_old", make_current=False)
    _write_run(storage_root, new_instance, run_id="run_new", make_current=True)
    gold = GoldBenchmark.model_validate({"papers": [_entry(0)]})

    historical = evaluate_schema_gold(
        gold, storage_root=storage_root, run_id="run_old"
    )
    paper = historical.papers[0]
    assert paper.run_kind == "historical"
    assert paper.run_id == "run_old"
    field_map = {f.field_id: f for f in paper.fields}
    # the historical run carries value "scheduling" -> mismatch vs gold
    assert field_map["research_problem.control_type"].value_match is False

    current = evaluate_schema_gold(gold, storage_root=storage_root)
    current_paper = current.papers[0]
    assert current_paper.run_kind == "current"
    assert current_paper.run_id == "run_new"
    current_map = {f.field_id: f for f in current_paper.fields}
    assert current_map["research_problem.control_type"].value_match is True


def test_evaluate_schema_gold_storage_failure_paper_error(tmp_path):
    storage_root = tmp_path / "storage"
    gold = GoldBenchmark.model_validate({"papers": [_entry(0)]})
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    paper = report.papers[0]
    assert paper.paper_error == "schema_read_failed"
    assert paper.fields == []
    assert any(i.type == "schema_read_failed" and i.severity == "error"
               for i in report.issues)


def test_evaluate_schema_gold_merges_gold_validation_issues(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    bad_entry = _entry(0)
    bad_entry.fields.append(
        GoldField.model_validate(
            {
                "field_id": "no.such_field",
                "expected_status": "explicit",
                "acceptable_values": ["x"],
                "value_judgement": "correct",
                "evidence_expectation": "optional",
                "notes": "n",
            }
        )
    )
    gold = GoldBenchmark.model_validate({"papers": [bad_entry]})
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    gold_issues = [i for i in report.issues if i.source == "gold_validation"]
    assert any(i.type == "field_id_not_in_schema" for i in gold_issues)


def test_evaluate_schema_gold_with_only_template_entries(tmp_path):
    template_entry = _seed_gold().papers[1]
    gold = GoldBenchmark.model_validate({"papers": [template_entry]})
    report = evaluate_schema_gold(gold, storage_root=tmp_path / "storage")
    assert report.papers[0].template is True
    overall = report.metrics.overall
    assert overall.paper_count == 1
    assert overall.field_count == 0
    assert overall.evaluated_field_count == 0
    assert report.metrics.per_paper[template_entry.paper_id].field_count == 0


# ---------------------------------------------------------------------------
# AC-E-35/33: determinism and invariants
# ---------------------------------------------------------------------------


def _assert_invariant(metrics) -> None:
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


def test_evaluation_deterministic():
    entry = _entry(0)
    first = evaluate_schema_instance(_seed_instance(), entry)
    second = evaluate_schema_instance(_seed_instance(), entry)
    assert first.model_dump() == second.model_dump()


def test_seed_gold_full_evaluation_counts_and_invariant():
    result = evaluate_schema_instance(_seed_instance(), _entry(0))
    metrics = compute_paper_metrics(result)
    assert metrics.field_count == 14
    assert metrics.missing_prediction_count == 1
    assert metrics.value_not_evaluated_count == 2
    assert metrics.evaluated_field_count == 11
    assert metrics.value_correct_count == 8
    assert metrics.value_partial_count == 1
    assert metrics.value_incorrect_count == 2
    assert metrics.value_accuracy == round(8 / 11, 4)
    assert metrics.status_accuracy == round(11 / 13, 4)
    assert metrics.evidence_required_count == 5
    assert metrics.evidence_present_rate == round(3 / 5, 4)
    assert metrics.weak_traceability_rate == round(2 / 5, 4)
    assert metrics.strict_traceability_rate is None
    assert metrics.not_found_correctness == 0.5
    assert metrics.issue_count == 10
    _assert_invariant(metrics)


def test_seed_gold_full_report_metrics_and_invariant(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    gold = GoldBenchmark.model_validate({"papers": [_entry(0)]})
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    overall = report.metrics.overall
    assert overall.paper_count == 1
    assert overall.field_count == 14
    assert overall.missing_prediction_count == 1
    assert overall.value_correct_count == 8
    assert overall.issue_count == 10
    _assert_invariant(overall)
    for metrics in report.metrics.per_paper.values():
        _assert_invariant(metrics)
    successful = [p for p in report.papers if p.paper_error is None]
    assert overall.paper_count >= len(successful)


def test_overall_invariant_with_errors_and_templates(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    gold = _seed_gold()
    report = evaluate_schema_gold(gold, storage_root=storage_root)
    overall = report.metrics.overall
    assert overall.paper_count == 3
    assert overall.field_count == 14  # template + paper_error fields excluded
    _assert_invariant(overall)


# ---------------------------------------------------------------------------
# FR-005 / AC-T001-F17..F20: human judgement primary, exact-match diagnostic,
# status mismatch statistics retained
# ---------------------------------------------------------------------------


def test_value_mismatch_diagnostic_and_human_judgement_primary():
    """AC-T001-F17/F18: value_judgement=correct with exact string mismatch ->
    value_mismatch is a warning diagnostic; human judgement stays primary and
    the exact match result is still surfaced."""
    entry = _entry_with_fields(
        [
            _gold_field(
                "rl_formulation.algorithm",
                acceptable=["PPO"],
                judgement="correct",
            )
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.algorithm": FieldResult(value="DQN", status="explicit")
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.value_match is False
    assert field.effective_judgement == "correct"
    mismatch = next(i for i in field.issues if i.type == "value_mismatch")
    assert mismatch.severity == "warning"
    metrics = compute_paper_metrics(evaluate_schema_instance(instance, entry))
    # human judgement drives the correctness bucket, not the exact match
    assert metrics.value_correct_count == 1
    assert metrics.value_accuracy == 1.0
    assert field.status_explanation is None or field.gold_review is False


def test_judgement_conflict_inverse_retained_when_incorrect_exact_match_true():
    """AC-T001-F19: value_judgement=incorrect but exact match true keeps a
    judgement_conflict warning and the human judgement stays authoritative."""
    entry = _entry_with_fields(
        [
            _gold_field(
                "rl_formulation.algorithm",
                acceptable=["PPO"],
                judgement="incorrect",
            )
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.algorithm": FieldResult(value="PPO", status="explicit")
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.value_match is True
    assert field.effective_judgement == "incorrect"
    issues = {i.type: i for i in field.issues}
    assert "judgement_conflict" in issues
    assert issues["judgement_conflict"].severity == "warning"
    metrics = compute_paper_metrics(evaluate_schema_instance(instance, entry))
    assert metrics.value_incorrect_count == 1
    assert metrics.value_accuracy == 0.0


def test_status_mismatch_statistics_retained_never_silent():
    """AC-T001-F20 / required test 8: status mismatches are still counted in
    metrics and per-field aggregation even though they are now classified."""
    entry = _entry_with_fields(
        [
            _gold_field(
                "rl_formulation.algorithm",
                acceptable=["PPO"],
                expected_status="explicit",
            )
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.algorithm": FieldResult(value="PPO", status="inferred")
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.status_match is False
    metrics = compute_paper_metrics(evaluate_schema_instance(instance, entry))
    assert metrics.status_accuracy == 0.0
    assert any(i.type == "status_mismatch" for i in field.issues)
    assert field.status_explanation


def test_blocking_status_mismatch_expected_assertive_predicted_absent_with_fact():
    """AC-T001-S3: expected assertive + predicted absent while gold carries a
    non-empty acceptable_values is a blocking error."""
    entry = _entry_with_fields(
        [
            _gold_field(
                "rl_formulation.algorithm",
                acceptable=["PPO"],
                expected_status="explicit",
            )
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.algorithm": FieldResult(value=None, status="not_found")
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    issue = next(i for i in field.issues if i.type == "status_mismatch")
    assert issue.severity == "error"
    assert "acceptable_values" in issue.message


def test_blocking_status_mismatch_expected_assertive_predicted_unclear():
    """AC-T001-S3: expected assertive + predicted unclear/conflicting is a
    blocking error (the system could not resolve a real fact)."""
    entry = _entry_with_fields(
        [
            _gold_field(
                "rl_formulation.algorithm",
                acceptable=["PPO"],
                expected_status="explicit",
            )
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "rl_formulation.algorithm": FieldResult(value=None, status="unclear")
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    issue = next(i for i in field.issues if i.type == "status_mismatch")
    assert issue.severity == "error"
    assert "unclear" in issue.message


def test_gold_status_review_absent_but_acceptable_values_present():
    """AC-T001-S4/S6: expected absent but gold carries non-empty
    acceptable_values is surfaced as a gold_status_review item, never
    auto-corrected."""
    entry = _entry_with_fields(
        [
            _gold_field(
                "results.main_results",
                acceptable=["result A", "result B"],
                expected_status="not_found",
                judgement="correct",
                expectation="not_required",
            )
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "results.main_results": FieldResult(value=None, status="not_found")
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.gold_review is True
    issue = next(i for i in field.issues if i.type == "gold_status_review")
    assert issue.severity == "warning"
    assert issue.source == "gold_validation"
    assert issue.explanation


def test_gold_status_review_assertive_with_absence_notes():
    """AC-T001-S4: expected assertive with no acceptable_values, evidence not
    required, and absence notes is a gold_status_review candidate."""
    entry = _entry_with_fields(
        [
            GoldField.model_validate(
                {
                    "field_id": "research_problem.line_scope",
                    "expected_status": "explicit",
                    "acceptable_values": None,
                    "value_judgement": "correct",
                    "evidence_expectation": "not_required",
                    "notes": "this paper does not mention line scope; not found anywhere",
                }
            )
        ]
    )
    instance = SchemaInstance(
        paper_id="paper-x",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields={
            "research_problem.line_scope": FieldResult(
                value=None, status="not_found"
            )
        },
    )
    field = evaluate_schema_instance(instance, entry).fields[0]
    assert field.gold_review is True
    assert any(i.type == "gold_status_review" for i in field.issues)
