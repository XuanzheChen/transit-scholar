# Layer3 Stage1 Workspace Grounding — Integration & Verification Report (frozen under contract v5 after full revalidation)

> 文档性质：实现完成情况说明 / 回归验证报告（Implementation Report + Verification Evidence）
> 适用范围：Layer3 Stage1（Workspace 控制面、Workspace 归属的 Schema / Base Wiki、只读 Grounding、绑定知识网关）
> 版本：PSC contract v5（REQ-001..REQ-008 / AC-001..AC-018）。T-001/T-002 的 contract-v4 P1 修复、
> T-002 的跨边界（cross-boundary）Schema 治理修复与 T-003 安全/隔离复验之后，由 T-004 按 v5 全量回归复验；
> 本文档的冻结声明仅在 AC-001..AC-017 全部以实际执行证据通过后作出（AC-018）。
> 本报告只描述**已实现并被测试证明**的 Layer3 Stage1 行为。Agentic Loop、LangGraph、Multi-Agent、
> ResearchPlan、Thought/Action、Agentic Wiki 自构建、论文发现/下载**均不属于** Layer3 Stage1（C-001），
> 本报告不声称也不暗示任何此类功能。

---

## 1. What Layer3 Stage1 implements (implemented behavior only)

Layer3 Stage1 delivers a **Workspace Grounding layer plus a bound, Workspace-safe
knowledge access gateway**, built by composition over the existing Layer1/L2S1/L2S2/L2S3
public APIs. No Agent runtime exists anywhere in this stage.

### 1.1 Persistent Workspace control plane (REQ-007 boundaries)
- `transit_scholar.db.models.Workspace` + `WorkspacePaperMembership` (SQLite/MySQL via the
  existing SQLAlchemy database layer, Alembic migration `e4f5a6b7c8d9`).
- Workspace fields: stable `id`, `name`, lifecycle `status`
  (`active`/`archived`/`deleting`/`deleted`), `schema_mode` (`bound`/`none`), the immutable
  `schema_id`/`schema_version`/`schema_hash` triple for bound mode, monotonic `revision`,
  `created_at`/`updated_at`. DB CHECK constraints enforce the bound-vs-none invariant and
  the status vocabulary independently of the service layer.
- Paper inclusion is Workspace-to-Paper membership (`workspace_paper_memberships`, unique
  `(workspace_id, paper_id)` pair). The global `papers` table gains no workspace column;
  Layer1/L2 public APIs never take a mandatory `workspace_id` (REQ-007 boundary, AC-014).

### 1.2 Control-plane service (`layer3.workspace.WorkspaceService`)
- `create` (bound or none), `get`, `list_workspaces`, `add_paper` (idempotent),
  `remove_paper` (visibility revoked before derived-file cleanup), `archive` (idempotent,
  preserves memberships/files), `delete` (two-phase: durable `deleting` + membership
  revocation committed BEFORE destructive cleanup, then `deleted` tombstone; global
  Paper/L2S1 assets never touched), `rebind_schema` (always rejected — binding immutable
  in Stage1, C-007).

### 1.3 Workspace-specific derived storage (`layer3.storage`)
- `workspace_layout` derives `<root>/<workspace_id>/schemas/` and `<root>/<workspace_id>/wiki/`
  from the persistent Workspace identity; `WorkspaceStorageLayout` injects those roots into
  the existing L2S2 `SchemaRunStorage` and L2S3 `WikiStore` (C-004).
- `compute_wiki_input_fingerprint` produces the deterministic Base Wiki input fingerprint
  (workspace id + schema triple + ordered membership + validated current Workspace Schema
  run identities). `current_schema_run_identities` remains a POINTER-LEVEL reader only —
  it is explicitly documented as never sufficient alone for Wiki freshness (REQ-002); the
  governed identity derivation in `WorkspaceSchemaService` validates every referenced run
  before its identity enters the fingerprint.
- `BuildProvenance` (`provenance.json` inside the Workspace Wiki root) records the last
  successful build input fingerprint, build revision and timestamp — never a boolean
  readiness flag.

### 1.4 Workspace-owned Schema governance (`layer3.schema.WorkspaceSchemaService`) — P1 repair B
- `materialize` (bound + active + member required) delegates to the L2S2 public
  `extract_schema` with the Workspace-specific storage injected; `storage`/`storage_root`
  injection by callers is rejected.
