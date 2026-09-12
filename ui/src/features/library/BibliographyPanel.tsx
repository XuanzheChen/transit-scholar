import { api } from '../../api'
import type { CitationRecord } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Note } from '../../components/Section'
import { citationParseTone, describeCitationParseStatus, describeStructuredValue } from './labels'

/**
 * Bibliography / citation records for one Paper.
 *
 * These are the bibliographic records the backend parsed for the Paper. They are
 * deliberately presented separately from answer citations, which belong to a
 * completed research turn.
 */
export function BibliographyPanel({ paperId }: { paperId: string }) {
  const resource = useApiResource((signal) => api.papers.citations(paperId, { signal }), [paperId])
  const records = resource.data ?? []

  return (
    <div data-testid="paper-bibliography">
      {resource.status === 'loading' ? <LoadingState label="Loading bibliography records…" /> : null}

      {resource.status === 'error' ? (
        <ErrorState error={resource.error} title="Bibliography unavailable" onRetry={resource.reload} />
      ) : null}

      {resource.status === 'ready' && records.length === 0 ? (
        <EmptyState
          title="No bibliography records"
          description="The backend has no citation records for this paper."
        />
      ) : null}

      {resource.status === 'ready' && records.length > 0 ? (
        <ul className="record-list" data-testid="bibliography-record-list">
          {records.map((record) => (
            <BibliographyRecord record={record} key={record.id} />
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function BibliographyRecord({ record }: { record: CitationRecord }) {
  const structured = record.structured_json ?? {}
  const entries = Object.entries(structured)

  return (
    <li className="record" data-testid={`bibliography-record-${record.id}`}>
      <div className="record__header">
        <span className="record__title">{record.source_format}</span>
        <Badge tone={citationParseTone(record.parse_status)}>
          {describeCitationParseStatus(record.parse_status)}
        </Badge>
        {record.is_selected ? <Badge tone="success">Selected</Badge> : <Badge>Not selected</Badge>}
      </div>

      {record.raw_text ? <p className="record__value">{record.raw_text}</p> : null}

      {entries.length > 0 ? (
        <dl className="detail-list">
          {entries.map(([key, value]) => (
            <div className="detail-list__row" key={key}>
              <dt>{key}</dt>
              <dd>{describeStructuredValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="card__meta">No structured citation fields were parsed.</p>
      )}

      {record.parse_warnings.length > 0 ? (
        <div className="record__warnings">
          <span className="card__meta">Parse warnings:</span>
          <ul className="plain-list">
            {record.parse_warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <Note>
        Citation record <code>{record.id}</code>, reported by the bibliography endpoint.
      </Note>
    </li>
  )
}
