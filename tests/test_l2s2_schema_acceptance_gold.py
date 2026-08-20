"""L2S2 Package E deterministic tests: gold loading and validation
(AC-E-56, AC-E-12..16).

Fully offline: no network, no PDF parsing, no storage access. The only
filesystem reads are the gold fixture / temporary gold files themselves and
the local schema plugin definitions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from transit_scholar.layer2.schema_acceptance import (
    GoldBenchmark,
    GoldField,
    GoldLoadError,
    GoldPaper,
    load_schema_gold,
    validate_schema_gold,
)
from transit_scholar.layer2.schema_acceptance.gold import (
    DRAFT_EVIDENCE_JUDGEMENT,
    DRAFT_GOLD_SOURCE,
    DRAFT_REVIEW_STATUS,
    DRAFT_VALUE_JUDGEMENT,
    GOLD_STATUS_DRAFT,
)

FIXTURES = Path(__file__).parent / "fixtures" / "l2s2_schema_acceptance"
SEED_GOLD = FIXTURES / "seed_gold.json"
SEED_GOLD_CLEAN = FIXTURES / "seed_gold_clean.json"
DRAFT_GOLD = FIXTURES / "draft_gold.json"
CODEX_REVIEWED_GOLD = FIXTURES / "codex_reviewed_gold.json"
TEMPLATE_GOLD = (
    Path(__file__).parent.parent
    / "src"
    / "transit_scholar"
    / "layer2"
    / "schema_acceptance"
    / "gold"
    / "template_real_papers_bus_control_rl_v1.json"
)

DRAFT_FIELD_IDS = [
    "research_problem.control_type",
    "research_problem.control_objective",
    "research_problem.network_scope",
    "research_problem.line_scope",
    "rl_formulation.rl_paradigm",
    "rl_formulation.algorithm",
    "rl_formulation.agent_definition",
    "decision_model.state",
    "decision_model.action",
    "decision_model.reward",
    "baselines.baselines",
    "experimental_setup.simulator",
    "metrics.metrics",
    "results.main_results",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _field_dict(**overrides) -> dict:
    field = {
        "field_id": "rl_formulation.algorithm",
        "expected_status": "explicit",
        "acceptable_values": ["PPO"],
        "value_judgement": "correct",
        "evidence_expectation": "required",
        "notes": "seed field",
    }
    field.update(overrides)
    return field


def _template_field_dict(**overrides) -> dict:
    field = {
        "field_id": "rl_formulation.algorithm",
        "expected_status": None,
        "acceptable_values": None,
        "value_judgement": "not_evaluated",
        "evidence_expectation": "required",
        "notes": "TEMPLATE: human_review_required - 待人工标注",
    }
    field.update(overrides)
    return field


def _paper_dict(**overrides) -> dict:
    paper = {
        "paper_id": "seed-eval-001",
        "title": "Synthetic Seed Paper",
        "pdf_relative_path": "data/stage7_acceptance/real_papers/example.pdf",
        "schema_id": "bus_control_rl",
        "schema_version": "1.0",
        "selected_for_v1": True,
        "notes": "",
        "fields": [_field_dict()],
    }
    paper.update(overrides)
    return paper


def _benchmark(papers: list[dict]) -> GoldBenchmark:
    return GoldBenchmark.model_validate({"papers": papers})


def _issue_types(issues) -> set[str]:
    return {issue.type for issue in issues}


def _errors(issues):
    return [issue for issue in issues if issue.severity == "error"]


# ---------------------------------------------------------------------------
# loading (AC-E-12)
# ---------------------------------------------------------------------------


def test_load_valid_seed_gold_succeeds():
    gold = load_schema_gold(SEED_GOLD)
    assert isinstance(gold, GoldBenchmark)
    assert gold.benchmark_id == "l2s2-bus-control-rl-seed-v1"
    assert gold.gold_version == "1.0"
    assert len(gold.papers) == 3
    assert gold.papers[0].paper_id == "seed-eval-001"
    assert len(gold.papers[0].fields) == 14


def test_load_clean_seed_gold_succeeds():
    gold = load_schema_gold(SEED_GOLD_CLEAN)
    assert len(gold.papers) == 1
    assert gold.papers[0].selected_for_v1 is True


def test_load_real_paper_template_succeeds():
    gold = load_schema_gold(TEMPLATE_GOLD)
    assert len(gold.papers) == 6
    assert {p.paper_id for p in gold.papers} >= {
        "transit-001",
        "transit-002",
        "transit-006",
        "transit-010",
        "transit-015",
        "transit-016",
    }
    for paper in gold.papers:
        assert 10 <= len(paper.fields) <= 15
        assert paper.gold_status == "template"


def test_load_missing_file_raises_gold_load_failed():
    with pytest.raises(GoldLoadError) as excinfo:
        load_schema_gold(FIXTURES / "does_not_exist.json")
    assert excinfo.value.error_code == "gold_load_failed"


def test_load_invalid_json_raises_gold_load_failed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(GoldLoadError) as excinfo:
        load_schema_gold(bad)
    assert excinfo.value.error_code == "gold_load_failed"


def test_load_missing_papers_key_raises(tmp_path):
    bad = tmp_path / "nopapers.json"
    bad.write_text(json.dumps({"benchmark_id": "x"}), encoding="utf-8")
    with pytest.raises(GoldLoadError) as excinfo:
        load_schema_gold(bad)
    assert excinfo.value.error_code == "gold_load_failed"


def test_load_empty_papers_raises(tmp_path):
    bad = tmp_path / "empty.json"
    bad.write_text(json.dumps({"papers": []}), encoding="utf-8")
    with pytest.raises(GoldLoadError) as excinfo:
        load_schema_gold(bad)
    assert excinfo.value.error_code == "gold_load_failed"


def test_load_top_level_not_object_raises(tmp_path):
    bad = tmp_path / "list.json"
    bad.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(GoldLoadError):
        load_schema_gold(bad)


# ---------------------------------------------------------------------------
# validation: paper entry issues (AC-E-14.1)
# ---------------------------------------------------------------------------


def test_validate_paper_missing_required_keys_reports_errors():
    gold = _benchmark([{"fields": [_field_dict()]}])
    issues = validate_schema_gold(gold)
    types = _issue_types(_errors(issues))
    assert "paper_missing_required_key" in types
    messages = " ".join(i.message for i in _errors(issues))
    for key in ("paper_id", "title", "pdf_relative_path", "schema_id", "selected_for_v1"):
        assert key in messages


def test_validate_paper_empty_fields_reports_error():
    gold = _benchmark([_paper_dict(fields=[])])
    issues = validate_schema_gold(gold)
    assert "paper_missing_required_key" in _issue_types(_errors(issues))


def test_validate_paper_bad_selected_for_v1_type_reports_error():
    gold = _benchmark([_paper_dict(selected_for_v1="yes")])
    issues = validate_schema_gold(gold)
    assert "paper_missing_required_key" in _issue_types(_errors(issues))


def test_validate_duplicate_paper_id_reports_error():
    gold = _benchmark([_paper_dict(), _paper_dict()])
    issues = validate_schema_gold(gold)
    assert "paper_id_duplicate" in _issue_types(_errors(issues))


# ---------------------------------------------------------------------------
# validation: field item issues (AC-E-14.2..5, 8)
# ---------------------------------------------------------------------------


def test_validate_field_missing_required_keys_reports_errors():
    gold = _benchmark([_paper_dict(fields=[{"notes": "x"}])])
    issues = validate_schema_gold(gold)
    types = _issue_types(_errors(issues))
    assert "field_missing_required_key" in types
    messages = " ".join(i.message for i in _errors(issues))
    for key in ("field_id", "value_judgement", "evidence_expectation"):
        assert key in messages


def test_validate_field_id_not_in_schema_reports_error():
    gold = _benchmark([_paper_dict(fields=[_field_dict(field_id="no.such_field")])])
    issues = validate_schema_gold(gold)
    assert "field_id_not_in_schema" in _issue_types(_errors(issues))


def test_validate_invalid_expected_status_reports_error():
    gold = _benchmark([_paper_dict(fields=[_field_dict(expected_status="definitely")])])
    issues = validate_schema_gold(gold)
    assert "field_invalid_expected_status" in _issue_types(_errors(issues))


def test_validate_invalid_value_judgement_reports_error():
    gold = _benchmark([_paper_dict(fields=[_field_dict(value_judgement="maybe")])])
    issues = validate_schema_gold(gold)
    assert "field_invalid_value_judgement" in _issue_types(_errors(issues))


def test_validate_invalid_evidence_expectation_reports_error():
    gold = _benchmark([_paper_dict(fields=[_field_dict(evidence_expectation="sometimes")])])
    issues = validate_schema_gold(gold)
    assert "field_invalid_evidence_expectation" in _issue_types(_errors(issues))


def test_validate_duplicate_field_id_reports_error():
    gold = _benchmark(
        [_paper_dict(fields=[_field_dict(), _field_dict(field_id="rl_formulation.algorithm")])]
    )
    issues = validate_schema_gold(gold)
    assert "field_id_duplicate" in _issue_types(_errors(issues))


# ---------------------------------------------------------------------------
# validation: pdf path safety (AC-E-14.6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "C:\\papers\\example.pdf",
        "C:/papers/example.pdf",
        "/home/user/papers/example.pdf",
        "../escape/example.pdf",
        "papers/../../escape.pdf",
    ],
)
def test_validate_unsafe_pdf_relative_path_reports_error(bad_path):
    gold = _benchmark([_paper_dict(pdf_relative_path=bad_path)])
    issues = validate_schema_gold(gold)
    assert "pdf_path_invalid" in _issue_types(_errors(issues))


# ---------------------------------------------------------------------------
# validation: schema existence (AC-E-14.7)
# ---------------------------------------------------------------------------


def test_validate_unknown_schema_id_reports_error():
    gold = _benchmark(
        [_paper_dict(schema_id="no_such_schema", fields=[_field_dict()])]
    )
    issues = validate_schema_gold(gold)
    assert "gold_schema_not_found" in _issue_types(_errors(issues))


# ---------------------------------------------------------------------------
# validation: template entry rules (AC-E-15)
# ---------------------------------------------------------------------------


def test_validate_clean_evaluated_gold_passes():
    issues = validate_schema_gold(load_schema_gold(SEED_GOLD_CLEAN))
    assert issues == []


def test_validate_seed_gold_passes():
    issues = validate_schema_gold(load_schema_gold(SEED_GOLD))
    assert issues == []


def test_validate_real_paper_template_passes():
    issues = validate_schema_gold(load_schema_gold(TEMPLATE_GOLD))
    assert issues == []


def test_validate_non_template_null_expected_status_reports_error():
    gold = _benchmark(
        [_paper_dict(fields=[_field_dict(expected_status=None, acceptable_values=None)])]
    )
    issues = validate_schema_gold(gold)
    assert "field_expected_status_null" in _issue_types(_errors(issues))


def test_validate_template_entry_with_null_fields_passes():
    gold = _benchmark(
        [
            _paper_dict(
                gold_status="template",
                fields=[_template_field_dict()],
            )
        ]
    )
    assert validate_schema_gold(gold) == []


def test_validate_template_entry_with_human_judgement_reports_error():
    gold = _benchmark(
        [
            _paper_dict(
                gold_status="template",
                fields=[
                    _template_field_dict(value_judgement="correct"),
                ],
            )
        ]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "template_field_invalid" in types


def test_validate_template_entry_with_filled_values_reports_error():
    gold = _benchmark(
        [
            _paper_dict(
                gold_status="template",
                fields=[
                    _template_field_dict(
                        expected_status="explicit",
                        acceptable_values=["PPO"],
                    ),
                ],
            )
        ]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "template_field_invalid" in types


def test_validate_template_entry_missing_markers_reports_error():
    gold = _benchmark(
        [
            _paper_dict(
                gold_status="template",
                fields=[_template_field_dict(notes="no markers here")],
            )
        ]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "template_field_invalid" in types


# ---------------------------------------------------------------------------
# zero file IO on pdf_relative_path (AC-E-10/16/54)
# ---------------------------------------------------------------------------


@pytest.fixture
def guard_pdf_io(monkeypatch):
    """Make any existence/opening check on a PDF-ish path explode."""
    forbidden = ("seed_paper.pdf", "stage7_acceptance", "example.pdf")
    real_exists = Path.exists
    real_is_file = Path.is_file
    real_open = Path.open

    def _is_pdf_path(path: Path) -> bool:
        return any(marker in str(path) for marker in forbidden)

    def guarded_exists(self):
        if _is_pdf_path(self):
            raise AssertionError(f"unexpected existence check on PDF path {self}")
        return real_exists(self)

    def guarded_is_file(self):
        if _is_pdf_path(self):
            raise AssertionError(f"unexpected is_file check on PDF path {self}")
        return real_is_file(self)

    def guarded_open(self, *args, **kwargs):
        if _is_pdf_path(self):
            raise AssertionError(f"unexpected open on PDF path {self}")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    monkeypatch.setattr(Path, "open", guarded_open)
    return None


def test_load_and_validate_never_touch_pdf_path(guard_pdf_io):
    gold = load_schema_gold(SEED_GOLD)
    assert validate_schema_gold(gold) == []
    gold = load_schema_gold(TEMPLATE_GOLD)
    assert validate_schema_gold(gold) == []


def test_models_keep_pdf_path_as_metadata_only():
    gold = load_schema_gold(SEED_GOLD)
    assert gold.papers[0].pdf_relative_path.startswith("tests/fixtures/")
    assert isinstance(gold.papers[0].pdf_relative_path, str)


# ---------------------------------------------------------------------------
# FR-005/FR-006 (task-2026-08-15-001): draft gold loading, markers, rules
# ---------------------------------------------------------------------------


def _draft_field_dict(**overrides) -> dict:
    field = {
        "field_id": "rl_formulation.algorithm",
        "expected_status": "explicit",
        "acceptable_values": ["PPO"],
        "value_judgement": DRAFT_VALUE_JUDGEMENT,
        "evidence_judgement": DRAFT_EVIDENCE_JUDGEMENT,
        "review_status": DRAFT_REVIEW_STATUS,
        "gold_source": DRAFT_GOLD_SOURCE,
        "not_human_gold": True,
        "evidence_expectation": "required",
        "notes": "draft candidate",
    }
    field.update(overrides)
    return field


def _draft_paper_dict(**overrides) -> dict:
    paper = {
        "paper_id": "draft-eval-001",
        "title": "Draft Paper",
        "pdf_relative_path": "data/stage7_acceptance/real_papers/example.pdf",
        "schema_id": "bus_control_rl",
        "schema_version": "1.0",
        "selected_for_v1": True,
        "gold_status": GOLD_STATUS_DRAFT,
        "notes": "draft gold entry",
        "fields": [_draft_field_dict()],
    }
    paper.update(overrides)
    return paper


def test_draft_gold_fixture_loads_and_validates():
    gold = load_schema_gold(DRAFT_GOLD)
    issues = validate_schema_gold(gold)
    assert [i for i in issues if i.severity == "error"] == []


def test_draft_gold_fixture_shape_and_markers():
    gold = load_schema_gold(DRAFT_GOLD)
    assert {p.paper_id for p in gold.papers} == {
        "transit-001",
        "transit-002",
        "transit-006",
        "transit-010",
        "transit-015",
        "transit-016",
    }
    assert {p.schema_id for p in gold.papers} == {"bus_control_rl"}
    for paper in gold.papers:
        assert paper.gold_status == GOLD_STATUS_DRAFT
        assert [f.field_id for f in paper.fields] == DRAFT_FIELD_IDS
        for field in paper.fields:
            assert field.value_judgement == DRAFT_VALUE_JUDGEMENT
            assert field.evidence_judgement == DRAFT_EVIDENCE_JUDGEMENT
            assert field.review_status == DRAFT_REVIEW_STATUS
            assert field.gold_source == DRAFT_GOLD_SOURCE
            assert field.not_human_gold is True
    total_fields = sum(len(p.fields) for p in gold.papers)
    assert total_fields == 84


def test_draft_gold_not_byte_identical_to_template():
    assert DRAFT_GOLD.read_bytes() != TEMPLATE_GOLD.read_bytes()


def test_draft_gold_contains_no_human_judgements():
    text = DRAFT_GOLD.read_text(encoding="utf-8")
    for forbidden in ("\"correct\"", "\"partially_correct\"", "\"incorrect\""):
        assert forbidden not in text


def test_draft_field_with_human_judgement_reports_error():
    gold = _benchmark(
        [
            _draft_paper_dict(
                fields=[_draft_field_dict(value_judgement="correct")],
            )
        ]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "draft_field_human_judgement" in types


def test_draft_field_missing_evidence_judgement_reports_error():
    gold = _benchmark(
        [_draft_paper_dict(fields=[_draft_field_dict(evidence_judgement=None)])]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "draft_field_human_judgement" in types


def test_draft_field_missing_markers_reports_error():
    gold = _benchmark(
        [
            _draft_paper_dict(
                fields=[_draft_field_dict(not_human_gold=False, gold_source=None)],
            )
        ]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "draft_field_marker_missing" in types


def test_evaluated_entry_with_draft_markers_reports_error():
    gold = _benchmark(
        [
            _paper_dict(
                fields=[
                    _field_dict(
                        not_human_gold=True,
                        gold_source=DRAFT_GOLD_SOURCE,
                        evidence_judgement="not_evaluated",
                        review_status="requires_human_review",
                    ),
                ],
            )
        ]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "draft_marker_on_non_draft" in types


def test_template_entry_with_draft_markers_reports_error():
    gold = _benchmark(
        [
            _paper_dict(
                gold_status="template",
                fields=[
                    _template_field_dict(
                        not_human_gold=True,
                        gold_source=DRAFT_GOLD_SOURCE,
                    ),
                ],
            )
        ]
    )
    types = _issue_types(_errors(validate_schema_gold(gold)))
    assert "draft_marker_on_non_draft" in types


def test_draft_entry_with_null_candidates_passes():
    """Draft entries may legitimately leave candidates null (unevaluated)."""
    gold = _benchmark(
        [
            _draft_paper_dict(
                fields=[_draft_field_dict(expected_status=None, acceptable_values=None)],
            )
        ]
    )
    assert validate_schema_gold(gold) == []


def test_codex_reviewed_gold_fixture_loads_and_validates():
    gold = load_schema_gold(CODEX_REVIEWED_GOLD)
    issues = validate_schema_gold(gold)
    assert [i for i in issues if i.severity == "error"] == []


def test_codex_reviewed_gold_fixture_is_evaluated_not_draft():
    gold = load_schema_gold(CODEX_REVIEWED_GOLD)
    assert gold.benchmark_id == "l2s2-bus-control-rl-real-papers-codex-reviewed-v1"
    assert {p.paper_id for p in gold.papers} == {
        "transit-001",
        "transit-002",
        "transit-006",
        "transit-010",
        "transit-015",
        "transit-016",
    }
    for paper in gold.papers:
        assert paper.gold_status == "evaluated"
        assert [f.field_id for f in paper.fields] == DRAFT_FIELD_IDS
        for field in paper.fields:
            assert field.expected_status is not None
            assert field.value_judgement == "correct"
            assert field.gold_source == "codex_pdf_text_review"
            assert field.not_human_gold is not True
    total_fields = sum(len(p.fields) for p in gold.papers)
    assert total_fields == 84