- **Immutable-binding enforcement (REQ-005):** the current `SchemaDefinition`
  resolved through the existing L2S2 loader is verified against the persisted Workspace
  binding triple (schema_id, schema_version, canonical schema_hash) BEFORE any L2S2
  extraction/persistence; a definition that differs in version or content hash (or cannot
  be resolved) fails explicitly with the stable `schema_binding_mismatch` error and writes
  nothing (AC-011).
- Read paths (`get_instance`, `get_field`, `current_run_identities`, per-Paper readiness)
  validate the **persisted run itself** through the existing L2S2 read-back integrity
  checks and then compare the recorded Schema identity — where the normal L2S2 current
  pointer/run metadata supplies `schema_hash`, that too — against the Workspace binding.
  `current.json` existence alone never makes a run usable (REQ-001/REQ-002).
- `validated_current_run_identities()` derives Wiki-fingerprint identities ONLY from
  validated compatible current runs: every member Paper's current run must pass the same
  `require_compatible_run()` governance boundary used by reads (readable through the
  normal L2S2 persistence integrity checks AND schema_id / schema_version / schema_hash
  fully matching the immutable Workspace binding, for both the current pointer and the
  persisted run manifest). A missing/corrupt/unreadable run, or a pointer/run-manifest
  that disagrees with the binding, yields `None` for that Paper and records the stable
  boundary code (`schema_missing` / `schema_binding_mismatch`) in the returned per-Paper
  error map — a current pointer alone never authorizes an identity (REQ-002/AC-007).
- `validate_binding()` / `require_compatible_run()` / `paper_schema_readiness()` are the
  shared helpers used by materialization, reads, Wiki build/status and Grounding,
  emitting the single stable `schema_binding_mismatch` code for every binding
  incompatibility (AC-011).
- `get_instance` / `get_field` surface `schema_missing` for missing/corrupt/unreadable
  runs; none-mode Workspaces surface `schema_disabled` with no fallback to global or
  foreign content (C-005).

### 1.5 Workspace-owned Base Wiki governance (`layer3.wiki.WorkspaceWikiService`) — P1 repair A + cross-boundary repair
- Build reuses the L2S3 `WorkspaceWikiBuildService` / `WikiStore` / `WikiService`
  composition via storage-root injection; `derive_workspace_context` reconstructs the L2S3
  `WorkspaceContext` from the persistent control plane (never the other way around).
- **Build gate (REQ-001/AC-001..AC-004):** BEFORE the L2S3 build consumes any member
  Paper's Schema run, `build()` loads every member's current `SchemaInstance` through the
  SAME governance boundary used by Schema reads — `WorkspaceSchemaService.get_instance()`
  (injectable `schemas` service, default constructed on the same session/data_root). A
  binding-incompatible pointer or persisted run (schema_hash / schema_version mismatch)
  fails explicitly with the stable `schema_binding_mismatch` code, a
  missing/corrupt/unreadable referenced run with `schema_missing` — nothing is consumed by
  L2S3, no fallback to global or foreign content, the existing snapshot is left untouched,
  and the L2S3 `schema_instance_loader` receives only the already-governed compatible
  `SchemaInstance` values (never a raw L2S2 `get_schema()` read). Fully compatible runs
  keep building through the existing Workspace-specific L2S3 composition (AC-004).
- `status()` is derived read-only from authoritative state: `ready` is now a
  **production-completeness state** (REQ-004), reached only when ALL of the following hold:
  - recorded provenance exists, belongs to this Workspace, and its input fingerprint
    equals the fingerprint recomputed from the current Workspace inputs (identity,
    immutable Schema binding triple, membership, validated current Workspace Schema run
    identities);
  - provenance `build_status == "complete"`;
  - persisted `WikiManifest.build_status == "complete"` (partial/failed → `error`);
  - the authoritative Wiki source snapshot passes the existing WikiStore integrity checks;
  - the mandatory persistent vector index exists (C-008), its `source_fingerprint` equals
    the authoritative snapshot fingerprint (not stale), its index version/vector metadata
    are valid, vector dimensions are consistent with the declared metadata dimension, and
    every required Wiki Page and existing Entity has a persisted vector (AC-010).
