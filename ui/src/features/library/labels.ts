import type { BadgeTone } from '../../components/Badge'
import type {
  DuplicateDecision,
  PaperImportResponse,
  PaperSummary,
} from '../../api'

/** Status tone mapping driven by API-reported paper status. */
export function paperStatusTone(status: string | null | undefined): BadgeTone {
  switch (status) {
    case 'ready':
    case 'complete':
    case 'completed':
      return 'success'
    case 'processing':
    case 'pending':
    case 'importing':
      return 'info'
    case 'failed':
    case 'error':
      return 'danger'
    case 'deleted':
      return 'warning'
    default:
      return 'neutral'
  }
}

export function describePaperIdentity(paper: {
  title: string | null
  publication_year: number | null
  venue: string | null
}): string {
  const parts = [paper.venue, paper.publication_year ? String(paper.publication_year) : null].filter(
    (value): value is string => Boolean(value),
  )
  return parts.length > 0 ? parts.join(' · ') : 'No venue or year recorded'
}

/**
 * Author names as reported by `GET /api/v1/papers/{id}`.
 *
 * The API returns structured author records; this helper only joins the names
 * it was given and never derives authors from any other field.
 */
export function describePaperAuthors(authors: Record<string, unknown>[]): string {
  const names = authors
    .map((author) => (typeof author.full_name === 'string' ? author.full_name.trim() : ''))
    .filter((name) => name.length > 0)
  return names.length > 0 ? names.join(', ') : 'No authors recorded'
}

const IDENTIFIER_FIELDS: { key: keyof PaperSummary; label: string }[] = [
  { key: 'doi', label: 'DOI' },
  { key: 'arxiv_id', label: 'arXiv' },
]

/** Compact identifier line for a paper card. */
export function describePaperIdentifiers(paper: PaperSummary): string {
  const parts = IDENTIFIER_FIELDS.map(({ key, label }) => {
    const value = paper[key]
    return typeof value === 'string' && value.length > 0 ? `${label} ${value}` : null
  }).filter((value): value is string => value !== null)
  return parts.length > 0 ? parts.join(' · ') : 'No DOI or arXiv identifier recorded'
}

/**
 * Plain-language description of the API-reported research readiness gate.
 *
 * The readiness value itself always comes from the API; only the sentence used
 * to present it is chosen here.
 */
export function describeReadiness(status: string | null | undefined): string {
  switch (status) {
    case 'ready':
      return 'Ready for research'
    case 'blocked':
      return 'Not ready for research'
    case 'failed':
      return 'Processing failed'
    default:
      return 'Readiness not reported'
  }
}

export function readinessTone(status: string | null | undefined): BadgeTone {
  switch (status) {
    case 'ready':
      return 'success'
    case 'blocked':
      return 'warning'
    case 'failed':
    case 'error':
      return 'danger'
    default:
      return 'neutral'
  }
}

const BLOCKER_TEXT: Record<string, string> = {
  import_failed: 'The PDF could not be imported.',
  exact_duplicate: 'This PDF matches a paper already in the Library.',
  metadata_extraction_failed: 'Bibliographic details could not be extracted from the PDF.',
  metadata_processing_pending: 'Bibliographic extraction is still in progress.',
  duplicate_detection_failed: 'Duplicate checking did not complete.',
  pending_duplicate_review: 'A possible duplicate is waiting to be reviewed.',
  paper_not_found: 'The paper record is missing.',
  paper_not_active: 'The paper is not in a state that supports research.',
  no_primary_file: 'No primary PDF file is registered for this paper.',
  primary_file_deleted: 'The registered PDF file was deleted.',
  source_file_missing: 'The stored PDF file could not be found on disk.',
}

/**
 * Present an API blocker code without inventing backend state.
 *
 * Known codes get a user-facing sentence; unknown codes are shown verbatim so
 * a new backend blocker is never silently hidden or misrepresented.
 */
export function describeReadinessBlocker(blocker: string): string {
  const known = BLOCKER_TEXT[blocker]
  if (known) {
    return known
  }
  for (const [code, text] of Object.entries(BLOCKER_TEXT)) {
    if (blocker.startsWith(`${code}:`)) {
      return text
    }
  }
  return blocker
}

/**
 * User-facing result of `POST /api/v1/papers/import`.
 *
 * Returns `null` for a successful import; otherwise a message built from the
 * API-provided status/error fields.
 */
export function describeImportOutcome(result: PaperImportResponse): string | null {
  if (result.status === 'failed') {
    return result.error_message ?? 'The PDF could not be imported.'
  }
  if (result.status === 'duplicate') {
    return 'This PDF matches a paper that is already in the Library.'
  }
  if (result.status === 'blocked') {
    return 'The paper was imported but is not yet ready for research.'
  }
  if (!result.paper_id) {
    return result.error_message ?? 'The import did not return a paper.'
  }
  return null
}

