import { api } from '../../api'
import { useActiveWorkspace } from '../../app/ActiveWorkspaceContext'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { ErrorState } from '../../components/AsyncState'
import { Link } from '../../app/router'
import { Spinner } from '../../components/Spinner'
import { describeWorkspaceSchema, workspaceStatusTone } from './labels'

/**
 * Sidebar block showing the currently open Workspace.
 *
 * The selection itself is a UI navigation preference; the name, status and
 * Schema binding always come from `GET /api/v1/workspaces/{id}`.
 */
export function ActiveWorkspaceBar() {
  const { activeWorkspaceId, selectWorkspace } = useActiveWorkspace()
  const resource = useApiResource(
    (signal) => (activeWorkspaceId ? api.workspaces.get(activeWorkspaceId, { signal }) : Promise.resolve(null)),
    [activeWorkspaceId],
  )

  if (activeWorkspaceId === null) {
    return (
      <div className="workspace-bar workspace-bar--empty">
        <p className="workspace-bar__label">Open workspace</p>
        <p className="workspace-bar__empty">No workspace is open.</p>
        <Link className="workspace-bar__link" to="/workspaces">
          Choose a workspace
        </Link>
      </div>
    )
  }

  return (
    <div className="workspace-bar">
      <p className="workspace-bar__label">Open workspace</p>
      {resource.status === 'loading' ? (
        <p className="workspace-bar__loading">
          <Spinner size="sm" /> Loading workspace…
        </p>
      ) : null}
      {resource.status === 'error' ? (
        <ErrorState
          error={resource.error}
          title="Workspace unavailable"
          onRetry={resource.reload}
        />
      ) : null}
      {resource.status === 'ready' && resource.data ? (
        <>
          <p className="workspace-bar__name">{resource.data.name}</p>
          <p className="workspace-bar__meta">
            <Badge tone={workspaceStatusTone(resource.data.status)}>{resource.data.status}</Badge>
            <span>{describeWorkspaceSchema(resource.data)}</span>
          </p>
        </>
      ) : null}
      <Button size="sm" variant="ghost" onClick={() => selectWorkspace(null)}>
        Close workspace
      </Button>
    </div>
  )
}
