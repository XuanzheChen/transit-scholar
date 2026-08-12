---
name: multi-agent-sdlc
description: Run the simplified agentic SDLC workflow for complex software development tasks with Planner, Generator, Evaluator, and deterministic Runner responsibilities. Use when the user explicitly starts Workflow Mode for a complex feature, requirements-to-code task, acceptance-design task, independent validation task, or bounded implementation/evaluation loop. Do not use for ordinary small coding fixes unless the user asks for Workflow Mode.
---

# Simplified Agentic SDLC

## Core Rule

Default to Direct Mode unless the user explicitly asks to start Workflow Mode. In Direct Mode, handle small code, docs, cleanup, and bug-fix requests directly.

Use Workflow Mode only for complex tasks that benefit from approved requirements, frozen acceptance criteria, one coarse implementation package, independent validation, bounded revision, and final Planner delivery.

## Operating Model

- Planner (P): user-facing Codex task. Own requirements, user approval, acceptance review, G plan review, scope decisions, exception handling, lightweight final closure, and final delivery. P does not run routine tests, manually relay G/E messages, repair output formatting, edit product code in Workflow Mode, or repeat E's technical acceptance after E accepts.
- Generator (G): implementation worker. First writes a user-facing implementation plan for P review. After P approves the plan and the user approves it, G reads approved requirements, frozen acceptance criteria, approved implementation plan, and the execution contract; then implements the complete package, writes or updates tests, runs self-tests, self-repairs within the same invocation, and reports facts. G does not modify requirements, acceptance criteria, workflow state, reviews, or workflow config.
- Evaluator (E): independent validator. Before implementation, E drafts executable acceptance criteria for P review. After implementation, E reviews diff and runs formal validation in a fresh context without product write permission. E returns `accept`, `needs_revision`, or `blocked`.
- Runner: deterministic control program, not an LLM Agent. Starts configured local agents, captures raw output, parses status headers, writes `status.json`, compiles contracts, checks path permissions, records Git evidence, transfers G output to E, enforces revision limits, and pauses for P only when semantic or product decisions are needed.

Read `references/workflow-protocol.md` as the canonical prose workflow and `references/state-machine.md` as the canonical machine-state workflow. Read `references/role-contracts.md` for role boundaries.

## Init And Safety Gates

Before starting Workflow Mode:

1. Verify a valid Git worktree. If Git is invalid, stop before invoking G/E. Never copy the project into a staging directory as fallback.
2. Load or create `.agentic-sdlc/config.yaml`.
3. Discover local callable agent CLIs when config is missing or stale.
4. Ask the user to select real G/E executors and the task directory.
5. Add the task directory to `.gitignore` when it is repository-local.
6. Record init choices in `.agentic-sdlc/init.json`.

Never simulate G/E with Planner, local subagents, background Codex threads, or ordinary assistant text. If the configured executor cannot be invoked, move to `blocked`.

Read `references/init-phase.md` for the init contract.

## Standard Workflow

Use this phase sequence:

1. `requirements_draft`: P discusses the user goal and writes complete `requirements.md`.
2. `requirements_user_review`: the user explicitly approves requirements.
3. `acceptance_design`: Runner invokes E to draft executable acceptance criteria.
4. `acceptance_planner_review`: P checks whether E's acceptance criteria faithfully reflect approved requirements. P may approve acceptance directly when no new product decision is introduced; user approval is required only for new or changed product decisions.
5. `generator_plan`: Runner invokes G to write an implementation plan, including intended files, technical approach, core logic, test strategy, risks, and why this approach satisfies the frozen acceptance criteria. G does not edit product code in this phase.
6. `generator_plan_planner_review`: P reviews G's implementation plan for scope, feasibility, and fidelity to requirements and acceptance. If P approves, P presents the plan to the user.
7. `generator_plan_user_review`: the user approves the G implementation plan before code implementation starts.
8. `implementation`: Runner invokes G for one complete implementation package. G may test and self-repair within this invocation.
9. `evaluation`: Runner invokes E in a fresh independent context for formal validation.
10. `revision`: only when E returns `needs_revision`; Runner sends targeted blocking issues to G, then returns to `evaluation`.
11. `final_planner_review`: after E returns `accept`, P performs only a lightweight closure check: confirm E really accepted, blocking count is zero, required task artifacts exist, required evidence is present, and there is no obvious permission or scope violation. P writes `summary.md` and does not run a second technical acceptance review unless E evidence is missing, inconsistent, or the user explicitly asks.
12. `done` or `blocked`.

