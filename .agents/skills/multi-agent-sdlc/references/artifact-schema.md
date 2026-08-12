# Artifact Schema

Use this compact task layout:

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

`summary.md` is created only after E accepts and Planner completes the lightweight final closure check.

Do not create round directories for format retries, transfers, or state updates. Create `revisions/` only when E returns `needs_revision` and actual code revision is needed.

`result.md` is always the worker's final Markdown response as extracted by the adapter, not a Planner rewrite. `status.json` is always parsed deterministically from `result.md`. If parsing fails, `status.json` must record `output_protocol: invalid_output`.

Validation command output belongs under `evidence/` or a round-local evidence directory with a `validation-summary.json` index and per-command stdout/stderr logs.
