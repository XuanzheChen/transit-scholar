import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { WikiSearchMode } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Disclosure } from '../../components/Disclosure'
import { Field, TextInput } from '../../components/Form'
import { Link } from '../../app/router'
import { Note } from '../../components/Section'
import { pluralize } from '../../lib/format'
import {
  describeAgenticEntryStatus,
  describeRetrievalMode,
  describeSearchMode,
  describeSearchSourceErrors,
  describeSearchSourceStatus,
  describeSearchStatus,
  describeWikiHitType,
  wikiHitHref,
  wikiSourceLabel,
  wikiSourceTone,
} from './labels'

/** Match modes the Wiki search API accepts. */
const SEARCH_MODES: WikiSearchMode[] = ['lexical', 'semantic']

/**
 * Wiki search over Schema Wiki content and Agent Learned entries.
 *
 * The search itself is entirely backend-side (`GET .../wiki/search`); the panel
 * only submits the term, the requested match mode, and renders the structured
 * hits. Every hit keeps its knowledge source and the retrieval mode the backend
 * used, so keyword and semantic results never blur together, and the match
 * control is offered only for modes the capabilities API reports (REQ-010).
 */
export function WikiSearchPanel({ workspaceId }: { workspaceId: string }) {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [mode, setMode] = useState<WikiSearchMode>('lexical')
  const [includeStale, setIncludeStale] = useState(false)

  const capabilities = useApiResource((signal) => api.system.capabilities(signal), [])
  const semanticAvailable = capabilities.data?.semantic_wiki_search === true
  // The API's default mode is keyword matching; never ask for a mode the
  // capabilities API has not reported as available.
  const effectiveMode: WikiSearchMode = semanticAvailable ? mode : 'lexical'

  const results = useApiResource(
    (signal) =>
      submitted
        ? api.wiki.search(workspaceId, submitted, {
            signal,
            mode: effectiveMode,
            includeStale,
            limit: 25,
          })
        : Promise.resolve(null),
    [workspaceId, submitted, effectiveMode, includeStale],
  )

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setSubmitted(query.trim())
  }

  const data = results.data
  const statusMessage = data ? describeSearchStatus(data.status) : null
  const sourceErrors = data ? describeSearchSourceErrors(data.source_errors) : null

  return (
    <section className="wiki-source" data-testid="wiki-search">
      <div className="wiki-source__header">
        <div>
          <p className="card__eyebrow">Wiki search</p>
          <p className="card__meta">
            Search Schema Wiki pages and topics together with Agent Learned entries. Every result is
            labelled with the knowledge source it came from.
          </p>
        </div>
      </div>

      <form className="search-form" onSubmit={handleSubmit} data-testid="wiki-search-form">
        <Field label="Search term" htmlFor="wiki-search-input">
          <TextInput
            id="wiki-search-input"
            data-testid="wiki-search-input"
            data-primary-input="wiki-search-input"
            value={query}
            placeholder="e.g. retrieval augmented generation"
            onChange={(event) => setQuery(event.target.value)}
          />
        </Field>

        <div className="search-form__mode" data-testid="wiki-search-mode">
          <span className="field__label" id="wiki-search-mode-label">
            Match
          </span>
          <div className="mode-toggle" role="radiogroup" aria-labelledby="wiki-search-mode-label">
            {SEARCH_MODES.map((option) => {
              const disabled = option === 'semantic' && !semanticAvailable
              const selected = effectiveMode === option
              return (
                <label
                  className={`mode-toggle__option${selected ? ' mode-toggle__option--active' : ''}${
                    disabled ? ' mode-toggle__option--disabled' : ''
                  }`}
                  key={option}
                  title={
                    disabled
                      ? 'Semantic search is not available on this local server.'
                      : undefined
                  }
                >
                  <input
                    type="radio"
                    name="wiki-search-mode"
                    value={option}
                    checked={selected}
                    disabled={disabled}
                    onChange={() => setMode(option)}
                    data-testid={`wiki-search-mode-${option}`}
                  />
                  <span>{describeSearchMode(option)}</span>
                </label>
              )
            })}
          </div>
        </div>

        <label className="search-form__check card__meta">
          <input
            type="checkbox"
            checked={includeStale}
            data-testid="wiki-search-include-stale"
            onChange={(event) => setIncludeStale(event.target.checked)}
          />
          <span>Include out-of-date Agent Learned entries</span>
        </label>

        <Button
          type="submit"
          variant="primary"
          disabled={query.trim().length === 0}
          data-testid="wiki-search-submit"
        >
          Search
        </Button>
      </form>

      {capabilities.status === 'ready' && !semanticAvailable ? (
        <Note>
          Semantic search is not available on this server, so Wiki search uses keyword matching.
        </Note>
      ) : null}
      {capabilities.status === 'error' ? (
        <ErrorState
          error={capabilities.error}
          title="Search mode availability could not be read"
          onRetry={capabilities.reload}
        />
      ) : null}

      {results.status === 'loading' ? <LoadingState label="Searching the workspace Wiki…" /> : null}
      {results.status === 'error' ? (
        <ErrorState error={results.error} onRetry={results.reload} />
      ) : null}

      {results.status === 'ready' && !data ? (
        <EmptyState
          title="Search the workspace Wiki"
          description="Enter a term above to search Schema Wiki content and Agent Learned entries."
        />
      ) : null}

      {results.status === 'ready' && data ? (
        <div className="stack" data-testid="wiki-search-output">
          <p className="card__meta" data-testid="wiki-search-summary">
            {pluralize(data.hits.length, 'result')} for “{submitted}” ·{' '}
            {describeSearchMode(effectiveMode)} matching
          </p>

          {statusMessage ? (
            <Note tone="warning">
              {statusMessage}
              {data.error_code ? ` Backend code: ${data.error_code}.` : ''}
              {sourceErrors ? ` ${sourceErrors}` : ''}
            </Note>
          ) : null}

          {data.hits.length === 0 ? (
            <EmptyState
              title="No matching knowledge"
              description={`No Schema Wiki content or Agent Learned entry matched “${submitted}” with ${describeSearchMode(
                effectiveMode,
              ).toLowerCase()} matching.`}
            />
          ) : (
            <ul className="card-list" data-testid="wiki-search-results">
              {data.hits.map((hit) => (
                <li
                  className="wiki-hit"
                  key={`${hit.source_kind}-${hit.type}-${hit.object_id}`}
                  data-testid="wiki-search-hit"
                >
                  <div className="card__title-row">
                    <Badge tone={wikiSourceTone(hit.source_kind)} testId="wiki-hit-source">
                      {wikiSourceLabel(hit.source_kind)}
                    </Badge>
                    <Badge tone="neutral">{describeWikiHitType(hit.type)}</Badge>
                    <Link to={wikiHitHref(hit)} className="wiki-hit__title">
                      {hit.title}
                    </Link>
                    {hit.lifecycle_status && hit.lifecycle_status !== 'active' ? (
                      <Badge tone="warning">{describeAgenticEntryStatus(hit.lifecycle_status)}</Badge>
                    ) : null}
                  </div>
                  <p className="wiki-hit__snippet">{hit.snippet}</p>
                  <p className="card__meta">
                    {describeRetrievalMode(hit.retrieval_mode)} · score {hit.score.toFixed(2)}
                    {hit.fusion_score !== null ? ` · combined ${hit.fusion_score.toFixed(2)}` : ''}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {data.status !== 'ok' && Object.keys(data.source_status).length > 0 ? (
            <Disclosure summary="Knowledge sources searched" testId="wiki-search-sources">
              <dl className="detail-list" data-testid="wiki-search-source-list">
                {Object.entries(data.source_status).map(([source, status]) => (
                  <div className="detail-list__row" key={source}>
                    <dt>{wikiSourceLabel(source)}</dt>
                    <dd>{describeSearchSourceStatus(status)}</dd>
                  </div>
                ))}
              </dl>
            </Disclosure>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