Do not split a user goal into micro-slices. Split into at most three complete packages only when there is an independent approval, migration/deployment boundary, context/time limit, independently verifiable package, or high-risk core behavior that must be confirmed first.

## Deterministic Runner Rules

Runner and adapters must handle these mechanics without asking Planner to interpret logs:

- Adapter must save raw stdout/stderr, extract the final Markdown response from the external CLI response field when available, write it verbatim to `result.md`, parse the stable status header into `status.json`, and mark `invalid_output` if parsing fails.
- Contract Compiler must build allowed write paths from approved scope, G plan files, acceptance-mentioned files, and deterministic test-impact expansion. If G needs an unapproved file, G must stop with `CONTRACT_UPDATE_REQUIRED` before editing it.
- E should not depend on an interactive shell permission loop. E proposes validation commands; Runner executes approved commands with `scripts/run_validation.py`, saves stdout/stderr/exit codes, and E reviews those artifacts.
- Workflow edits must run `scripts/workflow_lint.py` before delivery to catch stale phase paths, approval rules, and final-review wording across SKILL.md, references, roles, templates, and config.

## Output Protocol

G/E outputs are Markdown with a minimal stable status header. Do not require full JSON output.

G header:

```text
STATUS: completed
NEXT: evaluator
BLOCKING: false
```

E header:

```text
DECISION: accept
BLOCKING_COUNT: 0
REQUIRES_USER_DECISION: false
```

Adapter code must save raw output, write `result.md`, parse the header deterministically, and write `status.json`. Do not invoke another model just to reformat JSON or Markdown. If deterministic parsing fails, mark `invalid_output` and pause.

## Task Artifacts

Keep only core artifacts:

```text
tasks/{task_id}/
  requirements.md
  acceptance.md
  execution-contract.yaml
  state.json
  generator/
    plan.md
    result.md
    status.json
    self-test.log
  evaluator/
    result.md
    status.json
    acceptance.log
  revisions/
  evidence/
    changes.diff
    permission.json
    protected-data.json
    validation-summary.json
  summary.md
```

Do not create a new round directory for pure format retries, transfers, or state updates. Create `revisions/` only for actual code revision cycles.

For artifact details, read `references/artifact-schema.md`.

## Permission And Git Guard

Contract Compiler must generate `execution-contract.yaml` from templates and approved scope. Before invocation, check:

```text
write_paths intersect forbidden_paths = empty
```

For each invocation, record these dimensions independently:

```text
path_compliance: valid | invalid_paths
output_protocol: valid | invalid_output | invalid_schema
invocation_evidence: verified | missing | mismatched
```

Do not collapse them into one vague validity flag. Formatting failure does not automatically invalidate path-compliant code; path violations must be precisely restored.

Before each G/E call, capture the current worktree in a temporary Git tree. After the call, archive a binary Git diff and restore only paths that violate the round contract. The captured tree must include pre-existing user changes.

## Revision Limits

Default limits:

- one initial G implementation
- one initial G implementation plan, with P review and user approval before code
- one initial E validation
- at most two targeted G revisions
- at most two targeted E re-validations
- no pure serialization model rounds
- no P-driven slice-by-slice testing
- no split into more than three packages without approval

When limits are exceeded, move to `blocked` and wake P with a short exception summary.

## Bundled Resources

- `assets/default-config.yaml`: project workflow config template.
- `assets/roles/`: role prompt templates.
- `assets/templates/`: task artifact and contract templates.
- `references/`: phase, role, artifact, state, init, and guard details.
- `scripts/`: deterministic helpers for init, task creation, CLI invocation, status parsing, contract compilation, and Git guard checks.
