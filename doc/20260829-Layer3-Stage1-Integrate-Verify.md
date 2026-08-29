# Layer3 Stage1 Workspace Grounding — Integration & Verification Report (frozen after P1 repairs)

> 文档性质：实现完成情况说明 / 回归验证报告（Implementation Report + Verification Evidence）
> 适用范围：Layer3 Stage1（Workspace 控制面、Workspace 归属的 Schema / Base Wiki、只读 Grounding、绑定知识网关）
> 版本：PSC contract v4（REQ-001..REQ-006 / AC-001..AC-021），T-001/T-002 P1 修复后由 T-004 全量回归复验。
> 本报告只描述**已实现并被测试证明**的 Layer3 Stage1 行为。Agentic Loop、LangGraph、Multi-Agent、
> ResearchPlan、Thought/Action、Agentic Wiki 自构建、论文发现/下载**均不属于** Layer3 Stage1（C-001），
> 本报告不声称也不暗示任何此类功能。

---

## 1. What Layer3 Stage1 implements (implemented behavior only)

Layer3 Stage1 delivers a **Workspace Grounding layer plus a bound, Workspace-safe
knowledge access gateway**, built by composition over the existing Layer1/L2S1/L2S2/L2S3
public APIs. No Agent runtime exists anywhere in this stage.

### 1.1 Persistent Workspace control plane (REQ-001/REQ-005)
- `transit_scholar.db.models.Workspace` + `WorkspacePaperMembership` (SQLite/MySQL via the
  existing SQLAlchemy database layer, Alembic migration `e4f5a6b7c8d9`).
- Workspace fields: stable `id`, `name`, lifecycle `status`
  (`active`/`archived`/`deleting`/`deleted`), `schema_mode` (`bound`/`none`), the immutable
  `schema_id`/`schema_version`/`schema_hash` triple for bound mode, monotonic `revision`,
  `created_at`/`updated_at`. DB CHECK constraints enforce the bound-vs-none invariant and
  the status vocabulary independently of the service layer.
- Paper inclusion is Workspace-to-Paper membership (`workspace_paper_memberships`, unique
  `(workspace_id, paper_id)` pair). The global `papers` table gains no workspace column;
  Layer1/L2 public APIs never take a mandatory `workspace_id` (AC-018).

### 1.2 Control-plane service (`layer3.workspace.WorkspaceService`)
- `create` (bound or none), `get`, `list_workspaces`, `add_paper` (idempotent),
  `remove_paper` (visibility revoked before derived-file cleanup), `archive` (idempotent,
  preserves memberships/files), `delete` (two-phase: durable `deleting` + membership
  revocation committed BEFORE destructive cleanup, then `deleted` tombstone; global
  Paper/L2S1 assets never touched), `rebind_schema` (always rejected — binding immutable
  in Stage1, C-005).

### 1.3 Workspace-specific derived storage (`layer3.storage`)
- `workspace_layout` derives `<root>/<workspace_id>/schemas/` and `<root>/<workspace_id>/wiki/`
  from the persistent Workspace identity; `WorkspaceStorageLayout` injects those roots into
  the existing L2S2 `SchemaRunStorage` and L2S3 `WikiStore` (C-002/C-003).
- `compute_wiki_input_fingerprint` / `current_schema_run_identities` produce the
  deterministic Base Wiki input fingerprint (workspace id + schema triple + ordered
  membership + current Workspace Schema run identities).
- `BuildProvenance` (`provenance.json` inside the Workspace Wiki root) records the last
  successful build input fingerprint, build revision and timestamp — never a boolean
  readiness flag.

### 1.4 Workspace-owned Schema governance (`layer3.schema.WorkspaceSchemaService`) — P1 repair B
- `materialize` (bound + active + member required) delegates to the L2S2 public
  `extract_schema` with the Workspace-specific storage injected; `storage`/`storage_root`
  injection by callers is rejected.
