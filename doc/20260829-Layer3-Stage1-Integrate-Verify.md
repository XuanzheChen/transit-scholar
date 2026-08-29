# Layer3 Stage1 Workspace Grounding — Integration & Verification Report (T-006)

> 文档性质：实现完成情况说明 / 回归验证报告（Implementation Report + Verification Evidence）
> 适用范围：Layer3 Stage1（Workspace 控制面、Workspace 归属的 Schema / Base Wiki、只读 Grounding、绑定知识网关）
> 本报告只描述**已实现并被测试证明**的 Layer3 Stage1 行为。Agentic Loop、LangGraph、Multi-Agent、
> ResearchPlan、Thought/Action、Agentic Wiki 自构建、论文发现/下载**均不属于** Layer3 Stage1（C-001），
> 本报告不声称也不暗示任何此类功能。

---

## 1. What Layer3 Stage1 implements (implemented behavior only)

Layer3 Stage1 delivers a **Workspace Grounding layer plus a bound, Workspace-safe
knowledge access gateway**, built by composition over the existing Layer1/L2S1/L2S2/L2S3
public APIs. No Agent runtime exists anywhere in this stage.

### 1.1 Persistent Workspace control plane (REQ-001/002/003/006)
- `transit_scholar.db.models.Workspace` + `WorkspacePaperMembership` (SQLite/MySQL via the
  existing SQLAlchemy database layer, Alembic migration `e4f5a6b7c8d9`).
- Workspace fields: stable `id`, `name`, lifecycle `status`
  (`active`/`archived`/`deleting`/`deleted`), `schema_mode` (`bound`/`none`), the immutable
  `schema_id`/`schema_version`/`schema_hash` triple for bound mode, monotonic `revision`,
  `created_at`/`updated_at`. DB CHECK constraints enforce the bound-vs-none invariant and
  the status vocabulary independently of the service layer.
- Paper inclusion is Workspace-to-Paper membership (`workspace_paper_memberships`, unique
  `(workspace_id, paper_id)` pair). The global `papers` table gains no workspace column;
  Layer1/L2 public APIs never take a mandatory `workspace_id` (AC-024).

### 1.2 Control-plane service (`layer3.workspace.WorkspaceService`)
- `create` (bound or none), `get`, `list_workspaces`, `add_paper` (idempotent),
  `remove_paper` (visibility revoked before derived-file cleanup), `archive` (idempotent,
  preserves memberships/files), `delete` (two-phase: durable `deleting` + membership
  revocation committed BEFORE destructive cleanup, then `deleted` tombstone; global
  Paper/L2S1 assets never touched), `rebind_schema` (always rejected — binding immutable
  in Stage1).

### 1.3 Workspace-specific derived storage (`layer3.storage`)
- `workspace_layout` derives `<root>/<workspace_id>/schemas/` and `<root>/<workspace_id>/wiki/`
  from the persistent Workspace identity; `WorkspaceStorageLayout` injects those roots into
  the existing L2S2 `SchemaRunStorage` and L2S3 `WikiStore` (REQ-006, no persistence
  reimplementation).
- `compute_wiki_input_fingerprint` / `current_schema_run_identities` produce the
  deterministic Base Wiki input fingerprint (workspace id + schema triple + ordered
  membership + current Workspace Schema run identities).
- `BuildProvenance` (`provenance.json` inside the Workspace Wiki root) records the last
  successful build input fingerprint, build revision and timestamp — never a boolean
  readiness flag (REQ-007).

### 1.4 Workspace-owned Schema governance (`layer3.schema.WorkspaceSchemaService`)
- `materialize` (bound + active + member required) delegates to the L2S2 public
  `extract_schema` with the Workspace-specific storage injected; `storage`/`storage_root`
  injection by callers is rejected.
- `get_instance` / `get_field` delegate to `get_schema` / `get_field`; missing/corrupt runs
  surface `schema_missing`; none-mode Workspaces surface `schema_disabled` with no fallback
  to global or foreign content (AC-007).

### 1.5 Workspace-owned Base Wiki governance (`layer3.wiki.WorkspaceWikiService`)
- Build reuses the L2S3 `WorkspaceWikiBuildService` / `WikiStore` / `WikiService`
  composition via storage-root injection; `derive_workspace_context` reconstructs the L2S3
  `WorkspaceContext` from the persistent control plane (never the other way around).
