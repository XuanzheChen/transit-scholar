import { useId, useState } from 'react'
import { api } from '../../api'
import type { Workspace } from '../../api'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Button } from '../../components/Button'
import { ErrorState } from '../../components/AsyncState'
import { Field, RadioGroup, TextInput } from '../../components/Form'
import { Modal } from '../../components/Modal'
import { Note } from '../../components/Section'
import { SchemaSelection } from './SchemaSelection'
import type { SchemaSelectionValue } from './SchemaSelection'

type SchemaMode = 'none' | 'schema'

export interface CreateWorkspaceDialogProps {
  onClose: () => void
  onCreated: (workspace: Workspace) => void
}

/**
 * Workspace creation flow.
 *
 * Supports both creation modes required by REQ-003: a workspace without a
 * Schema, or a workspace bound to one existing Schema definition/version. The
 * permanence of a Schema binding is stated in the form before the create action
 * is available; nothing here attempts to change a binding afterwards.
 */
export function CreateWorkspaceDialog({ onClose, onCreated }: CreateWorkspaceDialogProps) {
  const nameId = useId()
  const [name, setName] = useState('')
  const [schemaMode, setSchemaMode] = useState<SchemaMode>('none')
  const [selection, setSelection] = useState<SchemaSelectionValue | null>(null)

  const create = useAsyncAction((payloadName: string, schema: SchemaSelectionValue | null) =>
    api.workspaces.create({
      name: payloadName,
      schema: schema ? { schema_id: schema.schemaId, version: schema.version } : null,
    }),
  )

  const trimmedName = name.trim()
  const nameError = create.status === 'idle' && name.length > 0 && trimmedName.length === 0
    ? 'Enter a workspace name.'
    : undefined
  const schemaMissing = schemaMode === 'schema' && !selection
  const canSubmit = trimmedName.length > 0 && !schemaMissing && !create.running

  async function handleSubmit(): Promise<void> {
    if (!canSubmit) {
      return
    }
    const workspace = await create.run(trimmedName, schemaMode === 'schema' ? selection : null)
    if (workspace) {
      onCreated(workspace)
    }
  }

  return (
    <Modal
      title="New workspace"
      description="A workspace keeps one research project's papers, conversations, and knowledge together."
      onClose={onClose}
      testId="create-workspace-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={create.running}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              void handleSubmit()
            }}
            busy={create.running}
            disabled={!canSubmit}
            data-testid="create-workspace-submit"
          >
            Create workspace
          </Button>
        </>
      }
    >
      <div className="stack stack--tight">
        <Field label="Workspace name" htmlFor={nameId} required error={nameError}>
          <TextInput
            id={nameId}
            data-testid="workspace-name-input"
            value={name}
            disabled={create.running}
            autoFocus
            placeholder="e.g. Bus scheduling literature review"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>

        <RadioGroup
          name="schema-mode"
          legend="Schema"
          value={schemaMode}
          onChange={(value) => setSchemaMode(value as SchemaMode)}
          options={[
            {
              value: 'none',
              label: 'Create without a Schema',
              description:
                'No Schema is bound. Schema-based knowledge and Schema Wiki do not apply to this workspace.',
            },
            {
              value: 'schema',
              label: 'Bind an existing Schema version',
              description:
                'Choose one Schema definition and version. The binding is permanent for this workspace.',
            },
          ]}
        />

        {schemaMode === 'schema' ? (
          <div className="stack stack--tight" data-testid="schema-binding-section">
            <SchemaSelection value={selection} onChange={setSelection} disabled={create.running} />

            <div className="warning-panel" role="alert" data-testid="schema-binding-warning">
              <p className="warning-panel__title">This Schema binding is permanent</p>
              <p className="warning-panel__text">
                {selection
                  ? `This workspace will stay bound to ${selection.schemaId} version ${selection.version}.`
                  : 'This workspace will stay bound to the Schema version you select.'}{' '}
                It cannot be changed later. To use a different Schema or version, create a new workspace
                instead.
              </p>
            </div>
          </div>
        ) : (
          <Note>
            A workspace created without a Schema stays without one. This is a normal product state: the
            workspace simply has no Schema-bound knowledge or Schema Wiki.
          </Note>
        )}

        {create.status === 'error' ? (
          <ErrorState error={create.error} title="Workspace could not be created" />
        ) : null}
      </div>
    </Modal>
  )
}