- **Immutable-binding enforcement (REQ-003/REQ-004):** the current `SchemaDefinition`
  resolved through the existing L2S2 loader is verified against the persisted Workspace
  binding triple (schema_id, schema_version, canonical schema_hash) BEFORE any L2S2
  extraction/persistence; a definition that differs in version or content hash (or cannot
  be resolved) fails explicitly with the stable `schema_binding_mismatch` error and writes
  nothing (AC-009/AC-010/AC-016; C-005).
- Read paths (`get_instance`, `get_field`, `current_run_identities`, per-Paper readiness)
  validate the **persisted run itself** through the existing L2S2 read-back integrity
  checks and then compare the recorded Schema identity — where the normal L2S2 current
  pointer/run metadata supplies `schema_hash`, that too — against the Workspace binding.
  `current.json` existence alone never makes a run usable (REQ-004/AC-012..AC-015).
- `validate_binding()` / `require_compatible_run()` / `paper_schema_readiness()` are the
  shared helpers used by materialization, reads and Grounding, emitting the single stable
  `schema_binding_mismatch` code for every binding incompatibility (AC-016).
- `get_instance` / `get_field` surface `schema_missing` for missing/corrupt/unreadable
  runs; none-mode Workspaces surface `schema_disabled` with no fallback to global or
  foreign content.

### 1.5 Workspace-owned Base Wiki governance (`layer3.wiki.WorkspaceWikiService`) — P1 repair A
- Build reuses the L2S3 `WorkspaceWikiBuildService` / `WikiStore` / `WikiService`
  composition via storage-root injection; `derive_workspace_context` reconstructs the L2S3
  `WorkspaceContext` from the persistent control plane (never the other way around).
- `status()` is derived read-only from authoritative state: `ready` is now a
  **production-completeness state** (REQ-001), reached only when ALL of the following hold:
  - recorded provenance exists, belongs to this Workspace, and its input fingerprint
    equals the fingerprint recomputed from the current Workspace inputs (identity,
    immutable Schema binding triple, membership, current Workspace Schema run identities);
  - provenance `build_status == "complete"`;
  - persisted `WikiManifest.build_status == "complete"` (partial/failed → `error`);
  - the authoritative Wiki source snapshot passes the existing WikiStore integrity checks;
  - the mandatory persistent vector index exists (C-010), its `source_fingerprint` equals
    the authoritative snapshot fingerprint (not stale), its index version/vector metadata
    are valid, vector dimensions are consistent with the declared metadata dimension, and
    every required Wiki Page and existing Entity has a persisted vector (AC-004/AC-005).
- Status vocabulary: `ready` (complete/current/valid), `stale` (input fingerprint
  mismatch / no recorded fingerprint), `missing` (no snapshot / empty membership),
  `unsupported` (no-schema), `error` (corrupt/unreadable provenance, non-complete
  provenance, partial/failed manifest, or any mandatory-vector-index failure) with stable
  `error_code` values (AC-001..AC-006).
- Readiness verification reuses the new L2S3 read-only `audit_vector_index_readonly`
  helper; `status()`/Grounding NEVER build indexes, embed documents, call LLMs or mutate
  Wiki artifacts (C-001).