- Freshness is derived: recorded fingerprint vs recomputed fingerprint → `ready` (plus
  WikiStore integrity checks) / `stale` / `missing` / `error` / `unsupported`. No-schema
  Workspaces report Base Wiki capability unsupported (AC-009).

### 1.6 Read-only Grounding (`layer3.grounding.WorkspaceGroundingService`)
- `ground(workspace_id)` returns the immutable, deterministic `GroundedWorkspace` snapshot:
  identity/status/revision, visible Papers with per-Paper asset availability (global L2S1
  readiness + Workspace Schema status), schema mode/binding, Schema coverage, Base Wiki
  status, capability summary and recommended actions (reported, never executed).
- Grounding only inspects: database control plane, global L2S1 assets, Workspace current
  Schema pointers and Wiki artifacts/provenance. It never calls LLM/embedding providers,
  never builds indexes/extracts Schema/rebuilds a Wiki, never mutates any state (AC-013).

### 1.7 Bound knowledge gateway (`layer3.knowledge.WorkspaceKnowledgeGateway`)
- Created with `workspace_id` (+ optional expected revision); upper-layer public methods
  (`list_papers`, `get_paper`, `search_evidence`, `read_evidence`, `get_schema_instance`,
  `get_schema_field`, `wiki_status`, `search_wiki`) never take a Workspace identifier
  (AC-022).
- Every call revalidates existence → expected revision (`workspace_changed`) → active
  (`workspace_not_active`) → membership (`paper_not_member`) BEFORE any lower-layer call
  (REQ-012 / AC-015/018/023). Evidence reads delegate to the existing L2S1 public API
  (`search_bm25` / `read_blocks`).

### 1.8 Stable error codes
`workspace_not_found`, `workspace_not_active`, `workspace_changed`, `paper_not_found`,
`paper_not_member`, `schema_binding_immutable`, `invalid_workspace_input`,
`schema_disabled`, `schema_missing`, `wiki_unsupported`, `wiki_missing`, `wiki_stale`,
`wiki_corrupt`, `empty_membership`, `workspace_mismatch` (provenance).

## 2. Not implemented in Layer3 Stage1 (explicit non-goals)

- Agentic Loop runtime, LangGraph, Multi-Agent orchestration, ResearchPlan execution,
  Thought/Action protocols (C-001 — verified by the static import-graph test
  `tests/integration/test_l3s1_no_agent_dependency.py`).
- Agentic Wiki self-building, paper discovery/download, Runtime/Executor/Supervisor
  configuration (C-010).
- Schema rebinding / schema migration between Workspaces (REQ-003, AC-005 — always
  rejected).
- Base Wiki construction for no-schema Workspaces (REQ-005 / AC-009 — reported as
  unsupported).
- Any `workspace_id` parameter added to existing global Paper / L2S1 public APIs (AC-024).

## 3. Repository layout

```text
src/transit_scholar/
  db/models.py                      # Workspace + WorkspacePaperMembership ORM models
  layer3/
    workspace/                      # control-plane service, models, errors, schema binding
    storage/                        # layout, fingerprint, provenance
    schema/                         # Workspace-owned Schema governance
    wiki/                           # Workspace-owned Base Wiki governance
    grounding/                      # read-only Grounding service + snapshots
    knowledge/                      # bound WorkspaceKnowledgeGateway + L2S1 delegate
alembic/versions/e4f5a6b7c8d9_create_workspace_tables.py

data/
  layer3/workspaces/<workspace_id>/
    schemas/<paper_id>/current.json, runs/...
    wiki/manifest.json, pages.jsonl, entities.jsonl, page_entity_links.jsonl,
         index/, provenance.json
```

## 4. Acceptance criteria evidence (AC-001 .. AC-024)

Every criterion has explicit passing automated-test or inspection evidence. “E2E” refers to
the new `tests/integration/` regression files added by T-006; unit suites are the existing
`tests/test_l3s1_*.py` files.