- **Freshness gate (REQ-002/AC-005..AC-007):** the recomputed fingerprint is built from
  `validated_current_run_identities()` only. When a member Paper's current persisted run
  becomes missing, corrupt, unreadable or binding-incompatible even with `current.json`
  byte-identical, status() reports non-ready (`stale`) with the derived stable code
  `schema_input_invalid` — a previously valid Wiki ceases to be ready/current; a genuine
  input change keeps `input_fingerprint_mismatch` (AC-010 normal path). Restoring the
  compatible run identity returns the unchanged snapshot to `ready` (round-trip regression).
- Status vocabulary: `ready` (complete/current/valid), `stale` (validated-input fingerprint
  mismatch / no recorded fingerprint; `schema_input_invalid` when a contributing run fails
  binding/readability validation), `missing` (no snapshot / empty membership),
  `unsupported` (no-schema), `error` (corrupt/unreadable provenance, non-complete
  provenance, partial/failed manifest, or any mandatory-vector-index failure) with stable
  `error_code` values (AC-010).
- Readiness verification reuses the new L2S3 read-only `audit_vector_index_readonly`
  helper; any unexpected store-level failure during the audit is contained as a stable
  `error` status with `error_code` from the exception (`wiki_corrupt` fallback) instead of
  surfacing an implementation exception (REQ-006 boundary containment). `status()`/Grounding
  NEVER build indexes, embed documents, call LLMs or mutate Wiki artifacts (C-003).
- No-schema Workspaces report Base Wiki capability unsupported with no fallback (REQ-007).

### 1.6 Read-only Grounding (`layer3.grounding.WorkspaceGroundingService`)
- `ground(workspace_id)` returns the immutable, deterministic `GroundedWorkspace` snapshot:
  identity/status/revision, visible Papers with per-Paper asset availability (global L2S1
  readiness + Workspace Schema readiness derived from actually usable, binding-compatible
  runs), schema mode/binding, Schema coverage, Base Wiki status, capability summary and
  recommended actions (reported, never executed).
- `capabilities.wiki_read` is `true` ONLY when the Base Wiki derived status is `ready`
  (REQ-002/AC-008..AC-009); every partial/failed/stale/missing/corrupt/index-invalid/Schema-
  input-invalid state exposes `wiki_read=false`.
- **Consistency with Schema governance (REQ-003):** when a member Paper that contributed
  to a previously ready Wiki derives `schema_status=missing` with
  `schema_error_code=schema_binding_mismatch` or `schema_missing` (current run missing/
  corrupt/unreadable/tampered while `current.json` stays present), the SAME Grounding
  snapshot reports `base_wiki.status != ready` (with `schema_input_invalid`) and
  `capabilities.wiki_read=false` — Grounding never reports a stale Wiki as ready/current
  solely because the previous fingerprint still matches stale pointer identity data
  (AC-008/AC-009).
- Per-Paper `schema_status=ready` means the persisted Workspace current run is readable
  through the L2S2 integrity checks AND fully compatible with the immutable Workspace
  binding; non-ready Papers carry the explicit `schema_error_code`
  (`schema_missing` / `schema_binding_mismatch`) instead of being silently marked ready.
- Grounding only inspects: database control plane, global L2S1 assets, Workspace current
  Schema runs and Wiki artifacts/provenance. It never calls LLM/embedding providers,
  never builds indexes/extracts Schema/rebuilds a Wiki, never mutates any state (C-003).

### 1.7 Bound knowledge gateway (`layer3.knowledge.WorkspaceKnowledgeGateway`)
- Created with `workspace_id` (+ optional expected revision); upper-layer public methods
  (`list_papers`, `get_paper`, `search_evidence`, `read_evidence`, `get_schema_instance`,
  `get_schema_field`, `wiki_status`, `search_wiki`) never take a Workspace identifier.
- Every call revalidates existence → expected revision (`workspace_changed`) → active
  (`workspace_not_active`) → membership (`paper_not_member`) BEFORE any lower-layer call
  (REQ-007). Evidence reads delegate to the existing L2S1 public API (`search_bm25` /
  `read_blocks`).
- Wiki search validates the corrected readiness boundary first: a missing/stale/error Base
  Wiki raises `WikiMissingError` / `WikiStaleError` / `WikiCorruptError` and is NEVER
  silently served as current content. Once a contributing Schema run fails binding or
  readability validation, the derived `schema_input_invalid` stale status degrades the
  read path explicitly (`wiki_stale`), never serving non-current facts. Schema views derive
  readiness from binding-compatible persisted runs.

