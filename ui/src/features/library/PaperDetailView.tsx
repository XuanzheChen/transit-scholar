import { api, classifyApiFailure } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Disclosure } from '../../components/Disclosure'
import { LinkButton } from '../../components/LinkButton'
import { Note, Section } from '../../components/Section'
import { formatFileSize, formatTimestamp } from '../../lib/format'
import { BibliographyPanel } from './BibliographyPanel'
import { DuplicateRelationsPanel } from './DuplicateRelationsPanel'
import { EnrichmentPanel } from './EnrichmentPanel'
import { MetadataCandidatesPanel } from './MetadataCandidatesPanel'
import { MetadataCorrectionPanel } from './MetadataCorrectionPanel'
import { PaperLibraryLifecyclePanel } from './PaperLibraryLifecyclePanel'
import { PaperWorkspaceMembership } from './PaperWorkspaceMembership'
import {
  describePaperAuthors,
  describePaperIdentifiers,
  describeReadiness,
  describeReadinessBlocker,
  paperStatusTone,
  readinessTone,
} from './labels'

/**
 * Paper detail.
 *
 * Presents the researcher-facing view first: identity, abstract, processing
 * status, research readiness, the local PDF, workspace membership, and the
 * global Library lifecycle. The extended Library capabilities (metadata
 * correction, duplicate adjudication, bibliography, deletion/restore,
 * enrichment) are exposed here, while low-level extraction and provider
 * diagnostics stay behind advanced disclosures.
 */
export function PaperDetailView({ paperId }: { paperId: string }) {
  return (
    <Section
      title="Paper"
      description="A paper stored in the global Library. Library membership is shared by every workspace."
      actions={<LinkButton to="/library">Back to Library</LinkButton>}
    >
      <PaperDetailBody paperId={paperId} />
    </Section>
  )
}

