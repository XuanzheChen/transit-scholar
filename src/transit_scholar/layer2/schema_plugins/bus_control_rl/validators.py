"""bus_control_rl cross-field validator entry point (FR-A-008, FR-C-005).

A deterministic, read-only callable that takes a ``SchemaInstance`` and
returns a list of ``ValidationIssue``. It never modifies the instance and
never performs network, LLM, or cross-paper inference.

Rules (Package A kept unchanged, Package C adds rules 3 and 4):

1. ``control_type`` mentions scheduling (not mixed/holding) while a holding
   upper bound is reported with an assertive status -> recheck whether the
   control type should be mixed.
2. ``rl_paradigm`` is SARL while ``agent_definition`` mentions multiple
   agents -> recheck whether MARL applies.
3. ``control_type`` asserts ``speed control`` or ``stop skipping`` (exact
   case-insensitive match) while a holding upper/lower bound is reported
   with an assertive status -> recheck whether the control type should
   include holding or mixed control.
4. ``rl_paradigm`` asserts SARL while ``centralized_or_decentralized``
   asserts ``decentralized`` -> recheck whether MARL or CTDE applies.
"""

from __future__ import annotations

from transit_scholar.layer2.schema_extraction import (
    FieldResult,
    SchemaInstance,
    ValidationIssue,
)

_MULTI_AGENT_CUES = ("multiple", "multi-agent", "each bus", "several")

_NO_HOLDING_CONTROL_TYPES = ("speed control", "stop skipping")


def _is_assertive(result: FieldResult | None) -> bool:
    return (
        result is not None
        and result.value not in (None, "")
        and result.status in ("explicit", "inferred")
    )


def validate(instance: SchemaInstance) -> list[ValidationIssue]:
    """Return cross-field consistency warnings for a bus_control_rl instance.

    Read-only: never modifies the instance. Deterministic: no LLM, no
    network, no randomness.
    """
    issues: list[ValidationIssue] = []
    fields = instance.fields

    control_type = fields.get("research_problem.control_type")
    holding_upper = fields.get("control_constraints.holding_upper_bound")
    if (
        control_type is not None
        and control_type.value not in (None, "")
        and holding_upper is not None
        and holding_upper.value not in (None, "")
        and holding_upper.status in ("explicit", "inferred")
    ):
        control_text = str(control_type.value).lower()
        if (
            "scheduling" in control_text
            and "mixed" not in control_text
            and "holding" not in control_text
        ):
            issues.append(
                ValidationIssue(
                    type="cross_field_consistency",
                    severity="warning",
                    message=(
                        f"control_type is {control_type.value!r} but a holding upper "
                        f"bound ({holding_upper.value!r}) is reported; consider "
                        f"whether mixed control applies"
                    ),
                    fields=[
                        "research_problem.control_type",
                        "control_constraints.holding_upper_bound",
                    ],
                    action="recheck",
                )
            )

    rl_paradigm = fields.get("rl_formulation.rl_paradigm")
    agent_definition = fields.get("rl_formulation.agent_definition")
    if (
        rl_paradigm is not None
        and rl_paradigm.value not in (None, "")
        and str(rl_paradigm.value).strip().upper() == "SARL"
        and agent_definition is not None
        and agent_definition.value not in (None, "")
        and any(
            cue in str(agent_definition.value).lower() for cue in _MULTI_AGENT_CUES
        )
    ):
        issues.append(
            ValidationIssue(
                type="cross_field_consistency",
                severity="warning",
                message=(
                    f"rl_paradigm is SARL but agent_definition "
                    f"({agent_definition.value!r}) mentions multiple agents; "
                    f"verify whether MARL applies"
                ),
                fields=[
                    "rl_formulation.rl_paradigm",
                    "rl_formulation.agent_definition",
                ],
                action="recheck",
            )
        )

    # Rule 3 (Package C): a holding-free control type reported together
    # with assertive holding bounds.
    holding_lower = fields.get("control_constraints.holding_lower_bound")
    if (
        control_type is not None
        and str(control_type.value).strip().lower() in _NO_HOLDING_CONTROL_TYPES
        and (_is_assertive(holding_upper) or _is_assertive(holding_lower))
    ):
        issues.append(
            ValidationIssue(
                type="cross_field_consistency",
                severity="warning",
                message=(
                    f"control_type is {control_type.value!r} but holding bounds "
                    f"are reported; verify whether the control type should "
                    f"include holding or mixed control"
                ),
                fields=[
                    "research_problem.control_type",
                    "control_constraints.holding_upper_bound",
                    "control_constraints.holding_lower_bound",
                ],
                action="recheck",
            )
        )

    # Rule 4 (Package C): SARL reported together with decentralized
    # execution.
    centralized = fields.get("rl_formulation.centralized_or_decentralized")
    if (
        rl_paradigm is not None
        and str(rl_paradigm.value).strip().upper() == "SARL"
        and centralized is not None
        and str(centralized.value).strip().lower() == "decentralized"
    ):
        issues.append(
            ValidationIssue(
                type="cross_field_consistency",
                severity="warning",
                message=(
                    f"rl_paradigm is SARL but execution is reported as "
                    f"decentralized; verify whether MARL or CTDE applies"
                ),
                fields=[
                    "rl_formulation.rl_paradigm",
                    "rl_formulation.centralized_or_decentralized",
                ],
                action="recheck",
            )
        )

    return issues
