import { api } from '../../api'
import { Link } from '../../app/router'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Disclosure } from '../../components/Disclosure'
import { Prose } from '../../components/Prose'
import { Note } from '../../components/Section'
import { formatTimestamp } from '../../lib/format'
import { ReferenceList } from './ReferenceList'
import { agenticEntryStatusTone, describeAgenticEntryStatus } from './labels'
import { usePaperTitles } from './usePaperTitles'

/**
 * Detail of one Agent Learned entry.
 *
 * The entry content, lifecycle state, source papers, and provenance references
 * come straight from the Agent Learned entries endpoint. The only extra lookup is
 * the Library title of each source paper, so references read as paper titles
 * instead of identifiers; the UI adds no interpretation of its own (REQ-010).
 */
export function AgentLearnedEntryDetailView({
  workspaceId,
  entryId,
}: {
  workspaceId: string
  entryId: string
}) {
  const entry = useApiResource(
    (signal) => api.wiki.agenticEntry(workspaceId, entryId, { signal }),
    [workspaceId, entryId],
  )
  const { titles, loading: titlesLoading } = usePaperTitles()

  if (entry.status === 'loading') {
    return <LoadingState label="Loading Agent Learned entry…" />
  }
  if (entry.status === 'error') {
    return <ErrorState error={entry.error} title="Agent Learned entry unavailable" onRetry={entry.reload} />
  }

  const record = entry.data
  if (!record) {
    return (
      <EmptyState
        title="Agent Learned entry not found"
        description="The backend did not return this Agent Learned entry for the open workspace."
      />
    )
  }

  return (
    <div className="stack" data-testid="wiki-entry-detail">
      <div className="context-card">
        <div className="card__title-row">
          <h3 className="card__title">{record.title}</h3>
          <Badge tone="accent">Agent Learned</Badge>
          <Badge
            tone={agenticEntryStatusTone(record.status)}
            testId="wiki-entry-lifecycle-status"
          >
            {describeAgenticEntryStatus(record.status)}
          </Badge>
        </div>
        <p className="card__meta" data-testid="wiki-entry-identity">
          Learned from research run {record.originating_agent_run_id} · updated{' '}
          {formatTimestamp(record.updated_at)}
        </p>
        <Prose text={record.content} testId="wiki-entry-content" />
      </div>

      {record.status === 'stale' ? (
        <Note tone="warning">
          The workspace knowledge this entry was learned from has changed since it was recorded, so
          the backend reports it as possibly out of date. It stays visible for reference.
        </Note>
      ) : null}
      {record.superseded_by ? (
        <Note tone="warning">
          <span data-testid="wiki-entry-superseded">
            This entry was superseded by a newer Agent Learned entry.{' '}
            <Link to={`/wiki/entries/${encodeURIComponent(record.superseded_by)}`}>
              Open the newer entry
            </Link>
            .
          </span>
        </Note>
      ) : null}

      <div className="subsection" data-testid="wiki-entry-source-papers">
        <h3 className="subsection__title">Source papers</h3>
        {record.paper_ids.length === 0 ? (
          <p className="card__meta">The backend did not record source papers for this entry.</p>
        ) : (
          <ul className="plain-list" data-testid="wiki-entry-papers">
            {record.paper_ids.map((paperId) => {
              const title = titles.get(paperId)
              return (
                <li key={paperId}>
                  <Link to={`/library/${encodeURIComponent(paperId)}`}>
                    {title ?? paperId}
                  </Link>
                  {title ? (
                    <>
                      {' '}
                      <code>{paperId}</code>
                    </>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
        {record.paper_ids.length > 0 && !titlesLoading && record.paper_ids.some((id) => !titles.has(id)) ? (
          <p className="card__meta">
            The Library did not report a title for every source paper, so identifiers are shown for
            the rest.
          </p>
        ) : null}
      </div>

      <div className="subsection" data-testid="wiki-entry-provenance">
        <h3 className="subsection__title">Provenance</h3>
        <dl className="detail-list">
          <div className="detail-list__row">
            <dt>Originating research run</dt>
            <dd>
              <code>{record.originating_agent_run_id}</code>
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Source claims</dt>
            <dd>
              <ReferenceList
                values={record.source_claim_ids}
                emptyLabel="The backend did not record source claims for this entry."
                testId="wiki-entry-claim-refs"
              />
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Evidence references</dt>
            <dd>
              <ReferenceList
                values={record.evidence_refs}
                emptyLabel="The backend did not record evidence references for this entry."
                testId="wiki-entry-evidence-refs"
              />
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Provenance references</dt>
            <dd>
              <ReferenceList
                values={record.provenance_refs}
                emptyLabel="The backend did not record provenance references for this entry."
                testId="wiki-entry-provenance-refs"
              />
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Recorded</dt>
            <dd>{formatTimestamp(record.created_at)}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Updated</dt>
            <dd>{formatTimestamp(record.updated_at)}</dd>
          </div>
        </dl>
      </div>

      <Disclosure summary="Record identifiers" testId="wiki-entry-identifiers">
        <dl className="detail-list">
          <div className="detail-list__row">
            <dt>Agent Learned entry</dt>
            <dd>
              <code>{record.entry_id}</code>
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Workspace</dt>
            <dd>
              <code>{record.workspace_id}</code>
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Lifecycle status</dt>
            <dd>{record.status}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Superseded by</dt>
            <dd>{record.superseded_by ? <code>{record.superseded_by}</code> : 'not superseded'}</dd>
          </div>
        </dl>
      </Disclosure>
    </div>
  )
}
