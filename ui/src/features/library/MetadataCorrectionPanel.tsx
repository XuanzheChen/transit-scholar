import { useId, useMemo, useState } from 'react'
import { api } from '../../api'
import type { MetadataUpdateRequest, PaperDetail } from '../../api'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Button } from '../../components/Button'
import { ErrorState } from '../../components/AsyncState'
import { Field, TextInput, Textarea } from '../../components/Form'
import { Note } from '../../components/Section'

/**
 * Metadata correction for one Paper.
 *
 * The form starts from the values the API reports, submits only the changed
 * fields through `PATCH /api/v1/papers/{id}/metadata`, and then asks the caller
 * to reload the Paper so the displayed values always come from the API again.
 *
 * The API applies only the fields present in the request body
 * (`exclude_none`), so an emptied control is reported as unchanged instead of
 * being sent as a local guess about how to clear a value.
 */
export function MetadataCorrectionPanel({
  paper,
  onUpdated,
}: {
  paper: PaperDetail
  onUpdated: () => void
}) {
  const fieldId = useId()
  const [form, setForm] = useState<MetadataForm>(() => formFromPaper(paper))

  // Reset the form whenever the API reports a different Paper revision (for
  // example after a successful correction, or when the routed paper changes).
  // This is React's documented "adjust state when a prop changes" pattern, so
  // the form never keeps a stale local copy of API state.
  const revisionKey = `${paper.paper_id}:${paper.updated_at ?? ''}:${paper.deleted_at ?? ''}`
  const [loadedKey, setLoadedKey] = useState(revisionKey)
  if (loadedKey !== revisionKey) {
    setLoadedKey(revisionKey)
    setForm(formFromPaper(paper))
  }

  const save = useAsyncAction((payload: MetadataUpdateRequest) =>
    api.papers.updateMetadata(paper.paper_id, payload),
  )

  const change = useMemo(() => buildMetadataChange(form, paper), [form, paper])
  const hasChanges = Object.keys(change.payload).length > 0

  async function handleSubmit(): Promise<void> {
    // A locally detectable input problem blocks the request; the API is never
    // asked to interpret an invalid value.
    if (change.problem) {
      return
    }
    const result = await save.run(change.payload)
    if (result) {
      onUpdated()
    }
  }

  function update<K extends keyof MetadataForm>(key: K, value: string): void {
    setForm((current) => ({ ...current, [key]: value }))
  }

  return (
    <form
      className="stack stack--tight"
      data-testid="metadata-correction-form"
      onSubmit={(event) => {
        event.preventDefault()
        void handleSubmit()
      }}
    >
      <Field label="Title" htmlFor={`${fieldId}-title`}>
        <TextInput
          id={`${fieldId}-title`}
          value={form.title}
          onChange={(event) => update('title', event.target.value)}
          data-testid="metadata-title-input"
        />
      </Field>

      <div className="field-row">
        <Field label="Venue" htmlFor={`${fieldId}-venue`}>
          <TextInput
            id={`${fieldId}-venue`}
            value={form.venue}
            onChange={(event) => update('venue', event.target.value)}
            data-testid="metadata-venue-input"
          />
        </Field>
        <Field label="Publication year" htmlFor={`${fieldId}-year`}>
          <TextInput
            id={`${fieldId}-year`}
            inputMode="numeric"
            value={form.publication_year}
            onChange={(event) => update('publication_year', event.target.value)}
            data-testid="metadata-year-input"
          />
        </Field>
      </div>

      <div className="field-row">
        <Field label="DOI" htmlFor={`${fieldId}-doi`}>
          <TextInput
            id={`${fieldId}-doi`}
            value={form.doi}
            onChange={(event) => update('doi', event.target.value)}
            data-testid="metadata-doi-input"
          />
        </Field>
        <Field label="arXiv identifier" htmlFor={`${fieldId}-arxiv`}>
          <TextInput
            id={`${fieldId}-arxiv`}
            value={form.arxiv_id}
            onChange={(event) => update('arxiv_id', event.target.value)}
            data-testid="metadata-arxiv-input"
          />
        </Field>
      </div>

      <Field
        label="Authors"
        htmlFor={`${fieldId}-authors`}
        description="One author name per line, in publication order."
      >
        <Textarea
          id={`${fieldId}-authors`}
          rows={3}
          value={form.authors}
          onChange={(event) => update('authors', event.target.value)}
          data-testid="metadata-authors-input"
        />
      </Field>

      <Field label="Abstract" htmlFor={`${fieldId}-abstract`}>
        <Textarea
          id={`${fieldId}-abstract`}
          rows={5}
          value={form.abstract}
          onChange={(event) => update('abstract', event.target.value)}
          data-testid="metadata-abstract-input"
        />
      </Field>

      {change.problem ? (
        <Note tone="warning">
          <span data-testid="metadata-correction-problem">{change.problem}</span>
        </Note>
      ) : null}

      {save.status === 'error' ? (
        <ErrorState error={save.error} title="Metadata correction was rejected" />
      ) : null}

      {save.status === 'done' && save.result ? (
        <p className="card__meta" data-testid="metadata-correction-result">
          Saved {save.result.updated_fields.length > 0 ? save.result.updated_fields.join(', ') : 'no fields'}.
          The form and title above were re-read from the API.
        </p>
      ) : null}

      <div className="action-row">
        <Button
          type="submit"
          variant="primary"
          busy={save.running}
          disabled={!hasChanges}
          data-testid="metadata-correction-submit"
        >
          Save metadata
        </Button>
        <span className="card__meta">
          {hasChanges
            ? `Fields to submit: ${Object.keys(change.payload).join(', ')}`
            : 'No metadata changes to submit.'}
        </span>
      </div>

      <Note>
        Corrections are applied by the backend and re-read from it. Empty controls are left
        unchanged because the metadata endpoint applies only the fields sent with the request.
      </Note>
    </form>
  )
}

