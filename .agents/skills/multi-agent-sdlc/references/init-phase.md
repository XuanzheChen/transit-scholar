# Init Phase

Init binds Workflow Mode to real local executors and a task storage location.

Required checks:

1. Verify the repository is a valid Git worktree.
2. Discover local agent CLIs with `scripts/discover_agents.py` when config is missing.
3. Ask the user to select Generator and Evaluator executors.
4. Ask where `tasks_root` should live.
5. Write `.agentic-sdlc/config.yaml` and `.agentic-sdlc/init.json`.
6. Add repository-local `tasks_root` to `.gitignore` with `scripts/ensure_gitignore.py`.

Do not use Planner, local subagents, Codex background threads, or another model as a substitute for the selected G/E executors. If the executor is unavailable, set state to `blocked`.

If a task needs a destructive database or production data operation, stop and request a separate approval immediately before that operation.