function PaperDetailBody({ paperId }: { paperId: string }) {
  const detail = useApiResource((signal) => api.papers.detail(paperId, { signal }), [paperId])
  const readiness = useApiResource(
    (signal) => api.papers.secondLayer(paperId, { signal }),
    [paperId],
  )

  // Keep the previously loaded Paper visible while a reload is in flight, but
  // never show another Paper's data after a route change.
  const paper = detail.data && detail.data.paper_id === paperId ? detail.data : null

  if (detail.status === 'loading' && !paper) {
    return <LoadingState label="Loading paper…" />
  }

  if (detail.status === 'error' && !paper) {
    return <ErrorState error={detail.error} title="Paper unavailable" onRetry={detail.reload} />
  }

  if (!paper) {
    return (
      <EmptyState
        title="Paper not found"
        description="The backend did not return this paper. It may have been removed from the Library."
        action={<LinkButton to="/library" variant="primary">Back to Library</LinkButton>}
      />
    )
  }

  function refreshPaper(): void {
    detail.reload()
    readiness.reload()
  }

  const files = paper.files ?? []
  const primaryFile = files.find((file) => file.is_primary) ?? files[0] ?? null
  const readinessBlocks =
    readiness.data && !readiness.data.second_layer_ready
      ? readiness.data.second_layer_blockers
      : []
  const readinessKind = readiness.status === 'error' ? classifyApiFailure(readiness.error) : null

  return (
    <div className="stack">
      <div className="context-card">
        <div className="card__title-row">
          <h3 className="card__title">{paper.title ?? 'Untitled paper'}</h3>
          <Badge tone={paperStatusTone(paper.status)} testId="paper-status">
            {paper.status}
          </Badge>
        </div>
        <p className="card__meta">{describePaperAuthors(paper.authors ?? [])}</p>
        <p className="card__meta">{describePaperIdentifiers(paper)}</p>
        {paper.venue || paper.publication_year ? (
          <p className="card__meta">
            {[paper.venue, paper.publication_year ? String(paper.publication_year) : null]
              .filter(Boolean)
              .join(' · ')}
          </p>
        ) : null}
        {paper.deleted_at ? (
          <Note tone="warning">
            This paper was removed from the Library on {formatTimestamp(paper.deleted_at)}.
          </Note>
        ) : null}
        {detail.status === 'loading' ? <p className="card__meta">Refreshing the paper…</p> : null}
        {detail.status === 'error' ? (
          <ErrorState error={detail.error} title="The latest paper state could not be read" onRetry={detail.reload} />
        ) : null}
      </div>

      <div className="detail-grid">
        <div className="subsection">
          <h3 className="subsection__title">Processing status</h3>
          <p className="card__meta">
            Reported by the backend as <strong>{paper.status}</strong>.
          </p>
        </div>

        <div className="subsection" data-testid="paper-readiness">
          <div className="subsection__header">
            <h3 className="subsection__title">Research readiness</h3>
            {readiness.status === 'ready' ? (
              <Badge tone={readinessTone(readiness.data?.status)}>
                {describeReadiness(readiness.data?.status)}
              </Badge>
            ) : null}
          </div>

          {readiness.status === 'loading' ? <LoadingState label="Checking readiness…" /> : null}

          {readinessKind === 'not_found' ? (
            <Note>The backend does not report readiness for this paper.</Note>
          ) : null}

          {readiness.status === 'error' && readinessKind !== 'not_found' ? (
            <ErrorState error={readiness.error} onRetry={readiness.reload} />
          ) : null}

          {readiness.status === 'ready' && readiness.data?.second_layer_ready ? (
            <p className="card__meta">This paper can be used for research in a workspace.</p>
          ) : null}

          {readiness.status === 'ready' && readiness.data && !readiness.data.second_layer_ready ? (
            <ul className="plain-list" data-testid="paper-readiness-blockers">
              {readinessBlocks.map((blocker) => (
                <li key={blocker}>{describeReadinessBlocker(blocker)}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>

      <div className="subsection">
        <div className="subsection__header">
          <h3 className="subsection__title">Local PDF</h3>
          {primaryFile ? (
            <a
              className="btn btn--primary btn--sm"
              href={api.papers.fileContentUrl(primaryFile.file_id)}
              target="_blank"
              rel="noreferrer"
              data-testid="open-primary-pdf"
            >
              <span>Open PDF</span>
            </a>
          ) : (
            <Button size="sm" disabled title="No registered PDF file for this paper">
              Open PDF
            </Button>
          )}
        </div>

        {files.length === 0 ? (
          <EmptyState
            title="No registered PDF"
            description="The backend has no PDF file registered for this paper."
          />
        ) : (
          <ul className="file-list" data-testid="paper-file-list">
            {files.map((file) => (
              <li className="file-list__item" key={file.file_id}>
                <div>
                  <p className="file-list__name">{file.original_filename ?? 'Registered PDF'}</p>
                  <p className="card__meta">
                    {formatFileSize(file.file_size_bytes)}
                    {file.page_count ? ` · ${file.page_count} pages` : ''}
                    {file.is_primary ? ' · primary file' : ''}
                  </p>
                </div>
                <a
                  className="btn btn--secondary btn--sm"
                  href={api.papers.fileContentUrl(file.file_id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <span>Open</span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="subsection">
        <h3 className="subsection__title">Abstract</h3>
        {paper.abstract ? (
          <p className="paper-abstract">{paper.abstract}</p>
        ) : (
          <p className="card__meta">
            No abstract is recorded for this paper. Abstracts are extracted by the backend when available.
          </p>
        )}
      </div>

      <div className="subsection" data-testid="paper-metadata-correction">
        <h3 className="subsection__title">Metadata correction</h3>
        <p className="card__meta">
          The current values come from the backend. Submitting applies the changed fields and reloads
          the paper from the API.
        </p>
        <MetadataCorrectionPanel paper={paper} onUpdated={refreshPaper} />
      </div>

      <div className="subsection" data-testid="paper-duplicate-review">
        <h3 className="subsection__title">Duplicate review</h3>
        <p className="card__meta">
          Possible duplicates involving this paper, as reported by the duplicate relations endpoint.
        </p>
        <DuplicateRelationsPanel paperId={paper.paper_id} onResolved={refreshPaper} />
      </div>

      <div className="subsection" data-testid="paper-bibliography-section">
        <h3 className="subsection__title">Bibliography</h3>
        <p className="card__meta">
          Bibliographic citation records the backend parsed for this paper.
        </p>
        <BibliographyPanel paperId={paper.paper_id} />
      </div>

      <div className="subsection">
        <h3 className="subsection__title">Workspace membership</h3>
        <PaperWorkspaceMembership paperId={paper.paper_id} />
      </div>

      <div className="subsection">
        <h3 className="subsection__title">Library</h3>
        <p className="card__meta">
          Global Library actions for this paper, separate from workspace membership.
        </p>
        <PaperLibraryLifecyclePanel paper={paper} onChanged={refreshPaper} />
      </div>

      <div className="advanced">
        <h3 className="advanced__title">Advanced</h3>
        <p className="card__meta">
          Low-level extraction, enrichment, and record diagnostics. Normal paper use does not require
          these views.
        </p>

        <Disclosure summary="Metadata candidates" testId="metadata-candidates-disclosure">
          <MetadataCandidatesPanel paperId={paper.paper_id} />
        </Disclosure>

        <Disclosure summary="Enrichment status" testId="enrichment-disclosure">
          <EnrichmentPanel paperId={paper.paper_id} onRefreshed={refreshPaper} />
        </Disclosure>

        <Disclosure summary="Record details">
          <dl className="detail-list">
            <div className="detail-list__row">
              <dt>Paper</dt>
              <dd>
                <code>{paper.paper_id}</code>
              </dd>
            </div>
            <div className="detail-list__row">
              <dt>Normalized title</dt>
              <dd>{paper.normalized_title ?? 'not recorded'}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Normalized DOI</dt>
              <dd>{paper.normalized_doi ?? 'not recorded'}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Registered files</dt>
              <dd>
                {files.length > 0 ? files.map((file) => file.file_id).join(', ') : 'none'}
              </dd>
            </div>
            <div className="detail-list__row">
              <dt>Created</dt>
              <dd>{formatTimestamp(paper.created_at)}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Updated</dt>
              <dd>{formatTimestamp(paper.updated_at)}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Deleted</dt>
              <dd>{formatTimestamp(paper.deleted_at)}</dd>
            </div>
            {readinessBlocks.length > 0 ? (
              <div className="detail-list__row">
                <dt>Readiness codes</dt>
                <dd>
                  <code>{readinessBlocks.join(', ')}</code>
                </dd>
              </div>
            ) : null}
          </dl>
        </Disclosure>
      </div>
    </div>
  )
}
