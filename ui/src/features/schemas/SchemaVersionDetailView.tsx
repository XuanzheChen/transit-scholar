import { api } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { ErrorState, LoadingState } from '../../components/AsyncState'
import { Note } from '../../components/Section'
import { shortHash } from '../../lib/format'
import { SCHEMA_VERSION_IMMUTABLE_STATEMENT, describeFieldType } from './labels'
import { readDefinitionSections } from './schemaDraft'

export interface SchemaVersionDetailViewProps {
  schemaId: string
  version: string
}

/**
 * Read-only detail of one created Schema version.
 *
 * The view shows the API-reported definition and states that the version is
 * immutable. It deliberately offers no edit action: a created version cannot be
 * changed in place (C-008).
 */
export function SchemaVersionDetailView({ schemaId, version }: SchemaVersionDetailViewProps) {
  const resource = useApiResource(
    (signal) => api.schemas.get(schemaId, version, { signal }),
    [schemaId, version],
  )

  if (resource.status === 'loading') {
    return <LoadingState label="Loading Schema version…" />
  }

  if (resource.status === 'error') {
    return (
      <ErrorState error={resource.error} title="Schema version unavailable" onRetry={resource.reload} />
    )
  }

  const schema = resource.data
  if (!schema) {
    return <LoadingState label="Loading Schema version…" />
  }

  const sections = readDefinitionSections(schema.definition)
  const fieldCount = sections.reduce((total, section) => total + section.fields.length, 0)

  return (
    <div className="stack" data-testid="schema-version-detail">
      <dl className="detail-list">
        <div className="detail-list__row">
          <dt>Schema ID</dt>
          <dd>
            <code>{schema.schema_id}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Version</dt>
          <dd>
            <code data-testid="schema-version-value">{schema.version}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Name</dt>
          <dd>{schema.name ?? '—'}</dd>
        </div>
        <div className="detail-list__row">
          <dt>Description</dt>
          <dd>{schema.description ?? '—'}</dd>
        </div>
        <div className="detail-list__row">
          <dt>Content hash</dt>
          <dd>
            <code title={schema.schema_hash}>{shortHash(schema.schema_hash)}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Content</dt>
          <dd>
            {sections.length} Section{sections.length === 1 ? '' : 's'} · {fieldCount} Field
            {fieldCount === 1 ? '' : 's'}
          </dd>
        </div>
      </dl>

      <div className="warning-panel warning-panel--static" data-testid="schema-version-immutable">
        <p className="warning-panel__title">This Schema version is immutable</p>
        <p className="warning-panel__text">{SCHEMA_VERSION_IMMUTABLE_STATEMENT}</p>
      </div>

      <ul className="record-list" data-testid="schema-version-sections">
        {sections.map((section) => (
          <li className="record" key={section.id} data-testid={`schema-version-section-${section.id}`}>
            <div className="record__header">
              <span className="record__title">{section.label || section.id}</span>
              <code>{section.id}</code>
              <Badge tone="neutral">
                {section.fields.length} Field{section.fields.length === 1 ? '' : 's'}
              </Badge>
            </div>
            <ul className="record-list">
              {section.fields.map((field) => (
                <li className="record" key={field.id} data-testid={`schema-version-field-${field.id}`}>
                  <div className="record__header">
                    <span className="record__title">{field.label || field.id}</span>
                    <code>{field.id}</code>
                    <Badge tone="info">{describeFieldType(field.type)}</Badge>
                    {field.evidenceRequired ? <Badge tone="accent">Evidence required</Badge> : null}
                    {field.allowInference ? null : <Badge tone="warning">Inference not allowed</Badge>}
                  </div>
                  {field.question ? <p className="record__value">{field.question}</p> : null}
                  {field.description ? <p className="card__description">{field.description}</p> : null}
                  {field.type === 'enum' ? (
                    <div className="record__warnings">
                      <span className="field__description">Options</span>
                      <ul className="ref-list">
                        {field.options.map((option) => (
                          <li className="ref-list__item" key={option}>
                            {option}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

      <Note>
        A created Schema version is never edited. To change this definition, create a new version.
      </Note>
    </div>
  )
}