/* ------------------------------------------------- extended library (T-006) */

/*
 * Every label below is chosen from an API-reported value. Unknown values are
 * shown verbatim so a new backend state is never silently hidden or replaced by
 * a locally invented status.
 */

/** True when the API state means a deleted Paper can be restored. */
export function isLibraryRestorable(paper: {
  status: string
  deleted_at?: string | null
}): boolean {
  return paper.deleted_at !== null && paper.deleted_at !== undefined
    ? true
    : paper.status === 'deleted' || paper.status === 'archived'
}

const DUPLICATE_STATUS_TEXT: Record<string, string> = {
  pending: 'Needs a decision',
  confirmed: 'Confirmed',
  rejected: 'Not a duplicate',
  ignored: 'Ignored',
}

export function describeDuplicateStatus(status: string): string {
  return DUPLICATE_STATUS_TEXT[status] ?? status
}

export function duplicateStatusTone(status: string): BadgeTone {
  switch (status) {
    case 'pending':
      return 'warning'
    case 'confirmed':
      return 'danger'
    case 'rejected':
      return 'success'
    default:
      return 'neutral'
  }
}

export interface DuplicateDecisionOption {
  value: DuplicateDecision
  label: string
  description: string
}

/**
 * The four adjudication decisions accepted by
 * `POST /api/v1/duplicate-relations/{id}/resolve`.
 */
export const DUPLICATE_DECISION_OPTIONS: DuplicateDecisionOption[] = [
  {
    value: 'same_paper',
    label: 'Same paper',
    description: 'Confirm that this is the same paper already in the Library.',
  },
  {
    value: 'different_version',
    label: 'Different version',
    description: 'Confirm a related version rather than an exact duplicate.',
  },
  {
    value: 'not_duplicate',
    label: 'Not a duplicate',
    description: 'Reject the suggested duplicate relation.',
  },
  {
    value: 'ignore',
    label: 'Ignore',
    description: 'Leave the relation untouched and stop asking about it.',
  },
]

const RELATION_TYPE_TEXT: Record<string, string> = {
  exact_duplicate: 'Exact duplicate',
  probable_duplicate: 'Probable duplicate',
  possible_version: 'Possible version',
  supplement_of: 'Supplement',
  related: 'Related',
}

export function describeRelationType(relationType: string): string {
  return RELATION_TYPE_TEXT[relationType] ?? relationType
}

/** API confidence rendered as a percentage; never derived from other fields. */
export function describeConfidence(confidence: number): string {
  if (!Number.isFinite(confidence)) {
    return 'confidence not reported'
  }
  return `${Math.round(confidence * 100)}% confidence`
}

const CITATION_PARSE_TEXT: Record<string, string> = {
  parsed: 'Parsed',
  pending: 'Not parsed yet',
  failed: 'Parsing failed',
  partial: 'Partially parsed',
  skipped: 'Parsing skipped',
}

export function describeCitationParseStatus(status: string): string {
  return CITATION_PARSE_TEXT[status] ?? status
}

export function citationParseTone(status: string): BadgeTone {
  switch (status) {
    case 'parsed':
      return 'success'
    case 'failed':
      return 'danger'
    case 'partial':
      return 'warning'
    case 'pending':
      return 'info'
    default:
      return 'neutral'
  }
}

const ENRICHMENT_STATUS_TEXT: Record<string, string> = {
  skipped: 'Not applicable',
  not_applicable: 'Not applicable',
  pending: 'Not refreshed yet',
  running: 'Refresh in progress',
  fetched: 'Provider data retrieved',
  partial: 'Partially retrieved',
  blocked: 'Blocked',
  not_found: 'No provider match',
  retry_scheduled: 'Retry scheduled',
  failed: 'Refresh failed',
  unavailable: 'Provider unavailable',
}

export function describeEnrichmentStatus(status: string): string {
  return ENRICHMENT_STATUS_TEXT[status] ?? status
}

export function enrichmentStatusTone(status: string): BadgeTone {
  switch (status) {
    case 'fetched':
    case 'complete':
    case 'completed':
      return 'success'
    case 'running':
    case 'pending':
    case 'retry_scheduled':
      return 'info'
    case 'partial':
    case 'blocked':
      return 'warning'
    case 'failed':
    case 'unavailable':
      return 'danger'
    default:
      return 'neutral'
  }
}

/** Formats a citation `structured_json` value without interpreting it. */
export function describeStructuredValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  if (Array.isArray(value)) {
    return value.map((item) => describeStructuredValue(item)).join(', ')
  }
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
