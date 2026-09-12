import { api } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'
import { formatTimestamp, pluralize } from '../../lib/format'
import { agenticEntryStatusTone, describeAgenticEntryStatus } from './labels'

/** Maximum characters of an entry shown in the list before the detail view. */
const EXCERPT_LENGTH = 280

function excerpt(text: string): string {
  return text.length > EXCERPT_LENGTH ? `${text.slice(0, EXCERPT_LENGTH).trimEnd()}…` : text
}

/**
 * Agent Learned content (the API's agentic Wiki entries).
 *
 * Entries are promoted by the backend from completed research runs. Stale and
 * superseded entries stay visible with an explicit lifecycle badge instead of
 * being hidden, so the list never overstates how current the knowledge is.
 */
export function AgentLearnedPanel({ workspaceId }: { workspaceId: string }) {
  const entries = useApiResource(
    (signal) => api.wiki.agenticEntries(workspaceId, true, { signal }),
    [workspaceId],
  )
  const items = entries.data?.items ?? []

  return (
    <section className="wiki-source wiki-source--agentic" data-testid="wiki-source-agentic">
      <div className="wiki-source__header">
        <div>
          <p className="card__eyebrow">Agent Learned</p>
          <p className="card__meta">
            Knowledge entries promoted from completed research runs in this workspace.
          </p>
        </div>
        {entries.status === 'ready' ? (
          <Badge tone="accent" testId="wiki-agentic-entry-count">
            {pluralize(items.length, 'entry', 'entries')}
          </Badge>
        ) : null}
      </div>

      {entries.status === 'loading' ? <LoadingState label="Loading Agent Learned entries…" /> : null}
      {entries.status === 'error' ? (
        <ErrorState error={entries.error} onRetry={entries.reload} />
      ) : null}

      {entries.status === 'ready' && items.length === 0 ? (
        <EmptyState
          title="No Agent Learned entries yet"
          description="Entries appear here after research runs in this workspace produce knowledge the backend promotes into the Wiki."
        />
      ) : null}

      {entries.status === 'ready' && items.length > 0 ? (
        <ul className="card-list" data-testid="wiki-agentic-entry-list">
          {items.map((entry) => (
            <li className="card" key={entry.entry_id}>
              <div className="card__main">
                <div className="card__title-row">
                  <h3 className="card__title">{entry.title}</h3>
                  <Badge tone="accent">Agent Learned</Badge>
                  <Badge tone={agenticEntryStatusTone(entry.status)}>
                    {describeAgenticEntryStatus(entry.status)}
                  </Badge>
                </div>
                <p className="card__description">{excerpt(entry.content)}</p>
                <p className="card__meta">
                  from run {entry.originating_agent_run_id} · updated{' '}
                  {formatTimestamp(entry.updated_at)}
                </p>
              </div>
              <div className="card__actions">
                <LinkButton to={`/wiki/entries/${encodeURIComponent(entry.entry_id)}`} size="sm">
                  Open
                </LinkButton>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
