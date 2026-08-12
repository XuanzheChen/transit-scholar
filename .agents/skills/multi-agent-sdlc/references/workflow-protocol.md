# Workflow Protocol

## Phases

1. Requirements: P writes `requirements.md`; user approval is required.
2. Acceptance design: Runner invokes E; E drafts `acceptance.md` with executable criteria.
3. Planner acceptance review: P checks criteria against requirements and freezes them. P approval is sufficient when E added no new product decision; user approval is required only for new or changed product decisions.
4. Generator plan: Runner invokes G to write an implementation plan without editing product code.
5. Planner plan review: P reviews G's implementation plan for scope, feasibility, and fidelity to requirements and acceptance.
6. User plan review: after P approves, the user approves G's implementation plan.
7. Implementation: Runner invokes G once for the complete package.
8. Evaluation: Runner automatically invokes E after compliant G completion.
9. Revision: only when E returns `needs_revision`; at most two cycles.
10. Final review: after E returns `accept`, P performs only a lightweight closure check: E really accepted, blocker count is zero, artifacts and evidence exist, and there is no obvious permission or scope violation. P then writes `summary.md` and does not repeat E's technical acceptance unless evidence is missing, contradictory, or the user explicitly asks.

## Automation Boundary

Runner, Adapter, Contract Compiler, and Git Guard handle mechanical work. Do not ask P or another model to perform deterministic parsing, format rewriting, Git diff capture, permission set conflict checks, or G/E transfer.

Adapter saves raw stdout/stderr, extracts the final Markdown response, writes `result.md`, parses the status header, and writes `status.json`. A parse failure becomes `invalid_output`; P must not repair or reinterpret it.

Contract Compiler combines approved scope, G plan files, acceptance-mentioned files, and deterministic test-impact expansion. If G needs an unapproved file, G must stop with `CONTRACT_UPDATE_REQUIRED`.

E proposes validation commands; Runner executes approved commands with the unified command runner and stores stdout/stderr/exit code evidence. E reviews those artifacts instead of relying on an interactive shell permission loop.

Workflow changes must pass the workflow lint script before delivery.

## No Format-Only Model Retry

If status headers are missing but facts are otherwise present, Adapter may extract status by deterministic rules. If it cannot, mark `invalid_output` and pause. Do not invoke a model just to reserialize output.
