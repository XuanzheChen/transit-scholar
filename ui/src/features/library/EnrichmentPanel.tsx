import { api } from '../../api'
import type { EnrichmentProvider } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { ErrorState, LoadingState } from '../../components/AsyncState'
import { Note } from '../../components/Section'
import { formatTimestamp } from '../../lib/format'
import {
  describeEnrichmentStatus,
  describeStructuredValue,
  enrichmentStatusTone,
} from './labels'

/**
 * Enrichment status and refresh (advanced).
 *
 * The panel reports exactly what `GET /api/v1/papers/{id}/enrichment` returns and
 * requests a refresh through `POST .../enrichment/refresh`. After a refresh the
 * caller reloads the Paper so no enrichment value is cached locally.
 */
export function EnrichmentPanel({
  paperId,
  onRefreshed,
}: {
  paperId: string
  onRefreshed: () => void
}) {
  const resource = useApiResource((signal) => api.papers.enrichment(paperId, { signal }), [paperId])
  const refresh = useAsyncAction(() => api.papers.refreshEnrichment(paperId))

  async function handleRefresh(): Promise<void> {
    const result = await refresh.run()
    if (result) {
      resource.reload()
      onRefreshed()
    }
  }

  return (
    <div className="stack stack--tight" data-testid="paper-enrichment">
      <div className="subsection__header">
        <span className="card__meta">Metadata enrichment status</span>
        <div className="action-row">
          {resource.status === 'ready' && resource.data ? (
            <Badge tone={enrichmentStatusTone(resource.data.metadata_enrichment_status)}>
              {describeEnrichmentStatus(resource.data.metadata_enrichment_status)}
            </Badge>
          ) : null}
          <Button
            size="sm"
            busy={refresh.running}
            onClick={() => {
              void handleRefresh()
            }}
            data-testid="refresh-enrichment"
          >
            Refresh enrichment
          </Button>
        </div>
      </div>

      {resource.status === 'loading' ? <LoadingState label="Loading enrichment status…" /> : null}

      {resource.status === 'error' ? (
        <ErrorState error={resource.error} title="Enrichment status unavailable" onRetry={resource.reload} />
      ) : null}

      {refresh.status === 'error' ? (
        <ErrorState error={refresh.error} title="Enrichment refresh was rejected" />
      ) : null}

      {refresh.status === 'done' ? (
        <p className="card__meta" data-testid="enrichment-refresh-result">
          The backend was asked to refresh enrichment. The status above comes from the enrichment
          endpoint.
        </p>
      ) : null}

      {resource.status === 'ready' && resource.data ? (
        <dl className="detail-list">
          <div className="detail-list__row">
            <dt>DOI used</dt>
            <dd>{resource.data.doi ?? 'not recorded'}</dd>
          </div>
          {Object.entries(resource.data.resolved).map(([field, value]) => (
            <div className="detail-list__row" key={field}>
              <dt>{field}</dt>
              <dd>{describeStructuredValue(value)}</dd>
            </div>
          ))}
          {resource.data.error_code || resource.data.error_message ? (
            <div className="detail-list__row">
              <dt>Reported problem</dt>
              <dd>{resource.data.error_message ?? resource.data.error_code}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {resource.status === 'ready' && resource.data && resource.data.providers.length > 0 ? (
        <ul className="record-list" data-testid="enrichment-provider-list">
          {resource.data.providers.map((provider) => (
            <EnrichmentProviderRecord provider={provider} key={provider.provider} />
          ))}
        </ul>
      ) : null}

      {resource.status === 'ready' && resource.data && resource.data.providers.length === 0 ? (
        <p className="card__meta">
          The backend reports no enrichment providers for this paper.
        </p>
      ) : null}

      <Note>
        Enrichment refreshes contact external metadata providers. The status, provider list, and
        resolved fields above always come from the API.
      </Note>
    </div>
  )
}

function EnrichmentProviderRecord({ provider }: { provider: EnrichmentProvider }) {
  return (
    <li className="record" data-testid={`enrichment-provider-${provider.provider}`}>
      <div className="record__header">
        <span className="record__title">{provider.provider}</span>
        <Badge tone={enrichmentStatusTone(provider.status)}>
          {describeEnrichmentStatus(provider.status)}
        </Badge>
      </div>

      <dl className="detail-list">
        <div className="detail-list__row">
          <dt>Fetched</dt>
          <dd>{formatTimestamp(provider.fetched_at)}</dd>
        </div>
        <div className="detail-list__row">
          <dt>Attempts</dt>
          <dd>{provider.attempt_count}</dd>
        </div>
        {provider.http_status !== null ? (
          <div className="detail-list__row">
            <dt>HTTP status</dt>
            <dd>{provider.http_status}</dd>
          </div>
        ) : null}
        {provider.next_retry_at ? (
          <div className="detail-list__row">
            <dt>Next retry</dt>
            <dd>{formatTimestamp(provider.next_retry_at)}</dd>
          </div>
        ) : null}
        {provider.fields.length > 0 ? (
          <div className="detail-list__row">
            <dt>Fields</dt>
            <dd>{provider.fields.join(', ')}</dd>
          </div>
        ) : null}
      </dl>

      {provider.error_code || provider.error_message ? (
        <p className="card__meta">
          {provider.error_message ?? 'The provider reported an error'}
          {provider.error_code ? ` (${provider.error_code})` : ''}
        </p>
      ) : null}
    </li>
  )
}
