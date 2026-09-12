import { useMemo } from 'react'
import { api } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { ErrorState, LoadingState, UnavailableState } from '../../components/AsyncState'
import { Field, Select } from '../../components/Form'
import { describeSchemaOption, schemaOptionKey } from './labels'

/** The Schema definition + version pair selected for a new Workspace. */
export interface SchemaSelectionValue {
  schemaId: string
  version: string
}

export interface SchemaSelectionProps {
  value: SchemaSelectionValue | null
  onChange: (value: SchemaSelectionValue | null) => void
  disabled?: boolean
}

/**
 * Schema definition/version picker backed by `GET /api/v1/schemas`.
 *
 * Only existing Schema versions can be selected here. Creating Schema versions
 * belongs to the Schema Builder iteration, so an empty catalog is reported as a
 * normal unavailable state instead of offering an editor that does not exist.
 */
export function SchemaSelection({ value, onChange, disabled = false }: SchemaSelectionProps) {
  const resource = useApiResource((signal) => api.schemas.list({ signal }), [])

  const options = useMemo(() => {
    const schemas = resource.data ?? []
    const sorted = [...schemas].sort((left, right) => {
      if (left.schema_id !== right.schema_id) {
        return left.schema_id.localeCompare(right.schema_id)
      }
      return left.version.localeCompare(right.version)
    })
    return sorted
  }, [resource.data])

  if (resource.status === 'loading') {
    return <LoadingState label="Loading available Schema versions…" />
  }

  if (resource.status === 'error') {
    return <ErrorState error={resource.error} onRetry={resource.reload} title="Schema catalog unavailable" />
  }

  if (options.length === 0) {
    return (
      <UnavailableState
        title="No Schema versions are available"
        description="This local server has no Schema definitions to bind. Create a workspace without a Schema, or add a Schema version through the Schema API first. A workspace without a Schema cannot be changed to a Schema later."
      />
    )
  }

  const currentKey = value ? `${value.schemaId}@${value.version}` : ''

  return (
    <Field
      label="Schema and version"
      htmlFor="workspace-schema-select"
      required
      description="The selected Schema version is bound to this workspace permanently."
    >
      <Select
        id="workspace-schema-select"
        data-testid="schema-select"
        disabled={disabled}
        value={currentKey}
        onChange={(event) => {
          const schema = options.find((option) => schemaOptionKey(option) === event.target.value)
          if (!schema) {
            onChange(null)
            return
          }
          onChange({ schemaId: schema.schema_id, version: schema.version })
        }}
      >
        <option value="">Select a Schema version…</option>
        {options.map((schema) => (
          <option key={schemaOptionKey(schema)} value={schemaOptionKey(schema)}>
            {describeSchemaOption(schema)}
          </option>
        ))}
      </Select>
    </Field>
  )
}
