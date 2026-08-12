# State Machine

States:

```text
requirements_draft
requirements_user_review
acceptance_design
acceptance_planner_review
generator_plan
generator_plan_planner_review
generator_plan_user_review
implementation
evaluation
revision
final_planner_review
done
blocked
```

Normal path:

```text
requirements_draft -> requirements_user_review -> acceptance_design -> acceptance_planner_review -> generator_plan -> generator_plan_planner_review -> generator_plan_user_review -> implementation -> evaluation -> final_planner_review -> done
```

Only E's `needs_revision` decision enters `revision`; after targeted G repair, return directly to `evaluation`.

Acceptance criteria are reviewed by Planner after E drafts them. User approval is needed only if the acceptance criteria introduce a new or changed product decision.

G must write an implementation plan before editing product code. Planner reviews it first, then the user approves it. Only after both approvals may G implement.

After E returns `accept`, Planner's final review is a lightweight closure check only: E accepted, blocker count is zero, artifacts and evidence exist, and no obvious permission or scope violation is present. Planner then writes `summary.md` and does not repeat E's technical acceptance unless evidence is missing, contradictory, or the user explicitly asks.

Any workflow rule change must pass `scripts/workflow_lint.py` before delivery.

Move to `blocked` when Git is invalid, executor invocation fails persistently, permissions conflict, destructive approval is missing, requirements are ambiguous, revision limits are exceeded, or G/E disagree materially about frozen acceptance criteria.
