import { api } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Note } from '../../components/Section'
import { describeConfidence } from './labels'

/**
 * Metadata candidate inspection (advanced).
 *
 * Candidates are the extraction candidates the backend recorded for a Paper.
 * They are shown read-only behind an advanced disclosure: the researcher-facing
 * correction flow is {@link MetadataCorrectionPanel}, and this panel never
 * decides which candidate is authoritative.
 */
export function MetadataCandidatesPanel({ paperId }: { paperId: string }) {
  const resource = useApiResource(
    (signal) => api.papers.metadataCandidates(paperId, { signal }),
    [paperId],
  )

  return (
    <div data-testid="metadata-candidates-panel">
      {resource.status === 'loading' ? <LoadingState label="Loading metadata candidates…" /> : null}

      {resource.status === 'error' ? (
        <ErrorState error={resource.error} title="Metadata candidates unavailable" onRetry={resource.reload} />
      ) : null}

      {resource.status === 'ready' && (resource.data ?? []).length === 0 ? (
        <EmptyState
          title="No metadata candidates"
          description="The backend recorded no extraction candidates for this paper."
        />
      ) : null}

      {resource.status === 'ready' && (resource.data ?? []).length > 0 ? (
        <ul className="record-list" data-testid="metadata-candidate-list">
          {(resource.data ?? []).map((candidate) => (
            <li className="record" key={candidate.id} data-testid={`metadata-candidate-${candidate.id}`}>
              <div className="record__header">
                <span className="record__title">{candidate.field_name}</span>
                {candidate.is_selected ? <Badge tone="success">Selected</Badge> : <Badge>Not selected</Badge>}
              </div>
              <p className="record__value">{candidate.value_text ?? 'No value recorded'}</p>
              <dl className="detail-list">
                <div className="detail-list__row">
                  <dt>Source</dt>
                  <dd>{candidate.source_type}</dd>
                </div>
                <div className="detail-list__row">
                  <dt>Location</dt>
                  <dd>{candidate.source_location ?? 'not recorded'}</dd>
                </div>
                <div className="detail-list__row">
                  <dt>Confidence</dt>
                  <dd>{describeConfidence(candidate.confidence)}</dd>
                </div>
                <div className="detail-list__row">
                  <dt>Candidate</dt>
                  <dd>
                    <code>{candidate.id}</code>
                  </dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      ) : null}

      <Note>
        Candidates are raw extraction records reported by the backend. Correcting the paper’s metadata
        is a separate action above this panel.
      </Note>
    </div>
  )
}
