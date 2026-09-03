"""Design-time fixed built-in research Role definitions."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer3.agent.models import (
    ClaimReasoningInput,
    ClaimReasoningOutput,
    ContextPolicy,
    EvidenceReasoningInput,
    EvidenceReasoningOutput,
    FinalSynthesisInput,
    FinalSynthesisOutput,
    FinalResponseArtifact,
    FinalSourceReference,
    QueryPlanningInput,
    QueryPlanningOutput,
    ResearchCoordinatorInput,
    ResearchCoordinatorOutput,
    RoleDefinition,
    RoleId,
    RoleRuntimeProfile,
)
from transit_scholar.layer3.prompts.builtin_roles import (
    CLAIM_REASONING_PROMPT,
    EVIDENCE_REASONING_PROMPT,
    FINAL_SYNTHESIS_PROMPT,
    QUERY_PLANNING_PROMPT,
    RESEARCH_COORDINATOR_PROMPT,
)


class BuiltinRoleRuntimeConfig(BaseModel):
    """External behavioral configuration for every built-in Role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    research_coordinator: RoleRuntimeProfile = Field(
        default_factory=lambda: RoleRuntimeProfile(max_steps=3, max_llm_calls=3)
    )
    query_planning: RoleRuntimeProfile = Field(default_factory=RoleRuntimeProfile)
    evidence_reasoning: RoleRuntimeProfile = Field(default_factory=RoleRuntimeProfile)
    claim_reasoning: RoleRuntimeProfile = Field(
        default_factory=lambda: RoleRuntimeProfile(max_steps=2, max_llm_calls=2)
    )
    final_synthesis: RoleRuntimeProfile = Field(default_factory=RoleRuntimeProfile)

    def for_role(self, role_id: RoleId) -> RoleRuntimeProfile:
        return getattr(self, role_id.value)

    @classmethod
    def with_overrides(
        cls,
        overrides: Mapping[RoleId | str, RoleRuntimeProfile | Mapping[str, object]] | None,
    ) -> "BuiltinRoleRuntimeConfig":
        values = {
            (key.value if isinstance(key, RoleId) else key): value
            for key, value in (overrides or {}).items()
        }
        return cls.model_validate(values)


class ResearchCoordinatorRole(RoleDefinition):
    def __init__(self, runtime_profile: RoleRuntimeProfile | None = None) -> None:
        super().__init__(
            role_id=RoleId.RESEARCH_COORDINATOR,
            description="Observe session progress and choose the next predefined responsibility.",
            prompt_template=RESEARCH_COORDINATOR_PROMPT,
            context_policy=ContextPolicy(
                included_sections={
                    "session",
                    "session_handoff",
                    "queries",
                    "retrieved_evidence",
                    "accepted_evidence",
                    "claims",
                }
            ),
            input_contract=ResearchCoordinatorInput,
            output_contract=ResearchCoordinatorOutput,
            allowed_actions={"INVOKE_ROLE", "FINISH_SESSION"},
            allowed_tools=set(),
            runtime_profile=runtime_profile or BuiltinRoleRuntimeConfig().research_coordinator,
        )


class QueryPlanningRole(RoleDefinition):
    def __init__(self, runtime_profile: RoleRuntimeProfile | None = None) -> None:
        super().__init__(
            role_id=RoleId.QUERY_PLANNING,
            description="Propose and refine queries from the current research state.",
            prompt_template=QUERY_PLANNING_PROMPT,
            context_policy=ContextPolicy(
                included_sections={
                    "session",
                    "queries",
                    "session_handoff",
                    "episodic_memory",
                }
            ),
            input_contract=QueryPlanningInput,
            output_contract=QueryPlanningOutput,
            allowed_actions={"CREATE_QUERY", "UPDATE_QUERY"},
            allowed_tools=set(),
            runtime_profile=runtime_profile or BuiltinRoleRuntimeConfig().query_planning,
        )


