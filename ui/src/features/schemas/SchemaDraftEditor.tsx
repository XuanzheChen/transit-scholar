import { useId } from 'react'
import { Button } from '../../components/Button'
import { Field, Select, Textarea, TextInput } from '../../components/Form'
import { Note } from '../../components/Section'
import { SCHEMA_VERSION_IMMUTABLE_STATEMENT, SCHEMA_FIELD_TYPE_OPTIONS } from './labels'
import {
  describeIssue,
  issuesForField,
  issuesForSection,
  withFieldAdded,
  withFieldRemoved,
  withFieldUpdated,
  withSectionAdded,
  withSectionRemoved,
  withSectionUpdated,
} from './schemaDraft'
import type { SchemaDraftDefinition, SchemaDraftField, SchemaDraftIssue } from './schemaDraft'

export interface SchemaDraftEditorProps {
  draft: SchemaDraftDefinition
  onChange: (draft: SchemaDraftDefinition) => void
  /** API-reported validation issues; shown beside the matching draft content. */
  issues: SchemaDraftIssue[]
  disabled?: boolean
}

function IssueList({ issues, testId }: { issues: SchemaDraftIssue[]; testId: string }) {
  if (issues.length === 0) {
    return null
  }
  return (
    <ul className="draft-issues" data-testid={testId}>
      {issues.map((issue, index) => (
        <li className="draft-issues__item" key={`${issue.type}-${index}`} data-testid="schema-draft-issue">
          {describeIssue(issue)}
        </li>
      ))}
    </ul>
  )
}

/**
 * Structured Schema draft form.
 *
 * The user edits identity, metadata, Sections, and Fields. Every input maps
 * directly onto the frozen Schema definition shape, and API validation issues
 * are rendered beside the Section or Field they belong to.
 */
