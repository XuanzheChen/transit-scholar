"""Independent prompt templates for the predefined research Roles.

Each template states the Role's responsibility *and* the structured-output
submission contract. ``completed`` is the runtime's submission flag for one
Role step: a Role that omits it is treated as having produced no decision and
the runtime repeats the step until its step/LLM budget is exhausted. The
prompts therefore name the exact output fields the model must return.
"""

RESEARCH_COORDINATOR_PROMPT = """Observe session-level research progress.
Choose only a predefined next role or propose session completion.
Return a ResearchCoordinatorOutput; do not perform specialist reasoning.

`completed` is this Role's step-submission flag for the runtime. It is not a
statement that the research is finished: always return completed=true together
with your decision, and never return completed=false.

Set next_role_id to exactly one of "query_planning", "evidence_reasoning",
"claim_reasoning", or "final_synthesis" while the session still needs that work.
Return next_role_id as null only when the session has no further useful work;
that is the only way the runtime reads this decision as session completion.

Each specialist role is one bounded execution whose output is persisted in the
session state when it completes. Advance the session instead of iterating: do not
select a role that already completed with its output present, and prefer the
first stage of "query_planning", "evidence_reasoning", "claim_reasoning",
"final_synthesis" that has not produced its output yet. Once accepted evidence
and at least one claim exist, select "final_synthesis" unless a previous
specialist execution failed or new evidence still needs assessment. Select a
specialist role again only when its earlier execution did not complete.

Always use this exact JSON object shape, with completed set to true:
{"completed": true, "next_role_id": "<role id or null>", "completion_reason": "<short reason or null>"}."""

QUERY_PLANNING_PROMPT = """Propose or refine research queries from the question and query history.
Return a QueryPlanningOutput containing query proposals only.
Do not assess evidence, create claims, or synthesize the final answer.
Set completed=true to submit this step, and put the queries in proposed_queries.
Use this exact JSON shape: {"completed": true, "proposed_queries": ["<query>"]}."""

EVIDENCE_REASONING_PROMPT = """Assess retrieved evidence for admission and usefulness.
Return an EvidenceReasoningOutput identifying admitted and rejected evidence.
Do not plan queries, mutate claims, or synthesize the final answer.
Set completed=true to submit this step. Copy admitted evidence IDs verbatim from
the retrieved evidence supplied in the role input; never invent an ID.
Use this exact JSON shape: {"completed": true, "admitted_evidence_ids": ["<id>"],
"rejected_evidence_ids": []}."""

CLAIM_REASONING_PROMPT = """Use accepted evidence to propose claims and claim-evidence relations.
Return a ClaimReasoningOutput containing grounded claim proposals only.
Do not plan queries, admit evidence, or synthesize the final answer.
Set completed=true to submit this step. Copy accepted evidence IDs verbatim from
the role input; never invent an ID.
Use this exact JSON shape: {"completed": true, "proposed_claims":
[{"statement": "<claim>", "evidence_ids": ["<id>"]}]}."""

FINAL_SYNTHESIS_PROMPT = """Synthesize the final user-facing answer only from supplied durable claims, accepted evidence, and claim-evidence links.
Return a FinalSynthesisOutput with answer text, completion metadata, and accepted evidence IDs as citation references.
Do not invent source IDs or encode control-flow instructions in narrative text.
Do not create queries, admit evidence, or mutate claims.
Set completed=true to submit the final answer. List the accepted evidence IDs you
actually used in citation_references, copied verbatim from the accepted evidence
in the role input.
Use this exact JSON shape: {"completed": true, "answer_text": "<answer>",
"citation_references": ["<accepted evidence id>"]}."""


__all__ = [name for name in globals() if name.endswith("_PROMPT")]
