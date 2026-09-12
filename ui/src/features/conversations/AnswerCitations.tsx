import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { api } from '../../api'
import type { AnswerEvidenceCitation } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { ErrorState, LoadingState } from '../../components/AsyncState'
import { Modal } from '../../components/Modal'
import { Note } from '../../components/Section'
import { firstPage, formatPageList, pdfUrlForPage } from '../../lib/pdf'

export interface AnswerCitationsProps {
  citations: AnswerEvidenceCitation[]
}

function citationTitle(citation: AnswerEvidenceCitation): string {
  return citation.paper_title ?? citation.paper_id ?? 'Unidentified source'
}

/**
 * Citation references for a completed answer.
 *
 * The citation model comes from the API (`answer_citations`); the UI never
 * derives citations from answer text. Selecting a reference exposes the paper,
 * page locator, evidence excerpt, and evidence/source identity, and offers the
 * registered local PDF through the existing file-content endpoint (REQ-007).
 *
 * Ergonomics added in the polish iteration: arrow-key movement between the
 * reference chips, previous/next stepping inside the detail dialog, and an
 * optional `#page=` viewer hint so a cited page opens directly in the browser's
 * native PDF viewer. The plain file-content URL stays the default open target.
 */
export function AnswerCitations({ citations }: AnswerCitationsProps) {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const listRef = useRef<HTMLOListElement>(null)

  const moveSelection = useCallback(
    (delta: number) => {
      setSelectedIndex((current) => {
        if (current === null) {
          return current
        }
        const next = current + delta
        return next < 0 || next >= citations.length ? current : next
      })
    },
    [citations.length],
  )

  if (citations.length === 0) {
    return null
  }

  function handleListKeyDown(event: ReactKeyboardEvent<HTMLOListElement>): void {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
      return
    }
    const chips = Array.from(
      listRef.current?.querySelectorAll<HTMLButtonElement>('.citation-chip') ?? [],
    )
    const focusedIndex = chips.findIndex((chip) => chip === document.activeElement)
    if (focusedIndex === -1) {
      return
    }
    const nextIndex = event.key === 'ArrowRight' ? focusedIndex + 1 : focusedIndex - 1
    if (nextIndex < 0 || nextIndex >= chips.length) {
      return
    }
    event.preventDefault()
    chips[nextIndex].focus()
  }

  const selected = selectedIndex === null ? null : citations[selectedIndex] ?? null

  return (
    <div className="citations" data-testid="answer-citations">
      <p className="citations__heading">
        Answer citations · {citations.length}
      </p>
      <ol
        className="citations__list"
        ref={listRef}
        onKeyDown={handleListKeyDown}
        aria-label="Answer citations"
      >
        {citations.map((citation, index) => {
          const pages = formatPageList(citation.pages)
          return (
            <li key={citation.evidence_id}>
              <button
                type="button"
                className={`citation-chip${
                  selectedIndex === index ? ' citation-chip--active' : ''
                }`}
                onClick={() => setSelectedIndex(index)}
                data-testid={`citation-reference-${index + 1}`}
                title={`Citation ${index + 1}: ${citationTitle(citation)}${pages ? ` · ${pages}` : ''}`}
              >
                <span className="citation-chip__label">[{index + 1}]</span>
                <span className="citation-chip__title">{citationTitle(citation)}</span>
                {pages ? <span className="citation-chip__pages">{pages}</span> : null}
              </button>
            </li>
          )
        })}
      </ol>

      {selected ? (
        <CitationDetailDialog
          citation={selected}
          label={(selectedIndex ?? 0) + 1}
          total={citations.length}
          onNavigate={moveSelection}
          onClose={() => setSelectedIndex(null)}
        />
      ) : null}
    </div>
  )
}

interface CitationDetailDialogProps {
  citation: AnswerEvidenceCitation
  label: number
  total: number
  onNavigate: (delta: number) => void
  onClose: () => void
}

