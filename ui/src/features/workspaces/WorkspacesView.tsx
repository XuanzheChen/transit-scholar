import { useState } from 'react'
import { api } from '../../api'
import type { Workspace } from '../../api'
import { navigate, useLocation } from '../../app/router'
import { useActiveWorkspace } from '../../app/ActiveWorkspaceContext'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { LinkButton } from '../../components/LinkButton'
import { Note, Section } from '../../components/Section'
import { formatTimestamp } from '../../lib/format'
import { CreateWorkspaceDialog } from './CreateWorkspaceDialog'
import { WorkspaceSettings } from './WorkspaceSettingsView'
import { describeWorkspaceSchema, workspaceStatusTone } from './labels'

/**
 * Workspaces section.
 *
 * `/workspaces` lists the Workspaces stored by the local server and creates new
 * ones. `/workspaces/{id}` shows that Workspace's settings, including its
 * permanent Schema binding. All Workspace state comes from the API.
 */
export function WorkspacesView() {
  const location = useLocation()
  const pathname = location.split('?')[0]
  const match = /^\/workspaces\/([^/]+)\/?$/.exec(pathname)

  if (match) {
    return <WorkspaceSettings workspaceId={decodeURIComponent(match[1])} />
  }

  return <WorkspaceList />
}

function WorkspaceList() {
  const { activeWorkspaceId, selectWorkspace } = useActiveWorkspace()
  const resource = useApiResource((signal) => api.workspaces.list({ signal }), [])
  const [createOpen, setCreateOpen] = useState(false)
  const workspaces = resource.data?.items ?? []

  function handleCreated(workspace: Workspace): void {
    setCreateOpen(false)
    selectWorkspace(workspace.workspace_id)
    navigate(`/workspaces/${encodeURIComponent(workspace.workspace_id)}`)
  }

  return (
    <Section
      title="Workspaces"
      description="A workspace keeps one research project's papers, conversations, and knowledge together."
      actions={
        <Button variant="primary" onClick={() => setCreateOpen(true)} data-testid="new-workspace-button">
          New workspace
        </Button>
      }
    >
      {resource.status === 'loading' ? <LoadingState label="Loading workspaces…" /> : null}

      {resource.status === 'error' ? <ErrorState error={resource.error} onRetry={resource.reload} /> : null}

      {resource.status === 'ready' && workspaces.length === 0 ? (
        <EmptyState
          title="No workspaces yet"
          description="Create a workspace to start a research project. A workspace can be created with or without a Schema."
          action={
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              New workspace
            </Button>
          }
        />
      ) : null}

      {resource.status === 'ready' && workspaces.length > 0 ? (
        <ul className="card-list" data-testid="workspace-list">
          {workspaces.map((workspace) => {
            const isOpen = workspace.workspace_id === activeWorkspaceId
            return (
              <li className="card" key={workspace.workspace_id}>
                <div className="card__main">
                  <div className="card__title-row">
                    <h3 className="card__title">{workspace.name}</h3>
                    <Badge tone={workspaceStatusTone(workspace.status)}>{workspace.status}</Badge>
                    {isOpen ? <Badge tone="info">Open</Badge> : null}
                  </div>
                  <p className="card__meta">
                    {describeWorkspaceSchema(workspace)} · updated {formatTimestamp(workspace.updated_at)}
                  </p>
                </div>
                <div className="card__actions">
                  <LinkButton to={`/workspaces/${encodeURIComponent(workspace.workspace_id)}`} size="sm">
                    Settings
                  </LinkButton>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => {
                      selectWorkspace(workspace.workspace_id)
                      navigate('/research')
                    }}
                  >
                    Open workspace
                  </Button>
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}

      <Note>
        A workspace can be created without a Schema or bound to one existing Schema version. A Schema
        binding is permanent: it cannot be changed after creation.
      </Note>

      {createOpen ? (
        <CreateWorkspaceDialog onClose={() => setCreateOpen(false)} onCreated={handleCreated} />
      ) : null}
    </Section>
  )
}