| AC | Criterion summary | Evidence (test file / method or inspection) |
|----|-------------------|----------------------------------------------|
| AC-001 | create persists id/name/status/schema/timestamps/revision; restart reconstruction | `test_l3s1_workspace_service.py` (create/get); E2E `test_l3s1_e2e_bound_workspace.py::test_e2e_bound_workspace_delete_preserves_global_assets` and `test_l3s1_e2e_none_workspace.py::test_e2e_none_workspace_delete_preserves_global_assets` (independent-session reads after commit) |
| AC-002 | one Paper, two Workspaces → two memberships, one global Paper | `test_l3s1_workspace_service.py` (multi-workspace membership); E2E `test_l3s1_e2e_isolation_governance.py::test_same_paper_same_schema_two_workspaces_stay_isolated` |
| AC-003 | duplicate membership addition rejected/no duplicate rows | `test_l3s1_workspace_domain.py` (DB unique pair), `test_l3s1_workspace_service.py` (idempotent add) |
| AC-004 | bound persists exact triple; none persists no triple | `test_l3s1_workspace_service.py`, `test_l3s1_grounding_snapshot.py`; E2E bound/none flows |
| AC-005 | schema switch request rejected without mutation | `test_l3s1_workspace_service.py` (`rebind_schema`) |
| AC-006 | same Paper+Schema in two Workspaces → distinct roots; one deletion leaves the other | `test_l3s1_storage_governance.py`, `test_l3s1_schema_workspace.py`, `test_l3s1_gateway_schema_isolation.py`; E2E `test_l3s1_e2e_isolation_governance.py::test_same_paper_same_schema_two_workspaces_stay_isolated` |
| AC-007 | no-schema Workspace → schema_disabled, no fallback | `test_l3s1_schema_workspace.py`, `test_l3s1_grounding_readonly.py`, `test_l3s1_gateway_schema_isolation.py`; E2E none flow |
| AC-008 | distinct Wiki roots; A's Wiki never returned for B | `test_l3s1_wiki_workspace.py`, `test_l3s1_gateway_wiki_isolation.py`; E2E isolation test |
| AC-009 | no-schema → wiki unsupported, no fabricated Wiki | `test_l3s1_wiki_workspace.py`, `test_l3s1_grounding_snapshot.py`; E2E none flow |
| AC-010 | membership/schema-run change → derived stale (no flag) | `test_l3s1_storage_governance.py`, `test_l3s1_wiki_workspace.py`, `test_l3s1_grounding_snapshot.py`; E2E `test_l3s1_e2e_isolation_governance.py::test_wiki_freshness_derived_from_fingerprint_real_flow` |
| AC-011 | unchanged inputs + intact artifacts → ready | `test_l3s1_wiki_workspace.py`, `test_l3s1_grounding_snapshot.py`; E2E bound flow + isolation flow |
| AC-012 | normalized snapshot with all required parts | `test_l3s1_grounding_snapshot.py`; E2E bound flow |
| AC-013 | Grounding read-only, no LLM/embedding, no mutation | `test_l3s1_grounding_readonly.py`; E2E `test_l3s1_e2e_isolation_governance.py::test_grounding_readonly_no_mutations_real_assets` |
| AC-014 | member added without L2S1/Schema readiness; Grounding exposes missing | `test_l3s1_lifecycle_membership_revocation.py`, `test_l3s1_grounding_snapshot.py`; E2E bound/none flows |
| AC-015 | removed Paper inaccessible via paper/RAG/Schema/Wiki paths | `test_l3s1_lifecycle_membership_revocation.py`, `test_l3s1_knowledge_stale_revalidation.py`, `test_l3s1_knowledge_evidence_delegation.py`; E2E bound flow |
| AC-016 | archive preserves memberships/files; active access → workspace_not_active | `test_l3s1_lifecycle_archive.py`, `test_l3s1_grounding_snapshot.py`, `test_l3s1_knowledge_bound_gateway.py`; E2E bound flow |
| AC-017 | delete two-phase, memberships + Workspace-owned storage removed, global assets intact | `test_l3s1_lifecycle_delete.py`, `test_l3s1_grounding_snapshot.py`, `test_l3s1_knowledge_bound_gateway.py`; E2E bound/none delete tests |
| AC-018 | non-member call fails before lower-layer call | `test_l3s1_knowledge_bound_gateway.py`, `test_l3s1_knowledge_evidence_delegation.py`, `test_l3s1_gateway_schema_isolation.py`; E2E bound/none flows |
| AC-019 | evidence search/read delegate to L2S1 public APIs, no duplication | `test_l3s1_knowledge_evidence_delegation.py`; E2E bound flow |
| AC-020 | Schema reads resolve the Workspace-specific root only | `test_l3s1_gateway_schema_isolation.py`; E2E bound flow + isolation test |
| AC-021 | Wiki read/search bound to the Workspace's own Wiki store | `test_l3s1_gateway_wiki_isolation.py`; E2E isolation test |
| AC-022 | gateway created with workspace_id; no per-call workspace_id | `test_l3s1_knowledge_bound_gateway.py`; E2E bound flow + `test_l3s1_no_agent_dependency.py::test_gateway_usable_without_agent_framework` |
| AC-023 | stale revision → workspace_changed / revalidation; never old-snapshot auth | `test_l3s1_lifecycle_revision_boundary.py`, `test_l3s1_knowledge_stale_revalidation.py`; E2E bound flow |
| AC-024 | Layer1/L2 APIs remain independent; Layer3 = composition + root injection | `test_l3s1_workspace_domain.py`, `test_l3s1_knowledge_evidence_delegation.py`, `test_l3s1_knowledge_bound_gateway.py`, `test_l3s1_schema_workspace.py`, `test_l3s1_wiki_workspace.py`; E2E delete tests (direct L2S1 use after delete) + `test_l3s1_no_agent_dependency.py` |

