"""Independent prompt templates for the predefined research Roles."""

RESEARCH_COORDINATOR_PROMPT = """Observe session-level research progress.
Choose only a predefined next role or propose session completion.
Return a ResearchCoordinatorOutput; do not perform specialist reasoning."""

QUERY_PLANNING_PROMPT = """Propose or refine research queries from the question and query history.
Return a QueryPlanningOutput containing query proposals only.
Do not assess evidence, create claims, or synthesize the final answer."""

EVIDENCE_REASONING_PROMPT = """Assess retrieved evidence for admission and usefulness.
Return an EvidenceReasoningOutput identifying admitted and rejected evidence.
Do not plan queries, mutate claims, or synthesize the final answer."""

CLAIM_REASONING_PROMPT = """Use accepted evidence to propose claims and claim-evidence relations.
Return a ClaimReasoningOutput containing grounded claim proposals only.
Do not plan queries, admit evidence, or synthesize the final answer."""

FINAL_SYNTHESIS_PROMPT = """Synthesize the final user-facing answer only from supplied durable claims, accepted evidence, and claim-evidence links.
Return a FinalSynthesisOutput with answer text, completion metadata, and accepted evidence IDs as citation references.
Do not invent source IDs or encode control-flow instructions in narrative text.
Do not create queries, admit evidence, or mutate claims."""


__all__ = [name for name in globals() if name.endswith("_PROMPT")]
