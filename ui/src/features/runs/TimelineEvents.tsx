import type { TimelineEvent } from '../../api'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Badge } from '../../components/Badge'
import { Note } from '../../components/Section'
import { formatTimestamp } from '../../lib/format'
import type { RunMonitorStatus } from './useAgentRunMonitor'
import { timelineEventDetails, timelineEventSummary, timelineKindLabel, timelineKindTone } from './labels'

export interface TimelineEventsProps {
  events: TimelineEvent[]
  status: RunMonitorStatus
  error: unknown | null
  onRetry?: () => void
}

/**
 * Research Timeline event list.
 *
 * Every row is rendered from the API Timeline projection: a user-facing kind,
 * the projected timestamp, and a curated set of structured artifact fields.
 * No hidden model reasoning or raw provider payload is available here, and none
 * is rendered (C-006).
 */
export function TimelineEvents({ events, status, error, onRetry }: TimelineEventsProps) {
  if (status === 'loading' && events.length === 0) {
    return <LoadingState label="Loading research timeline…" />
  }

  if (status === 'error' && events.length === 0) {
    return <ErrorState error={error} title="Research timeline unavailable" onRetry={onRetry} />
  }

  if (events.length === 0) {
    return (
      <EmptyState
        title="No research steps yet"
        description="Structured research steps appear here as the agent works."
      />
    )
  }

  return (
    <div className="timeline">
      {status === 'error' ? (
        <Note tone="warning">
          The latest timeline update could not be loaded; showing the steps received so far.
        </Note>
      ) : null}
      <ol className="timeline__list" data-testid="run-timeline-events">
        {events.map((event) => {
          const details = timelineEventDetails(event)
          const summary = timelineEventSummary(event)
          return (
            <li className="timeline__item" key={event.sequence} data-testid={`timeline-event-${event.sequence}`}>
              <div className="timeline__header">
                <Badge tone={timelineKindTone(event.kind)}>{timelineKindLabel(event.kind)}</Badge>
                <span className="timeline__time">{formatTimestamp(event.timestamp)}</span>
              </div>
              {summary ? <p className="timeline__summary">{summary}</p> : null}
              {details.length > 0 ? (
                <dl className="detail-list timeline__details">
                  {details.map((row) => (
                    <div className="detail-list__row" key={`${event.sequence}-${row.label}`}>
                      <dt>{row.label}</dt>
                      <dd>{row.value}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
