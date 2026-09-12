/**
 * Response and request shapes for the frozen `/api/v1/*` HTTP contract.
 *
 * These types deliberately mirror the backend DTOs. They are transport
 * descriptions only: no business logic, no derived state, and no second
 * authoritative copy of backend state lives here.
 */

/** Stable error envelope produced by the API error handlers. */
export interface ApiErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
}

export interface ApiErrorEnvelope {
  error: ApiErrorBody
}

export interface HealthResponse {
  status: string
}

export interface CapabilityResponse {
  pause_resume: boolean
  user_schema_creation: boolean
  base_wiki: boolean
  agentic_wiki: boolean
  semantic_wiki_search: boolean
  pdf_upload_max_bytes: number
}

/* ------------------------------------------------------------------ papers */

export interface PaperSummary {
  paper_id: string
  title: string | null
  publication_year: number | null
  venue: string | null
  doi: string | null
  arxiv_id: string | null
  status: string
  primary_file_id: string | null
  created_at: string | null
  updated_at: string | null
}

export interface PaperFile {
  file_id: string
  original_filename: string | null
  mime_type: string | null
  file_size_bytes: number | null
  is_primary: boolean
  page_count: number | null
}

export interface PaperDetail extends PaperSummary {
  normalized_title: string | null
  abstract: string | null
  normalized_doi: string | null
  authors: Record<string, unknown>[]
  files: PaperFile[]
  duplicate_relations: Record<string, unknown>[]
  deleted_at: string | null
}

export interface PaperListResponse {
  items: PaperSummary[]
}

export interface PaperImportResponse {
  paper_id: string | null
  file_id: string | null
  status: string
  import_status: string | null
  metadata_status: string | null
  duplicate_status: string | null
  current_stage: string | null
  second_layer_ready: boolean
  second_layer_blockers: string[]
  error_code: string | null
  error_message: string | null
}

export interface SecondLayerResponse {
  paper_id: string
  status: string
  second_layer_ready: boolean
  second_layer_blockers: string[]
  error_code: string | null
  error_message: string | null
}

export interface MetadataUpdateRequest {
  title?: string | null
  abstract?: string | null
  publication_year?: number | null
  venue?: string | null
  doi?: string | null
  arxiv_id?: string | null
  authors?: string[] | null
}

export interface PaperActionResponse {
  paper_id: string
  status: string
  updated_fields: string[]
  audit_log_id: string | null
}

export interface MetadataCandidate {
  id: string
  paper_id: string | null
  paper_file_id: string | null
  field_name: string
  value_text: string | null
  source_type: string
  source_location: string | null
  confidence: number
  is_selected: boolean
}

/** Bibliographic citation record belonging to a paper (not an answer citation). */
export interface CitationRecord {
  id: string
  paper_id: string
  source_format: string
  raw_text: string | null
  structured_json: Record<string, unknown> | null
  parse_status: string
  parse_warnings: string[]
  is_selected: boolean
}

export type DuplicateDecision = 'same_paper' | 'different_version' | 'not_duplicate' | 'ignore'

export interface DuplicateRelation {
  relation_id: string
  source_paper_id: string
  target_paper_id: string
  relation_type: string
  confidence: number
  status: string
  reasons: Record<string, unknown>[]
}

export interface DuplicateRelationListResponse {
  items: DuplicateRelation[]
}

export interface DuplicateResolutionResponse {
  relation_id: string
  status: string
  decision: string
  audit_log_id: string | null
}

export interface EnrichmentProvider {
  provider: string
  status: string
  http_status: number | null
  fetched_at: string | null
  attempt_count: number
  next_retry_at: string | null
  fields: string[]
  error_code: string | null
  error_message: string | null
}

export interface EnrichmentResponse {
  paper_id: string
  doi: string | null
  metadata_enrichment_status: string
  providers: EnrichmentProvider[]
  resolved: Record<string, string>
  error_code: string | null
  error_message: string | null
}

/* -------------------------------------------------------------- workspaces */

export interface SchemaSelectionRequest {
  schema_id: string
  version: string
}

export interface WorkspaceCreateRequest {
  name: string
  schema?: SchemaSelectionRequest | null
}

export interface SchemaBinding {
  schema_id: string
  schema_version: string
  schema_hash: string
}

export interface Workspace {
  workspace_id: string
  name: string
  status: string
  schema_mode: string
  schema_binding: SchemaBinding | null
  revision: number
  created_at: string
  updated_at: string
}

export interface WorkspaceListResponse {
  items: Workspace[]
}

export interface WorkspacePaperRequest {
  paper_id: string
}

export interface WorkspacePaper {
  workspace_id: string
  paper_id: string
  created_at: string | null
  already_member: boolean
}

export interface WorkspacePaperListResponse {
  items: WorkspacePaper[]
}

export interface WorkspaceSchemaResponse {
  schema_mode: string
  binding: SchemaBinding | null
}

export interface PaperSchemaStateResponse {
  workspace_id: string
  paper_id: string
  status: string
  error_code: string | null
}

export interface SchemaMaterializationResponse {
  workspace_id: string
  paper_id: string
  run_id: string | null
  status: string
}

/* ----------------------------------------------------------- conversations */

export interface ConversationCreateRequest {
  title?: string | null
}

