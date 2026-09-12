import { useState } from 'react'
import { api } from '../../api'
import type { DuplicateDecision, DuplicateRelation } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'
import { useAsyncAction } from '../../hooks/useAsyncAction'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { EmptyState, ErrorState, LoadingState } from '../../components/AsyncState'
import { Select } from '../../components/Form'
import { Note } from '../../components/Section'
import {
  DUPLICATE_DECISION_OPTIONS,
  describeConfidence,
  describeDuplicateStatus,
  describeRelationType,
  describeStructuredValue,
  duplicateStatusTone,
} from './labels'

/**
 * Duplicate review and adjudication for one Paper.
 *
 * The backend reports every duplicate relation involving the Paper. Only
 * relations whose API status is `pending` accept a decision; already decided
 * relations are shown read-only so the UI never re-submits a settled relation
 * or invents a local resolution state.
 */
export function DuplicateRelationsPanel({
  paperId,
  onResolved,
}: {
  paperId: string
  onResolved: () => void
}) {
  const resource = useApiResource(
    (signal) => api.papers.duplicateRelations(paperId, { signal }),
    [paperId],
  )
  const [decisions, setDecisions] = useState<Record<string, DuplicateDecision>>({})

  const resolve = useAsyncAction((relationId: string, decision: DuplicateDecision) =>
    api.papers.resolveDuplicateRelation(relationId, decision),
  )

  const relations = resource.data?.items ?? []
  const pendingCount = relations.filter((relation) => relation.status === 'pending').length

  async function handleResolve(relationId: string): Promise<void> {
    const decision = decisions[relationId] ?? DUPLICATE_DECISION_OPTIONS[0].value
    const result = await resolve.run(relationId, decision)
    if (result) {
      resource.reload()
      onResolved()
    }
  }

  return (
    <div data-testid="duplicate-relations-panel">
      {resource.status === 'loading' ? <LoadingState label="Loading duplicate review…" /> : null}

      {resource.status === 'error' ? (
        <ErrorState error={resource.error} title="Duplicate review unavailable" onRetry={resource.reload} />
      ) : null}

      {resource.status === 'ready' && relations.length === 0 ? (
        <EmptyState
          title="No duplicate relations"
          description="The backend reports no possible duplicates involving this paper."
        />
      ) : null}

      {resource.status === 'ready' && relations.length > 0 ? (
        <ul className="record-list" data-testid="duplicate-relation-list">
          {relations.map((relation) => (
            <DuplicateRelationRecord
              key={relation.relation_id}
              relation={relation}
              paperId={paperId}
              decision={decisions[relation.relation_id] ?? DUPLICATE_DECISION_OPTIONS[0].value}
              busy={resolve.running}
              onDecisionChange={(decision) =>
                setDecisions((current) => ({ ...current, [relation.relation_id]: decision }))
              }
              onResolve={() => {
                void handleResolve(relation.relation_id)
              }}
            />
          ))}
        </ul>
      ) : null}

      {resolve.status === 'error' ? (
        <ErrorState error={resolve.error} title="Duplicate decision was rejected" />
      ) : null}

      {resolve.status === 'done' && resolve.result ? (
        <p className="card__meta" data-testid="duplicate-resolution-result">
          Relation {resolve.result.relation_id} is now {describeDuplicateStatus(resolve.result.status)} (
          {resolve.result.decision}).
        </p>
      ) : null}

      <Note>
        {relations.length === 0
          ? 'Duplicate review appears here when the backend finds a possible duplicate.'
          : pendingCount > 0
            ? `${pendingCount} relation${pendingCount === 1 ? '' : 's'} still need a decision. Decisions are applied by the backend and re-read from it.`
            : 'All duplicate relations for this paper have been decided. Decisions are reported by the backend.'}
      </Note>
    </div>
  )
}

function DuplicateRelationRecord({
  relation,
  paperId,
  decision,
  busy,
  onDecisionChange,
  onResolve,
}: {
  relation: DuplicateRelation
  paperId: string
  decision: DuplicateDecision
  busy: boolean
  onDecisionChange: (decision: DuplicateDecision) => void
  onResolve: () => void
}) {
  const otherPaperId =
    relation.source_paper_id === paperId ? relation.target_paper_id : relation.source_paper_id
  const pending = relation.status === 'pending'

  return (
    <li className="record" data-testid={`duplicate-relation-${relation.relation_id}`}>
      <div className="record__header">
        <span className="record__title">{describeRelationType(relation.relation_type)}</span>
        <Badge tone={duplicateStatusTone(relation.status)}>
          {describeDuplicateStatus(relation.status)}
        </Badge>
        <span className="card__meta">{describeConfidence(relation.confidence)}</span>
      </div>

      <dl className="detail-list">
        <div className="detail-list__row">
          <dt>Other paper</dt>
          <dd>
            <code>{otherPaperId}</code>
          </dd>
        </div>
        <div className="detail-list__row">
          <dt>Relation</dt>
          <dd>
            <code>{relation.relation_id}</code>
          </dd>
        </div>
      </dl>

      {relation.reasons.length > 0 ? (
        <ul className="plain-list" data-testid={`duplicate-reasons-${relation.relation_id}`}>
          {relation.reasons.map((reason, index) => (
            <li key={index}>
              {Object.entries(reason)
                .map(([key, value]) => `${key}: ${describeStructuredValue(value)}`)
                .join(' · ')}
            </li>
          ))}
        </ul>
      ) : null}

      {pending ? (
        <div className="action-row">
          <label className="field__label" htmlFor={`duplicate-decision-${relation.relation_id}`}>
            Decision
          </label>
          <Select
            id={`duplicate-decision-${relation.relation_id}`}
            value={decision}
            onChange={(event) => onDecisionChange(event.target.value as DuplicateDecision)}
            data-testid={`duplicate-decision-${relation.relation_id}`}
          >
            {DUPLICATE_DECISION_OPTIONS.map((option) => (
              <option value={option.value} key={option.value}>
                {option.label} — {option.description}
              </option>
            ))}
          </Select>
          <Button
            variant="primary"
            size="sm"
            busy={busy}
            onClick={onResolve}
            data-testid={`duplicate-resolve-${relation.relation_id}`}
          >
            Submit decision
          </Button>
        </div>
      ) : (
        <p className="card__meta">
          This relation already has a decision reported by the backend and is shown read-only.
        </p>
      )}
    </li>
  )
}
