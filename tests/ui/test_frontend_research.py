"""Core Research Conversation and AgentRun UI behavior (T-003).

These tests pin the primary research path to the implementation:

* Conversation listing/creation and persisted turns use the frozen Conversation
  API; the frontend never rebuilds conversation context.
* Prompt submission uses the existing Conversation Turn endpoint and hands the
  returned AgentRun identifier to polling immediately.
* Run status and the Timeline are polled through the Run APIs, with the Timeline
  fetched incrementally through the API sequence cursor.
* The single primary control is derived from API Run state plus reported
  pause/resume capability, and Resume continues the same Run.
* The final answer and its API-provided citations are rendered on completion,
  with the Timeline collapsed by default, and a path to the local PDF.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "ui"
UI_SRC = UI_ROOT / "src"
FEATURES = UI_SRC / "features"

RESEARCH_VIEW = "features/conversations/ResearchView.tsx"
CONVERSATION_LIST = "features/conversations/ConversationList.tsx"
NEW_CONVERSATION = "features/conversations/NewConversationDialog.tsx"
CONVERSATION_TURNS = "features/conversations/ConversationTurns.tsx"
ANSWER_CITATIONS = "features/conversations/AnswerCitations.tsx"
PROMPT_COMPOSER = "features/conversations/PromptComposer.tsx"
RUN_LABELS = "features/runs/labels.ts"
RUN_MONITOR = "features/runs/useAgentRunMonitor.ts"
ACTIVE_RUN = "features/runs/ActiveRunPanel.tsx"
COMPLETED_TIMELINE = "features/runs/CompletedRunTimeline.tsx"
TIMELINE_EVENTS = "features/runs/TimelineEvents.tsx"


def _read(relative: str) -> str:
    return (UI_SRC / relative).read_text(encoding="utf-8")


def _package() -> dict:
    return json.loads((UI_ROOT / "package.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ conversations


def test_conversation_list_and_creation_use_the_conversation_api():
    view = _read(RESEARCH_VIEW)
    assert "api.conversations.list(" in view
    assert "api.conversations.read(" in view
    assert "ConversationList" in view

    listing = _read(CONVERSATION_LIST)
    assert 'data-testid="new-conversation-button"' in listing
    assert 'data-testid="conversation-list"' in listing
    assert "onNewConversation" in listing

    dialog = _read(NEW_CONVERSATION)
    assert "api.conversations.create(" in dialog
    assert 'testId="new-conversation-dialog"' in dialog
    assert 'data-testid="create-conversation-submit"' in dialog


def test_persisted_turns_render_from_the_conversation_api():
    view = _read(RESEARCH_VIEW)
    # Turns are read from the Conversation API response, not reconstructed.
    assert "conversation.data?.turns" in view

    turns = _read(CONVERSATION_TURNS)
    assert "turn.user_message" in turns
    assert "turn.final_answer" in turns
    assert "turn.answer_citations" in turns
    assert "turn.error_message" in turns
    assert 'data-testid="conversation-turns"' in turns
    assert 'data-testid="turn-final-answer"' in turns
    assert "AnswerCitations" in turns


def test_prompt_submission_uses_the_turn_endpoint_and_does_not_wait_for_the_run():
    composer = _read(PROMPT_COMPOSER)
    assert "api.conversations.submitTurn(" in composer
    assert 'data-testid="prompt-input"' in composer
    assert 'data-testid="research-primary-control"' in composer
    # The submission response (turn id + AgentRun id) is handed straight back to
    # the view; nothing awaits run completion.
    assert "onSubmitted(result.agent_run_id)" in composer
    assert "await api.runs" not in composer


# ------------------------------------------------------------------- runs


def test_run_monitoring_polls_status_and_incremental_timeline():
    monitor = _read(RUN_MONITOR)
    assert "api.runs.read(" in monitor
    assert "api.runs.timeline(" in monitor
    # Polling interval inside the recommended 500-1000 ms window.
    assert "RUN_POLL_INTERVAL_MS = 800" in monitor
    assert "window.setTimeout" in monitor
    assert "window.setInterval" not in monitor
    # Incremental Timeline fetch through the API sequence cursor.
    assert "cursorRef" in monitor
    assert "timeline.next_sequence" in monitor
    assert "after" in monitor
    # Polling stops on a terminal API status.
    assert "isTerminalRunStatus(state.status)" in monitor
    assert "settledRef.current = true" in monitor
    # Every poll owns a generation, and direct pause/resume API responses
    # invalidate older in-flight generations before applying their state.
    assert "requestGenerationRef" in monitor
    assert "requestGeneration === requestGenerationRef.current" in monitor
    assert "requestGenerationRef.current += 1" in monitor

    view = _read(RESEARCH_VIEW)
    assert "useAgentRunMonitor(" in view
    assert "turn.agent_run_id" in view
    assert "api.runs.read(" not in view, "run transport belongs to the monitor hook"


def test_run_monitor_keeps_transport_inside_the_api_module():
    for relative in (RUN_MONITOR, ACTIVE_RUN, COMPLETED_TIMELINE, RESEARCH_VIEW):
        text = _read(relative)
        assert "fetch(" not in text, f"{relative} bypasses the API module"


def test_timeline_is_expanded_while_active_and_collapsed_on_completion():
    active = _read(ACTIVE_RUN)
    assert 'data-testid="active-run"' in active
    assert 'testId="run-status"' in active
    assert 'testId="run-timeline"' in active
    # Expanded by default while the run is active, collapsed once settled.
    assert "useState(true)" in active
    assert "monitor.settled ? false : expandedWhileActive" in active


def test_settled_run_keeps_refreshing_until_the_persisted_outcome_lands():
    """The durable turn can be written after the run's terminal status.

    A real provider run publishes its terminal status before the post-run
    workspace promotion writes the turn's final answer, so a single reload at
    settle time observes a completed run with no answer yet. The view must keep
    reloading the persisted turn until it carries the run's outcome, and it must
    stay bounded.
    """
    view = _read(RESEARCH_VIEW)
    assert "onSettled: handleRunSettled" in view
    assert "SETTLE_REFRESH_INTERVAL_MS" in view
    assert "SETTLE_REFRESH_LIMIT" in view
    assert "window.setTimeout(refresh, SETTLE_REFRESH_INTERVAL_MS)" in view
    assert "attempts < SETTLE_REFRESH_LIMIT" in view
    # The stop condition is the API-derived persisted outcome, never a
    # frontend-invented completion.
    assert "turn.final_answer" in view
    assert "turn.status === 'failed'" in view
    assert "reloadConversation()" in view
    # The refresh loop is cleaned up when the view unmounts.
    assert "window.clearTimeout(settleTimerRef.current)" in view

    completed = _read(COMPLETED_TIMELINE)
    assert "useState(false)" in completed
    assert "poll: false" in completed
    assert 'testId={`turn-timeline-${runId}`}' in completed
    # A finished run's Timeline is only fetched when the reader opens it.
    assert "enabled: expanded" in completed


def test_timeline_renders_structured_api_events_only():
    events = _read(TIMELINE_EVENTS)
    assert "timelineKindLabel(event.kind)" in events
    assert "timelineEventSummary(event)" in events
    assert "timelineEventDetails(event)" in events
    assert 'data-testid="run-timeline-events"' in events
    # Raw event payloads are never dumped into the page.
    assert "JSON.stringify(event" not in events
    assert "JSON.stringify(monitor" not in events


# --------------------------------------------------------- primary control


def test_single_primary_control_follows_run_state_and_capability():
    labels = _read(RUN_LABELS)
    for kind in ("'send'", "'pause'", "'pausing'", "'resume'"):
        assert kind in labels, f"primary control state {kind} is missing"
    assert "pauseResumeAvailable" in labels
    assert "run.pause_requested" in labels
    assert "run.status === 'paused'" in labels
    assert "run.status === 'running'" in labels
    # Cooperative pause is explained, never described as a forced stop.
    assert "next safe point" in labels

    composer = _read(PROMPT_COMPOSER)
    assert "api.runs.pause(" in composer
    assert "api.runs.resume(" in composer
    assert "data-control={control.kind}" in composer
    # Pause and Resume target the identifier of the Run being monitored, so a
    # resume continues the same run instead of creating a replacement.
    assert "pause.run(runId)" in composer
    assert "resume.run(runId)" in composer

    view = _read(RESEARCH_VIEW)
    assert "capabilities?.pause_resume" in view
    assert "onRunState={monitor.applyState}" in view


# ----------------------------------------------------------------- answers


def test_final_answer_citations_render_from_the_api_citation_model():
    citations = _read(ANSWER_CITATIONS)
    assert "AnswerEvidenceCitation" in citations
    assert "citation.paper_title" in citations
    assert "citation.paper_id" in citations
    assert "citation.pages" in citations
    assert "citation.evidence_quote" in citations
    assert "citation.evidence_id" in citations
    assert "citation.source_kind" in citations
    assert 'data-testid="answer-citations"' in citations
    assert 'testId="citation-detail-dialog"' in citations
    # The first iteration uses the browser-native PDF viewer through the
    # existing file-content endpoint.
    assert "api.papers.files(" in citations
    assert "api.papers.fileContentUrl(" in citations
    assert 'data-testid="open-citation-pdf"' in citations
    assert 'target="_blank"' in citations


# ---------------------------------------------------------- explicit states


def test_research_states_are_explicit_and_api_derived():
    view = _read(RESEARCH_VIEW)
    for component in ("LoadingState", "EmptyState", "ErrorState"):
        assert component in view
    composer = _read(PROMPT_COMPOSER)
    assert "ErrorState" in composer
    monitor = _read(RUN_MONITOR)
    assert "'error'" in monitor
    assert "setError(cause)" in monitor
    # No locally invented run status: display status comes from the API.
    assert "display_status" in _read(ACTIVE_RUN)


# ------------------------------------------------------------------ evidence


def test_research_smoke_harnesses_are_available():
    flow = UI_ROOT / "scripts" / "smoke-research-flow.mjs"
    assert flow.is_file()
    body = flow.read_text(encoding="utf-8")
    for marker in (
        "new-conversation-button",
        "create-conversation-submit",
        "prompt-input",
        "research-primary-control",
        "run-status",
        "run-timeline",
        "citation-reference-1",
        "citation-detail-dialog",
        "open-citation-pdf",
        "deferredRunReadPending",
        "a slow Run poll remained single-in-flight",
        "an out-of-order stale Run poll could not overwrite",
    ):
        assert marker in body, f"deterministic research smoke does not exercise {marker}"

    live = UI_ROOT / "scripts" / "smoke-research-live.mjs"
    assert live.is_file()
    live_body = live.read_text(encoding="utf-8")
    for marker in ("conversations", "turns", "runs", "timeline"):
        assert marker in live_body, f"live research smoke does not exercise {marker}"

    scripts = _package()["scripts"]
    assert "smoke:research" in scripts
    assert "smoke:research-live" in scripts
