import type { WikiOverview } from '../../api'
import { Badge } from '../../components/Badge'
import { Disclosure } from '../../components/Disclosure'
import { formatTimestamp, shortHash } from '../../lib/format'
import { describeWikiCapabilityReason, describeWikiStatus, wikiStatusTone } from './labels'

export interface WikiStatusPanelProps {
  overview: WikiOverview
  /** `schema_mode` reported by the Workspace API, used for explanations. */
  schemaMode: string | null
}

/**
 * Wiki capability and status summary.
 *
 * Every value is read from `GET /api/v1/workspaces/{id}/wiki`. The panel never
 * infers a status locally: an unavailable or unbuilt Schema Wiki is shown as
 * the exact status the backend reported, and the raw capability reason stays
 * available behind a disclosure.
 */
export function WikiStatusPanel({ overview, schemaMode }: WikiStatusPanelProps) {
  const base = overview.base_wiki
  const capability = overview.base_wiki_capability

  return (
    <article className="context-card" data-testid="wiki-status">
      <p className="card__eyebrow">Schema Wiki status</p>
      <div className="card__title-row">
        <h3 className="card__title">{describeWikiStatus(base.status)}</h3>
        <Badge tone={wikiStatusTone(base.status)} testId="wiki-status-badge">
          {base.status}
        </Badge>
        <Badge tone={capability.read_supported ? 'info' : 'neutral'} testId="wiki-read-capability">
          {capability.read_supported ? 'Readable' : 'Not readable'}
        </Badge>
        <Badge tone={capability.build_supported ? 'info' : 'neutral'} testId="wiki-build-capability">
          {capability.build_supported ? 'Build supported' : 'Build unavailable'}
        </Badge>
      </div>
      <p className="card__meta" data-testid="wiki-status-meta">
        build revision {base.build_revision ?? 'not built'} · built {formatTimestamp(base.built_at)}
        {base.error_code ? ` · backend code ${base.error_code}` : ''}
      </p>
      <p className="card__meta" data-testid="wiki-agentic-count">
        Agent Learned entries in this workspace: {overview.agentic_wiki_entry_count}
      </p>

      <Disclosure summary="Wiki capability details" testId="wiki-capability-details">
        <dl className="detail-list">
          <div className="detail-list__row">
            <dt>Workspace</dt>
            <dd>
              <code>{overview.workspace_id}</code>
            </dd>
          </div>
          <div className="detail-list__row">
            <dt>Workspace Schema mode</dt>
            <dd>{schemaMode ?? 'not reported'}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Schema Wiki read support</dt>
            <dd>{capability.read_supported ? 'supported' : 'unsupported'}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Schema Wiki build support</dt>
            <dd>{capability.build_supported ? 'supported' : 'unsupported'}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Capability reason</dt>
            <dd>{describeWikiCapabilityReason(capability.reason)}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Manifest status</dt>
            <dd>{base.manifest_status ?? 'not reported'}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Input fingerprint</dt>
            <dd>{shortHash(base.fingerprint)}</dd>
          </div>
          <div className="detail-list__row">
            <dt>Recorded fingerprint</dt>
            <dd>{shortHash(base.recorded_fingerprint)}</dd>
          </div>
        </dl>
      </Disclosure>
    </article>
  )
}
