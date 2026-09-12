/**
 * Presentation labels for AgentRun state and research Timeline events.
 *
 * These helpers only translate API-provided values into user-facing text. They
 * never derive backend state: every status, phase, and timeline entry rendered
 * by the UI comes from the frozen `/api/v1/*` contract (C-002).
 */
import type { BadgeTone } from '../../components/Badge'
import type { RunState, TimelineEvent, TimelineEventKind } from '../../api'

/** AgentRun statuses that no longer change. */
export const TERMINAL_RUN_STATUSES: readonly string[] = ['completed', 'failed', 'cancelled']

export function isTerminalRunStatus(status: string | null | undefined): boolean {
  return status !== null && status !== undefined && TERMINAL_RUN_STATUSES.includes(status)
}

const RUN_STATUS_LABELS: Record<string, string> = {
  created: 'Starting',
  running: 'Running',
  paused: 'Paused',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const RUN_STATUS_TONES: Record<string, BadgeTone> = {
  created: 'neutral',
  running: 'info',
  paused: 'warning',
  completed: 'success',
  failed: 'danger',
  cancelled: 'danger',
}

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABELS[status] ?? status
}

export function runStatusTone(status: string): BadgeTone {
  return RUN_STATUS_TONES[status] ?? 'neutral'
}

const RUN_PHASE_LABELS: Record<string, string> = {
  planning: 'Planning',
  query_planning: 'Planning queries',
  research: 'Researching',
  evidence_reasoning: 'Reviewing evidence',
  claim_reasoning: 'Building claims',
  synthesis: 'Writing the answer',
  completed: 'Completed',
  failed: 'Failed',
}

export function runPhaseLabel(phase: string): string {
  return RUN_PHASE_LABELS[phase] ?? phase
}

/** The single primary control state required by REQ-006. */
export type PrimaryControlKind = 'send' | 'pause' | 'pausing' | 'resume'

export interface PrimaryControlState {
  kind: PrimaryControlKind
  label: string
  disabled: boolean
  busy: boolean
  /** Explanation for a control that is visible but not usable right now. */
  hint: string | null
}

export interface PrimaryControlInput {
  run: RunState | null
  /** `pause_resume` from `GET /api/v1/capabilities`. */
  pauseResumeAvailable: boolean
  promptEmpty: boolean
  submitting: boolean
}

/**
 * Resolve the one primary Send/Pause/Pausing/Resume control from API Run state
 * and reported capability. Cooperative pause is never presented as an
 * immediate forced stop.
 */
export function resolvePrimaryControl(input: PrimaryControlInput): PrimaryControlState {
  const { run, pauseResumeAvailable, promptEmpty, submitting } = input
  const active = run !== null && !isTerminalRunStatus(run.status)

  if (!active) {
    return {
      kind: 'send',
      label: 'Send',
      disabled: promptEmpty || submitting,
      busy: submitting,
      hint: null,
    }
  }

  if (!pauseResumeAvailable) {
    return {
      kind: 'pause',
      label: 'Pause',
      disabled: true,
      busy: false,
      hint: 'This backend does not report pause and resume support for active runs.',
    }
  }

  if (run.pause_requested || run.display_status === 'pause_requested') {
    return {
      kind: 'pausing',
      label: 'Pausing',
      disabled: true,
      busy: true,
      hint: 'Pause requested. The agent pauses at its next safe point; it does not abort an in-flight step.',
    }
  }

  if (run.status === 'paused') {
    return {
      kind: 'resume',
      label: 'Resume',
      disabled: false,
      busy: false,
      hint: 'Resume this paused run to continue the same research.',
    }
  }

  if (run.status === 'running') {
    return {
      kind: 'pause',
      label: 'Pause',
      disabled: false,
      busy: false,
      hint: 'Ask the agent to pause at its next safe point.',
    }
  }

  return {
    kind: 'pause',
    label: 'Pause',
    disabled: true,
    busy: false,
    hint: 'The run is starting; pause becomes available once it is executing.',
  }
}

/* --------------------------------------------------------------- timeline */

export const TIMELINE_KIND_LABELS: Record<TimelineEventKind, string> = {
  planning: 'Planning',
  research_session: 'Research session',
  query: 'Query',
  retrieval: 'Retrieval',
  evidence: 'Evidence',
  claim: 'Claim',
  synthesis: 'Synthesis',
  status: 'Status',
  warning: 'Warning',
  error: 'Error',
}

const TIMELINE_KIND_TONES: Record<TimelineEventKind, BadgeTone> = {
  planning: 'info',
  research_session: 'info',
  query: 'neutral',
  retrieval: 'neutral',
  evidence: 'neutral',
  claim: 'neutral',
  synthesis: 'info',
  status: 'neutral',
  warning: 'warning',
  error: 'danger',
}

export function timelineKindLabel(kind: TimelineEventKind): string {
  return TIMELINE_KIND_LABELS[kind] ?? kind
}

export function timelineKindTone(kind: TimelineEventKind): BadgeTone {
  return TIMELINE_KIND_TONES[kind] ?? 'neutral'
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/**
 * One-line user-facing description of a timeline event.
 *
 * Only structured product artifacts the API projected are read here. Hidden
 * reasoning, prompts, and provider payloads are never present in the API
 * projection, and are never rendered (C-006).
 */
export function timelineEventSummary(event: TimelineEvent): string | null {
  const data = event.data
  return (
    text(data.research_question) ??
    text(data.query_text) ??
    text(data.statement) ??
    text(data.summary) ??
    text(data.preview) ??
    text(data.action_type) ??
    text(data.role_status) ??
    text(data.status) ??
    text(data.termination_reason)
  )
}

export interface TimelineDetailRow {
  label: string
  value: string
}

/** Structured, user-safe detail rows for a timeline event. */
export function timelineEventDetails(event: TimelineEvent): TimelineDetailRow[] {
  const data = event.data
  const rows: TimelineDetailRow[] = []

  const researchQuestion = text(data.research_question)
  if (researchQuestion) rows.push({ label: 'Research question', value: researchQuestion })
  const queryText = text(data.query_text)
  if (queryText) rows.push({ label: 'Query', value: queryText })
  const statement = text(data.statement)
  if (statement) rows.push({ label: 'Claim', value: statement })
  const preview = text(data.preview)
  if (preview) rows.push({ label: 'Evidence excerpt', value: preview })
  const paperId = text(data.paper_id)
  if (paperId) rows.push({ label: 'Paper', value: paperId })
  if (Array.isArray(data.pages)) {
    const pages = data.pages.filter((page): page is number => typeof page === 'number')
    if (pages.length > 0) rows.push({ label: 'Pages', value: pages.join(', ') })
  }
  const actionType = text(data.action_type)
  if (actionType) rows.push({ label: 'Action', value: actionType })
  const roleId = text(data.role_id)
  if (roleId) rows.push({ label: 'Step', value: roleId })
  const roleStatus = text(data.role_status)
  if (roleStatus) rows.push({ label: 'Step status', value: roleStatus })
  const status = text(data.status)
  if (status) rows.push({ label: 'Status', value: status })
  const terminationReason = text(data.termination_reason)
  if (terminationReason) rows.push({ label: 'Reason', value: terminationReason })
  const code = text(data.code)
  if (code) rows.push({ label: 'Code', value: code })
  const summary = text(data.summary)
  if (summary) rows.push({ label: 'Summary', value: summary })
  const retryable = data.retryable
  if (typeof retryable === 'boolean') rows.push({ label: 'Retryable', value: retryable ? 'yes' : 'no' })

  return rows
}
