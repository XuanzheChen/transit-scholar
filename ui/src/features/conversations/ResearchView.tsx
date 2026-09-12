import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import type { Turn } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { useBackendStatus } from '../../app/BackendStatusContext'
import { Badge } from '../../components/Badge'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Note, Section } from '../../components/Section'
import { formatTimestamp, pluralize } from '../../lib/format'
import { RequiresWorkspace } from '../workspaces/RequiresWorkspace'
import { describeWorkspaceSchema, workspaceStatusTone } from '../workspaces/labels'
import { ActiveRunPanel } from '../runs/ActiveRunPanel'
import { useAgentRunMonitor } from '../runs/useAgentRunMonitor'
import { ConversationList } from './ConversationList'
import { ConversationTurns } from './ConversationTurns'
import { NewConversationDialog } from './NewConversationDialog'
import { PromptComposer } from './PromptComposer'

const NO_TURNS: Turn[] = []

/**
 * Bounded refresh window after an AgentRun reaches a terminal status.
 *
 * The durable turn's final answer is written after the run's terminal status is
 * published (post-run workspace promotion runs in between), so a single reload
 * at settle time can observe a completed run whose turn still has no answer.
 * The view keeps the persisted-turn refresh alive until the turn carries the
 * answer, within this bounded budget.
 */
export const SETTLE_REFRESH_INTERVAL_MS = 1500
export const SETTLE_REFRESH_LIMIT = 80

/** In-view Conversation selection; a navigation choice, never backend state. */
interface ConversationSelection {
  workspaceId: string
  conversationId: string | null
}

/** Echo of a just-submitted prompt, until the persisted turn is reloaded. */
interface Submission {
  conversationId: string
  runId: string
}

/**
 * Research: the core product experience.
 *
 * Conversation list and creation, persisted turns, prompt submission,
 * AgentRun monitoring, the research Timeline, pause/resume, final answers, and
 * citations. Every piece of state is read from the frozen `/api/v1/*` API; the
 * frontend never reconstructs conversation context or AgentRun state
 * (REQ-004, REQ-005, REQ-006, REQ-007, REQ-012).
 */
export function ResearchView() {
  return (
    <Section
      title="Research"
      description="Ask questions about the papers in the open workspace and follow the agent's research progress."
    >
      <RequiresWorkspace>{(workspaceId) => <ResearchWorkspace workspaceId={workspaceId} />}</RequiresWorkspace>
    </Section>
  )
}

