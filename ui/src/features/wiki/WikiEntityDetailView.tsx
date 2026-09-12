import { api } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Prose } from '../../components/Prose'
import { formatTimestamp } from '../../lib/format'

/**
 * Detail of one Schema Wiki topic (the API's Wiki entity).
 *
 * Rendered from the structured Wiki entity response; aliases and description
 * are shown exactly as the backend recorded them.
 */
export function WikiEntityDetailView({
  workspaceId,
  entityId,
}: {
  workspaceId: string
  entityId: string
}) {
  const entity = useApiResource(
    (signal) => api.wiki.entity(workspaceId, entityId, { signal }),
    [workspaceId, entityId],
  )

  if (entity.status === 'loading') {
    return <LoadingState label="Loading Schema Wiki topic…" />
  }
  if (entity.status === 'error') {
    return (
      <ErrorState error={entity.error} title="Schema Wiki topic unavailable" onRetry={entity.reload} />
    )
  }

  const record = entity.data
  if (!record) {
    return (
      <EmptyState
        title="Wiki topic not found"
        description="The backend did not return this Schema Wiki topic for the open workspace."
      />
    )
  }

  return (
    <div className="stack" data-testid="wiki-entity-detail">
      <div className="context-card">
        <div className="card__title-row">
          <h3 className="card__title">{record.canonical_name}</h3>
          <Badge tone="info">Schema Wiki</Badge>
          {record.kind ? <Badge tone="neutral">{record.kind}</Badge> : null}
        </div>
        <p className="card__meta">
          Shared topic recorded by the backend from this workspace&apos;s Schema Wiki content ·
          updated {formatTimestamp(record.updated_at)}
        </p>
        <Prose text={record.description} testId="wiki-entity-description" />
      </div>

      <div className="subsection">
        <h3 className="subsection__title">Aliases</h3>
        {record.aliases.length === 0 ? (
          <p className="card__meta">The backend did not record any aliases for this topic.</p>
        ) : (
          <ul className="plain-list" data-testid="wiki-entity-aliases">
            {record.aliases.map((alias) => (
              <li key={alias}>{alias}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="subsection">
        <h3 className="subsection__title">Record details</h3>
        <dl className="detail-list">
          <div className="detail-list__row">
            <dt>Topic</dt>
            <dd>
              <code>{record.entity_id}</code>
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Workspace</dt>
            <dd>
              <code>{record.workspace_id}</code>
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Created</dt>
            <dd>{formatTimestamp(record.created_at)}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Updated</dt>
            <dd>{formatTimestamp(record.updated_at)}</dd>
          </div>
        </dl>
      </div>
    </div>
  )
}