class EvidenceReasoningRole(RoleDefinition):
    def __init__(self, runtime_profile: RoleRuntimeProfile | None = None) -> None:
        super().__init__(
            role_id=RoleId.EVIDENCE_REASONING,
            description="Assess retrieved evidence for admission and usefulness.",
            prompt_template=EVIDENCE_REASONING_PROMPT,
            context_policy=ContextPolicy(
                included_sections={"session", "queries", "retrieved_evidence"}
            ),
            input_contract=EvidenceReasoningInput,
            output_contract=EvidenceReasoningOutput,
            allowed_actions={"ADMIT_EVIDENCE"},
            allowed_tools=set(),
            runtime_profile=runtime_profile or BuiltinRoleRuntimeConfig().evidence_reasoning,
        )


class ClaimReasoningRole(RoleDefinition):
    def __init__(self, runtime_profile: RoleRuntimeProfile | None = None) -> None:
        super().__init__(
            role_id=RoleId.CLAIM_REASONING,
            description="Propose claims and claim-evidence relations from accepted evidence.",
            prompt_template=CLAIM_REASONING_PROMPT,
            context_policy=ContextPolicy(
                included_sections={"session", "accepted_evidence", "claims"}
            ),
            input_contract=ClaimReasoningInput,
            output_contract=ClaimReasoningOutput,
            allowed_actions={"CREATE_CLAIM", "UPDATE_CLAIM", "LINK_EVIDENCE"},
            allowed_tools=set(),
            runtime_profile=runtime_profile or BuiltinRoleRuntimeConfig().claim_reasoning,
        )


class FinalSynthesisRole(RoleDefinition):
    def __init__(self, runtime_profile: RoleRuntimeProfile | None = None) -> None:
        super().__init__(
            role_id=RoleId.FINAL_SYNTHESIS,
            description="Synthesize a cited final response from durable research state.",
            prompt_template=FINAL_SYNTHESIS_PROMPT,
            context_policy=ContextPolicy(
                included_sections={
                    "session",
                    "accepted_evidence",
                    "claims",
                    "claim_evidence_links",
                }
            ),
            input_contract=FinalSynthesisInput,
            output_contract=FinalSynthesisOutput,
            allowed_actions={"FINISH_SESSION"},
            allowed_tools=set(),
            runtime_profile=runtime_profile or BuiltinRoleRuntimeConfig().final_synthesis,
        )

    @staticmethod
    def finalize(
        role_input: FinalSynthesisInput | dict[str, object],
        output: FinalSynthesisOutput | dict[str, object],
    ) -> FinalResponseArtifact:
        """Validate a structured decision and enrich its citations from durable state."""
        synthesis_input = FinalSynthesisInput.model_validate(role_input)
        decision = FinalSynthesisOutput.model_validate(output)
        evidence_by_id = {
            evidence.evidence_id: evidence for evidence in synthesis_input.accepted_evidence
        }
        unknown = set(decision.citation_references) - evidence_by_id.keys()
        if unknown:
            raise ValueError(
                f"final citations reference unknown evidence: {', '.join(sorted(unknown))}"
            )
        claims_by_evidence: dict[str, set[str]] = {}
        for link in synthesis_input.claim_evidence_links:
            claims_by_evidence.setdefault(link.evidence_id, set()).add(link.claim_id)
        sources = [
            FinalSourceReference(
                evidence_id=evidence_id,
                claim_ids=tuple(sorted(claims_by_evidence.get(evidence_id, set()))),
                locator=evidence_by_id[evidence_id].locator,
                source_metadata=evidence_by_id[evidence_id].source_metadata,
                retrieval_provenance=evidence_by_id[evidence_id].retrieval_provenance,
            )
            for evidence_id in decision.citation_references
        ]
        return FinalResponseArtifact(
            **decision.model_dump(exclude={"source_references"}),
            source_references=sources,
        )


def built_in_roles(config: BuiltinRoleRuntimeConfig | None = None) -> tuple[RoleDefinition, ...]:
    profiles = config or BuiltinRoleRuntimeConfig()
    return (
        ResearchCoordinatorRole(profiles.research_coordinator),
        QueryPlanningRole(profiles.query_planning),
        EvidenceReasoningRole(profiles.evidence_reasoning),
        ClaimReasoningRole(profiles.claim_reasoning),
        FinalSynthesisRole(profiles.final_synthesis),
    )


__all__ = [
    "BuiltinRoleRuntimeConfig",
    "ClaimReasoningRole",
    "EvidenceReasoningRole",
    "FinalSynthesisRole",
    "QueryPlanningRole",
    "ResearchCoordinatorRole",
    "built_in_roles",
]
