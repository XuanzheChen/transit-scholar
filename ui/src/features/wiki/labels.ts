import type { BadgeTone } from '../../components/Badge'
import type {
  AgenticWikiEntry,
  BaseWikiCapability,
  WikiSearchHit,
  WikiSearchResponse,
  WikiStatusValue,
} from '../../api'

/** Status tone mapping driven by API-reported base Wiki status. */
export function wikiStatusTone(status: WikiStatusValue): BadgeTone {
  switch (status) {
    case 'ready':
      return 'success'
    case 'stale':
      return 'warning'
    case 'error':
      return 'danger'
    case 'unsupported':
    case 'missing':
    default:
      return 'neutral'
  }
}

/** User-facing label for base Wiki status values. */
export function describeWikiStatus(status: WikiStatusValue): string {
  switch (status) {
    case 'unsupported':
      return 'Not available for this workspace'
    case 'missing':
      return 'Not built yet'
    case 'ready':
      return 'Ready'
    case 'stale':
      return 'Needs rebuild'
    case 'error':
      return 'Build error'
    default:
      return status
  }
}

/** The two kinds of workspace knowledge the API distinguishes. */
export type WikiSourceKind = 'base_wiki' | 'agentic_wiki'

/**
 * User-facing name of a knowledge source.
 *
 * The product speaks about "Schema Wiki" and "Agent Learned" knowledge; the
 * internal backend knowledge-source vocabulary is never required of users
 * (REQ-010).
 */
export function wikiSourceLabel(kind: string): string {
  return kind === 'agentic_wiki' ? 'Agent Learned' : 'Schema Wiki'
}

/** Distinct badge tone per knowledge source so the two never look alike. */
export function wikiSourceTone(kind: string): BadgeTone {
  return kind === 'agentic_wiki' ? 'accent' : 'info'
}

const HIT_TYPE_LABELS: Record<WikiSearchHit['type'], string> = {
  page: 'Wiki page',
  entity: 'Wiki topic',
  entry: 'Agent Learned entry',
}

/** User-facing description of what kind of Wiki object a search hit is. */
export function describeWikiHitType(type: WikiSearchHit['type']): string {
  return HIT_TYPE_LABELS[type]
}

/** User-facing label for an Agent Learned entry lifecycle status. */
export function describeAgenticEntryStatus(status: AgenticWikiEntry['status']): string {
  switch (status) {
    case 'active':
      return 'Active'
    case 'stale':
      return 'May be out of date'
    case 'superseded':
      return 'Superseded'
    default:
      return status
  }
}

export function agenticEntryStatusTone(status: AgenticWikiEntry['status']): BadgeTone {
  switch (status) {
    case 'active':
      return 'success'
    case 'stale':
      return 'warning'
    case 'superseded':
    default:
      return 'neutral'
  }
}

/** User-facing label for how a search hit was retrieved. */
export function describeRetrievalMode(mode: WikiSearchHit['retrieval_mode']): string {
  return mode === 'semantic' ? 'semantic match' : 'keyword match'
}

/** User-facing name of a Wiki search match mode. */
export function describeSearchMode(mode: string): string {
  return mode === 'semantic' ? 'Semantic' : 'Keyword'
}

/**
 * User-visible summary of one knowledge source's search state.
 *
 * The search API reports these states per source; `null` means no status was
 * reported at all.
 */
export function describeSearchSourceStatus(status: string | null | undefined): string {
  switch (status) {
    case 'ok':
      return 'Searched successfully'
    case 'degraded':
      return 'Partly searched'
    case 'empty':
      return 'Nothing stored to search'
    case 'error':
      return 'Could not be searched'
    case 'unavailable':
      return 'Not available'
    case undefined:
    case null:
      return 'No status reported'
    default:
      return status
  }
}

/**
 * User-visible summary of an overall Wiki search status.
 *
 * `null` means the backend reported a fully successful search.
 */
export function describeSearchStatus(status: WikiSearchResponse['status']): string | null {
  switch (status) {
    case 'degraded':
      return 'Some knowledge sources could not be searched.'
    case 'error':
      return 'Wiki search could not be completed.'
    default:
      return null
  }
}

/** Render the per-source failures the backend reported for a search. */
export function describeSearchSourceErrors(
  sourceErrors: Record<string, string | null>,
): string | null {
  const failed = Object.entries(sourceErrors).filter(([, message]) => Boolean(message))
  if (failed.length === 0) {
    return null
  }
  return failed.map(([source, message]) => `${source}: ${message}`).join(' · ')
}

/** Route to the detail view for a search hit. */
export function wikiHitHref(hit: WikiSearchHit): string {
  const id = encodeURIComponent(hit.object_id)
  switch (hit.type) {
    case 'entity':
      return `/wiki/entities/${id}`
    case 'entry':
      return `/wiki/entries/${id}`
    case 'page':
    default:
      return `/wiki/pages/${id}`
  }
}

/** Readable rendering of the backend's capability reason code. */
export function describeWikiCapabilityReason(reason: string | null): string {
  switch (reason) {
    case 'unsupported_schema_mode_or_empty_membership':
      return 'The backend reports that this workspace has no Schema binding or no member papers yet.'
    default:
      return reason ?? 'The backend did not report a capability reason.'
  }
}

/**
 * Explanation for a Workspace whose Schema Wiki cannot be read.
 *
 * A Workspace created without a Schema never has Schema Wiki content, so the
 * absence is a normal product state rather than an application failure
 * (REQ-010 / AC-012). The workspace Schema mode comes from the Workspace API,
 * and the capability comes from the Wiki overview API.
 */
export function explainSchemaWikiUnavailable(
  capability: BaseWikiCapability,
  schemaMode: string | null,
): { title: string; description: string } {
  if (schemaMode === 'none') {
    return {
      title: 'Schema Wiki is not available for this workspace',
      description:
        'This workspace was created without a Schema, so it has no Schema-based Wiki content. This is a normal workspace state, not a failure: the workspace still collects Agent Learned knowledge from its research.',
    }
  }
  return {
    title: 'Schema Wiki is not available for this workspace',
    description:
      'Schema Wiki content is built from an immutable workspace Schema binding plus its member papers. This workspace does not currently meet those conditions, so no Schema Wiki content exists. ' +
      (capability.reason
        ? describeWikiCapabilityReason(capability.reason)
        : 'The backend did not report a capability reason.'),
  }
}
