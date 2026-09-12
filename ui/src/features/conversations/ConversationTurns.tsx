import type { Turn } from '../../api'
import { Badge } from '../../components/Badge'
import { Note } from '../../components/Section'
import { LoadingState } from '../../components/AsyncState'
import { formatTimestamp } from '../../lib/format'
import { CompletedRunTimeline } from '../runs/CompletedRunTimeline'
import { AnswerCitations } from './AnswerCitations'

export interface ConversationTurnsProps {
  turns: Turn[]
  /** The AgentRun currently being monitored; its Timeline is rendered elsewhere. */
  activeRunId: string | null
}

function turnStatusTone(status: string): 'neutral' | 'info' | 'success' | 'danger' {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running' || status === 'preparing') return 'info'
  return 'neutral'
}

/**
 * Persisted Conversation turns.
 *
 * Turns, final answers, and citations are rendered exactly as returned by the
 * Conversation API. The frontend keeps no second copy of conversation history
 * and performs no context reconstruction (REQ-004 / C-002).
 */
export function ConversationTurns({ turns, activeRunId }: ConversationTurnsProps) {
  return (
    <ol className="turns" data-testid="conversation-turns">
      {turns.map((turn) => {
        const active = turn.agent_run_id !== null && turn.agent_run_id === activeRunId
        return (
          <li className="turn" key={turn.turn_id} data-testid={`turn-${turn.sequence}`}>
            <div className="turn__user">
              <p className="turn__role">You</p>
              <p className="turn__message">{turn.user_message}</p>
              {turn.created_at ? <p className="turn__meta">asked {formatTimestamp(turn.created_at)}</p> : null}
            </div>

            <div className="turn__assistant">
              <div className="turn__assistant-header">
                <p className="turn__role">TransitScholar</p>
                <Badge tone={turnStatusTone(turn.status)} testId="turn-status">
                  {turn.status}
                </Badge>
              </div>

              {turn.final_answer ? (
                <div className="turn__answer" data-testid="turn-final-answer">
                  <p className="turn__answer-text">{turn.final_answer}</p>
                  <AnswerCitations citations={turn.answer_citations} />
                </div>
              ) : null}

              {!turn.final_answer && turn.status === 'failed' ? (
                <p className="turn__error" role="alert" data-testid="turn-error">
                  {turn.error_message ?? 'The agent run failed before producing an answer.'}
                </p>
              ) : null}

              {!turn.final_answer && turn.status !== 'failed' && active ? (
                <LoadingState label="Research in progress…" />
              ) : null}

              {!turn.final_answer && turn.status !== 'failed' && !active ? (
                <Note>The agent has not produced an answer for this turn yet.</Note>
              ) : null}

              {turn.agent_run_id && !active ? <CompletedRunTimeline runId={turn.agent_run_id} /> : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
