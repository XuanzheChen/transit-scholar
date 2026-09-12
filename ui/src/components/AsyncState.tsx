import type { ReactNode } from 'react'
import { classifyApiFailure, toApiError } from '../api'
import type { ApiFailureKind } from '../api'
import { Button } from './Button'
import { Spinner } from './Spinner'

/** Explicit loading state; never renders stale or invented content. */
export function LoadingState({ label = 'Loading…', description }: { label?: string; description?: ReactNode }) {
  return (
    <div className="state-block" role="status" aria-live="polite">
      <Spinner />
      <p className="state-block__title">{label}</p>
      {description ? <p className="state-block__description">{description}</p> : null}
    </div>
  )
}

/** Explicit empty state for a successful request that returned nothing. */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="state-block state-block--empty">
      <p className="state-block__title">{title}</p>
      {description ? <p className="state-block__description">{description}</p> : null}
      {action ? <div className="state-block__action">{action}</div> : null}
    </div>
  )
}

/**
 * Explicit, normal product state for a capability the backend reports as
 * unavailable. This is deliberately not styled as an application error.
 */
export function UnavailableState({
  title,
  description,
  action,
}: {
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="state-block state-block--unavailable">
      <p className="state-block__title">{title}</p>
      {description ? <p className="state-block__description">{description}</p> : null}
      {action ? <div className="state-block__action">{action}</div> : null}
    </div>
  )
}

const FAILURE_LABELS: Record<ApiFailureKind, string> = {
  unavailable: 'Backend unavailable',
  unavailable_capability: 'Capability unavailable',
  validation: 'Request rejected',
  conflict: 'Conflicting state',
  not_found: 'Not found',
  error: 'Request failed',
}

/**
 * Short, honest guidance for each failure class.
 *
 * The wording describes what the user can do next; it never substitutes a
 * locally invented backend status for the API-provided one.
 */
const FAILURE_GUIDANCE: Partial<Record<ApiFailureKind, string>> = {
  unavailable:
    'Confirm the local TransitScholar server is running, then retry. The view never invents a replacement state for the backend.',
  unavailable_capability:
    'The backend reports this capability as unavailable, so the action cannot be completed right now.',
  validation: 'The backend rejected these values. Adjust them and submit again.',
  conflict:
    'The backend reports a conflicting state. Reload to see the current state before retrying.',
  not_found: 'The requested record is not present on this local server.',
}

export interface ErrorStateProps {
  error: unknown
  title?: string
  description?: ReactNode
  onRetry?: () => void
}

/**
 * User-visible failure state. The message and code always come from the API
 * response envelope; the UI never substitutes a locally invented status.
 */
export function ErrorState({ error, title, description, onRetry }: ErrorStateProps) {
  const apiError = toApiError(error)
  const kind = classifyApiFailure(apiError)
  const heading = title ?? FAILURE_LABELS[kind]
  const guidance = description ? null : FAILURE_GUIDANCE[kind] ?? null
  const reference = [
    apiError.code,
    apiError.status > 0 ? `HTTP ${apiError.status}` : 'no response',
    `${apiError.method} ${apiError.path}`,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div
      className="state-block state-block--error"
      role="alert"
      data-testid="error-state"
      data-failure-kind={kind}
    >
      <p className="state-block__title">{heading}</p>
      <p className="state-block__description">{description ?? apiError.message}</p>
      {guidance ? <p className="state-block__guidance">{guidance}</p> : null}
      <p className="state-block__reference">{reference}</p>
      {onRetry ? (
        <div className="state-block__action">
          <Button onClick={onRetry}>Retry</Button>
        </div>
      ) : null}
    </div>
  )
}
