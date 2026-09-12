import { useEffect } from 'react'
import { api } from '../../api'
import { useActiveWorkspace } from '../../app/ActiveWorkspaceContext'
import { useApiResource } from '../../hooks/useApiResource'
import { Badge } from '../../components/Badge'
import { EmptyState, ErrorState, LoadingState, UnavailableState } from '../../components/AsyncState'
import { Note, Section } from '../../components/Section'
import { LinkButton } from '../../components/LinkButton'
import { formatTimestamp, shortHash } from '../../lib/format'
import { describeWorkspaceSchemaMode, workspaceStatusTone } from './labels'
import { PaperSchemaReadinessPanel } from './PaperSchemaReadinessPanel'
import { WorkspacePapersPanel } from './WorkspacePapersPanel'

/**
 * Workspace settings.
 *
 * Shows the authoritative Workspace record from `GET /api/v1/workspaces/{id}`,
 * including the Schema binding and Workspace status. The Schema binding is
 * presented as read-only information: no control in this view (or anywhere
 * else) offers to switch an existing Workspace to another Schema or version.
 */
export function WorkspaceSettingsView({ workspaceId }: { workspaceId: string }) {
  const { selectWorkspace } = useActiveWorkspace()
  const workspace = useApiResource((signal) => api.workspaces.get(workspaceId, { signal }), [workspaceId])

  // Opening a workspace's settings makes it the open workspace for the other
  // product sections. The selection is a navigation identifier only.
  useEffect(() => {
    selectWorkspace(workspaceId)
  }, [selectWorkspace, workspaceId])

  if (workspace.status === 'loading') {
    return <LoadingState label="Loading workspace settings…" />
  }

  if (workspace.status === 'error') {
    return (
      <ErrorState
        error={workspace.error}
        title="Workspace settings unavailable"
        onRetry={workspace.reload}
      />
    )
  }

  const record = workspace.data
  if (!record) {
    return (
      <EmptyState
        title="Workspace not available"
        description="The backend did not return this workspace. It may have been removed."
      />
    )
  }

  const binding = record.schema_binding

  return (
    <div className="stack">
      <div className="context-card">
        <div className="card__title-row">
          <h3 className="card__title">{record.name}</h3>
          <Badge tone={workspaceStatusTone(record.status)}>{record.status}</Badge>
        </div>
        <p className="card__meta">
          {describeWorkspaceSchemaMode(record)} · revision {record.revision} · created{' '}
          {formatTimestamp(record.created_at)} · updated {formatTimestamp(record.updated_at)}
        </p>
        <div className="card__actions">
          <LinkButton to="/research" variant="primary" size="sm">
            Open research
          </LinkButton>
        </div>
      </div>

      <div className="subsection" data-testid="workspace-schema-settings">
        <div className="subsection__header">
          <h3 className="subsection__title">Schema binding</h3>
          {record.schema_mode === 'none' ? (
            <Badge tone="neutral">No Schema</Badge>
          ) : (
            <Badge tone="info">Bound</Badge>
          )}
        </div>

        {record.schema_mode === 'none' || !binding ? (
          <UnavailableState
            title="This workspace has no Schema binding"
            description="The workspace was created without a Schema, so Schema-based knowledge and Schema Wiki do not apply. This is a normal workspace state. A Schema cannot be added to an existing workspace."
          />
        ) : (
          <dl className="detail-list">
            <div className="detail-list__row">
              <dt>Schema</dt>
              <dd>{binding.schema_id}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Version</dt>
              <dd data-testid="workspace-schema-version">{binding.schema_version}</dd>
            </div>
            <div className="detail-list__row">
              <dt>Definition hash</dt>
              <dd>
                <code title={binding.schema_hash}>{shortHash(binding.schema_hash)}</code>
              </dd>
            </div>
            <div className="detail-list__row">
              <dt>Binding</dt>
              <dd>
                <span data-testid="workspace-schema-immutable">Permanent — cannot be changed</span>
              </dd>
            </div>
          </dl>
        )}

        <div className="warning-panel warning-panel--static" data-testid="workspace-schema-immutable-notice">
          <p className="warning-panel__title">A Schema binding is immutable</p>
          <p className="warning-panel__text">
            A workspace keeps the Schema version it was created with. There is no way to switch this
            workspace to another Schema or version; create a new workspace for a different Schema.
          </p>
        </div>
      </div>

      <div className="subsection" data-testid="workspace-paper-schema-readiness">
        <div className="subsection__header">
          <h3 className="subsection__title">Paper Schema readiness</h3>
        </div>
        <p className="card__meta">
          Workspace Schema content is materialized per member paper through the Schema API. The
          backend reports each paper&apos;s state below.
        </p>
        <PaperSchemaReadinessPanel workspaceId={workspaceId} />
      </div>

      <WorkspacePapersPanel workspaceId={workspaceId} />

      <Note>
        Workspace identifier <code>{record.workspace_id}</code> is managed by the local TransitScholar
        server. This view reads Workspace state from the API and does not cache a second copy.
      </Note>
    </div>
  )
}

/** Settings section wrapper used by the Workspaces route. */
export function WorkspaceSettings({ workspaceId }: { workspaceId: string }) {
  return (
    <Section
      title="Workspace settings"
      description="The workspace record stored by this local server, including its permanent Schema binding."
    >
      <WorkspaceSettingsView workspaceId={workspaceId} />
    </Section>
  )
}