function CitationDetailDialog({
  citation,
  label,
  total,
  onNavigate,
  onClose,
}: CitationDetailDialogProps) {
  const paperId = citation.paper_id
  const pagesText = formatPageList(citation.pages)
  const targetPage = firstPage(citation.pages)

  const files = useApiResource(
    (signal) => (paperId ? api.papers.files(paperId, { signal }) : Promise.resolve([])),
    [paperId],
  )

  const primaryFile = files.data?.find((file) => file.is_primary) ?? files.data?.[0] ?? null
  const pdfHref = primaryFile ? api.papers.fileContentUrl(primaryFile.file_id) : null
  const pagedPdfHref = pdfHref && targetPage !== null ? pdfUrlForPage(pdfHref, targetPage) : null

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) {
        return
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault()
        onNavigate(1)
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        onNavigate(-1)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onNavigate])

  return (
    <Modal
      title={`Citation [${label}]`}
      description="Provenance recorded by the agent for this answer."
      onClose={onClose}
      testId="citation-detail-dialog"
      size="lg"
      footer={
        <>
          <button
            type="button"
            className="btn btn--ghost btn--md"
            onClick={() => onNavigate(-1)}
            disabled={label <= 1}
            data-testid="citation-previous"
          >
            Previous
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--md"
            onClick={() => onNavigate(1)}
            disabled={label >= total}
            data-testid="citation-next"
          >
            Next
          </button>
          <button type="button" className="btn btn--ghost btn--md" onClick={onClose}>
            Close
          </button>
          {pagedPdfHref ? (
            <a
              className="btn btn--secondary btn--md"
              href={pagedPdfHref}
              target="_blank"
              rel="noreferrer"
              data-testid="open-citation-pdf-page"
              title={`Open the browser PDF viewer at page ${targetPage}`}
            >
              Open at p. {targetPage}
            </a>
          ) : null}
          {pdfHref ? (
            <a
              className="btn btn--primary btn--md"
              href={pdfHref}
              target="_blank"
              rel="noreferrer"
              data-testid="open-citation-pdf"
            >
              <span>Open PDF</span>
            </a>
          ) : null}
        </>
      }
    >
      <p className="citation-detail__position" data-testid="citation-position">
        Citation {label} of {total}
        {pagesText ? ` · ${pagesText}` : ''}
      </p>

      <dl className="detail-list">
        <div className="detail-list__row">
          <dt>Label</dt>
          <dd>[{label}]</dd>
        </div>
        <div className="detail-list__row">
          <dt>Paper title</dt>
          <dd>{citation.paper_title ?? 'not reported'}</dd>
        </div>
        <div className="detail-list__row">
          <dt>Paper identity</dt>
          <dd>
            <code>{citation.paper_id ?? 'not reported'}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Page locator</dt>
          <dd>
            {citation.pages && citation.pages.length > 0
              ? citation.pages.map((page) => `p. ${page}`).join(', ')
              : 'not reported'}
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Evidence excerpt</dt>
          <dd className="citation-detail__quote">
            {citation.evidence_quote ? `“${citation.evidence_quote}”` : 'not reported'}
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Evidence identity</dt>
          <dd>
            <code>{citation.evidence_id}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Source kind</dt>
          <dd>{citation.source_kind}</dd>
        </div>
        <div className="detail-list__row">
          <dt>Source block</dt>
          <dd>
            <code>{citation.block_id ?? 'not reported'}</code>
          </dd>
        </div>
        {citation.character_start !== null || citation.character_end !== null ? (
          <div className="detail-list__row">
            <dt>Source span</dt>
            <dd>
              {citation.character_start ?? '?'}–{citation.character_end ?? '?'}
            </dd>
          </div>
        ) : null}
        <div className="detail-list__row">
          <dt>Research session</dt>
          <dd>
            <code>{citation.research_session_id}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Parse run</dt>
          <dd>
            <code>{citation.parse_run_id ?? 'not reported'}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Source version</dt>
          <dd>
            <code>{citation.canonical_source_version ?? 'not reported'}</code>
          </dd>
        </div>
      </dl>

      <div className="citation-detail__pdf">
        {files.status === 'loading' ? <LoadingState label="Locating the local PDF…" /> : null}
        {files.status === 'error' ? (
          <ErrorState error={files.error} title="The local PDF could not be located" />
        ) : null}
        {files.status === 'ready' && !primaryFile ? (
          <Note>No registered PDF file is available for this paper.</Note>
        ) : null}
        {primaryFile ? (
          <Note>
            Opening uses the browser&apos;s built-in PDF viewer. The file is served by the local
            TransitScholar server. Registered as {primaryFile.original_filename ?? primaryFile.file_id}
            {primaryFile.page_count !== null ? ` · ${primaryFile.page_count} pages` : ''}
            {primaryFile.file_size_bytes !== null ? ` · ${primaryFile.file_size_bytes} bytes` : ''}.
          </Note>
        ) : null}
      </div>
    </Modal>
  )
}
