import { useState } from 'react'
import { api, toApiError } from '../../api'
import type { PaperDetail } from '../../api'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Button } from '../../components/Button'
import { ErrorState } from '../../components/AsyncState'
import { Modal } from '../../components/Modal'
import { Note } from '../../components/Section'
import { formatTimestamp } from '../../lib/format'
import { isLibraryRestorable } from './labels'

/**
 * Global Library lifecycle for one Paper: deletion and restoration.
 *
 * This is deliberately a different action from Workspace membership removal.
 * "Remove from workspace" (rendered by {@link PaperWorkspaceMembership}) only
 * ends membership; "Delete from Library" removes the paper from the whole local
 * Library and always requires an explicit confirmation before the delete request
 * is sent. Restoration is offered only when the API reports a deleted state.
 */
export function PaperLibraryLifecyclePanel({
  paper,
  onChanged,
}: {
  paper: PaperDetail
  onChanged: () => void
}) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const remove = useAsyncAction(() => api.papers.removeFromLibrary(paper.paper_id))
  const restore = useAsyncAction(() => api.papers.restoreToLibrary(paper.paper_id))

  const restorable = isLibraryRestorable(paper)
  const removeErrorCode = remove.status === 'error' ? toApiError(remove.error).code : null

  async function handleDelete(): Promise<void> {
    const result = await remove.run()
    if (result) {
      setConfirmOpen(false)
      onChanged()
    }
  }

  async function handleRestore(): Promise<void> {
    const result = await restore.run()
    if (result) {
      onChanged()
    }
  }

  return (
    <div className="warning-panel warning-panel--static" data-testid="library-deletion">
      <p className="warning-panel__title">Delete from Library</p>

      {restorable ? (
        <>
          <p className="warning-panel__text" data-testid="library-deletion-deleted-state">
            This paper was deleted from the global Library
            {paper.deleted_at ? ` on ${formatTimestamp(paper.deleted_at)}` : ''}. It is not listed in
            the Library unless deleted papers are shown, and it is not available to any workspace.
          </p>
          <div className="action-row">
            <Button
              variant="primary"
              size="sm"
              busy={restore.running}
              onClick={() => {
                void handleRestore()
              }}
              data-testid="restore-library-paper"
            >
              Restore to Library
            </Button>
          </div>
          {restore.status === 'error' ? (
            <ErrorState error={restore.error} title="Paper could not be restored" />
          ) : null}
          {restore.status === 'done' && restore.result ? (
            <p className="card__meta" data-testid="restore-library-result">
              The backend reports the paper status as {restore.result.status}.
            </p>
          ) : null}
        </>
      ) : (
        <>
          <p className="warning-panel__text" data-testid="library-deletion-scope">
            Deleting removes this paper and its PDF from the global TransitScholar Library for every
            workspace. This is different from “Remove from workspace”, which only ends membership in
            one workspace and keeps the paper in the Library.
          </p>
          <div className="action-row">
            <Button
              variant="danger"
              size="sm"
              onClick={() => setConfirmOpen(true)}
              data-testid="delete-library-paper"
            >
              Delete from Library…
            </Button>
          </div>
          {removeErrorCode === 'PAPER_IN_USE' ? (
            <Note tone="warning">
              A paper that belongs to an active workspace cannot be deleted. Remove it from those
              workspaces first.
            </Note>
          ) : null}
          {remove.status === 'error' ? (
            <ErrorState error={remove.error} title="Paper could not be deleted from the Library" />
          ) : null}
          {remove.status === 'done' && remove.result ? (
            <p className="card__meta" data-testid="delete-library-result">
              The backend reports the paper status as {remove.result.status}.
            </p>
          ) : null}
        </>
      )}

      {confirmOpen ? (
        <Modal
          title="Delete this paper from the Library?"
          description="This is a Library-wide action, not a workspace change."
          onClose={() => setConfirmOpen(false)}
          testId="delete-library-confirm-dialog"
          footer={
            <>
              <Button onClick={() => setConfirmOpen(false)} data-testid="cancel-delete-library-paper">
                Cancel
              </Button>
              <Button
                variant="danger"
                busy={remove.running}
                onClick={() => {
                  void handleDelete()
                }}
                data-testid="confirm-delete-library-paper"
              >
                Delete from Library
              </Button>
            </>
          }
        >
          <p>
            {paper.title ?? 'This paper'} will be deleted from the global TransitScholar Library and
            will no longer be available to any workspace.
          </p>
          <p className="card__meta">
            Its registered local PDF moves to the Library trash. The paper can be restored later
            while the backend keeps it in a deleted state. “Remove from workspace” is a different
            action and never deletes a paper from the Library.
          </p>
        </Modal>
      ) : null}
    </div>
  )
}
