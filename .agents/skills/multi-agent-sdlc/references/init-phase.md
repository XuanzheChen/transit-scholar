# Init Phase

Init binds Workflow Mode to real local executors and a task storage location.

Required checks:

1. Verify the repository is a valid Git worktree.
2. Discover local agent CLIs with `scripts/discover_agents.py` when config is missing.
3. Ask the user to select Generator and Evaluator executors and models.
4. Ask the user to select the reasoning effort/model variant independently for Generator and Evaluator. Do not infer or silently inherit it from a previous task.
5. Ask where `tasks_root` should live.
6. Write executor, model and reasoning effort/variant to both `.agentic-sdlc/config.yaml` and `.agentic-sdlc/init.json`.
7. Ensure the configured invocation adapter passes the selected effort to the external CLI and records configured and actual effort in execution evidence.
8. Add repository-local `tasks_root` to `.gitignore` with `scripts/ensure_gitignore.py`.

OpenCode/OpenCode Go rule:

- If Generator or Evaluator uses OpenCode/OpenCode Go, record
  `requires_unsandboxed: true` for that role in both config and init records.
- Invoke the adapter outside the Codex filesystem sandbox so it inherits the
  user's real terminal environment, credentials, opt-in state, and OpenCode home
  config.
- Do not override `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, OpenCode config paths, or
  cache paths in the adapter unless the user explicitly requests it.
- If a sandboxed OpenCode call fails with a config-directory or opt-in error, do
  not change model or retry inside the sandbox; rerun the same adapter command
  unsandboxed.

Before each Workflow Mode task, compare `config.yaml` with `init.json`. Executor, model, model binding, reasoning effort/variant, adapter and task root must agree. Any mismatch makes the init record stale and must be resynchronized before G/E invocation.

Run the deterministic check from the repository root:

```text
python .agents/skills/multi-agent-sdlc/scripts/check_init_consistency.py
```

Do not invoke G/E unless it exits successfully.

Do not use Planner, local subagents, Codex background threads, or another model as a substitute for the selected G/E executors. If the executor is unavailable, set state to `blocked`.

If a task needs a destructive database or production data operation, stop and request a separate approval immediately before that operation.