interface MetadataForm {
  title: string
  venue: string
  doi: string
  arxiv_id: string
  publication_year: string
  abstract: string
  authors: string
}

/** Correctable free-text fields accepted by the metadata endpoint. */
type EditableTextField = 'title' | 'venue' | 'doi' | 'arxiv_id' | 'abstract'

function formFromPaper(paper: PaperDetail): MetadataForm {
  return {
    title: paper.title ?? '',
    venue: paper.venue ?? '',
    doi: paper.doi ?? '',
    arxiv_id: paper.arxiv_id ?? '',
    publication_year: paper.publication_year === null ? '' : String(paper.publication_year),
    abstract: paper.abstract ?? '',
    authors: authorNames(paper).join('\n'),
  }
}

function authorNames(paper: PaperDetail): string[] {
  return (paper.authors ?? [])
    .map((author) => (typeof author.full_name === 'string' ? author.full_name.trim() : ''))
    .filter((name) => name.length > 0)
}

/**
 * Build the correction payload from the form and the API-reported Paper.
 *
 * Only genuinely different, non-empty values are sent; the returned `problem`
 * describes a locally detectable input problem without inventing backend state.
 */
function buildMetadataChange(
  form: MetadataForm,
  paper: PaperDetail,
): { payload: MetadataUpdateRequest; problem: string | null } {
  const payload: MetadataUpdateRequest = {}

  const textFields: { key: EditableTextField; current: string | null }[] = [
    { key: 'title', current: paper.title },
    { key: 'venue', current: paper.venue },
    { key: 'doi', current: paper.doi },
    { key: 'arxiv_id', current: paper.arxiv_id },
    { key: 'abstract', current: paper.abstract },
  ]
  for (const { key, current } of textFields) {
    const value = form[key].trim()
    if (value.length > 0 && value !== (current ?? '').trim()) {
      payload[key] = value
    }
  }

  const year = form.publication_year.trim()
  if (year.length > 0) {
    if (!/^\d{4}$/.test(year)) {
      return { payload, problem: 'Publication year must be a four-digit year.' }
    }
    if (Number(year) !== paper.publication_year) {
      payload.publication_year = Number(year)
    }
  } else if (paper.publication_year !== null) {
    return {
      payload,
      problem:
        'Publication year cannot be cleared from this form; enter a corrected four-digit year instead.',
    }
  }

  const names = form.authors
    .split('\n')
    .map((name) => name.trim())
    .filter((name) => name.length > 0)
  const currentNames = authorNames(paper)
  if (names.join('\n') !== currentNames.join('\n')) {
    if (names.length === 0) {
      return {
        payload,
        problem: 'At least one author name is required; the author list cannot be emptied.',
      }
    }
    payload.authors = names
  }

  return { payload, problem: null }
}