## 5. Verification runs (T-006)

All runs executed with the repository virtualenv on a cleanly migrated SQLite
database (`alembic upgrade head` → `e4f5a6b7c8d9`) with the offline Layer2
guard active.

| Suite | Command | Result |
|-------|---------|--------|
| Layer3 Stage1 unit suite | `pytest tests/test_l3s1_*.py` | **151 passed** |
| Layer3 Stage1 end-to-end regression (new) | `pytest tests/integration/*.py` | **12 passed** |
| Layer3 Stage1 complete | combined above | **163 passed** |
| Database/Layer1 regression (new Workspace tables) | `pytest tests/test_stage1_database.py tests/test_database_lifecycle.py tests/test_stage5_citation.py` | **59 passed** |
| L2S1 regression | `pytest tests/test_l2s1_*.py` | **167 passed / 3 failed (pre-existing, see §6)** |
| L2S2 regression | `pytest tests/test_l2s2_*.py` | **629 passed** |
| L2S3 regression | `pytest tests/test_l2s3_*.py` | **98 passed** |

Static inspection (also asserted dynamically by
`tests/integration/test_l3s1_no_agent_dependency.py`): no Layer3 Stage1 source
module imports LangGraph, LangChain or any Agent-runtime package, and the
Layer3 source tree contains no ResearchPlan / Thought-Action / Agentic-Wiki /
self-building vocabulary (C-001).

## 6. Pre-existing findings outside Layer3 Stage1 scope

Running the L2S1 regression suite exposes 3 failures that are **present at the
untouched repository HEAD** (verified on a clean detached-HEAD worktree of
`29952a35`; none of them involve Layer3 files):

- `test_l2s1_gate.py::test_gate_layer2_parse_code_imports_no_layer1_write_path`
- `test_l2s1_parser.py::test_parser_no_llm_repair_or_voting_in_source`
- `test_l2s1_safety.py::test_safety_no_scope_creep_source_scan`

All three are L2S1-era source scans over `src/transit_scholar/layer2/**` that
were not updated when L2S3 was merged: `layer2/wiki/application.py` imports
`transit_scholar.metadata` (the production `read_paper_metadata` loader) and
`layer2/wiki/proposals.py` calls `client.generate(` — both legitimate L2S3
machinery that the older scans flag. They are unrelated to Layer3 Stage1 and
outside this task's allowed scope (`tests/test_l2s1_*.py` is not editable
here); they need a follow-up L2S3-scope update of those three scan tests.

A second pre-existing harness quirk: `tests/test_layer1_realset.py` cannot be
collected by a plain `pytest tests` invocation because it imports a
`validate_layer1_realset` script that does not exist under `scripts/` (the
repo only ships `l2s2_runtime_smoke.py` / `l2s3_production_smoke.py` there).
This also reproduces at HEAD and is unrelated to Layer3 Stage1.

## 7. Operational notes

- Workspace-owned Schema/Wiki heavy artifacts stay file-backed under the Workspace-specific
  root; the database holds only the control plane (REQ-006).
- Stale Wiki content is never silently served as current: reads raise `wiki_stale` (or
  `wiki_missing`/`wiki_corrupt`) until a rebuild records a fresh fingerprint (REQ-007
  recommendation preserved).
- All integration tests run fully offline (fake parsers/LLM/embedding providers, isolated
  data roots, isolated SQLite databases), consistent with the Layer2 suite conventions.