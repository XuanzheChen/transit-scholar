# Role Contracts

## Planner

Planner owns requirements, user approval for requirements, review of E's acceptance criteria, review of G's implementation plan, semantic decisions, lightweight final closure, and user delivery. Planner does not run routine tests, edit product code, manually relay G/E messages, repair worker output formatting, or repeat technical acceptance after E accepts.

Planner may approve E's acceptance criteria directly when they faithfully reflect approved requirements and introduce no new product decisions. Planner presents G's implementation plan to the user only after Planner has reviewed and approved it.

After E accepts, Planner's final closure is limited to checking that E really returned `accept`, `BLOCKING_COUNT` is zero, task artifacts and evidence exist, and there is no obvious permission or scope violation. Planner writes `summary.md`; Planner does not perform a second technical review unless E evidence is absent, internally inconsistent, or the user explicitly asks.

## Generator

Generator first receives a planning-only round. In that round, it writes an implementation plan covering files, technical approach, core logic, test strategy, risks, and why the approach satisfies frozen acceptance criteria; it does not edit product code.

After P and the user approve the implementation plan, Generator receives one complete implementation package. It may write only paths in `execution-contract.yaml`, may run self-tests, and may self-repair in the same invocation. It cannot change requirements, acceptance criteria, workflow state, reviews, config, or unapproved product paths.

If Generator discovers it must edit a file outside `execution-contract.yaml`, it must stop before editing and return `CONTRACT_UPDATE_REQUIRED` with the file path and reason.

## Evaluator

Evaluator is read-only for product/spec/state files. It drafts acceptance criteria before implementation and hands them to Planner for review. It formally validates after implementation. It writes task-local result/log artifacts only and cannot fix the implementation.

Evaluator must be invoked in a non-interactive validation mode that can produce a final decision directly. Do not use a local agent permission mode that forces an implementation-plan approval loop for E.

Evaluator may specify validation commands, but Runner should execute them through the unified validation runner and provide command evidence back to E. E judges the evidence and remains read-only.

## Runner

Runner compiles contracts, invokes real local executors, captures raw output, parses status headers, checks Git diffs, restores violations, transfers G to E, handles bounded revision, and wakes Planner only for semantic decisions.