- No-schema Workspaces report Base Wiki capability unsupported with no fallback (AC-009
  equivalent in this contract's REQ-005 boundaries).

### 1.6 Read-only Grounding (`layer3.grounding.WorkspaceGroundingService`)
- `ground(workspace_id)` returns the immutable, deterministic `GroundedWorkspace` snapshot:
  identity/status/revision, visible Papers with per-Paper asset availability (global L2S1
  readiness + Workspace Schema readiness derived from actually usable, binding-compatible
  runs), schema mode/binding, Schema coverage, Base Wiki status, capability summary and
  recommended actions (reported, never executed).
- `capabilities.wiki_read` is `true` ONLY when the Base Wiki derived status is `ready`
  (REQ-002/AC-007); every partial/failed/stale/missing/corrupt/index-invalid state exposes
  `wiki_read=false`.
- Per-Paper `schema_status=ready` means the persisted Workspace current run is readable
  through the L2S2 integrity checks AND fully compatible with the immutable Workspace
  binding; non-ready Papers carry the explicit `schema_error_code`
  (`schema_missing` / `schema_binding_mismatch`) instead of being silently marked ready
  (REQ-004/AC-012..AC-016).
- Grounding only inspects: database control plane, global L2S1 assets, Workspace current
  Schema runs and Wiki artifacts/provenance. It never calls LLM/embedding providers,
  never builds indexes/extracts Schema/rebuilds a Wiki, never mutates any state (C-001).

### 1.7 Bound knowledge gateway (`layer3.knowledge.WorkspaceKnowledgeGateway`)
- Created with `workspace_id` (+ optional expected revision); upper-layer public methods
  (`list_papers`, `get_paper`, `search_evidence`, `read_evidence`, `get_schema_instance`,
  `get_schema_field`, `wiki_status`, `search_wiki`) never take a Workspace identifier.
- Every call revalidates existence → expected revision (`workspace_changed`) → active
  (`workspace_not_active`) → membership (`paper_not_member`) BEFORE any lower-layer call
  (C-008). Evidence reads delegate to the existing L2S1 public API (`search_bm25` /
  `read_blocks`).
- Wiki search validates the corrected readiness boundary first: a missing/stale/error Base
  Wiki raises `WikiMissingError` / `WikiStaleError` / `WikiCorruptError` and is NEVER
  silently served as current content (REQ-002/AC-008). Schema views derive readiness from
  binding-compatible persisted runs (REQ-004).

### 1.8 Stable error codes
- Workspace/lifecycle: `workspace_not_found`, `workspace_not_active`, `workspace_changed`,
  `paper_not_found`, `paper_not_member`, `invalid_workspace_input`,
  `schema_binding_immutable`, `empty_membership`.
- Schema: `schema_disabled`, `schema_missing`, **`schema_binding_mismatch`** (single
  stable code for every binding incompatibility, AC-016).
- Wiki: `wiki_unsupported`, `wiki_missing`, `wiki_stale`, `wiki_corrupt`,
  `workspace_mismatch` (provenance), `snapshot_missing`, `no_recorded_fingerprint`,
  `input_fingerprint_mismatch`, `build_provenance_unreadable`,
  `build_provenance_incomplete`, `manifest_build_partial`, `manifest_build_failed`,
  `vector_index_missing`, `vector_index_stale`, `vector_index_incompatible`.

## 2. Not implemented in Layer3 Stage1 (explicit non-goals)

- Agentic Loop runtime, LangGraph, Multi-Agent orchestration, ResearchPlan execution,
  Thought/Action protocols (C-001 — verified by the static import-graph test
  `tests/integration/test_l3s1_no_agent_dependency.py`).
- Agentic Wiki self-building, paper discovery/download, Runtime/Executor/Supervisor
  configuration.
- Schema rebinding / schema migration between Workspaces (C-005 — always rejected).
- Base Wiki construction for no-schema Workspaces (reported as unsupported).
- Any `workspace_id` parameter added to existing global Paper / L2S1 public APIs (AC-018).
- Semantic Wiki search provider wiring and Pydantic snapshot freezing remain explicit
  non-blocking follow-ups (C-013); Stage1 search contract is lexical by default.

## 3. Repository layout

```text
src/transit_scholar/
  db/models.py                      # Workspace + WorkspacePaperMembership ORM models
  layer3/
    workspace/                      # control-plane service, models, errors, schema binding
    storage/                        # layout, fingerprint, provenance
    schema/                         # Workspace-owned Schema governance (binding-enforced)
    wiki/                           # Workspace-owned Base Wiki governance (production-complete)
    grounding/                      # read-only Grounding service + snapshots
    knowledge/                      # bound WorkspaceKnowledgeGateway + L2S1 delegate
  layer2/wiki/service.py            # + audit_vector_index_readonly (read-only, provider-free)
alembic/versions/e4f5a6b7c8d9_create_workspace_tables.py

data/
  layer3/workspaces/<workspace_id>/
    schemas/<paper_id>/current.json, runs/...
    wiki/manifest.json, pages.jsonl, entities.jsonl, page_entity_links.jsonl,
         index/, provenance.json
```

## 4. P1 repair regression evidence (T-001: Base Wiki readiness; T-002: Schema binding)

35 new deterministic regression tests (T-003 verified the preserved safety/isolation
suites on top of them; all executed again for this report):

- `tests/test_l3s1_wiki_workspace.py`:
  `test_partial_manifest_is_not_ready` (AC-001), `test_failed_manifest_is_not_ready`
  (AC-002), `test_non_complete_provenance_is_not_ready[partial|failed]` (AC-003),
  `test_missing_vector_index_is_explicit_error` (AC-004),
  `test_stale_vector_index_is_explicit_error`,
  `test_vector_index_incompatible_dimensions_is_error`,
  `test_vector_index_incomplete_coverage_is_error` (AC-005),
  `test_complete_current_valid_wiki_is_ready_and_searchable` (AC-006).
- `tests/test_l3s1_gateway_wiki_isolation.py`:
  `test_gateway_wiki_search_rejects_partial_manifest`,
  `test_gateway_wiki_search_rejects_missing_vector_index`,
  `test_gateway_wiki_search_rejects_stale_vector_index`,
  `test_gateway_wiki_search_rejects_incomplete_provenance` (AC-008).
- `tests/test_l3s1_grounding_snapshot.py`:
  `test_grounding_wiki_read_false_for_partial_manifest`,
  `test_grounding_wiki_read_false_for_failed_manifest`,
  `test_grounding_wiki_read_false_for_incomplete_provenance`,
  `test_grounding_wiki_read_false_for_missing_vector_index`,
  `test_grounding_wiki_read_false_for_stale_vector_index`,
  `test_grounding_wiki_read_false_for_incompatible_vector_index`,
  `test_grounding_wiki_read_true_only_for_complete_current_valid_wiki` (AC-007);
  `test_grounding_non_ready_when_current_pointer_references_missing_run` (AC-012),
  `test_grounding_non_ready_for_readable_run_with_version_mismatch` (AC-013),
  `test_grounding_non_ready_for_pointer_hash_mismatch` (AC-014),
  `test_grounding_ready_for_readable_binding_compatible_run` (AC-015).
- `tests/test_l3s1_schema_workspace.py`:
  `test_materialize_rejects_same_id_version_with_changed_hash` (AC-010),
  `test_materialize_rejects_changed_schema_version_without_updating_state` (AC-009),
  `test_materialize_unresolvable_current_definition_fails_explicitly` (AC-009/AC-010);
  `test_pointer_identity_mismatch_rejected_with_stable_code`,
  `test_pointer_schema_hash_mismatch_rejected_with_stable_code`,
  `test_readable_run_with_version_mismatch_rejected_with_stable_code`,
  `test_readable_run_with_schema_id_mismatch_rejected_with_stable_code`,
  `test_readable_run_with_manifest_hash_mismatch_rejected_with_stable_code` (AC-013/
  AC-014/AC-016), `test_pointer_to_missing_run_reports_schema_missing_not_ready`
  (AC-012), `test_compatible_run_supports_every_read_surface` (AC-011/AC-015).
- `tests/test_l3s1_gateway_schema_isolation.py`:
  `test_gateway_rejects_binding_incompatible_pointer_with_stable_code` (AC-016).
- `tests/test_l3s1_grounding_readonly.py`: existing read-only guarantees re-executed
  unchanged (no new provider/mutation paths introduced by the repairs, C-001).

## 5. Acceptance criteria evidence (AC-001 .. AC-021)

Every criterion has passing automated-test evidence executed for this report. "E2E"
refers to `tests/integration/test_l3s1_*.py`.

| AC | Criterion summary | Evidence (test file / method) |
|----|-------------------|-------------------------------|
| AC-001 | partial Manifest + matching fingerprint → not ready | `test_l3s1_wiki_workspace.py::test_partial_manifest_is_not_ready` |
| AC-002 | failed Manifest + matching fingerprint → not ready | `test_l3s1_wiki_workspace.py::test_failed_manifest_is_not_ready` |
| AC-003 | non-complete provenance → not ready even with readable sources | `test_l3s1_wiki_workspace.py::test_non_complete_provenance_is_not_ready[partial/failed]` |
| AC-004 | mandatory vector index absent → non-ready + stable error, no current/ready exposure | `test_l3s1_wiki_workspace.py::test_missing_vector_index_is_explicit_error`; `test_l3s1_grounding_snapshot.py::test_grounding_wiki_read_false_for_missing_vector_index`; `test_l3s1_gateway_wiki_isolation.py::test_gateway_wiki_search_rejects_missing_vector_index` |
| AC-005 | stale/incompatible index, invalid dimensions, missing coverage → non-ready + stable error | `test_l3s1_wiki_workspace.py::test_stale_vector_index_is_explicit_error`, `::test_vector_index_incompatible_dimensions_is_error`, `::test_vector_index_incomplete_coverage_is_error`; grounding/gateway counterparts |
| AC-006 | complete provenance+manifest+snapshot+current compatible full-coverage index → ready | `test_l3s1_wiki_workspace.py::test_complete_current_valid_wiki_is_ready_and_searchable`; `test_l3s1_grounding_snapshot.py::test_grounding_wiki_read_true_only_for_complete_current_valid_wiki` |
| AC-007 | non-ready Base Wiki (partial/failed/build-status/index-invalid) → capabilities.wiki_read=false | six `test_l3s1_grounding_snapshot.py::test_grounding_wiki_read_false_for_*` regressions |
| AC-008 | search/search_wiki never serve non-ready snapshots; explicit error boundary | four `test_l3s1_gateway_wiki_isolation.py::test_gateway_wiki_search_rejects_*`; `test_l3s1_wiki_workspace.py::test_wiki_read_rejects_stale_snapshot_explicitly`, `::test_corrupted_artifacts_are_not_ready` |
| AC-009 | changed SchemaDefinition version → materialize fails before L2S2 persistence, no run created/updated | `test_l3s1_schema_workspace.py::test_materialize_rejects_changed_schema_version_without_updating_state` |
| AC-010 | same id/version, changed content hash → materialize fails before L2S2 persistence | `test_l3s1_schema_workspace.py::test_materialize_rejects_same_id_version_with_changed_hash`, `::test_materialize_unresolvable_current_definition_fails_explicitly` |
| AC-011 | exact triple match → normal materialization through L2S2 public `extract_schema` with Workspace storage | `test_l3s1_schema_workspace.py::test_materialize_writes_only_workspace_root_and_reads_match`, `::test_compatible_run_supports_every_read_surface`; E2E bound flow |
| AC-012 | current.json exists but run missing/corrupt/unreadable → Grounding not ready | `test_l3s1_grounding_snapshot.py::test_grounding_non_ready_when_current_pointer_references_missing_run`; `test_l3s1_schema_workspace.py::test_pointer_to_missing_run_reports_schema_missing_not_ready` |
| AC-013 | readable run with incompatible schema_id/version → not usable, Grounding not ready | `test_l3s1_grounding_snapshot.py::test_grounding_non_ready_for_readable_run_with_version_mismatch`; `test_l3s1_schema_workspace.py::test_readable_run_with_version_mismatch_rejected_with_stable_code`, `::test_readable_run_with_schema_id_mismatch_rejected_with_stable_code` |
| AC-014 | persisted pointer/run schema_hash incompatible → run rejected, Grounding not ready | `test_l3s1_grounding_snapshot.py::test_grounding_non_ready_for_pointer_hash_mismatch`; `test_l3s1_schema_workspace.py::test_pointer_schema_hash_mismatch_rejected_with_stable_code`, `::test_readable_run_with_manifest_hash_mismatch_rejected_with_stable_code` |
| AC-015 | readable compatible run → get_instance/get_field/current identities/Grounding ready | `test_l3s1_schema_workspace.py::test_compatible_run_supports_every_read_surface`; `test_l3s1_grounding_snapshot.py::test_grounding_ready_for_readable_binding_compatible_run` |
| AC-016 | binding mismatch surfaces one stable explicit code, never mapped to ready | `SchemaBindingMismatchError.code == "schema_binding_mismatch"`; `test_l3s1_schema_workspace.py::test_pointer_identity_mismatch_rejected_with_stable_code` (+ run/pointer hash/version/id mismatch tests), `test_l3s1_gateway_schema_isolation.py::test_gateway_rejects_binding_incompatible_pointer_with_stable_code` |
| AC-017 | isolation, no-fallback, read-only Grounding, stale-revision, membership-first, archive, two-phase delete, global-asset survival preserved | complete `tests/test_l3s1_*.py` + `tests/integration/test_l3s1_*.py` suites (lifecycle/isolation/readonly/no-agent-dependency modules) |
| AC-018 | no mandatory workspace_id added to Layer1/Layer2 public APIs | `test_l3s1_schema_workspace.py::test_l2s2_public_api_usable_independently_without_workspace_id`, `test_l3s1_workspace_domain.py`, gateway/no-agent E2E tests |
| AC-019 | complete L3S1 unit + integration suites pass | **186 + 12 = 198 passed** (see §6) |
| AC-020 | affected database/Layer1, L2S2, L2S3 suites green, no new failures (only documented baseline pre-existing L2S1 failures) | 59 + 629 + 98 passed (see §6, §7) |
| AC-021 | freeze wording only after AC-001..AC-020 pass with executed evidence | this document §6/§7 evidence + §8 freeze declaration |

## 6. Verification runs (T-004 full regression revalidation)

All runs executed with the repository virtualenv (Python 3.11.15, pytest 9.1.1) on an
isolated migrated SQLite database (`alembic upgrade head` → `e4f5a6b7c8d9`), offline
Layer2 guard active, `-p no:cacheprovider`.

| Suite | Command | Result |
|-------|---------|--------|
| Targeted T-001/T-002 repaired regressions (6 repaired unit files) | `pytest tests/test_l3s1_wiki_workspace.py tests/test_l3s1_gateway_wiki_isolation.py tests/test_l3s1_grounding_snapshot.py tests/test_l3s1_grounding_readonly.py tests/test_l3s1_schema_workspace.py tests/test_l3s1_gateway_schema_isolation.py` | **83 passed** |
| Layer3 Stage1 unit suite (complete) | `pytest tests/test_l3s1_*.py` | **186 passed** (151 pre-repair + 35 new) |
| Layer3 Stage1 integration suite (complete) | `pytest tests/integration/test_l3s1_*.py` | **12 passed** |
| Layer3 Stage1 combined unit + integration | above two | **198 passed** |
| Database/Layer1 regression (Workspace tables) | `pytest tests/test_stage1_database.py tests/test_database_lifecycle.py tests/test_stage5_citation.py` | **59 passed** |
| L2S2 regression (Layer3 composes over L2S2) | `pytest tests/test_l2s2_*.py` | **629 passed** |
| L2S3 regression (Layer3 composes over L2S3) | `pytest tests/test_l2s3_*.py` | **98 passed** |
| L2S1 regression (informational, pre-existing) | `pytest tests/test_l2s1_gate.py tests/test_l2s1_parser.py tests/test_l2s1_safety.py` (3 scan tests) | **3 failed — pre-existing, see §7** |

Static inspection (also asserted dynamically by
`tests/integration/test_l3s1_no_agent_dependency.py`): no Layer3 Stage1 source module
imports LangGraph, LangChain or any Agent-runtime package, and the Layer3 source tree
contains no ResearchPlan / Thought-Action / Agentic-Wiki / self-building vocabulary
(C-001). Grounding read-only guarantee is asserted by
`test_l3s1_grounding_readonly.py` (recording collaborators, file/DB mutation spies).

## 7. Pre-existing findings outside Layer3 Stage1 scope (re-verified for T-004)

The L2S1 regression suite exposes 3 failures that are **present at the untouched
pre-L3S1 baseline** and unrelated to this repair. Independently re-verified for this
report: the two source files flagged by the scans
(`src/transit_scholar/layer2/wiki/application.py`,
`src/transit_scholar/layer2/wiki/proposals.py`) are byte-identical between baseline
commit `29952a35` and the current tree (`git diff 29952a35 HEAD -- <files>` empty; no
worktree changes), and the executed failure messages are exactly the documented ones:

- `test_l2s1_gate.py::test_gate_layer2_parse_code_imports_no_layer1_write_path` —
  `layer2/wiki/application.py` imports `transit_scholar.metadata`;
- `test_l2s1_parser.py::test_parser_no_llm_repair_or_voting_in_source` —
  `layer2/wiki/proposals.py` contains forbidden construct `.generate(`;
- `test_l2s1_safety.py::test_safety_no_scope_creep_source_scan` —
  `layer2/wiki/application.py` imports forbidden module `transit_scholar.metadata`.

All three are L2S1-era source scans over `src/transit_scholar/layer2/**` that were not
updated when L2S3 was merged; they are legitimate L2S3 machinery. They are unrelated to
Layer3 Stage1, outside this task's allowed scope, and need a follow-up L2S3-scope update
of those three scan tests.

A second pre-existing harness quirk: `tests/test_layer1_realset.py` cannot be collected
by a plain `pytest` invocation because it imports a `validate_layer1_realset` script that
does not exist under `scripts/` (re-verified: `ModuleNotFoundError: No module named
'validate_layer1_realset'` during collection). This also reproduces at the baseline and
is unrelated to Layer3 Stage1.

Neither finding is introduced by the repairs: the L2S2 (629), L2S3 (98) and
database/Layer1 (59) suites required by AC-020 are fully green at the repaired tree.

## 8. Formal freeze declaration

All Contract acceptance criteria AC-001 through AC-021 have passing executed evidence:

- AC-001..AC-008 (Base Wiki production-completeness readiness + wiki_read/search
  boundaries): targeted regressions pass (83 passed) within the complete L3S1 suites;
- AC-009..AC-016 (immutable Schema binding on materialization, reads, readiness and
  Grounding, single stable `schema_binding_mismatch` code): targeted regressions pass;
- AC-017..AC-018 (safety/isolation preserved, no mandatory workspace_id): complete L3S1
  unit + integration suites pass (198 passed);
- AC-019: complete Layer3 Stage1 unit suite **186 passed**, integration suite
  **12 passed**;
- AC-020: affected database/Layer1 **59 passed**, L2S2 **629 passed**, L2S3 **98 passed**
  — no new failures; the only failures anywhere are the three pre-existing L2S1 scan
  failures that reproduce unchanged on the pre-L3S1 baseline (§7);
- AC-021: this document records the actual executed counts above before any freeze
  wording.

**Layer3 Stage1 is therefore formally freeze-ready and is declared FROZEN as of this
report under contract v4 (REQ-001..REQ-006 / AC-001..AC-021).** Any subsequent change to
the Layer3 Stage1 sources or to the covered regression suites requires a re-run of the
AC-019/AC-020 verification gates before the freeze label may be re-asserted.

## 9. Operational notes

- Workspace-owned Schema/Wiki heavy artifacts stay file-backed under the Workspace-specific
  root; the database holds only the control plane (C-003).
- Stale/partial/failed/index-invalid Wiki content is never silently served as current:
  reads raise `wiki_stale` / `wiki_corrupt` / `wiki_missing` until a rebuild records a
  fresh complete provenance (C-011).
- Binding-incompatible Schema content is never silently usable: all Layer3 boundaries
  raise `schema_binding_mismatch` (C-012).
- All integration tests run fully offline (fake parsers/LLM/embedding providers, isolated
  data roots, isolated SQLite databases), consistent with the Layer2 suite conventions.