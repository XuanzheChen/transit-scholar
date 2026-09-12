import { useMemo, useState } from 'react'
import { api } from '../../api'
import type { SchemaDraftRequest, SchemaResponse, SchemaValidationResponse } from '../../api'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { ErrorState } from '../../components/AsyncState'
import { Button } from '../../components/Button'
import { Modal } from '../../components/Modal'
import { Note } from '../../components/Section'
import { SchemaDraftEditor } from './SchemaDraftEditor'
import {
  SCHEMA_VERSION_CONFIRMATION_LABEL,
  SCHEMA_VERSION_IMMUTABLE_STATEMENT,
  describeFieldType,
} from './labels'
import {
  createEmptySchemaDraft,
  describeIssue,
  describeIssueLocation,
  toDraftIssues,
  toSchemaDraftRequest,
} from './schemaDraft'
import type { SchemaDraftDefinition, SchemaDraftIssue } from './schemaDraft'

type BuilderStep = 'draft' | 'confirm'

export interface SchemaBuilderDialogProps {
  onClose: () => void
  onCreated: (schema: SchemaResponse) => void
}

function IssueSummary({ title, issues }: { title: string; issues: SchemaDraftIssue[] }) {
  if (issues.length === 0) {
    return null
  }
  return (
    <div className="warning-panel" role="alert" data-testid="schema-validation-summary">
      <p className="warning-panel__title">{title}</p>
      <ul className="draft-issues">
        {issues.map((issue, index) => (
          <li className="draft-issues__item" key={`${issue.type}-${index}`} data-testid="schema-validation-issue">
            <span className="draft-issues__location">{describeIssueLocation(issue)}</span>{' '}
            {describeIssue(issue)}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Structured Schema creation.
 *
 * The flow enforces the two rules the contract requires of this iteration:
 *
 * 1. the draft is validated through `POST /api/v1/schemas/validate` before
 *    creation is offered, and creation stays unavailable while the API reports
 *    the draft as invalid;
 * 2. immediately before creation the user must confirm that the version is
 *    immutable and that future changes require a new version.
 *
 * There is deliberately no edit-in-place path for an existing version.
 */
export function SchemaBuilderDialog({ onClose, onCreated }: SchemaBuilderDialogProps) {
  const [draft, setDraft] = useState<SchemaDraftDefinition>(() => createEmptySchemaDraft())
  const [step, setStep] = useState<BuilderStep>('draft')
  const [validation, setValidation] = useState<SchemaValidationResponse | null>(null)
  const [validatedFingerprint, setValidatedFingerprint] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)

  const validateAction = useAsyncAction((request: SchemaDraftRequest) => api.schemas.validate(request))
  const createAction = useAsyncAction((request: SchemaDraftRequest) => api.schemas.create(request))

  const request = useMemo(() => toSchemaDraftRequest(draft), [draft])
  const requestFingerprint = useMemo(() => JSON.stringify(request), [request])
  const issues = useMemo(() => toDraftIssues(validation?.issues ?? []), [validation])
  // A draft that changed after validation is unvalidated again: the API result
  // only ever describes the exact draft that was submitted.
  const draftIsValidated = validation?.valid === true && validatedFingerprint === requestFingerprint

  const fieldCount = draft.sections.reduce((total, section) => total + section.fields.length, 0)

  function handleDraftChange(next: SchemaDraftDefinition): void {
    setDraft(next)
    setConfirmed(false)
  }

  async function handleValidate(): Promise<void> {
    const result = await validateAction.run(request)
    setValidation(result)
    if (result && result.valid) {
      setValidatedFingerprint(requestFingerprint)
      setConfirmed(false)
      setStep('confirm')
      return
    }
    setValidatedFingerprint(null)
  }

  async function handleCreate(): Promise<void> {
    if (!draftIsValidated || !confirmed) {
      return
    }
    const created = await createAction.run(request)
    if (created) {
      onCreated(created)
    }
  }

  const footer =
    step === 'draft' ? (
      <>
        <Button variant="ghost" onClick={onClose} disabled={validateAction.running}>
          Cancel
        </Button>
        <Button
          variant="primary"
          onClick={() => {
            void handleValidate()
          }}
          busy={validateAction.running}
          data-testid="schema-validate-submit"
        >
          Validate draft
        </Button>
      </>
    ) : (
      <>
        <Button variant="ghost" onClick={() => setStep('draft')} disabled={createAction.running}>
          Back to draft
        </Button>
        <Button
          variant="primary"
          onClick={() => {
            void handleCreate()
          }}
          busy={createAction.running}
          disabled={!confirmed || !draftIsValidated}
          data-testid="schema-create-submit"
        >
          Create immutable version
        </Button>
      </>
    )

  return (
    <Modal
      title="New Schema version"
      description="Define the structured knowledge TransitScholar extracts from papers. A created version is immutable."
      onClose={onClose}
      testId="schema-builder-dialog"
      size="lg"
      footer={footer}
    >
      {step === 'draft' ? (
        <div className="stack">
          <SchemaDraftEditor
            draft={draft}
            onChange={handleDraftChange}
            issues={issues}
            disabled={validateAction.running}
          />

          {validateAction.status === 'error' ? (
            <ErrorState
              error={validateAction.error}
              title="The Schema draft could not be validated"
              description="The validation result is unknown, so creation stays unavailable."
            />
          ) : null}

          {validation && !validation.valid ? (
            <div className="stack stack--tight" data-testid="schema-validation-rejected">
              <IssueSummary title="The API rejected this Schema draft" issues={issues} />
              <Note tone="warning">
                Creation stays unavailable until validation succeeds. Fix the reported issues and validate
                again.
              </Note>
            </div>
          ) : null}

          {validation && validation.valid ? (
            <Note>
              <span data-testid="schema-validation-valid">
                The API reports this draft as valid.
              </span>
            </Note>
          ) : null}

          {validation === null ? (
            <Note>
              Validating the draft calls the existing Schema validation API. Nothing is created until
              validation succeeds and you confirm the immutable version.
            </Note>
          ) : null}
        </div>
      ) : (
        <div className="stack" data-testid="schema-builder-confirm">
          <div className="warning-panel" role="alert" data-testid="schema-version-immutable-warning">
            <p className="warning-panel__title">This Schema version is immutable</p>
            <p className="warning-panel__text">
              {SCHEMA_VERSION_IMMUTABLE_STATEMENT} Existing Workspaces that bind this version keep using
              it.
            </p>
          </div>

          <dl className="detail-list" data-testid="schema-draft-review">
            <div className="detail-list__row">
              <dt>Schema ID</dt>
              <dd>
                <code>{request.schema_id}</code>
              </dd>
            </div>
            <div className="detail-list__row">
              <dt>Version</dt>
              <dd>
                <code>{request.version}</code>
              </dd>
            </div>
            <div className="detail-list__row">
              <dt>Name</dt>
              <dd>{request.name ?? '—'}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Description</dt>
              <dd>{request.description ?? '—'}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Content</dt>
              <dd>
                {request.sections.length} Section{request.sections.length === 1 ? '' : 's'} · {fieldCount}{' '}
                Field{fieldCount === 1 ? '' : 's'}
              </dd>
            </div>
          </dl>

          <ul className="record-list" data-testid="schema-draft-review-sections">
            {draft.sections.map((section) => (
              <li className="record" key={section.key}>
                <div className="record__header">
                  <span className="record__title">{section.label || section.id || 'Untitled section'}</span>
                  <code>{section.id || 'no id'}</code>
                </div>
                <ul className="plain-list">
                  {section.fields.map((field) => (
                    <li key={field.key}>
                      <code>{field.id || 'no id'}</code> · {field.label || 'Untitled field'} ·{' '}
                      {describeFieldType(field.type)}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>

          <label className="search-form__check">
            <input
              type="checkbox"
              data-testid="schema-immutable-confirm"
              checked={confirmed}
              disabled={createAction.running}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            <span>{SCHEMA_VERSION_CONFIRMATION_LABEL}</span>
          </label>

          {!validatedFingerprint || validatedFingerprint !== requestFingerprint ? (
            <Note tone="warning">
              This draft changed after validation. Return to the draft and validate it again before
              creating the version.
            </Note>
          ) : null}

          {createAction.status === 'error' ? (
            <ErrorState error={createAction.error} title="The Schema version could not be created" />
          ) : null}
        </div>
      )}
    </Modal>
  )
}