### 1.8 Read-only vector audit helper (`layer2.wiki.service.audit_vector_index_readonly`) — REQ-006 repair
- The provider-free L2S3 helper used by Layer3 readiness checks now imports
  `WikiStoreError` from the L2S3 Wiki store module; a `WikiStoreError`-derived failure
  while loading the authoritative Page/Entity snapshot is translated to a stable
  `WikiCorruptionError` ("vector audit cannot read the authoritative source snapshot") —
  never a `NameError` from an unavailable symbol (AC-012). Normal execution on a valid
  complete Wiki remains provider-free, side-effect-free and successful (AC-013).

### 1.9 Stable error codes
- Workspace/lifecycle: `workspace_not_found`, `workspace_not_active`, `workspace_changed`,
  `paper_not_found`, `paper_not_member`, `invalid_workspace_input`,
  `schema_binding_immutable`, `empty_membership`.
- Schema: `schema_disabled`, `schema_missing`, **`schema_binding_mismatch`** (single
  stable code for every binding incompatibility, AC-011).
- Wiki: `wiki_unsupported`, `wiki_missing`, `wiki_stale`, `wiki_corrupt`,
  `workspace_mismatch` (provenance), `snapshot_missing`, `no_recorded_fingerprint`,
  `input_fingerprint_mismatch`, **`schema_input_invalid`** (derived non-ready code when a
  member Schema run contributing to the recorded build fails binding/readability
  validation), `build_provenance_unreadable`, `build_provenance_incomplete`,
  `manifest_build_partial`, `manifest_build_failed`, `vector_index_missing`,
  `vector_index_stale`, `vector_index_incompatible`.

## 2. Not implemented in Layer3 Stage1 (explicit non-goals)

- Agentic Loop runtime, LangGraph, Multi-Agent orchestration, ResearchPlan execution,
  Thought/Action protocols (REQ-007 — verified by the static import-graph test
  `tests/integration/test_l3s1_no_agent_dependency.py`).
- Agentic Wiki self-building, paper discovery/download, Runtime/Executor/Supervisor
  configuration.
- Schema rebinding / schema migration between Workspaces (C-007 — always rejected).
- Base Wiki construction for no-schema Workspaces (reported as unsupported).
- Any `workspace_id` parameter added to existing global Paper / L2S1 public APIs (AC-014).
- Semantic Wiki search provider wiring and Pydantic snapshot freezing remain explicit
  non-blocking follow-ups (C-010); Stage1 search contract is lexical by default.

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
  layer2/wiki/service.py            # + audit_vector_index_readonly (read-only, provider-free, REQ-006)
alembic/versions/e4f5a6b7c8d9_create_workspace_tables.py

data/
  layer3/workspaces/<workspace_id>/
    schemas/<paper_id>/current.json, runs/...
    wiki/manifest.json, pages.jsonl, entities.jsonl, page_entity_links.jsonl,
         index/, provenance.json
