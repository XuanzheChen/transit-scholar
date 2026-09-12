import { useMemo, useState } from 'react'
import { api } from '../../api'
import type { PaperImportResponse } from '../../api'
import { useActiveWorkspace } from '../../app/ActiveWorkspaceContext'
import { navigate, useLocation } from '../../app/router'
import { useApiResource } from '../../hooks/useApiResource'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'
import { Note, Section } from '../../components/Section'
import { ImportPaperDialog } from './ImportPaperDialog'
import { PaperDetailView } from './PaperDetailView'
import {
  describePaperIdentifiers,
  describePaperIdentity,
  isLibraryRestorable,
  paperStatusTone,
} from './labels'

/**
 * Library section.
 *
 * `/library` lists the local papers and imports new PDFs. `/library/{id}` shows
 * one paper's metadata, readiness, local PDF, workspace membership, and the
 * extended management actions (correction, duplicates, bibliography, deletion,
 * restore, enrichment). Every value comes from the Papers API; the Library is
 * global and not scoped to a workspace.
 */
export function LibraryView() {
  const location = useLocation()
  const pathname = location.split('?')[0]
  const match = /^\/library\/([^/]+)\/?$/.exec(pathname)

  if (match) {
    return <PaperDetailView paperId={decodeURIComponent(match[1])} />
  }

  return <LibraryList />
}

function LibraryList() {
  const { activeWorkspaceId } = useActiveWorkspace()
  const [includeDeleted, setIncludeDeleted] = useState(false)
  const resource = useApiResource(
    (signal) => api.papers.list({ signal, limit: 100, includeDeleted }),
    [includeDeleted],
  )
  const workspace = useApiResource(
    (signal) =>
      activeWorkspaceId ? api.workspaces.get(activeWorkspaceId, { signal }) : Promise.resolve(null),
    [activeWorkspaceId],
  )
  const membership = useApiResource(
    (signal) =>
      activeWorkspaceId
        ? api.workspaces.listPapers(activeWorkspaceId, { signal })
        : Promise.resolve(null),
    [activeWorkspaceId],
  )
  const restore = useAsyncAction((paperId: string) => api.papers.restoreToLibrary(paperId))
  const [importOpen, setImportOpen] = useState(false)
  const papers = resource.data?.items ?? []

  const memberIds = useMemo(
    () => new Set((membership.data?.items ?? []).map((item) => item.paper_id)),
    [membership.data],
  )

  function handleImported(result: PaperImportResponse): void {
    setImportOpen(false)
    resource.reload()
    if (result.paper_id) {
      navigate(`/library/${encodeURIComponent(result.paper_id)}`)
    }
  }

  async function handleRestore(paperId: string): Promise<void> {
    const result = await restore.run(paperId)
    if (result) {
      resource.reload()
    }
  }

  return (
    <Section
      title="Library"
      description="The local collection of papers available to every workspace."
      actions={
        <Button variant="primary" onClick={() => setImportOpen(true)} data-testid="import-pdf-button">
          Import PDF
        </Button>
      }
    >
      <div className="library-toolbar">
        <label className="search-form__check">
          <input
            type="checkbox"
            checked={includeDeleted}
            onChange={(event) => setIncludeDeleted(event.target.checked)}
            data-testid="library-include-deleted"
          />
          <span>Show papers deleted from the Library</span>
        </label>
        <span className="card__meta">
          Deleted papers stay out of the active Library until they are restored.
        </span>
      </div>

      {resource.status === 'loading' ? <LoadingState label="Loading library…" /> : null}

      {resource.status === 'error' ? <ErrorState error={resource.error} onRetry={resource.reload} /> : null}

      {resource.status === 'ready' && papers.length === 0 ? (
        <EmptyState
          title="The library is empty"
          description={
            includeDeleted
              ? 'No papers, including deleted papers, are recorded in this local Library.'
              : 'Import a PDF to add the first paper to this local Library.'
          }
          action={
            <Button variant="primary" onClick={() => setImportOpen(true)}>
              Import PDF
            </Button>
          }
        />
      ) : null}

      {restore.status === 'error' ? (
        <ErrorState error={restore.error} title="Paper could not be restored to the Library" />
      ) : null}

      {resource.status === 'ready' && papers.length > 0 ? (
        <ul className="card-list" data-testid="library-paper-list">
          {papers.map((paper) => {
            const inWorkspace = activeWorkspaceId !== null && memberIds.has(paper.paper_id)
            const deleted = isLibraryRestorable(paper)
            return (
              <li className="card" key={paper.paper_id} data-testid={`paper-card-${paper.paper_id}`}>
                <div className="card__main">
                  <div className="card__title-row">
                    <h3 className="card__title">{paper.title ?? 'Untitled paper'}</h3>
                    <Badge tone={paperStatusTone(paper.status)}>{paper.status}</Badge>
                    {deleted ? <Badge tone="warning">Deleted from Library</Badge> : null}
                    {inWorkspace && !deleted ? <Badge tone="info">In workspace</Badge> : null}
                  </div>
                  <p className="card__meta">{describePaperIdentity(paper)}</p>
                  <p className="card__meta">{describePaperIdentifiers(paper)}</p>
                </div>
                <div className="card__actions">
                  <LinkButton to={`/library/${encodeURIComponent(paper.paper_id)}`} size="sm">
                    Details
                  </LinkButton>
                  {deleted ? (
                    <Button
                      variant="primary"
                      size="sm"
                      busy={restore.running}
                      onClick={() => {
                        void handleRestore(paper.paper_id)
                      }}
                      data-testid={`restore-library-paper-${paper.paper_id}`}
                    >
                      Restore to Library
                    </Button>
                  ) : paper.primary_file_id ? (
                    <a
                      className="btn btn--secondary btn--sm"
                      href={api.papers.fileContentUrl(paper.primary_file_id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <span>Open PDF</span>
                    </a>
                  ) : (
                    <Button size="sm" disabled title="No registered PDF file for this paper">
                      Open PDF
                    </Button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}

      {activeWorkspaceId !== null && membership.status === 'error' ? (
        <ErrorState error={membership.error} title="Workspace membership unavailable" onRetry={membership.reload} />
      ) : null}

      {activeWorkspaceId !== null ? (
        <Note>
          The “In workspace” marker reflects membership in {workspace.data?.name ?? 'the open workspace'}.
          Open a paper to add or remove that membership. Removing a paper from a workspace never deletes it
          from the Library, and deleting a paper from the Library is a separate, confirmed action.
        </Note>
      ) : (
        <Note>
          No workspace is open, so workspace membership is not shown. Open a workspace to manage which
          Library papers it uses.
        </Note>
      )}

      {importOpen ? (
        <ImportPaperDialog onClose={() => setImportOpen(false)} onImported={handleImported} />
      ) : null}
    </Section>
  )
}