function ResearchWorkspace({ workspaceId }: { workspaceId: string }) {
  const workspace = useApiResource(
    (signal) => api.workspaces.get(workspaceId, { signal }),
    [workspaceId],
  )
  const conversations = useApiResource(
    (signal) => api.conversations.list(workspaceId, { signal }),
    [workspaceId],
  )
  const { capabilities } = useBackendStatus()

  const [selection, setSelection] = useState<ConversationSelection>({
    workspaceId,
    conversationId: null,
  })
  const [submission, setSubmission] = useState<Submission | null>(null)
  const [creatingConversation, setCreatingConversation] = useState(false)

  const explicitConversationId = selection.workspaceId === workspaceId ? selection.conversationId : null
  // With no explicit choice the first conversation of the open workspace wins,
  // so the view is usable without an extra click.
  const selectedConversationId =
    explicitConversationId ?? conversations.data?.items[0]?.conversation_id ?? null

  const conversation = useApiResource(
    (signal) =>
      selectedConversationId
        ? api.conversations.read(selectedConversationId, { signal })
        : Promise.resolve(null),
    [selectedConversationId],
  )

  const turns = conversation.data?.turns ?? NO_TURNS

  const activeTurn = useMemo(() => {
    for (let index = turns.length - 1; index >= 0; index -= 1) {
      const turn = turns[index]
      if (turn.agent_run_id && turn.status !== 'completed' && turn.status !== 'failed') {
        return turn
      }
    }
    return null
  }, [turns])

  // A just-submitted run is monitored immediately. Once the reloaded turn
  // carries the same AgentRun identifier, the persisted turn takes over.
  const pendingSubmittedRunId =
    submission !== null &&
    submission.conversationId === selectedConversationId &&
    !turns.some((turn) => turn.agent_run_id === submission.runId)
      ? submission.runId
      : null

  const activeRunId = activeTurn?.agent_run_id ?? pendingSubmittedRunId
  const reloadConversation = conversation.reload

  // Fresh turns for the settle refresh loop, without re-creating the callback.
  const turnsRef = useRef(turns)
  turnsRef.current = turns
  const settleTimerRef = useRef<number | null>(null)
  useEffect(
    () => () => {
      if (settleTimerRef.current !== null) {
        window.clearTimeout(settleTimerRef.current)
      }
    },
    [],
  )

  const handleRunSettled = useCallback(
    (settledRunId: string) => {
      // Refresh the persisted turn until it carries this run's durable outcome,
      // so the final answer and citations appear once the backend has written
      // them.
      let attempts = 0
      const refresh = () => {
        reloadConversation()
        attempts += 1
        const persisted = turnsRef.current.some(
          (turn) =>
            turn.agent_run_id === settledRunId &&
            (Boolean(turn.final_answer) || turn.status === 'failed'),
        )
        if (!persisted && attempts < SETTLE_REFRESH_LIMIT) {
          settleTimerRef.current = window.setTimeout(refresh, SETTLE_REFRESH_INTERVAL_MS)
        }
      }
      refresh()
    },
    [reloadConversation],
  )

  const monitor = useAgentRunMonitor(activeRunId, {
    enabled: activeRunId !== null,
    poll: true,
    onSettled: handleRunSettled,
  })

  const handleSubmitted = useCallback(
    (agentRunId: string) => {
      if (selectedConversationId) {
        setSubmission({ conversationId: selectedConversationId, runId: agentRunId })
      }
      reloadConversation()
    },
    [selectedConversationId, reloadConversation],
  )

  const pauseResumeAvailable = capabilities?.pause_resume === true
  const conversationItems = conversations.data?.items ?? []

  return (
    <div className="stack" data-testid="research-view">
      <div className="context-card">
        {workspace.status === 'loading' && !workspace.data ? (
          <LoadingState label="Loading workspace…" />
        ) : null}
        {workspace.status === 'error' ? (
          <ErrorState error={workspace.error} title="Workspace unavailable" onRetry={workspace.reload} />
        ) : null}
        {workspace.data ? (
          <>
            <div className="card__title-row">
              <h3 className="card__title">{workspace.data.name}</h3>
              <Badge tone={workspaceStatusTone(workspace.data.status)}>{workspace.data.status}</Badge>
            </div>
            <p className="card__meta">
              {describeWorkspaceSchema(workspace.data)} · updated{' '}
              {formatTimestamp(workspace.data.updated_at)}
            </p>
          </>
        ) : null}
      </div>

      {conversations.status === 'loading' && conversations.data === null ? (
        <LoadingState label="Loading conversations…" />
      ) : null}

      {conversations.status === 'error' ? (
        <ErrorState
          error={conversations.error}
          title="Conversations unavailable"
          onRetry={conversations.reload}
        />
      ) : null}

      {conversations.data ? (
        <div className="research-layout">
          <ConversationList
            conversations={conversationItems}
            selectedConversationId={selectedConversationId}
            onSelect={(conversationId) => setSelection({ workspaceId, conversationId })}
            onNewConversation={() => setCreatingConversation(true)}
          />

          <div className="research-main">
            {selectedConversationId === null ? (
              <EmptyState
                title="No conversation selected"
                description="Select a conversation or create a new one to start researching."
              />
            ) : null}

            {selectedConversationId !== null && conversation.status === 'loading' && !conversation.data ? (
              <LoadingState label="Loading conversation…" />
            ) : null}

            {selectedConversationId !== null && conversation.status === 'error' ? (
              <ErrorState
                error={conversation.error}
                title="Conversation unavailable"
                onRetry={conversation.reload}
              />
            ) : null}

            {selectedConversationId !== null && conversation.data ? (
              <div className="research-main__inner" data-testid="active-conversation">
                <div className="subsection__header">
                  <h3 className="subsection__title">
                    {conversation.data.title ?? 'Untitled conversation'}
                  </h3>
                  <span className="card__meta">{pluralize(turns.length, 'turn')}</span>
                </div>

                {turns.length === 0 ? (
                  <EmptyState
                    title="No turns yet"
                    description="Ask the first research question below."
                  />
                ) : (
                  <ConversationTurns turns={turns} activeRunId={activeRunId} />
                )}

                {activeRunId ? <ActiveRunPanel runId={activeRunId} monitor={monitor} /> : null}

                <PromptComposer
                  conversationId={selectedConversationId}
                  run={monitor.run}
                  pauseResumeAvailable={pauseResumeAvailable}
                  onSubmitted={handleSubmitted}
                  onRunState={monitor.applyState}
                />
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {conversations.data ? (
        <Note>
          Conversation context is resolved by the backend. This view submits prompts through the
          Conversation API and follows the resulting run through the Run status and Timeline APIs.
        </Note>
      ) : null}

      {creatingConversation ? (
        <NewConversationDialog
          workspaceId={workspaceId}
          onClose={() => setCreatingConversation(false)}
          onCreated={(created) => {
            setCreatingConversation(false)
            conversations.reload()
            setSelection({ workspaceId, conversationId: created.conversation_id })
          }}
        />
      ) : null}
    </div>
  )
}
