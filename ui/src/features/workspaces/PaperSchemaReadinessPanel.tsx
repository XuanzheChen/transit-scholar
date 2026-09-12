import { useMemo } from 'react'
import { api, toApiError } from '../../api'
import type { SchemaBinding } from '../../api'
import { Link } from '../../app/router'
import { useApiResource } from '../../hooks/useApiResource'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Note } from '../../components/Section'
import {
  describePaperSchemaCode,
  describePaperSchemaStatus,
  isPaperSchemaMaterializable,
  paperSchemaStatusTone,
} from './labels'

export interface PaperSchemaReadinessPanelProps {
  workspaceId: string
  /** Called after the API accepts a Schema materialization request. */
  onMaterialized?: (paperId: string) => void
}

/**
 * Workspace Scope: per-Paper Schema readiness and materialization.
 *
 * The Workspace Schema binding makes a Schema version permanent for every
 * member Paper, but the Paper's Schema content is materialized afterwards
 * through `GET/POST /api/v1/workspaces/{id}/papers/{paper_id}/schema[/materialize]`.
 * This panel exposes exactly that: the API-reported readiness of every member
 * Paper and the existing materialization action where the API says a Paper is
 * not materialized yet.
 *
 * The frontend adds no readiness rule of its own. A `ready` Paper is not
 * offered materialization, a `missing` Paper is, and a Workspace without a
 * Schema binding is explained as a normal product state rather than as a
 * failure (REQ-012 / AC-014).
 */
export function PaperSchemaReadinessPanel({
  workspaceId,
  onMaterialized,
}: PaperSchemaReadinessPanelProps) {
  const workspace = useApiResource(
    (signal) => api.workspaces.get(workspaceId, { signal }),
    [workspaceId],
  )

  if (workspace.status === 'loading') {
    return <LoadingState label="Checking paper Schema status…" />
  }
  if (workspace.status === 'error') {
    return (
      <ErrorState
        error={workspace.error}
        title="Paper Schema status unavailable"
        onRetry={workspace.reload}
      />
    )
  }

  const record = workspace.data
  const binding = record?.schema_binding ?? null

  if (!record || record.schema_mode === 'none' || !binding) {
    return (
      <div className="schema-readiness" data-testid="paper-schema-readiness">
        <Note>
          <span data-testid="paper-schema-readiness-not-applicable">
            This workspace has no Schema binding, so paper Schema materialization does not apply to
            it. Schema readiness is therefore not tracked here; Agent Learned knowledge is
            unaffected.
          </span>
        </Note>
      </div>
    )
  }

  return (
    <div className="schema-readiness" data-testid="paper-schema-readiness">
      <PaperSchemaReadinessList
        workspaceId={workspaceId}
        binding={binding}
        onMaterialized={onMaterialized}
      />
    </div>
  )
}

function PaperSchemaReadinessList({
  workspaceId,
  binding,
  onMaterialized,
}: {
  workspaceId: string
  binding: SchemaBinding
  onMaterialized?: (paperId: string) => void
}) {
  const members = useApiResource(
    (signal) => api.workspaces.listPapers(workspaceId, { signal }),
    [workspaceId],
  )
  const library = useApiResource((signal) => api.papers.list({ signal, limit: 500 }), [])

  const titles = useMemo(() => {
    const map = new Map<string, string>()
    for (const paper of library.data?.items ?? []) {
      map.set(paper.paper_id, paper.title ?? 'Untitled paper')
    }
    return map
  }, [library.data])

  if (members.status === 'loading') {
    return <LoadingState label="Checking paper Schema status…" />
  }
  if (members.status === 'error') {
    return (
      <ErrorState
        error={members.error}
        title="Paper Schema status unavailable"
        onRetry={members.reload}
      />
    )
  }

  const items = members.data?.items ?? []

  if (items.length === 0) {
    return (
      <EmptyState
        title="No papers in this workspace"
        description="Schema content is materialized per member paper. Add a paper from the Library and its Schema status will appear here."
      />
    )
  }

  return (
    <>
      <p className="card__meta" data-testid="paper-schema-readiness-binding">
        Schema {binding.schema_id} version {binding.schema_version} · the backend reports readiness
        for each member paper below.
      </p>
      <ul className="card-list" data-testid="paper-schema-readiness-list">
        {items.map((member) => (
          <li
            className="card"
            key={member.paper_id}
            data-testid={`paper-schema-readiness-row-${member.paper_id}`}
          >
            <PaperSchemaReadinessRow
              workspaceId={workspaceId}
              paperId={member.paper_id}
              title={titles.get(member.paper_id) ?? member.paper_id}
              onMaterialized={onMaterialized}
            />
          </li>
        ))}
      </ul>
    </>
  )
}

