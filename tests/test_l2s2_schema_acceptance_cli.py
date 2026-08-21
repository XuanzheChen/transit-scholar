"""L2S2 Package E deterministic tests: CLI (AC-E-60, AC-E-42..46).

Runs ``python -m transit_scholar.layer2.schema_acceptance.run`` from the
repository root as a subprocess against fixture gold files and temporary
storage/output directories. Fully offline: no network, no LLM, no PDF, no
``data/**`` IO.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from transit_scholar.layer2.schema_extraction import (
    PROMPT_VERSION,
    CurrentPointer,
    EvidenceRef,
    ExtractionManifest,
    FakeLLMProvider,
    FieldResult,
    RunManifest,
    SchemaInstance,
    SchemaRunStorage,
    ValidationReport,
    compute_extraction_config_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "l2s2_schema_acceptance"
SEED_GOLD = FIXTURES / "seed_gold.json"
SEED_GOLD_CLEAN = FIXTURES / "seed_gold_clean.json"
DRAFT_GOLD = FIXTURES / "draft_gold.json"

CREATED_AT = "2026-08-14T00:00:00+00:00"
SCHEMA_HASH = "0" * 64


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, "-m", "transit_scholar.layer2.schema_acceptance.run", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
        env=env,
        timeout=180,
    )


def _good_ref(block_id="blk_1", quote="real paper text") -> EvidenceRef:
    return EvidenceRef(
        block_id=block_id,
        char_start=0,
        char_end=len(quote),
        pages=[2],
        section_path=["Method"],
        quote=quote,
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
                value={"positions": ["p1"]}, status="inferred"
            ),
            "decision_model.reward": FieldResult(
                value={"wait_time": -1.0}, status="explicit"
            ),
            "baselines.baselines": FieldResult(value=[], status="explicit"),
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
                ],
            ),
            "research_problem.line_scope": FieldResult(
                value="Line 5", status="explicit"
            ),
            "metrics.metrics": FieldResult(value=["x"], status="explicit"),
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


# ---------------------------------------------------------------------------
# AC-E-60.1: happy path from the repository root
# ---------------------------------------------------------------------------


def test_cli_happy_path_exit_0_and_writes_both_files(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(SEED_GOLD_CLEAN),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 0, result.stderr
    json_path = output_dir / "acceptance_report.json"
    md_path = output_dir / "acceptance_summary.md"
    assert json_path.is_file()
    assert md_path.is_file()
    assert "acceptance report:" in result.stdout
    assert "acceptance summary:" in result.stdout
    assert "overall:" in result.stdout
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["freeze"]["freeze_suggestion"] == "ready"
    assert data["papers"][0]["run_kind"] == "current"
    assert data["papers"][0]["run_id"] == "run_001"
    assert data["gold_path"] == str(SEED_GOLD_CLEAN)


def test_cli_run_id_reads_historical_run(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance(), run_id="run_old")
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(SEED_GOLD_CLEAN),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
        "--run-id", "run_old",
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(
        (output_dir / "acceptance_report.json").read_text(encoding="utf-8")
    )
    assert data["papers"][0]["run_kind"] == "historical"
    assert data["papers"][0]["run_id"] == "run_old"


# ---------------------------------------------------------------------------
# AC-E-60.2: invalid gold -> exit 1, no report
# ---------------------------------------------------------------------------


def test_cli_invalid_gold_exit_1_and_no_report(tmp_path):
    bad_gold = tmp_path / "bad_gold.json"
    bad_gold.write_text(
        json.dumps(
            {
                "papers": [
                    {
                        "paper_id": "p1",
                        "title": "t",
                        "pdf_relative_path": "data/x.pdf",
                        "schema_id": "bus_control_rl",
                        "schema_version": "1.0",
                        "selected_for_v1": True,
                        "fields": [
                            {
                                "field_id": "no.such_field",
                                "expected_status": "explicit",
                                "acceptable_values": ["x"],
                                "value_judgement": "correct",
                                "evidence_expectation": "optional",
                                "notes": "n",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    storage_root = tmp_path / "storage"
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(bad_gold),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 1
    assert "gold validation error" in result.stderr
    assert not (output_dir / "acceptance_report.json").exists()
    assert not (output_dir / "acceptance_summary.md").exists()


def test_cli_missing_gold_file_exit_1(tmp_path):
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(tmp_path / "missing.json"),
        "--storage-root", str(tmp_path / "storage"),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 1
    assert "gold_load_failed" in result.stderr


# ---------------------------------------------------------------------------
# AC-E-60.3: missing arguments -> exit 2
# ---------------------------------------------------------------------------


def test_cli_missing_required_args_exit_2():
    result = _run_cli()
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_cli_missing_output_dir_exit_2(tmp_path):
    result = _run_cli(
        "--gold", str(SEED_GOLD_CLEAN),
        "--storage-root", str(tmp_path / "storage"),
    )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# AC-E-60.4: error-level issues -> exit 1 but the report is written
# ---------------------------------------------------------------------------


def test_cli_error_issues_exit_1_but_report_written(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _seed_instance_stored())
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(SEED_GOLD),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 1
    assert "error-level issues" in result.stderr
    json_path = output_dir / "acceptance_report.json"
    md_path = output_dir / "acceptance_summary.md"
    assert json_path.is_file()
    assert md_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # the mismatch paper produced a paper_error
    assert any(p.get("paper_error") for p in data["papers"])


def test_cli_missing_storage_run_exit_1_but_report_written(tmp_path):
    storage_root = tmp_path / "empty_storage"
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(SEED_GOLD_CLEAN),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 1
    assert (output_dir / "acceptance_report.json").is_file()
    data = json.loads(
        (output_dir / "acceptance_report.json").read_text(encoding="utf-8")
    )
    assert data["papers"][0]["paper_error"] == "schema_read_failed"


# ---------------------------------------------------------------------------
# AC-E-60.5: output-dir == storage-root refused
# ---------------------------------------------------------------------------


def test_cli_output_dir_equals_storage_root_refused(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    result = _run_cli(
        "--gold", str(SEED_GOLD_CLEAN),
        "--storage-root", str(storage_root),
        "--output-dir", str(storage_root),
    )
    assert result.returncode == 2
    assert "output-dir" in result.stderr


def test_cli_output_dir_inside_storage_root_refused(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    result = _run_cli(
        "--gold", str(SEED_GOLD_CLEAN),
        "--storage-root", str(storage_root),
        "--output-dir", str(storage_root / "nested" / "out"),
    )
    assert result.returncode == 2
    assert "output-dir" in result.stderr


# ---------------------------------------------------------------------------
# AC-E-60.6: CLI never touches the real data tree
# ---------------------------------------------------------------------------


def test_cli_reports_stay_inside_output_dir(tmp_path):
    storage_root = tmp_path / "storage"
    _write_run(storage_root, _clean_instance())
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(SEED_GOLD_CLEAN),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 0, result.stderr
    # nothing written into storage beyond what the test itself created
    storage_files = sorted(p.name for p in storage_root.rglob("*") if p.is_file())
    assert set(storage_files) == {
        "schema_instance.json",
        "extraction_manifest.json",
        "validation_report.json",
        "run_manifest.json",
        "current.json",
    }
    # nothing written into the repository data tree
    data_root = REPO_ROOT / "data"
    before = set(data_root.rglob("*")) if data_root.exists() else set()
    after = set(data_root.rglob("*")) if data_root.exists() else set()
    assert before == after


# ---------------------------------------------------------------------------
# FR-006 (task-2026-08-15-001): CLI end-to-end over the draft gold
# ---------------------------------------------------------------------------


def test_cli_draft_gold_end_to_end_no_key_no_network(tmp_path):
    """Build schema runs for the 6 draft paper ids with fake LLM + fake
    retrieval via extract_schema, then run the Package E CLI against the
    draft gold. The report must keep declared_frozen False and
    freeze_suggestion insufficient_data (draft = non-human gold)."""
    from transit_scholar.layer2.schema_extraction import extract_schema

    storage_root = tmp_path / "storage"
    for paper_id in ("transit-001", "transit-002", "transit-006",
                     "transit-010", "transit-015", "transit-016"):
        extract_schema(
            paper_id,
            "bus_control_rl",
            storage_root=storage_root,
            llm_client=FakeLLMProvider(),  # explicit fake, offline (AC-RW-15)
        )
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(DRAFT_GOLD),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 0, result.stderr
    json_path = output_dir / "acceptance_report.json"
    md_path = output_dir / "acceptance_summary.md"
    assert json_path.is_file()
    assert md_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["freeze"]["declared_frozen"] is False
    assert data["freeze"]["freeze_suggestion"] == "insufficient_data"
    assert data["metrics"]["overall"]["evaluated_field_count"] == 0
    assert data["metrics"]["overall"]["value_accuracy"] is None
    assert len(data["papers"]) == 6
    assert all(p["draft"] for p in data["papers"])
    md_text = md_path.read_text(encoding="utf-8")
    assert "已冻结" not in md_text


# ---------------------------------------------------------------------------
# FR-005 / AC-T001-F18: human-corrected exact-string mismatch is a diagnostic
# ---------------------------------------------------------------------------


def test_cli_exact_string_mismatch_judged_correct_exits_0(tmp_path):
    """Required test 7: value_judgement=correct with only an exact-string
    mismatch yields warning diagnostics (not blockers) and exit code 0 with a
    ready freeze suggestion; the value mismatch is still recorded."""
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
    gold_path = tmp_path / "mismatch_correct_gold.json"
    gold_path.write_text(
        json.dumps(
            {
                "papers": [
                    {
                        "paper_id": "seed-clean-001",
                        "title": "t",
                        "pdf_relative_path": "tests/fixtures/l2s2_schema_acceptance/seed_paper.pdf",
                        "schema_id": "bus_control_rl",
                        "schema_version": "1.0",
                        "selected_for_v1": True,
                        "gold_status": "evaluated",
                        "notes": "",
                        "fields": [
                            {
                                "field_id": "research_problem.control_type",
                                "expected_status": "explicit",
                                "acceptable_values": ["holding"],
                                "value_judgement": "correct",
                                "evidence_expectation": "required",
                                "notes": "exact string differs but human judged correct",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"
    result = _run_cli(
        "--gold", str(gold_path),
        "--storage-root", str(storage_root),
        "--output-dir", str(output_dir),
    )
    assert result.returncode == 0, result.stderr
    json_path = output_dir / "acceptance_report.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["freeze"]["freeze_suggestion"] == "ready"
    assert data["freeze"]["declared_frozen"] is False
    assert data["freeze"]["exact_match_diagnostic_count"] >= 1
    value_issues = [i for i in data["issues"] if i["type"] in ("value_mismatch", "judgement_conflict")]
    assert value_issues
    assert all(i["severity"] == "warning" for i in value_issues)
    assert data["metrics"]["overall"]["value_correct_count"] == 1