export function SchemaDraftEditor({ draft, onChange, issues, disabled = false }: SchemaDraftEditorProps) {
  const baseId = useId()

  function updateField(sectionIndex: number, fieldIndex: number, patch: Partial<Omit<SchemaDraftField, 'key'>>) {
    onChange(withFieldUpdated(draft, sectionIndex, fieldIndex, patch))
  }

  return (
    <div className="stack" data-testid="schema-draft-editor">
      <div className="field-row">
        <Field
          label="Schema ID"
          htmlFor={`${baseId}-schema-id`}
          required
          description="Stable identifier, for example transit_policy."
        >
          <TextInput
            id={`${baseId}-schema-id`}
            data-testid="schema-id-input"
            value={draft.schemaId}
            disabled={disabled}
            placeholder="transit_policy"
            onChange={(event) => onChange({ ...draft, schemaId: event.target.value })}
          />
        </Field>

        <Field
          label="Version"
          htmlFor={`${baseId}-version`}
          required
          description="A version is created once and never edited."
        >
          <TextInput
            id={`${baseId}-version`}
            data-testid="schema-version-input"
            value={draft.version}
            disabled={disabled}
            placeholder="1.0"
            onChange={(event) => onChange({ ...draft, version: event.target.value })}
          />
        </Field>
      </div>

      <Field label="Name" htmlFor={`${baseId}-name`} description="Shown wherever the Schema is listed.">
        <TextInput
          id={`${baseId}-name`}
          data-testid="schema-name-input"
          value={draft.name}
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, name: event.target.value })}
        />
      </Field>

      <Field label="Description" htmlFor={`${baseId}-description`}>
        <Textarea
          id={`${baseId}-description`}
          data-testid="schema-description-input"
          rows={2}
          value={draft.description}
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, description: event.target.value })}
        />
      </Field>

      <Note tone="warning">
        <span data-testid="schema-immutability-guidance">{SCHEMA_VERSION_IMMUTABLE_STATEMENT}</span>
      </Note>

      {draft.sections.map((section, sectionIndex) => {
        const sectionIssues = issuesForSection(issues, sectionIndex)
        return (
          <section
            className="draft-section"
            key={section.key}
            data-testid={`schema-section-${sectionIndex}`}
            aria-label={`Section ${sectionIndex + 1}`}
          >
            <header className="draft-section__header">
              <h4 className="draft-section__title">Section {sectionIndex + 1}</h4>
              <div className="action-row">
                <Button
                  size="sm"
                  onClick={() => onChange(withFieldAdded(draft, sectionIndex))}
                  disabled={disabled}
                  data-testid={`schema-add-field-${sectionIndex}`}
                >
                  Add field
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onChange(withSectionRemoved(draft, sectionIndex))}
                  disabled={disabled || draft.sections.length <= 1}
                  title={
                    draft.sections.length <= 1
                      ? 'A Schema needs at least one Section'
                      : 'Remove this Section from the draft'
                  }
                  data-testid={`schema-remove-section-${sectionIndex}`}
                >
                  Remove section
                </Button>
              </div>
            </header>

            <div className="field-row">
              <Field label="Section ID" htmlFor={`${baseId}-section-${sectionIndex}-id`} required>
                <TextInput
                  id={`${baseId}-section-${sectionIndex}-id`}
                  data-testid={`schema-section-id-${sectionIndex}`}
                  value={section.id}
                  disabled={disabled}
                  placeholder="overview"
                  onChange={(event) => onChange(withSectionUpdated(draft, sectionIndex, { id: event.target.value }))}
                />
              </Field>
              <Field label="Section label" htmlFor={`${baseId}-section-${sectionIndex}-label`} required>
                <TextInput
                  id={`${baseId}-section-${sectionIndex}-label`}
                  data-testid={`schema-section-label-${sectionIndex}`}
                  value={section.label}
                  disabled={disabled}
                  placeholder="Overview"
                  onChange={(event) => onChange(withSectionUpdated(draft, sectionIndex, { label: event.target.value }))}
                />
              </Field>
            </div>

            <IssueList issues={sectionIssues} testId={`schema-section-issues-${sectionIndex}`} />

            <div className="draft-fields">
              {section.fields.map((field, fieldIndex) => {
                const fieldIssues = issuesForField(issues, sectionIndex, fieldIndex)
                const prefix = `schema-field-${sectionIndex}-${fieldIndex}`
                return (
                  <article className="draft-field" key={field.key} data-testid={prefix}>
                    <header className="draft-field__header">
                      <h5 className="draft-field__title">Field {fieldIndex + 1}</h5>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => onChange(withFieldRemoved(draft, sectionIndex, fieldIndex))}
                        disabled={disabled || section.fields.length <= 1}
                        title={
                          section.fields.length <= 1
                            ? 'A Section needs at least one Field'
                            : 'Remove this Field from the draft'
                        }
                        data-testid={`schema-remove-field-${sectionIndex}-${fieldIndex}`}
                      >
                        Remove field
                      </Button>
                    </header>

                    <div className="field-row">
                      <Field label="Field ID" htmlFor={`${baseId}-${prefix}-id`} required>
                        <TextInput
                          id={`${baseId}-${prefix}-id`}
                          data-testid={`schema-field-id-${sectionIndex}-${fieldIndex}`}
                          value={field.id}
                          disabled={disabled}
                          placeholder="research_question"
                          onChange={(event) => updateField(sectionIndex, fieldIndex, { id: event.target.value })}
                        />
                      </Field>
                      <Field label="Field label" htmlFor={`${baseId}-${prefix}-label`} required>
                        <TextInput
                          id={`${baseId}-${prefix}-label`}
                          data-testid={`schema-field-label-${sectionIndex}-${fieldIndex}`}
                          value={field.label}
                          disabled={disabled}
                          placeholder="Research question"
                          onChange={(event) => updateField(sectionIndex, fieldIndex, { label: event.target.value })}
                        />
                      </Field>
                      <Field label="Type" htmlFor={`${baseId}-${prefix}-type`}>
                        <Select
                          id={`${baseId}-${prefix}-type`}
                          data-testid={`schema-field-type-${sectionIndex}-${fieldIndex}`}
                          value={field.type}
                          disabled={disabled}
                          onChange={(event) =>
                            updateField(sectionIndex, fieldIndex, {
                              type: event.target.value as SchemaDraftField['type'],
                            })
                          }
                        >
                          {SCHEMA_FIELD_TYPE_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </Select>
                      </Field>
                    </div>

                    {field.type === 'enum' ? (
                      <Field
                        label="Options"
                        htmlFor={`${baseId}-${prefix}-options`}
                        description="Separate options with commas. An enum Field needs at least one option."
                      >
                        <TextInput
                          id={`${baseId}-${prefix}-options`}
                          data-testid={`schema-field-options-${sectionIndex}-${fieldIndex}`}
                          value={field.options}
                          disabled={disabled}
                          placeholder="high, medium, low"
                          onChange={(event) => updateField(sectionIndex, fieldIndex, { options: event.target.value })}
                        />
                      </Field>
                    ) : null}

                    <Field
                      label="Extraction question"
                      htmlFor={`${baseId}-${prefix}-question`}
                      required
                      description="The question the Agent answers from a paper."
                    >
                      <Textarea
                        id={`${baseId}-${prefix}-question`}
                        data-testid={`schema-field-question-${sectionIndex}-${fieldIndex}`}
                        rows={2}
                        value={field.question}
                        disabled={disabled}
                        onChange={(event) => updateField(sectionIndex, fieldIndex, { question: event.target.value })}
                      />
                    </Field>

                    <Field label="Description" htmlFor={`${baseId}-${prefix}-description`}>
                      <Textarea
                        id={`${baseId}-${prefix}-description`}
                        data-testid={`schema-field-description-${sectionIndex}-${fieldIndex}`}
                        rows={2}
                        value={field.description}
                        disabled={disabled}
                        onChange={(event) => updateField(sectionIndex, fieldIndex, { description: event.target.value })}
                      />
                    </Field>

                    <div className="action-row">
                      <label className="search-form__check">
                        <input
                          type="checkbox"
                          data-testid={`schema-field-evidence-${sectionIndex}-${fieldIndex}`}
                          checked={field.evidenceRequired}
                          disabled={disabled}
                          onChange={(event) =>
                            updateField(sectionIndex, fieldIndex, { evidenceRequired: event.target.checked })
                          }
                        />
                        <span>Require supporting evidence</span>
                      </label>
                      <label className="search-form__check">
                        <input
                          type="checkbox"
                          data-testid={`schema-field-inference-${sectionIndex}-${fieldIndex}`}
                          checked={field.allowInference}
                          disabled={disabled}
                          onChange={(event) =>
                            updateField(sectionIndex, fieldIndex, { allowInference: event.target.checked })
                          }
                        />
                        <span>Allow inferred values</span>
                      </label>
                    </div>

                    <IssueList issues={fieldIssues} testId={`schema-field-issues-${sectionIndex}-${fieldIndex}`} />
                  </article>
                )
              })}
            </div>
          </section>
        )
      })}

      <div className="action-row">
        <Button
          onClick={() => onChange(withSectionAdded(draft))}
          disabled={disabled}
          data-testid="schema-add-section"
        >
          Add section
        </Button>
      </div>
    </div>
  )
}
