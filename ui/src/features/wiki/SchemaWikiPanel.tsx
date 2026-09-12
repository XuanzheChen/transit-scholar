import { api } from '../../api'
import type { WikiOverview, WikiStatusValue } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState, UnavailableState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'
import { Note } from '../../components/Section'
import { formatTimestamp } from '../../lib/format'
import { PaperSchemaReadinessPanel } from '../workspaces/PaperSchemaReadinessPanel'
import { describeWikiStatus, explainSchemaWikiUnavailable, wikiStatusTone } from './labels'

export interface SchemaWikiPanelProps {
  workspaceId: string
  overview: WikiOverview
  schemaMode: string | null
  /** Reload the Wiki overview after a build changes the backend state. */
  onWikiChanged: () => void
}

/**
 * Schema Wiki content (the API's base Wiki).
 *
 * Pages and topics are read from the Wiki pages/entities endpoints and are only
 * requested when the API reports Schema Wiki reads as supported, so a no-Schema
 * Workspace never triggers an avoidable failing request. The panel presents the
 * unavailable case as an explanatory normal state rather than an error
 * (REQ-010 / AC-011 / AC-012).
 */
export function SchemaWikiPanel({
  workspaceId,
  overview,
  schemaMode,
  onWikiChanged,
}: SchemaWikiPanelProps) {
  const capability = overview.base_wiki_capability
  const readSupported = capability.read_supported

  const pages = useApiResource(
    (signal) => (readSupported ? api.wiki.pages(workspaceId, { signal }) : Promise.resolve(null)),
    [workspaceId, readSupported],
  )
  const entities = useApiResource(
    (signal) => (readSupported ? api.wiki.entities(workspaceId, { signal }) : Promise.resolve(null)),
    [workspaceId, readSupported],
  )
  const build = useAsyncAction(() => api.wiki.build(workspaceId))

  async function handleBuild(): Promise<void> {
    const result = await build.run()
    if (result) {
      pages.reload()
      entities.reload()
      onWikiChanged()
    }
  }

  if (!readSupported) {
    if (capability.build_supported) {
      return (
        <section className="wiki-source wiki-source--schema" data-testid="wiki-source-schema">
          <SchemaWikiHeader status={overview.base_wiki.status} />
          <EmptyState
            title="Schema Wiki has not been built yet"
            description="This workspace has an immutable Schema binding and member papers, but no Schema Wiki snapshot has been produced yet. Build it to read Schema Wiki pages and topics."
            action={
              <Button
                variant="primary"
                busy={build.running}
                onClick={() => void handleBuild()}
                data-testid="wiki-build-button"
              >
                Build Schema Wiki
              </Button>
            }
          />
          {build.error ? <ErrorState error={build.error} onRetry={() => void handleBuild()} /> : null}
          <SchemaReadinessSection workspaceId={workspaceId} onMaterialized={onWikiChanged} />
        </section>
      )
    }

    const explanation = explainSchemaWikiUnavailable(capability, schemaMode)
    return (
      <section className="wiki-source wiki-source--schema" data-testid="wiki-source-schema">
        <SchemaWikiHeader status={overview.base_wiki.status} />
        <div data-testid="wiki-schema-unavailable">
          <UnavailableState title={explanation.title} description={explanation.description} />
        </div>
        <Note>
          Agent Learned entries do not depend on a Schema binding, so they remain available in the
          Agent Learned tab.
        </Note>
      </section>
    )
  }

  const pageItems = pages.data?.items ?? []
  const entityItems = entities.data?.items ?? []

  return (
    <section className="wiki-source wiki-source--schema" data-testid="wiki-source-schema">
      <div className="wiki-source__header">
        <div>
          <p className="card__eyebrow">Schema Wiki</p>
          <p className="card__meta">
            Structured knowledge built from this workspace&apos;s Schema binding and member papers.
          </p>
        </div>
        {capability.build_supported ? (
          <Button busy={build.running} onClick={() => void handleBuild()} data-testid="wiki-build-button">
            {overview.base_wiki.status === 'ready' ? 'Rebuild Schema Wiki' : 'Build Schema Wiki'}
          </Button>
        ) : null}
      </div>

      {overview.base_wiki.status === 'stale' ? (
        <Note tone="warning">
          The workspace inputs changed since this Schema Wiki was built, so its content may be out of
          date. Rebuild it to refresh the content.
        </Note>
      ) : null}
      {overview.base_wiki.status === 'error' ? (
        <Note tone="warning">
          The backend reports a problem with this Schema Wiki build
          {overview.base_wiki.error_code ? ` (${overview.base_wiki.error_code})` : ''}.
        </Note>
      ) : null}
      {build.error ? <ErrorState error={build.error} onRetry={() => void handleBuild()} /> : null}

      <SchemaReadinessSection workspaceId={workspaceId} onMaterialized={onWikiChanged} />

      <div className="subsection">
        <h3 className="subsection__title">Wiki pages</h3>
        {pages.status === 'loading' ? <LoadingState label="Loading Schema Wiki pages…" /> : null}
        {pages.status === 'error' ? <ErrorState error={pages.error} onRetry={pages.reload} /> : null}
        {pages.status === 'ready' && pageItems.length === 0 ? (
          <EmptyState
            title="No Wiki pages"
            description="The built Schema Wiki did not produce any paper pages for this workspace."
          />
        ) : null}
        {pages.status === 'ready' && pageItems.length > 0 ? (
          <ul className="card-list" data-testid="wiki-page-list">
            {pageItems.map((page) => (
              <li className="card" key={page.page_id}>
                <div className="card__main">
                  <div className="card__title-row">
                    <h3 className="card__title">{page.title}</h3>
                    <Badge tone="info">Schema Wiki</Badge>
                    <Badge tone={page.build_status === 'complete' ? 'success' : 'neutral'}>
                      {page.build_status}
                    </Badge>
                  </div>
                  <p className="card__description">{page.summary}</p>
                  <p className="card__meta">
                    {page.schema_id}@{page.schema_version} · revision {page.build_revision} · updated{' '}
                    {formatTimestamp(page.updated_at)}
                  </p>
                </div>
                <div className="card__actions">
                  <LinkButton to={`/wiki/pages/${encodeURIComponent(page.page_id)}`} size="sm">
                    Open
                  </LinkButton>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="subsection">
        <h3 className="subsection__title">Wiki topics</h3>
        {entities.status === 'loading' ? <LoadingState label="Loading Schema Wiki topics…" /> : null}
        {entities.status === 'error' ? (
          <ErrorState error={entities.error} onRetry={entities.reload} />
        ) : null}
        {entities.status === 'ready' && entityItems.length === 0 ? (
          <EmptyState
            title="No Wiki topics"
            description="The built Schema Wiki did not record any shared topics for this workspace."
          />
        ) : null}
        {entities.status === 'ready' && entityItems.length > 0 ? (
          <ul className="card-list" data-testid="wiki-entity-list">
            {entityItems.map((entity) => (
              <li className="card" key={entity.entity_id}>
                <div className="card__main">
                  <div className="card__title-row">
                    <h3 className="card__title">{entity.canonical_name}</h3>
                    <Badge tone="info">Schema Wiki</Badge>
                    {entity.kind ? <Badge tone="neutral">{entity.kind}</Badge> : null}
                  </div>
                  <p className="card__description">{entity.description}</p>
                  {entity.aliases.length > 0 ? (
                    <p className="card__meta">Also known as {entity.aliases.join(', ')}</p>
                  ) : null}
                </div>
                <div className="card__actions">
                  <LinkButton to={`/wiki/entities/${encodeURIComponent(entity.entity_id)}`} size="sm">
                    Open
                  </LinkButton>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  )
}

function SchemaWikiHeader({ status }: { status: WikiStatusValue }) {
  return (
    <div className="wiki-source__header">
      <div>
        <p className="card__eyebrow">Schema Wiki</p>
        <p className="card__meta">
          Structured knowledge built from the workspace Schema binding and its member papers.
        </p>
      </div>
      <Badge tone={wikiStatusTone(status)}>{describeWikiStatus(status)}</Badge>
    </div>
  )
}

/**
 * Per-Paper Schema readiness, shown next to the Schema Wiki content it feeds.
 *
 * Schema Wiki pages are derived from Paper Schema materialization, so a Workspace
 * whose papers are not materialized yet explains itself here instead of leaving
 * the researcher with an empty Wiki (REQ-010 / REQ-012).
 */
function SchemaReadinessSection({
  workspaceId,
  onMaterialized,
}: {
  workspaceId: string
  onMaterialized: (paperId: string) => void
}) {
  return (
    <div className="subsection" data-testid="wiki-schema-readiness-section">
      <h3 className="subsection__title">Paper Schema readiness</h3>
      <p className="card__meta">
        Each Schema Wiki page is built from the Schema content materialized for one workspace paper.
        Request materialization for any paper the backend reports as not materialized yet.
      </p>
      <PaperSchemaReadinessPanel workspaceId={workspaceId} onMaterialized={onMaterialized} />
    </div>
  )
}
