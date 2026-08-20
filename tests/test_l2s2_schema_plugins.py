"""L2S2 Package A deterministic tests: plugin loader, shipped plugin
definitions, custom schema proof, and bus_control_rl validator entry
(requirements.md section 7 / AC-L2S2A-08..11, 15).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from transit_scholar.layer2.schema_extraction import (
    FieldResult,
    SchemaInstance,
    ValidationIssue,
    compute_schema_hash,
    get_schema_definition,
    list_schema_plugins,
)
from transit_scholar.layer2.schema_extraction.loader import (
    InvalidSchemaDefinitionError,
    SchemaPluginNotFoundError,
)
from transit_scholar.layer2.schema_plugins.bus_control_rl.validators import (
    validate as bus_validate,
)

BUS_SECTION_ORDER = (
    "research_problem",
    "rl_formulation",
    "decision_model",
    "control_constraints",
    "baselines",
    "experimental_setup",
    "metrics",
    "results",
    "comparability",
)

BUS_FIELD_IDS = (
    "research_problem.control_type",
    "research_problem.control_objective",
    "research_problem.network_scope",
    "research_problem.line_scope",
    "research_problem.problem_description",
    "rl_formulation.rl_paradigm",
    "rl_formulation.algorithm",
    "rl_formulation.agent_definition",
    "rl_formulation.agent_granularity",
    "rl_formulation.centralized_or_decentralized",
    "rl_formulation.training_execution_paradigm",
    "decision_model.state",
    "decision_model.action",
    "decision_model.reward",
    "decision_model.decision_timing",
    "decision_model.decision_interval",
    "decision_model.action_constraints",
    "control_constraints.holding_upper_bound",
    "control_constraints.holding_lower_bound",
    "control_constraints.holding_location",
    "control_constraints.operational_constraints",
    "control_constraints.other_constraints",
    "baselines.baselines",
    "experimental_setup.simulator",
    "experimental_setup.route_or_network",
    "experimental_setup.demand_setting",
    "experimental_setup.scenario_setting",
    "experimental_setup.training_setting",
    "experimental_setup.random_seed",
    "experimental_setup.evaluation_protocol",
    "metrics.metrics",
    "results.main_results",
    "results.baseline_comparisons",
    "results.ablation_results",
    "results.main_conclusions",
    "comparability.comparable_dimensions",
    "comparability.important_differences",
    "comparability.comparison_caveats",
    "comparability.comparability_summary",
)

BUS_FIELD_COUNTS = {
    "research_problem": 5,
    "rl_formulation": 6,
    "decision_model": 6,
    "control_constraints": 5,
    "baselines": 1,
    "experimental_setup": 7,
    "metrics": 1,
    "results": 4,
    "comparability": 4,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bus_instance(overrides: dict | None = None) -> SchemaInstance:
    """A bus_control_rl instance with all 39 fields present as benign
    ``not_found`` results; ``overrides`` replaces individual fields."""
    fields: dict[str, FieldResult] = {
        field_id: FieldResult(value=None, status="not_found")
        for field_id in BUS_FIELD_IDS
    }
    fields.update(overrides or {})
    return SchemaInstance(
        paper_id="paper_001",
        schema_id="bus_control_rl",
        schema_version="1.0",
        fields=fields,
    )


# ---------------------------------------------------------------------------
# AC-L2S2A-08 plugin loader
# ---------------------------------------------------------------------------


def test_list_schema_plugins_discovers_both_plugins():
    plugins = list_schema_plugins()
    assert plugins == sorted(plugins)
    assert "bus_control_rl" in plugins
    assert "generic_research_paper" in plugins


def test_get_schema_definition_returns_validated_definition():
    definition = get_schema_definition("bus_control_rl")
    assert isinstance(definition.schema_id, str)
    assert definition.schema_id == "bus_control_rl"


def test_bus_control_rl_hash_stable_across_independent_loads():
    first = get_schema_definition("bus_control_rl")
    second = get_schema_definition("bus_control_rl")
    assert compute_schema_hash(first) == compute_schema_hash(second)


def test_unknown_schema_id_raises_not_found():
    with pytest.raises(SchemaPluginNotFoundError) as excinfo:
        get_schema_definition("no_such_plugin")
    assert "NotFound" in type(excinfo.value).__name__
    assert "no_such_plugin" in str(excinfo.value)


# ---------------------------------------------------------------------------
# AC-L2S2A-09 bus_control_rl official definition
# ---------------------------------------------------------------------------


def test_bus_control_rl_identity():
    definition = get_schema_definition("bus_control_rl")
    assert definition.schema_id == "bus_control_rl"
    assert definition.version == "1.0"


def test_bus_control_rl_sections_exact_order():
    definition = get_schema_definition("bus_control_rl")
    assert [s.id for s in definition.sections] == list(BUS_SECTION_ORDER)


def test_bus_control_rl_field_counts_per_section():
    definition = get_schema_definition("bus_control_rl")
    counts = {s.id: len(s.fields) for s in definition.sections}
    assert counts == BUS_FIELD_COUNTS
    assert sum(counts.values()) == 39


def test_bus_control_rl_field_ids_exact_order():
    definition = get_schema_definition("bus_control_rl")
    flattened = [f.id for s in definition.sections for f in s.fields]
    assert flattened == list(BUS_FIELD_IDS)


def test_bus_control_rl_every_field_has_nonempty_label_and_question():
    definition = get_schema_definition("bus_control_rl")
    for section in definition.sections:
        for field in section.fields:
            assert field.label.strip(), field.id
            assert field.question.strip(), field.id


def test_bus_control_rl_structured_field_types():
    definition = get_schema_definition("bus_control_rl")
    by_id = {f.id: f for s in definition.sections for f in s.fields}
    for field_id in ("decision_model.state", "decision_model.action", "decision_model.reward"):
        assert by_id[field_id].type == "object", field_id
    for field_id in ("baselines.baselines", "metrics.metrics"):
        assert by_id[field_id].type == "list", field_id


# ---------------------------------------------------------------------------
# AC-L2S2A-09' generic_research_paper custom schema proof (FR-A-009)
# ---------------------------------------------------------------------------


def test_generic_research_paper_loaded():
    definition = get_schema_definition("generic_research_paper")
    assert definition.schema_id == "generic_research_paper"
    assert definition.version == "1.0"
    field_ids = [f.id for s in definition.sections for f in s.fields]
    for required in ("research_question", "method", "dataset", "metrics", "main_findings"):
        assert required in field_ids


def test_custom_plugin_discovers_without_core_change(monkeypatch, tmp_path):
    """Proving the plugin abstraction: a brand-new plugin directory with a
    legal schema.yaml is discovered and loaded with no engine core change."""
    plugin_dir = tmp_path / "my_custom_schema"
    plugin_dir.mkdir()
    (plugin_dir / "schema.yaml").write_text(
        "schema_id: my_custom_schema\n"
        'version: "0.1"\n'
        "name: Custom Schema\n"
        "sections:\n"
        "  - id: overview\n"
        "    label: Overview\n"
        "    fields:\n"
        "      - id: headline\n"
        "        label: Headline\n"
        "        question: What is the headline?\n"
        "        type: string\n",
        encoding="utf-8",
    )
    from transit_scholar.layer2.schema_extraction import loader

    monkeypatch.setattr(loader, "plugins_root", lambda: tmp_path)
    assert list_schema_plugins() == ["my_custom_schema"]
    definition = get_schema_definition("my_custom_schema")
    assert definition.schema_id == "my_custom_schema"
    assert [f.id for f in definition.sections[0].fields] == ["headline"]


# ---------------------------------------------------------------------------
# AC-L2S2A-08 loader error cases (invalid definitions)
# ---------------------------------------------------------------------------


def _install_plugin(monkeypatch, tmp_path, name: str, yaml_body: str) -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    (plugin_dir / "schema.yaml").write_text(yaml_body, encoding="utf-8")
    from transit_scholar.layer2.schema_extraction import loader

    monkeypatch.setattr(loader, "plugins_root", lambda: tmp_path)
    return plugin_dir


def test_invalid_yaml_not_a_mapping(monkeypatch, tmp_path):
    _install_plugin(monkeypatch, tmp_path, "bad_plugin", "- 1\n- 2\n")
    with pytest.raises(InvalidSchemaDefinitionError) as excinfo:
        get_schema_definition("bad_plugin")
    assert "SchemaDefinition" in type(excinfo.value).__name__
    assert "bad_plugin" in str(excinfo.value)


def test_invalid_yaml_missing_required_sections(monkeypatch, tmp_path):
    _install_plugin(
        monkeypatch,
        tmp_path,
        "bad_plugin",
        'schema_id: bad_plugin\nversion: "1.0"\n',
    )
    with pytest.raises(InvalidSchemaDefinitionError) as excinfo:
        get_schema_definition("bad_plugin")
    assert "SchemaDefinition" in type(excinfo.value).__name__
    assert "bad_plugin" in str(excinfo.value)


def test_invalid_yaml_missing_section_fields(monkeypatch, tmp_path):
    _install_plugin(
        monkeypatch,
        tmp_path,
        "bad_plugin",
        'schema_id: bad_plugin\n'
        'version: "1.0"\n'
        "sections:\n"
        "  - id: s1\n"
        "    label: S1\n",
    )
    with pytest.raises(InvalidSchemaDefinitionError) as excinfo:
        get_schema_definition("bad_plugin")
    assert "SchemaDefinition" in type(excinfo.value).__name__


def test_invalid_yaml_illegal_field_type(monkeypatch, tmp_path):
    _install_plugin(
        monkeypatch,
        tmp_path,
        "bad_plugin",
        'schema_id: bad_plugin\n'
        'version: "1.0"\n'
        "sections:\n"
        "  - id: s1\n"
        "    label: S1\n"
        "    fields:\n"
        "      - id: f1\n"
        "        label: F1\n"
        "        question: Q?\n"
        "        type: nonsense\n",
    )
    with pytest.raises(InvalidSchemaDefinitionError) as excinfo:
        get_schema_definition("bad_plugin")
    assert "SchemaDefinition" in type(excinfo.value).__name__
    assert "bad_plugin" in str(excinfo.value)


def test_invalid_yaml_enum_without_options(monkeypatch, tmp_path):
    _install_plugin(
        monkeypatch,
        tmp_path,
        "bad_plugin",
        'schema_id: bad_plugin\n'
        'version: "1.0"\n'
        "sections:\n"
        "  - id: s1\n"
        "    label: S1\n"
        "    fields:\n"
        "      - id: f1\n"
        "        label: F1\n"
        "        question: Q?\n"
        "        type: enum\n",
    )
    with pytest.raises(InvalidSchemaDefinitionError) as excinfo:
        get_schema_definition("bad_plugin")
    assert "SchemaDefinition" in type(excinfo.value).__name__


def test_invalid_yaml_duplicate_field_ids(monkeypatch, tmp_path):
    _install_plugin(
        monkeypatch,
        tmp_path,
        "bad_plugin",
        'schema_id: bad_plugin\n'
        'version: "1.0"\n'
        "sections:\n"
        "  - id: s1\n"
        "    label: S1\n"
        "    fields:\n"
        "      - id: dup\n"
        "        label: D1\n"
        "        question: Q?\n"
        "        type: string\n"
        "      - id: dup\n"
        "        label: D2\n"
        "        question: Q?\n"
        "        type: string\n",
    )
    with pytest.raises(InvalidSchemaDefinitionError) as excinfo:
        get_schema_definition("bad_plugin")
    assert "SchemaDefinition" in type(excinfo.value).__name__
    assert "bad_plugin" in str(excinfo.value)


def test_list_plugins_raises_on_invalid_discovered_yaml(monkeypatch, tmp_path):
    _install_plugin(
        monkeypatch,
        tmp_path,
        "broken",
        'schema_id: broken\nversion: "1.0"\n',  # no sections
    )
    with pytest.raises(InvalidSchemaDefinitionError):
        list_schema_plugins()


def test_directory_without_schema_yaml_is_ignored(monkeypatch, tmp_path):
    (tmp_path / "not_a_plugin").mkdir()
    from transit_scholar.layer2.schema_extraction import loader

    monkeypatch.setattr(loader, "plugins_root", lambda: tmp_path)
    assert list_schema_plugins() == []


# ---------------------------------------------------------------------------
# AC-L2S2A-10 bus_control_rl validator entry
# ---------------------------------------------------------------------------


def test_validator_returns_validation_issue_list():
    instance = _bus_instance(
        {
            "research_problem.control_type": FieldResult(value="scheduling", status="explicit"),
            "control_constraints.holding_upper_bound": FieldResult(value="60 s", status="explicit"),
        }
    )
    issues = bus_validate(instance)
    assert isinstance(issues, list)
    assert all(isinstance(issue, ValidationIssue) for issue in issues)


def test_validator_triggers_scheduling_with_holding_bound():
    instance = _bus_instance(
        {
            "research_problem.control_type": FieldResult(value="scheduling", status="explicit"),
            "control_constraints.holding_upper_bound": FieldResult(value="60 s", status="explicit"),
        }
    )
    issues = bus_validate(instance)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.type == "cross_field_consistency"
    assert issue.severity == "warning"
    assert issue.action == "recheck"
    assert issue.fields == [
        "research_problem.control_type",
        "control_constraints.holding_upper_bound",
    ]


def test_validator_triggers_sarl_with_multi_agent_definition():
    instance = _bus_instance(
        {
            "rl_formulation.rl_paradigm": FieldResult(value="SARL", status="explicit"),
            "rl_formulation.agent_definition": FieldResult(
                value="multiple independently acting buses", status="explicit"
            ),
        }
    )
    issues = bus_validate(instance)
    assert len(issues) == 1
    assert issues[0].fields == [
        "rl_formulation.rl_paradigm",
        "rl_formulation.agent_definition",
    ]


def test_validator_triggers_speed_control_with_holding_bound():
    instance = _bus_instance(
        {
            "research_problem.control_type": FieldResult(
                value="speed control", status="explicit"
            ),
            "control_constraints.holding_upper_bound": FieldResult(
                value="60 s", status="explicit"
            ),
        }
    )
    issues = bus_validate(instance)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.type == "cross_field_consistency"
    assert issue.severity == "warning"
    assert issue.action == "recheck"
    assert issue.fields == [
        "research_problem.control_type",
        "control_constraints.holding_upper_bound",
        "control_constraints.holding_lower_bound",
    ]


def test_validator_triggers_stop_skipping_with_holding_lower_bound():
    instance = _bus_instance(
        {
            "research_problem.control_type": FieldResult(
                value="Stop Skipping", status="explicit"
            ),
            "control_constraints.holding_lower_bound": FieldResult(
                value="0 s", status="explicit"
            ),
        }
    )
    issues = bus_validate(instance)
    assert len(issues) == 1
    assert issues[0].type == "cross_field_consistency"
    assert issues[0].action == "recheck"
    assert issues[0].fields == [
        "research_problem.control_type",
        "control_constraints.holding_upper_bound",
        "control_constraints.holding_lower_bound",
    ]


def test_validator_triggers_sarl_with_decentralized_execution():
    instance = _bus_instance(
        {
            "rl_formulation.rl_paradigm": FieldResult(
                value="SARL", status="explicit"
            ),
            "rl_formulation.centralized_or_decentralized": FieldResult(
                value="decentralized", status="explicit"
            ),
        }
    )
    issues = bus_validate(instance)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.type == "cross_field_consistency"
    assert issue.severity == "warning"
    assert issue.action == "recheck"
    assert issue.fields == [
        "rl_formulation.rl_paradigm",
        "rl_formulation.centralized_or_decentralized",
    ]


def test_validator_benign_instance_returns_empty_list():
    instance = _bus_instance(
        {
            "research_problem.control_type": FieldResult(value="holding", status="explicit"),
            "control_constraints.holding_upper_bound": FieldResult(value="60 s", status="explicit"),
            "rl_formulation.rl_paradigm": FieldResult(value="MARL", status="explicit"),
            "rl_formulation.agent_definition": FieldResult(
                value="each bus runs a local policy", status="explicit"
            ),
        }
    )
    assert bus_validate(instance) == []


def test_validator_is_deterministic():
    instance = _bus_instance(
        {
            "research_problem.control_type": FieldResult(value="scheduling", status="explicit"),
            "control_constraints.holding_upper_bound": FieldResult(value="60 s", status="explicit"),
        }
    )
    first = bus_validate(instance)
    second = bus_validate(instance)
    assert [i.model_dump() for i in first] == [i.model_dump() for i in second]


def test_validator_is_read_only():
    instance = _bus_instance(
        {
            "research_problem.control_type": FieldResult(value="scheduling", status="explicit"),
            "control_constraints.holding_upper_bound": FieldResult(value="60 s", status="explicit"),
        }
    )
    before = copy.deepcopy(instance)
    bus_validate(instance)
    assert instance.model_dump() == before.model_dump()