export interface ConversationSummary {
  conversation_id: string
  workspace_id: string
  title: string | null
  created_at: string | null
}

export interface ConversationListResponse {
  items: ConversationSummary[]
}

/** Provenance for admitted evidence supporting an Agent answer. */
export interface AnswerEvidenceCitation {
  evidence_id: string
  research_session_id: string
  paper_id: string | null
  paper_title: string | null
  source_kind: string
  pages: number[] | null
  block_id: string | null
  character_start: number | null
  character_end: number | null
  parse_run_id: string | null
  canonical_source_version: string | null
  evidence_quote: string | null
}

export interface PublicAssistantResponse {
  answer_text?: string | null
  answer?: string | null
  citation_references?: string[] | null
  citations?: string[] | null
}

export interface Turn {
  turn_id: string
  conversation_id: string
  sequence: number
  user_message: string
  resolved_user_goal: string | null
  agent_run_id: string | null
  status: string
  assistant_response: PublicAssistantResponse | null
  final_answer: string | null
  answer_citations: AnswerEvidenceCitation[]
  error_message: string | null
  created_at: string | null
  completed_at: string | null
}

export interface Conversation extends ConversationSummary {
  turns: Turn[]
}

export interface TurnCreateRequest {
  message: string
}

export interface TurnSubmissionResponse {
  turn_id: string
  agent_run_id: string
}

/* -------------------------------------------------------------------- runs */

export interface RunState {
  agent_run_id: string
  workspace_id: string
  status: string
  phase: string
  user_goal: string
  pause_requested: boolean
  display_status: string | null
}

export type TimelineEventKind =
  | 'planning'
  | 'research_session'
  | 'query'
  | 'retrieval'
  | 'evidence'
  | 'claim'
  | 'synthesis'
  | 'status'
  | 'warning'
  | 'error'

export interface TimelineEvent {
  sequence: number
  kind: TimelineEventKind
  timestamp: string
  research_session_id: string | null
  data: Record<string, unknown>
}

export interface TimelineResponse {
  events: TimelineEvent[]
  next_sequence: number
}

/* ----------------------------------------------------------------- schemas */

export interface SchemaDraftRequest {
  schema_id: string
  version: string
  sections: Record<string, unknown>[]
  name?: string | null
  description?: string | null
  status_semantics?: Record<string, unknown> | null
}

export interface SchemaValidationResponse {
  valid: boolean
  issues: Record<string, unknown>[]
}

export interface SchemaResponse {
  schema_id: string
  version: string
  name: string | null
  description: string | null
  schema_hash: string
  definition: Record<string, unknown>
}

/* -------------------------------------------------------------------- wiki */

export type WikiStatusValue = 'unsupported' | 'missing' | 'ready' | 'stale' | 'error'

/** Match mode accepted by the Wiki search endpoint. */
export type WikiSearchMode = 'lexical' | 'semantic'

export interface BaseWikiStatus {
  workspace_id: string
  status: WikiStatusValue
  manifest_status: string | null
  fingerprint: string | null
  recorded_fingerprint: string | null
  build_revision: number | null
  built_at: string | null
  error_code: string | null
}

export interface BaseWikiCapability {
  build_supported: boolean
  read_supported: boolean
  reason: string | null
}

export interface WikiOverview {
  workspace_id: string
  base_wiki: BaseWikiStatus
  base_wiki_capability: BaseWikiCapability
  agentic_wiki_entry_count: number
}

export interface WikiBuildResponse {
  workspace_id: string
  status: BaseWikiStatus
  fingerprint: string
  build_revision: number
}

export interface WikiPage {
  page_id: string
  workspace_id: string
  paper_id: string
  title: string
  summary: string
  schema_id: string
  schema_version: string
  build_status: string
  created_at: string
  updated_at: string
  build_revision: number
}

export interface WikiPageListResponse {
  items: WikiPage[]
}

export interface WikiEntity {
  entity_id: string
  workspace_id: string
  canonical_name: string
  aliases: string[]
  description: string
  kind: string | null
  created_at: string
  updated_at: string
}

export interface WikiEntityListResponse {
  items: WikiEntity[]
}

export interface AgenticWikiEntry {
  entry_id: string
  workspace_id: string
  title: string
  content: string
  source_claim_ids: string[]
  evidence_refs: string[]
  provenance_refs: string[]
  paper_ids: string[]
  originating_agent_run_id: string
  status: 'active' | 'stale' | 'superseded'
  superseded_by: string | null
  created_at: string
  updated_at: string
}

export interface AgenticWikiEntryListResponse {
  items: AgenticWikiEntry[]
}

export interface WikiSearchHit {
  type: 'page' | 'entity' | 'entry'
  object_id: string
  title: string
  score: number
  snippet: string
  retrieval_mode: 'lexical' | 'semantic'
  source_kind: 'base_wiki' | 'agentic_wiki'
  lifecycle_status: 'active' | 'stale' | null
  source_score: number | null
  local_rank: number | null
  fusion_score: number | null
}

export interface WikiSearchResponse {
  status: 'ok' | 'degraded' | 'error'
  hits: WikiSearchHit[]
  error_code: string | null
  source_status: Record<string, string>
  source_errors: Record<string, string | null>
}