function PaperSchemaReadinessRow({
  workspaceId,
  paperId,
  title,
  onMaterialized,
}: {
  workspaceId: string
  paperId: string
  title: string
  onMaterialized?: (paperId: string) => void
}) {
  const readiness = useApiResource(
    (signal) => api.workspaces.paperSchema(workspaceId, paperId, { signal }),
    [workspaceId, paperId],
  )
  const materialize = useAsyncAction(() =>
    api.workspaces.materializePaperSchema(workspaceId, paperId),
  )

  async function handleMaterialize(): Promise<void> {
    const result = await materialize.run()
    if (result) {
      readiness.reload()
      onMaterialized?.(paperId)
    }
  }

  const status = readiness.data?.status
  const code = describePaperSchemaCode(readiness.data?.error_code)
  const materializeFailure = materialize.status === 'error' ? toApiError(materialize.error) : null

  return (
    <>
      <div className="card__main">
        <div className="card__title-row">
          <h4 className="card__title">{title}</h4>
          {readiness.data ? (
            <Badge tone={paperSchemaStatusTone(status)} testId={`paper-schema-status-${paperId}`}>
              {describePaperSchemaStatus(status)}
            </Badge>
          ) : null}
        </div>
        <p className="card__meta">
          <Link to={`/library/${encodeURIComponent(paperId)}`}>Open paper</Link>
        </p>

        {readiness.status === 'loading' ? (
          <p className="card__meta">Checking this paper&apos;s Schema status…</p>
        ) : null}
        {readiness.status === 'error' ? (
          <ErrorState
            error={readiness.error}
            title="This paper's Schema status could not be read"
            onRetry={readiness.reload}
          />
        ) : null}
        {readiness.status === 'ready' && status === 'ready' ? (
          <p className="card__meta">
            Schema content is materialized for this paper under the workspace binding.
          </p>
        ) : null}
        {readiness.status === 'ready' && code ? (
          <p className="card__meta" data-testid={`paper-schema-code-${paperId}`}>
            {code}
          </p>
        ) : null}

        {materialize.status === 'error' ? (
          <ErrorState error={materialize.error} title="Schema materialization was not accepted" />
        ) : null}
        {materializeFailure?.code === 'WORKSPACE_BUSY' ? (
          <Note tone="warning">
            <span data-testid={`paper-schema-materialize-busy-${paperId}`}>
              The backend refused this request because the workspace is busy with another operation,
              such as an active research run. Nothing was changed here; try again once the workspace
              is free.
            </span>
          </Note>
        ) : null}
        {materialize.status === 'done' && materialize.result ? (
          <p className="card__meta" data-testid={`paper-schema-materialize-result-${paperId}`}>
            The backend accepted the request: run {materialize.result.run_id ?? 'not reported'} ·
            status {materialize.result.status}
          </p>
        ) : null}
      </div>
      <div className="card__actions">
        {isPaperSchemaMaterializable(status) ? (
          <Button
            size="sm"
            busy={materialize.running}
            onClick={() => {
              void handleMaterialize()
            }}
            data-testid={`materialize-paper-schema-${paperId}`}
          >
            Materialize Schema
          </Button>
        ) : null}
      </div>
    </>
  )
}
