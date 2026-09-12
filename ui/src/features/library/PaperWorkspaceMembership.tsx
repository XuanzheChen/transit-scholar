import { useMemo } from 'react'
import { api } from '../../api'
import { useActiveWorkspace } from '../../app/ActiveWorkspaceContext'
import { useApiResource } from '../../hooks/useApiResource'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { ErrorState, LoadingState, UnavailableState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'
import { Note } from '../../components/Section'

/**
 * Workspace membership for one Paper.
 *
 * "Add to workspace" and "Remove from workspace" change membership only. They
 * are deliberately different actions from deleting a Paper from the global
 * Library, which this iteration does not offer.
 */
export function PaperWorkspaceMembership({ paperId }: { paperId: string }) {
  const { activeWorkspaceId } = useActiveWorkspace()
  const workspace = useApiResource(
    (signal) =>
      activeWorkspaceId ? api.workspaces.get(activeWorkspaceId, { signal }) : Promise.resolve(null),
    [activeWorkspaceId],
  )
  const members = useApiResource(
    (signal) =>
      activeWorkspaceId
        ? api.workspaces.listPapers(activeWorkspaceId, { signal })
        : Promise.resolve(null),
    [activeWorkspaceId],
  )

  const add = useAsyncAction((workspaceId: string, id: string) =>
    api.workspaces.addPaper(workspaceId, id),
  )
  const remove = useAsyncAction((workspaceId: string, id: string) =>
    api.workspaces.removePaper(workspaceId, id),
  )

  const isMember = useMemo(
    () => (members.data?.items ?? []).some((item) => item.paper_id === paperId),
    [members.data, paperId],
  )

  if (activeWorkspaceId === null) {
    return (
      <UnavailableState
        title="No workspace is open"
        description="Open a workspace to add or remove this paper as a workspace member. The paper stays in the global Library either way."
        action={<LinkButton to="/workspaces">Choose a workspace</LinkButton>}
      />
    )
  }

  if (members.status === 'loading' || workspace.status === 'loading') {
    return <LoadingState label="Loading workspace membership…" />
  }

  if (members.status === 'error') {
    return (
      <ErrorState error={members.error} onRetry={members.reload} title="Membership unavailable" />
    )
  }

  const workspaceLabel = workspace.data?.name ?? activeWorkspaceId
  const workspaceId: string = activeWorkspaceId

  async function handleAdd(): Promise<void> {
    const result = await add.run(workspaceId, paperId)
    if (result) {
      members.reload()
    }
  }

  async function handleRemove(): Promise<void> {
    const result = await remove.run(workspaceId, paperId)
    if (result) {
      members.reload()
    }
  }

  return (
    <div className="membership" data-testid="paper-workspace-membership">
      <div className="membership__status">
        <span data-testid="paper-membership-state">
          <Badge tone={isMember ? 'success' : 'neutral'}>
            {isMember ? 'In this workspace' : 'Not in this workspace'}
          </Badge>
        </span>
        <span className="membership__workspace">{workspaceLabel}</span>
      </div>

      {isMember ? (
        <Button
          variant="danger"
          size="sm"
          busy={remove.running}
          onClick={() => {
            void handleRemove()
          }}
          data-testid="remove-paper-from-workspace"
        >
          Remove from workspace
        </Button>
      ) : (
        <Button
          variant="primary"
          size="sm"
          busy={add.running}
          onClick={() => {
            void handleAdd()
          }}
          data-testid="add-paper-to-workspace"
        >
          Add to workspace
        </Button>
      )}

      {add.status === 'done' && add.result?.already_member ? (
        <p className="card__meta">This paper was already a member of {workspaceLabel}.</p>
      ) : null}

      {add.status === 'error' ? (
        <ErrorState error={add.error} title="Paper could not be added to the workspace" />
      ) : null}

      {remove.status === 'error' ? (
        <ErrorState error={remove.error} title="Paper could not be removed from the workspace" />
      ) : null}

      <Note>
        Removing a paper from a workspace ends its membership only. The paper remains in the global
        Library and in any other workspace.
      </Note>
    </div>
  )
}
