import { useState } from 'react'
import { api } from '../../api'
import type { CapabilityResponse, SchemaResponse } from '../../api'
import { useLocation } from '../../app/router'
import { useBackendStatus } from '../../app/BackendStatusContext'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState, UnavailableState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'
import { Note, Section } from '../../components/Section'
import { shortHash } from '../../lib/format'
import { SchemaBuilderDialog } from './SchemaBuilderDialog'
import { SchemaVersionDetailView } from './SchemaVersionDetailView'
import { describeSchemaVersion, schemaVersionHref, schemaVersionKey } from './labels'
import { readDefinitionSections } from './schemaDraft'

interface SchemaVersionRoute {
  schemaId: string
  version: string
}

/**
 * Resolve `/schemas/{schemaId}/versions/{version}` to the read-only detail
 * route. Any other path is the catalog.
 */
function parseVersionRoute(pathname: string): SchemaVersionRoute | null {
  const segments = pathname.split('/').filter((segment) => segment !== '')
  if (segments.length !== 4) {
    return null
  }
  const [root, schemaId, versions, version] = segments
  if (root !== 'schemas' || versions !== 'versions') {
    return null
  }
  return { schemaId: decodeURIComponent(schemaId), version: decodeURIComponent(version) }
}

/**
 * Schemas product area.
 *
 * The catalog lists the Schema definitions and versions the API reports. The
 * detail route shows one created version read-only. Creating a new version is a
 * structured, API-validated flow with an explicit immutable-version
 * confirmation; there is no edit-in-place path for an existing version.
 */
export function SchemasView() {
  const pathname = useLocation().split('?')[0]
  const route = parseVersionRoute(pathname)

  if (route) {
    return (
      <Section
        title="Schema version"
        description="One created Schema definition and version, shown read-only."
        actions={<LinkButton to="/schemas">Back to Schemas</LinkButton>}
      >
        <SchemaVersionDetailView schemaId={route.schemaId} version={route.version} />
      </Section>
    )
  }

  return <SchemaCatalogView />
}

function SchemaCatalogView() {
  const resource = useApiResource((signal) => api.schemas.list({ signal }), [])
  const { capabilities, capabilitiesError } = useBackendStatus()
  const [builderOpen, setBuilderOpen] = useState(false)
  const [created, setCreated] = useState<SchemaResponse | null>(null)

  const schemas = resource.data ?? []
  const creationAvailable = capabilities?.user_schema_creation === true

  function handleCreated(schema: SchemaResponse): void {
    setBuilderOpen(false)
    setCreated(schema)
    resource.reload()
  }

  return (
    <Section
      title="Schemas"
      description="Schemas describe the structured knowledge TransitScholar extracts from papers. Each version is immutable once created."
      actions={
        <Button
          variant="primary"
          onClick={() => setBuilderOpen(true)}
          disabled={!creationAvailable}
          title={
            creationAvailable
              ? 'Create a new immutable Schema version'
              : 'Creating Schema versions is not available from this backend'
          }
          data-testid="new-schema-button"
        >
          New schema
        </Button>
      }
    >
      {created ? (
        <div className="banner banner--info" role="status" data-testid="schema-created-banner">
          <div>
            <p className="banner__title">Schema version created</p>
            <p className="banner__text">
              {created.schema_id} version {created.version} was created and is immutable.
            </p>
          </div>
          <LinkButton to={schemaVersionHref(created)} data-testid="schema-created-open">
            View version
          </LinkButton>
        </div>
      ) : null}

      {renderCreationCapability(capabilities, capabilitiesError)}

      {resource.status === 'loading' ? <LoadingState label="Loading schemas…" /> : null}

      {resource.status === 'error' ? <ErrorState error={resource.error} onRetry={resource.reload} /> : null}

      {resource.status === 'ready' && schemas.length === 0 ? (
        <EmptyState
          title="No schemas available"
          description="No Schema definitions are stored on this local server. A created Schema version is immutable; changes require a new version."
        />
      ) : null}

      {resource.status === 'ready' && schemas.length > 0 ? (
        <ul className="card-list" data-testid="schema-catalog-list">
          {schemas.map((schema) => {
            const key = schemaVersionKey(schema)
            const sections = readDefinitionSections(schema.definition)
            const fieldCount = sections.reduce((total, section) => total + section.fields.length, 0)
            return (
              <li className="card" key={key} data-testid={`schema-catalog-item-${key}`}>
                <div className="card__main">
                  <div className="card__title-row">
                    <h3 className="card__title">{describeSchemaVersion(schema)}</h3>
                    <Badge tone="info">version {schema.version}</Badge>
                    <Badge tone="neutral" title="A created Schema version cannot be edited">
                      immutable
                    </Badge>
                  </div>
                  <p className="card__meta">
                    {schema.schema_id} · {sections.length} Section{sections.length === 1 ? '' : 's'} ·{' '}
                    {fieldCount} Field{fieldCount === 1 ? '' : 's'} · hash {shortHash(schema.schema_hash)}
                  </p>
                  {schema.description ? <p className="card__description">{schema.description}</p> : null}
                  <div className="card__actions">
                    <LinkButton
                      to={schemaVersionHref(schema)}
                      size="sm"
                      data-testid={`schema-version-open-${key}`}
                    >
                      View definition
                    </LinkButton>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}

      <Note>
        A created Schema version is immutable. Editing a Schema means creating a new version, so this
        catalog offers no edit action.
      </Note>

      {builderOpen ? (
        <SchemaBuilderDialog onClose={() => setBuilderOpen(false)} onCreated={handleCreated} />
      ) : null}
    </Section>
  )
}

/**
 * Explicit, API-derived state for whether Schema creation is offered.
 *
 * The button is never enabled optimistically: only
 * `user_schema_creation === true` from the capabilities API turns creation on.
 */
function renderCreationCapability(
  capabilities: CapabilityResponse | null,
  capabilitiesError: unknown | null,
) {
  if (capabilities?.user_schema_creation === true) {
    return null
  }
  if (capabilitiesError) {
    return (
      <div data-testid="schema-creation-capability-error">
        <ErrorState
          error={capabilitiesError}
          title="Schema creation capability unknown"
          description="The capabilities API could not be read, so this UI cannot offer Schema creation."
        />
      </div>
    )
  }
  if (capabilities === null) {
    return <LoadingState label="Checking whether Schema creation is available…" />
  }
  return (
    <div data-testid="schema-creation-unavailable">
      <UnavailableState
        title="Creating Schema versions is not available"
        description="This backend reports Schema creation as unavailable, so new versions can only be read. Existing Schema versions remain selectable when creating a Workspace."
      />
    </div>
  )
}