```

## 4. Regression evidence (T-001/T-002/T-003 contract-v4 repairs + T-002 cross-boundary repair + T-004 revalidation)

Contract-v4 repair regressions (T-001: Base Wiki readiness; T-002: Schema binding; T-003:
preserved safety/isolation, 35 deterministic tests) and the v5 cross-boundary repair
regressions — 15 new deterministic tests (12 Wiki build/freshness governance, 2 Grounding
consistency, 1 read-only vector audit) — all executed again for this T-004 report:

- `tests/test_l3s1_wiki_workspace.py` (32 tests):
  - contract-v4 readiness: `test_partial_manifest_is_not_ready`, `test_failed_manifest_is_not_ready`,
    `test_non_complete_provenance_is_not_ready[partial|failed]`, `test_missing_vector_index_is_explicit_error`,
    `test_stale_vector_index_is_explicit_error`, `test_vector_index_incompatible_dimensions_is_error`,
    `test_vector_index_incomplete_coverage_is_error`, `test_complete_current_valid_wiki_is_ready_and_searchable`
    (AC-010), plus existing isolation/freshness/read-boundary regressions;
  - REQ-001 build gate (AC-001..AC-003): `test_build_rejects_pointer_schema_hash_mismatch`,
    `test_build_rejects_run_manifest_schema_hash_mismatch`, `test_build_rejects_run_manifest_schema_version_mismatch`,
    `test_build_rejects_pointer_to_missing_referenced_run`, `test_build_rejects_corrupt_referenced_run`,
    `test_build_rejects_invalid_run_even_after_prior_successful_build` (the L2S3 composition is proven
    never constructed for invalid inputs via a must-not-run factory);
  - REQ-001/AC-004: `test_compatible_runs_still_build_through_workspace_composition`;
  - REQ-002 freshness (AC-005..AC-007): `test_run_manifest_hash_tamper_invalidates_ready_wiki`,
    `test_run_manifest_version_mismatch_invalidates_ready_wiki`, `test_missing_current_run_invalidates_ready_wiki`,
    `test_corrupt_current_run_invalidates_ready_wiki` (all with `current.json` byte-identical, stable
    `schema_input_invalid` code, degraded `wiki_stale` reads), `test_restored_compatible_run_returns_the_same_wiki_to_ready`.
- `tests/test_l3s1_gateway_wiki_isolation.py`:
  `test_gateway_wiki_search_rejects_partial_manifest`, `test_gateway_wiki_search_rejects_missing_vector_index`,
  `test_gateway_wiki_search_rejects_stale_vector_index`, `test_gateway_wiki_search_rejects_incomplete_provenance`
  plus cross-Workspace isolation/no-schema regressions (AC-010/AC-014).
- `tests/test_l3s1_grounding_snapshot.py` (26 tests):
  six `test_grounding_wiki_read_false_for_*` + `test_grounding_wiki_read_true_only_for_complete_current_valid_wiki`
  (AC-010); pointer/run governance `test_grounding_non_ready_when_current_pointer_references_missing_run`,
  `test_grounding_non_ready_for_readable_run_with_version_mismatch`,
  `test_grounding_non_ready_for_pointer_hash_mismatch`, `test_grounding_ready_for_readable_binding_compatible_run`;
  REQ-003 consistency (AC-008/AC-009):
  `test_grounding_wiki_read_false_when_contributing_run_binding_mismatched`,
  `test_grounding_wiki_read_false_when_contributing_run_missing` — same-snapshot
  `base_wiki.status != ready` + `wiki_read=false` with `schema_input_invalid` and matching recommended actions.
- `tests/test_l3s1_schema_workspace.py` (19 tests): materialize binding rejection
  (`test_materialize_rejects_same_id_version_with_changed_hash`,
  `test_materialize_rejects_changed_schema_version_without_updating_state`,
  `test_materialize_unresolvable_current_definition_fails_explicitly`), stable-code read rejections
  (`test_pointer_identity_mismatch_rejected_with_stable_code`,
  `test_pointer_schema_hash_mismatch_rejected_with_stable_code`,
  `test_readable_run_with_version_mismatch_rejected_with_stable_code`,
  `test_readable_run_with_schema_id_mismatch_rejected_with_stable_code`,
  `test_readable_run_with_manifest_hash_mismatch_rejected_with_stable_code`),
  `test_pointer_to_missing_run_reports_schema_missing_not_ready`,
  `test_compatible_run_supports_every_read_surface`, isolation/no-fallback/API-independence regressions (AC-011/AC-014).
- `tests/test_l3s1_gateway_schema_isolation.py`: `test_gateway_rejects_binding_incompatible_pointer_with_stable_code`
  + isolation regressions (AC-011/AC-014).
- `tests/test_l3s1_grounding_readonly.py`: read-only guarantees re-executed unchanged (no new
  provider/mutation paths introduced by the repairs, C-003).
- `tests/test_l2s3_package_b_service.py` (L2S3 suite): **new** `test_readonly_vector_audit_wraps_wikistore_failures`
  — a `WikiStoreError`-derived failure during required Page/Entity loading surfaces a stable
  `WikiCorruptionError`, never a `NameError` (AC-012), and the same helper audits a valid complete
  index cleanly before the failure is injected (AC-013).

## 5. Acceptance criteria evidence (AC-001 .. AC-018)

Every criterion has passing automated-test evidence executed for this T-004 report. "E2E"
refers to `tests/integration/test_l3s1_*.py`.

| AC | Criterion summary | Evidence (test file / method) |
|----|-------------------|-------------------------------|
| AC-001 | binding-incompatible schema_hash (pointer or persisted run) → `build()` fails before L2S3 | `test_l3s1_wiki_workspace.py::test_build_rejects_pointer_schema_hash_mismatch`, `::test_build_rejects_run_manifest_schema_hash_mismatch` (stable `schema_binding_mismatch`, no manifest/provenance written, L2S3 factory must not run) |
| AC-002 | binding-incompatible schema_version → `build()` fails before L2S3 | `test_l3s1_wiki_workspace.py::test_build_rejects_run_manifest_schema_version_mismatch` |
| AC-003 | missing/corrupt/unreadable referenced run → `build()` fails explicitly, no fallback/foreign content | `test_l3s1_wiki_workspace.py::test_build_rejects_pointer_to_missing_referenced_run` (schema_missing), `::test_build_rejects_corrupt_referenced_run`, `::test_build_rejects_invalid_run_even_after_prior_successful_build` (previous snapshot untouched) |
| AC-004 | all member runs readable + fully compatible → build through existing L2S3 Workspace-specific composition | `test_l3s1_wiki_workspace.py::test_compatible_runs_still_build_through_workspace_composition` (+ recorded validated-identity provenance, status ready); E2E bound flow |
| AC-005 | run-manifest schema_hash/version changed after ready Wiki, current.json unchanged → status() != ready | `test_l3s1_wiki_workspace.py::test_run_manifest_hash_tamper_invalidates_ready_wiki`, `::test_run_manifest_version_mismatch_invalidates_ready_wiki` (stable `schema_input_invalid`) |
| AC-006 | current run missing/corrupt/unreadable after ready Wiki, current.json present → status() != ready | `test_l3s1_wiki_workspace.py::test_missing_current_run_invalidates_ready_wiki`, `::test_corrupt_current_run_invalidates_ready_wiki` |
| AC-007 | fingerprint/provenance identity derived only from validated compatible runs; pointer alone never authorizes | identical-`current.json` tamper tests above (identity derivation yields `None` for invalid runs: `WorkspaceSchemaService.current_run_identities` assertions), `::test_restored_compatible_run_returns_the_same_wiki_to_ready` |
| AC-008 | schema_status non-ready `schema_binding_mismatch` for contributing Paper → same snapshot wiki != ready, wiki_read=false | `test_l3s1_grounding_snapshot.py::test_grounding_wiki_read_false_when_contributing_run_binding_mismatched` |
| AC-009 | schema_status non-ready `schema_missing` → same snapshot wiki != ready, wiki_read=false | `test_l3s1_grounding_snapshot.py::test_grounding_wiki_read_false_when_contributing_run_missing` |
| AC-010 | contract-v4 readiness regressions (partial/failed provenance, partial/failed manifest, missing/stale/incompatible index, invalid dimensions, incomplete coverage, valid complete ready) remain green | complete `tests/test_l3s1_wiki_workspace.py` + six `test_grounding_wiki_read_false_for_*` + `::test_grounding_wiki_read_true_only_for_complete_current_valid_wiki` + gateway `test_gateway_wiki_search_rejects_*` |
| AC-011 | contract-v4 Schema binding regressions (version/hash mismatch materialization, pointer/run binding mismatch, missing run, stable `schema_binding_mismatch`, compatible reads/Grounding) remain green | complete `tests/test_l3s1_schema_workspace.py` + `tests/test_l3s1_gateway_schema_isolation.py` |
| AC-012 | read-only vector audit WikiStoreError failure → stable WikiCorruptionError, never NameError | `tests/test_l2s3_package_b_service.py::test_readonly_vector_audit_wraps_wikistore_failures` |
| AC-013 | direct normal execution of read-only vector audit on valid complete Wiki: provider-free, side-effect-free, successful | same test (clean `[]` audit before failure injection); `test_l3s1_wiki_workspace.py::test_complete_current_valid_wiki_is_ready_and_searchable` |
| AC-014 | isolation, no-schema no-fallback, read-only Grounding, revision/membership revalidation, archive, two-phase delete, global asset preservation, Layer1/L2 API independence proven | complete `tests/test_l3s1_*.py` + `tests/integration/test_l3s1_*.py` suites (lifecycle/isolation/readonly/no-agent modules; `test_l3s1_schema_workspace.py::test_l2s2_public_api_usable_independently_without_workspace_id`) |
| AC-015 | all targeted regressions for REQ-001..REQ-006 pass | targeted 6-file run: **97 passed** (see §6) |
| AC-016 | complete L3S1 unit suite + complete integration suite pass | **200 + 12 = 212 passed** (see §6) |
| AC-017 | affected L2S2 and L2S3 suites introduce no new failures | L2S2 **629 passed**, L2S3 **99 passed** — only documented pre-existing L2S1 baseline findings remain (see §7) |
| AC-018 | freeze wording only after AC-001..AC-017 pass with actual executed evidence | this document §6/§7 executed evidence + §8 freeze declaration |

## 6. Verification runs (T-004 full regression revalidation, contract v5)

All runs executed with the repository virtualenv (Python 3.11.15, pytest 9.1.1) on the
isolated migrated SQLite database (`alembic upgrade head` → `e4f5a6b7c8d9`, conftest
session fixture), offline Layer2 guard active (`TRANSIT_SCHOLAR_BLOCK_NETWORK=true`, also
shipped by the project `.env`), isolated per-test data roots
(`temp/pytest-runs/`), `-p no:cacheprovider`.

| Suite | Command | Result |
|-------|---------|--------|
| Targeted T-001/T-002/T-003 regressions (6 repaired unit files) | `pytest tests/test_l3s1_wiki_workspace.py tests/test_l3s1_gateway_wiki_isolation.py tests/test_l3s1_grounding_snapshot.py tests/test_l3s1_grounding_readonly.py tests/test_l3s1_schema_workspace.py tests/test_l3s1_gateway_schema_isolation.py` | **97 passed** |
| Layer3 Stage1 unit suite (complete) | `pytest tests/test_l3s1_*.py` (16 files) | **200 passed** (151 pre-repair + 35 contract-v4 repair + 14 cross-boundary repair) |
| Layer3 Stage1 integration suite (complete) | `pytest tests/integration/test_l3s1_*.py` (4 files) | **12 passed** |
| Layer3 Stage1 combined unit + integration | above two | **212 passed** |
| Database/Layer1 regression (Workspace tables) | `pytest tests/test_stage1_database.py tests/test_database_lifecycle.py tests/test_stage5_citation.py` | **59 passed** |
| L2S2 regression (Layer3 composes over L2S2) | `pytest tests/test_l2s2_*.py` | **629 passed** |
| L2S3 regression (Layer3 composes over L2S3; includes new REQ-006 audit test) | `pytest tests/test_l2s3_*.py` | **99 passed** |
| L2S1 regression (informational, pre-existing) | `pytest tests/test_l2s1_gate.py::test_gate_layer2_parse_code_imports_no_layer1_write_path tests/test_l2s1_parser.py::test_parser_no_llm_repair_or_voting_in_source tests/test_l2s1_safety.py::test_safety_no_scope_creep_source_scan` | **3 failed — pre-existing, reproduced unchanged, see §7** |

The complete lifecycle/isolation/no-fallback/read-only perimeter stays green inside the
L3S1 unit + integration suites: `test_l3s1_lifecycle_archive.py` (6), `test_l3s1_lifecycle_delete.py` (7),
`test_l3s1_lifecycle_membership_revocation.py` (5), `test_l3s1_lifecycle_revision_boundary.py` (6),
`test_l3s1_storage_governance.py` (28), `test_l3s1_workspace_domain.py` (13),
`test_l3s1_workspace_service.py` (21), `test_l3s1_gateway_schema_isolation.py` (7),
`test_l3s1_gateway_wiki_isolation.py` (8), `test_l3s1_grounding_readonly.py` (5),
`test_l3s1_knowledge_*.py` (17) — all passed, and the E2E isolation/governance/none-workspace/
no-agent-dependency integration modules (12) passed.

Static inspection (also asserted dynamically by
`tests/integration/test_l3s1_no_agent_dependency.py`): no Layer3 Stage1 source module
imports LangGraph, LangChain or any Agent-runtime package, and the Layer3 source tree
contains no ResearchPlan / Thought-Action / Agentic-Wiki / self-building vocabulary
(REQ-007). Grounding read-only guarantee is asserted by
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
  `Layer2 parse code imports forbidden module transit_scholar.metadata in
  src/transit_scholar/layer2/wiki/application.py`;
- `test_l2s1_parser.py::test_parser_no_llm_repair_or_voting_in_source` —
  `layer2/wiki/proposals.py contains forbidden construct '.generate('`;
- `test_l2s1_safety.py::test_safety_no_scope_creep_source_scan` —
  `layer2/wiki/application.py imports forbidden module transit_scholar.metadata`.

All three are L2S1-era source scans over `src/transit_scholar/layer2/**` that were not
updated when L2S3 was merged; they are legitimate L2S3 machinery. They are unrelated to
Layer3 Stage1, outside this task's allowed scope, and need a follow-up L2S3-scope update
of those three scan tests.

A second pre-existing harness quirk: `tests/test_layer1_realset.py` cannot be collected
by a plain `pytest` invocation because it imports a `validate_layer1_realset` script that
does not exist under `scripts/` (re-verified for this report:
`ModuleNotFoundError: No module named 'validate_layer1_realset'` during collection). This
also reproduces at the baseline and is unrelated to Layer3 Stage1.

Neither finding is introduced by the repairs: the L2S2 (629), L2S3 (99) and
database/Layer1 (59) suites required by AC-017/AC-014 are fully green at the repaired tree.

## 8. Formal freeze declaration (AC-018)

All Contract v5 acceptance criteria AC-001 through AC-017 have passing executed evidence,
recorded in §6/§7 with actual commands and counts before any freeze wording:

- AC-001..AC-003 (Wiki build consumes only binding-compatible, readable Workspace Schema
  runs; explicit `schema_binding_mismatch` / `schema_missing` before L2S3): 5 build-gate
  rejection tests + re-build regression in `test_l3s1_wiki_workspace.py`;
- AC-004 (compatible runs still build through the Workspace-specific L2S3 composition):
  `test_compatible_runs_still_build_through_workspace_composition`;
- AC-005..AC-007 (freshness derived from validated current runs; current.json alone never
  authorizes readiness; stable `schema_input_invalid`): 5 tamper/missing/corrupt/round-trip
  tests in `test_l3s1_wiki_workspace.py`;
- AC-008..AC-009 (Grounding never contradicts Schema governance in the same snapshot):
  2 new tests in `test_l3s1_grounding_snapshot.py`;
- AC-010 (contract-v4 wiki readiness preserved): complete `test_l3s1_wiki_workspace.py` +
  grounding/gateway wiki-readiness counterparts — green;
- AC-011 (contract-v4 Schema binding preserved): complete `test_l3s1_schema_workspace.py`
  + `test_l3s1_gateway_schema_isolation.py` — green;
- AC-012..AC-013 (read-only vector audit fails safe on WikiStore errors, provider-free on
  valid wikis): `tests/test_l2s3_package_b_service.py::test_readonly_vector_audit_wraps_wikistore_failures`
  — green;
- AC-014 (safety/isolation/no-fallback/read-only/revalidation/archive/two-phase-delete/
  global-asset preservation, no mandatory workspace_id): complete L3S1 unit **200 passed**
  + integration **12 passed** suites;
- AC-015: targeted T-001/T-002/T-003 regressions **97 passed**;
- AC-016: complete Layer3 Stage1 unit suite **200 passed**, integration suite **12 passed**
  (combined **212 passed**);
- AC-017: affected database/Layer1 **59 passed**, L2S2 **629 passed**, L2S3 **99 passed**
  — no new failures; the only failures anywhere are the three pre-existing L2S1 scan
  failures and the `test_layer1_realset.py` collection quirk, both reproducing unchanged on
  the pre-L3S1 baseline (§7);
- AC-018: this document records the actual executed counts above before any freeze wording.

**Layer3 Stage1 is therefore formally freeze-ready and is declared FROZEN as of this
report under contract v5 (REQ-001..REQ-008 / AC-001..AC-018).** The blanket "frozen" label
was removed after the cross-boundary repair was introduced and is re-asserted here only
because the complete v5 acceptance set now has executed passing evidence at the repaired
tree. Any subsequent change to the Layer3 Stage1 sources or to the covered regression
suites requires a re-run of the AC-015/AC-016/AC-017 verification gates before the freeze
label may be re-asserted.

## 9. Operational notes

- Workspace-owned Schema/Wiki heavy artifacts stay file-backed under the Workspace-specific
  root; the database holds only the control plane (C-004).
- Stale/partial/failed/index-invalid Wiki content is never silently served as current:
  reads raise `wiki_stale` / `wiki_corrupt` / `wiki_missing` until a rebuild records a
  fresh complete provenance; a contributing Schema run that fails binding/readability
  validation also degrades the previously ready Wiki to `stale` with `schema_input_invalid`.
- Binding-incompatible Schema content is never silently usable: all Layer3 boundaries
  raise `schema_binding_mismatch`; the Wiki build/status/fingerprint derivation shares the
  same governed boundary as Schema reads (one source of truth for "this Workspace Schema
  run is consumable").
- All integration tests run fully offline (fake parsers/LLM/embedding providers, isolated
  data roots, isolated SQLite databases), consistent with the Layer2 suite conventions.