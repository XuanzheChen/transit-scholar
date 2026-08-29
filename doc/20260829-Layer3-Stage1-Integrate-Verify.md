# Layer3 Stage1 Workspace Grounding — Integration & Verification Report (frozen under contract v7 after full revalidation)

> 文档性质：实现完成情况说明 / 回归验证报告（Implementation Report + Verification Evidence）
> 适用范围：Layer3 Stage1（Workspace 控制面、Workspace 归属的 Schema / Base Wiki、只读 Grounding、绑定知识网关）
> 版本：PSC contract v7（REQ-001..REQ-011 / AC-001..AC-018）。T-001/T-002 的 governed-snapshot
> 修复（一次捕获、同一持久化 run 同时提供 SchemaInstance 与 fingerprint/provenance 身份）在完整权威
> Schema 构建快照闭合中扩展为 `WorkspaceWikiSchemaBuildSnapshot`（T-001：绑定 schema 三元组 + 与被
> 绑定定义逐项校验的当前 `SchemaDefinition` + 全部捕获的校验 run），T-003 的 v5/v4 治理与就绪回归
> 保全之后，由 T-004 在构建快照闭合后按 v7 全量回归复验；
> 本文档的冻结声明仅在 AC-001..AC-017 全部以实际执行证据通过后作出（AC-018）。
> 本报告只描述**已实现并被测试证明**的 Layer3 Stage1 行为。Agentic Loop、LangGraph、Multi-Agent、
> ResearchPlan、Thought/Action、Agentic Wiki 自构建、论文发现/下载**均不属于** Layer3 Stage1（REQ-010，见 §2），
> 本报告不声称也不暗示任何此类功能。

---

## 1. What Layer3 Stage1 implements (implemented behavior only)

Layer3 Stage1 delivers a **Workspace Grounding layer plus a bound, Workspace-safe
knowledge access gateway**, built by composition over the existing Layer1/L2S1/L2S2/L2S3
public APIs. No Agent runtime exists anywhere in this stage.

### 1.1 Persistent Workspace control plane (REQ-010 boundaries)
- `transit_scholar.db.models.Workspace` + `WorkspacePaperMembership` (SQLite/MySQL via the
  existing SQLAlchemy database layer, Alembic migration `e4f5a6b7c8d9`).
- Workspace fields: stable `id`, `name`, lifecycle `status`
  (`active`/`archived`/`deleting`/`deleted`), `schema_mode` (`bound`/`none`), the immutable
  `schema_id`/`schema_version`/`schema_hash` triple for bound mode, monotonic `revision`,
  `created_at`/`updated_at`. DB CHECK constraints enforce the bound-vs-none invariant and
  the status vocabulary independently of the service layer.
- Paper inclusion is Workspace-to-Paper membership (`workspace_paper_memberships`, unique
  `(workspace_id, paper_id)` pair). The global `papers` table gains no workspace column;
  Layer1/L2 public APIs never take a mandatory `workspace_id` (REQ-010 boundary, AC-014).

### 1.2 Control-plane service (`layer3.workspace.WorkspaceService`)
- `create` (bound or none), `get`, `list_workspaces`, `add_paper` (idempotent),
  `remove_paper` (visibility revoked before derived-file cleanup), `archive` (idempotent,
  preserves memberships/files), `delete` (two-phase: durable `deleting` + membership
  revocation committed BEFORE destructive cleanup, then `deleted` tombstone; global
  Paper/L2S1 assets never touched), `rebind_schema` (always rejected — binding immutable
  in Stage1, REQ-010/AC-014 boundary).

### 1.3 Workspace-specific derived storage (`layer3.storage`)
- `workspace_layout` derives `<root>/<workspace_id>/schemas/` and `<root>/<workspace_id>/wiki/`
  from the persistent Workspace identity; `WorkspaceStorageLayout` injects those roots into
  the existing L2S2 `SchemaRunStorage` and L2S3 `WikiStore` (C-008).
- `compute_wiki_input_fingerprint` produces the deterministic Base Wiki input fingerprint
  (workspace id + schema triple + ordered membership + validated current Workspace Schema
  run identities). `current_schema_run_identities` remains a POINTER-LEVEL reader only —
  it is explicitly documented as never sufficient alone for Wiki freshness (REQ-005); the
  governed identity used by build/fingerprint/provenance comes from the frozen complete
  build snapshot captured by `WorkspaceSchemaService.capture_build_snapshot()`
  (T-001 / REQ-002), whose governed per-run capture validates every referenced run before
  its identity enters the fingerprint (REQ-005 / REQ-007).
- `BuildProvenance` (`provenance.json` inside the Workspace Wiki root) records the last
  successful build input fingerprint, build revision and timestamp — never a boolean
  readiness flag.

### 1.4 Workspace-owned Schema governance (`layer3.schema.WorkspaceSchemaService`) — with governed complete build snapshot (T-001)
- `materialize` (bound + active + member required) delegates to the L2S2 public
  `extract_schema` with the Workspace-specific storage injected; `storage`/`storage_root`
  injection by callers is rejected.
- **Immutable-binding enforcement (REQ-003):** the current `SchemaDefinition`
  resolved through the existing L2S2 loader is verified against the persisted Workspace
  binding triple (schema_id, schema_version, canonical schema_hash) BEFORE any L2S2
  extraction/persistence; a definition that differs in version or content hash (or cannot
  be resolved) fails explicitly with the stable `schema_binding_mismatch` error and writes
  nothing (AC-011).
- Read paths (`get_instance`, `get_field`, `current_run_identities`, per-Paper readiness)
  validate the **persisted run itself** through the existing L2S2 read-back integrity
  checks and then compare the recorded Schema identity — where the normal L2S2 current
  pointer/run metadata supplies `schema_hash`, that too — against the Workspace binding.
  `current.json` existence alone never makes a run usable (REQ-007).
- `validated_current_run_identities()` derives Wiki-fingerprint identities ONLY from
  validated compatible current runs: every member Paper's current run must pass the same
  `require_compatible_run()` governance boundary used by reads (readable through the
  normal L2S2 persistence integrity checks AND schema_id / schema_version / schema_hash
  fully matching the immutable Workspace binding, for both the current pointer and the
  persisted run manifest). A missing/corrupt/unreadable run, or a pointer/run-manifest
  that disagrees with the binding, yields `None` for that Paper and records the stable
  boundary code (`schema_missing` / `schema_binding_mismatch`) in the returned per-Paper
  error map — a current pointer alone never authorizes an identity (REQ-007/AC-011).
- **Governed per-run capture (REQ-001/REQ-005):**
  `capture_current_run(workspace_id, paper_id)` and the bulk
  `capture_current_runs(workspace_id, paper_ids)` return one frozen
  `ValidatedCurrentSchemaRun` per Paper carrying the exact `SchemaInstance` AND the exact
  run identity (run_id / schema_id / schema_version / schema_hash / current run status)
  resolved from the SAME validated persisted run: `current.json` is read once, the
  captured pointer A is validated against the immutable Workspace binding, the persisted
  run A is read explicitly by the captured run_id through the normal L2S2 persistence
  integrity + binding checks, and the `SchemaInstance` is read from that same run A
  (the v6 run-snapshot invariant, REQ-005). A bulk capture aborts on the first invalid
  member in deterministic sorted order and NEVER returns a partially governed set. A
  concurrent `current.json` switch A→B after capture cannot retroactively change the
  snapshot (C-003 / AC-008).
- **Complete authoritative build snapshot (T-001 / REQ-001/REQ-003/REQ-004,
  AC-001..AC-007):** `capture_build_snapshot(workspace_id, paper_ids=None)` freezes ONE
  `WorkspaceWikiSchemaBuildSnapshot` — the Workspace binding triple, the EXACT current
  `SchemaDefinition` used by the build, and every (or every requested) member's
  `ValidatedCurrentSchemaRun` keyed by deterministic sorted `paper_id` — BEFORE any L2S3
  Wiki build execution (C-001):
  1. the current `SchemaDefinition` for the bound schema_id is resolved through the
     existing L2S2 loader and its deterministic hash is derived with the SAME canonical
     hashing used at Workspace creation (`binding_for` / `compute_schema_hash`);
  2. exact schema_id / schema_version / schema_hash equality with the immutable binding
     is REQUIRED (`resolve_validated_definition`): same id/version but a changed content
     hash, a changed version, or an unresolvable definition all reject with the stable
     `schema_binding_mismatch` code BEFORE any run is captured and before L2S3 execution
     (REQ-003 / AC-002 / AC-003);
  3. every (or every requested) Paper's validated current run is captured through the
     governed per-run capture above — the v6 run-snapshot semantics stay intact (REQ-005);
  4. definition and runs are frozen into ONE immutable snapshot: definition and runs are
     mutually compatible with the same immutable Workspace binding by construction
     (REQ-004), and a post-capture definition/current change can never alter the frozen
     snapshot (C-004/C-006). `binding_identity` exposes exactly the snapshot's schema
     triple consumed by the input fingerprint and provenance (REQ-006 / AC-010).
  A missing/corrupt/unreadable run aborts the whole capture with its stable code
  (`schema_missing`, REQ-007/AC-011); a binding-incompatible pointer or persisted run
  aborts with `schema_binding_mismatch`. An incomplete snapshot is never returned, so no
  build can consume a partially governed set; capture itself is read-only, so a failed
  capture cannot disturb an existing Wiki/provenance (C-007).
- `validate_binding()` / `require_compatible_run()` / `paper_schema_readiness()` are the
  shared helpers used by materialization, reads, capture, Wiki build/status and
  Grounding, emitting the single stable `schema_binding_mismatch` code for every binding
  incompatibility (AC-011).
- `get_instance` / `get_field` surface `schema_missing` for missing/corrupt/unreadable
  runs; none-mode Workspaces surface `schema_disabled` with no fallback to global or
  foreign content (REQ-010 / C-009).

### 1.5 Workspace-owned Base Wiki governance (`layer3.wiki.WorkspaceWikiService`) — one frozen complete build snapshot for build and provenance (T-001/T-002)
- Build reuses the L2S3 `WorkspaceWikiBuildService` / `WikiStore` / `WikiService`
  composition via storage-root injection; `derive_workspace_context` reconstructs the L2S3
  `WorkspaceContext` from the persistent control plane (never the other way around).
- **Build gate / one frozen complete build snapshot (REQ-001/REQ-002/REQ-003/REQ-004,
  AC-001..AC-007):** BEFORE the L2S3 build consumes anything, `build()` captures ONE
  complete authoritative build snapshot through the SAME governed boundary used by Schema
  reads — `WorkspaceSchemaService.capture_build_snapshot(member_paper_ids)`: the current
  `SchemaDefinition` is resolved and proven against the immutable Workspace binding by
  exact schema_id / schema_version / deterministic schema_hash (REQ-003), then every
  member's current run is governed-captured (`current.json` read exactly ONCE; the
  captured pointer AND the referenced persisted run both pass the normal L2S2
  persistence-integrity checks and full binding compatibility; the exact `SchemaInstance`
  and the exact run identity come from the same persisted run, REQ-005). A definition
  that no longer matches the binding — same id/version with changed content hash, changed
  version, or unresolvable — fails with the stable `schema_binding_mismatch` code BEFORE
  any run capture and before L2S3 execution (AC-002 / AC-003); a missing/corrupt/
  unreadable run fails with `schema_missing` and a binding-incompatible pointer or
  persisted run with `schema_binding_mismatch` (AC-011) — nothing is consumed by L2S3, no
  fallback to global or foreign content, and the existing Wiki/provenance is left
  untouched (C-007). The Layer3-composed `WorkspaceWikiBuildService` receives ONLY the
  captured inputs: the captured `SchemaDefinition` through `schema_definition_loader` and
  the captured per-Paper `SchemaInstance` values through `schema_instance_loader`
  (AC-004 / AC-005) — the default current-definition/current-instance resolvers are never
  used for that build (REQ-002 / C-002; the global/default loaders remain only for
  independent Layer2 usage, C-010). Fully compatible runs build normally through the
  Workspace-specific L2S3 composition (AC-011).
- Both the L2S3 inputs AND the fingerprint/provenance identities are derived from the
  SAME frozen snapshot (AC-006/AC-009/AC-010 / C-001/C-002): the build never performs a
  second authoritative definition/current-run resolution, so it is impossible to build
  Wiki content from definition/run B while recording definition/run A in
  fingerprint/provenance, and `BuildProvenance.schema_runs` exactly matches the captured
  `ValidatedCurrentSchemaRun` identities (AC-009). A concurrent `current.json` change
  (A→B) or a globally resolvable definition change AFTER capture leaves this build's
  content and recorded identity at A (REQ-005/REQ-006, AC-007/AC-008); the next `status()`
  compares the governed current against the recorded identity and derives stale — never
  ready (AC-008/AC-011).
- `status()` is derived read-only from authoritative state: `ready` is now a
  **production-completeness state** (REQ-008), reached only when ALL of the following hold:
  - recorded provenance exists, belongs to this Workspace, and its input fingerprint
    equals the fingerprint recomputed from the current Workspace inputs (identity,
    immutable Schema binding triple, membership, governed captured current Workspace
    Schema run identities);
  - provenance `build_status == "complete"`;
  - persisted `WikiManifest.build_status == "complete"` (partial/failed → `error`);
  - the authoritative Wiki source snapshot passes the existing WikiStore integrity checks;
  - the mandatory persistent vector index exists (C-006), its `source_fingerprint` equals
    the authoritative snapshot fingerprint (not stale), its index version/vector metadata
    are valid, vector dimensions are consistent with the declared metadata dimension, and
    every required Wiki Page and existing Entity has a persisted vector (AC-012).
- **Freshness gate (REQ-005/REQ-007, AC-008/AC-011):** the recomputed fingerprint is built
  from the governed capture of the CURRENT runs (`capture_current_runs`) only. A
  concurrent `current.json` change A→B (B valid and binding-compatible) AFTER `build()`
  captured A leaves the finished build's provenance/fingerprint at A — the build MUST NOT
  silently switch provenance to B (deterministic race regression,
  `test_current_switch_after_capture_keeps_build_and_provenance_on_a`); the next
  `status()` compares the governed current B against the recorded A and derives non-ready
  `stale` (`input_fingerprint_mismatch`) — never `ready` (AC-008). A normal build with
  current remaining A records A and returns ready. When a member Paper's current persisted
  run becomes missing, corrupt, unreadable or binding-incompatible even with `current.json`
  byte-identical, status() reports non-ready (`stale`) with the derived stable code
  `schema_input_invalid` — a previously valid Wiki ceases to be ready/current (REQ-007 /
  AC-011); a genuine input change keeps `input_fingerprint_mismatch`. Restoring the
  compatible run identity returns the unchanged snapshot to `ready` (round-trip
  regression).
- Status vocabulary: `ready` (complete/current/valid), `stale` (governed-input fingerprint
  mismatch / no recorded fingerprint; `schema_input_invalid` when a contributing run fails
  binding/readability validation), `missing` (no snapshot / empty membership),
  `unsupported` (no-schema), `error` (corrupt/unreadable provenance, non-complete
  provenance, partial/failed manifest, or any mandatory-vector-index failure) with stable
  `error_code` values (AC-012).
- Readiness verification reuses the new L2S3 read-only `audit_vector_index_readonly`
  helper; any unexpected store-level failure during the audit is contained as a stable
  `error` status with `error_code` from the exception (`wiki_corrupt` fallback) instead of
  surfacing an implementation exception (REQ-009 boundary containment). `status()`/Grounding
  NEVER build indexes, embed documents, call LLMs or mutate Wiki artifacts (C-008).
- No-schema Workspaces report Base Wiki capability unsupported with no fallback (REQ-010).

### 1.6 Read-only Grounding (`layer3.grounding.WorkspaceGroundingService`)
- `ground(workspace_id)` returns the immutable, deterministic `GroundedWorkspace` snapshot:
  identity/status/revision, visible Papers with per-Paper asset availability (global L2S1
  readiness + Workspace Schema readiness derived from actually usable, binding-compatible
  runs), schema mode/binding, Schema coverage, Base Wiki status, capability summary and
  recommended actions (reported, never executed).
- `capabilities.wiki_read` is `true` ONLY when the Base Wiki derived status is `ready`
  (REQ-007/AC-011); every partial/failed/stale/missing/corrupt/index-invalid/Schema-
  input-invalid state exposes `wiki_read=false`.
- **Consistency with Schema governance (REQ-007):** when a member Paper that contributed
  to a previously ready Wiki derives `schema_status=missing` with
  `schema_error_code=schema_binding_mismatch` or `schema_missing` (current run missing/
  corrupt/unreadable/tampered while `current.json` stays present), the SAME Grounding
  snapshot reports `base_wiki.status != ready` (with `schema_input_invalid`) and
  `capabilities.wiki_read=false` — Grounding never reports a stale Wiki as ready/current
  solely because the previous fingerprint still matches stale pointer identity data
  (AC-011).
- Per-Paper `schema_status=ready` means the persisted Workspace current run is readable
  through the L2S2 integrity checks AND fully compatible with the immutable Workspace
  binding; non-ready Papers carry the explicit `schema_error_code`
  (`schema_missing` / `schema_binding_mismatch`) instead of being silently marked ready.
- Grounding only inspects: database control plane, global L2S1 assets, Workspace current
  Schema runs and Wiki artifacts/provenance. It never calls LLM/embedding providers,
  never builds indexes/extracts Schema/rebuilds a Wiki, never mutates any state (C-008).

### 1.7 Bound knowledge gateway (`layer3.knowledge.WorkspaceKnowledgeGateway`)
- Created with `workspace_id` (+ optional expected revision); upper-layer public methods
  (`list_papers`, `get_paper`, `search_evidence`, `read_evidence`, `get_schema_instance`,
  `get_schema_field`, `wiki_status`, `search_wiki`) never take a Workspace identifier.
- Every call revalidates existence → expected revision (`workspace_changed`) → active
  (`workspace_not_active`) → membership (`paper_not_member`) BEFORE any lower-layer call
  (REQ-010). Evidence reads delegate to the existing L2S1 public API (`search_bm25` /
  `read_blocks`).
- Wiki search validates the corrected readiness boundary first: a missing/stale/error Base
  Wiki raises `WikiMissingError` / `WikiStaleError` / `WikiCorruptError` and is NEVER
  silently served as current content. Once a contributing Schema run fails binding or
  readability validation, the derived `schema_input_invalid` stale status degrades the
  read path explicitly (`wiki_stale`), never serving non-current facts. Schema views derive
  readiness from binding-compatible persisted runs.

### 1.8 Read-only vector audit helper (`layer2.wiki.service.audit_vector_index_readonly`) — REQ-009 repair
- The provider-free L2S3 helper used by Layer3 readiness checks now imports
  `WikiStoreError` from the L2S3 Wiki store module; a `WikiStoreError`-derived failure
  while loading the authoritative Page/Entity snapshot is translated to a stable
  `WikiCorruptionError` ("vector audit cannot read the authoritative source snapshot") —
  never a `NameError` from an unavailable symbol (AC-013). Normal execution on a valid
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
  Thought/Action protocols (REQ-010 — verified by the static import-graph test
  `tests/integration/test_l3s1_no_agent_dependency.py`).
- Agentic Wiki self-building, paper discovery/download, Runtime/Executor/Supervisor
  configuration.
- Schema rebinding / schema migration between Workspaces (REQ-010/AC-014 boundary —
  always rejected).
- Base Wiki construction for no-schema Workspaces (reported as unsupported).
- Any `workspace_id` parameter added to existing global Paper / L2S1 public APIs (AC-014).
- Semantic Wiki search provider wiring and Pydantic snapshot freezing beyond the
  build-snapshot object remain explicit non-blocking follow-ups (C-011); Stage1 search
  contract is lexical by default.

## 3. Repository layout

```text
src/transit_scholar/
  db/models.py                      # Workspace + WorkspacePaperMembership ORM models
  layer3/
    workspace/                      # control-plane service, models, errors, schema binding
    storage/                        # layout, fingerprint, provenance
    schema/                         # Workspace-owned Schema governance (binding-enforced)
                                   #   + governed complete build snapshot
                                   #   (snapshot.py: WorkspaceWikiSchemaBuildSnapshot +
                                   #   ValidatedCurrentSchemaRun)
    wiki/                           # Workspace-owned Base Wiki governance (production-complete)
    grounding/                      # read-only Grounding service + snapshots
    knowledge/                      # bound WorkspaceKnowledgeGateway + L2S1 delegate
  layer2/wiki/service.py            # + audit_vector_index_readonly (read-only, provider-free, REQ-009)
alembic/versions/e4f5a6b7c8d9_create_workspace_tables.py

data/
  layer3/workspaces/<workspace_id>/
    schemas/<paper_id>/current.json, runs/...
    wiki/manifest.json, pages.jsonl, entities.jsonl, page_entity_links.jsonl,
         index/, provenance.json
```

## 4. Regression evidence (T-001/T-002 governed-snapshot repair + complete build snapshot closure + T-003 v5-preservation + T-004 v7 full revalidation)

Contract-v7 repair regressions: T-001/T-002 introduced the governed current-run build
snapshot (`ValidatedCurrentSchemaRun`, `capture_current_run(s)`) — 13 new deterministic
tests (10 capture/snapshot tests in `test_l3s1_schema_workspace.py` + 3 build/race tests
in `test_l3s1_wiki_workspace.py`); the complete authoritative build snapshot closure
(REQ-001/REQ-003/REQ-004) added `WorkspaceWikiSchemaBuildSnapshot` +
`capture_build_snapshot` + `resolve_validated_definition` with 12 new deterministic tests
(8 definition/snapshot tests in `test_l3s1_schema_workspace.py` + 4 build/race tests in
`test_l3s1_wiki_workspace.py`); T-003 preserved the v4/v5 governance/readiness
regression set. All of it was executed again for this T-004 report:

- `tests/test_l3s1_wiki_workspace.py` (39 tests):
  - T-001/REQ-001 (AC-001): `test_provenance_schema_runs_match_governed_snapshot_identities`
    — a successful build records provenance `schema_runs` exactly equal to the identity of
    the governed snapshot collection, and every snapshot binds the exact `SchemaInstance`
    with its exact run identity from the SAME persisted run;
  - T-002/REQ-002 (AC-004..AC-006): `test_build_uses_one_governed_capture_for_content_and_identity`
    — a recording spy proves `build()` performs exactly ONE `capture_current_runs` call and
    never consults the identity-only resolution; the L2S3 composition consumed exactly the
    captured snapshot instances; provenance `schema_runs` and the input fingerprint are
    recomputed from exactly the captured snapshot identities; the normal non-racing build
    stays ready (AC-008);
  - T-002/REQ-005 (AC-008): `test_current_switch_after_capture_keeps_build_and_provenance_on_a`
    — deterministic A→B race: current=A is captured, the pointer switches to a valid
    compatible run B between capture and finalization; the build consumes the captured A
    instance, provenance/fingerprint record A (never B), status() with current=B returns
    `stale` (`input_fingerprint_mismatch`) and never `ready`; a later normal rebuild over
    stable current B records B and returns `ready` (AC-008);
  - T-001/REQ-001/REQ-006 (AC-001/AC-004/AC-005/AC-006/AC-009/AC-010):
    `test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3` — the complete
    build snapshot (SchemaDefinition A + run A snapshots) is exactly the set the recorded
    build consumed: provenance `schema_runs` and the input fingerprint recomputed from the
    snapshot's binding triple and captured run identities exactly match the recorded build
    (REQ-006 / AC-009 / AC-010), `compute_schema_hash(snapshot.definition)` equals the
    binding hash (AC-001), and the Layer3-composed `WorkspaceWikiBuildService` accepts
    ONLY the captured inputs — the captured definition through
    `schema_definition_loader` and the captured run-A instances through
    `schema_instance_loader` (`load_build_inputs` == captured, AC-004/AC-005);
  - T-001/REQ-002/REQ-004 (AC-006/AC-007, scenario C):
    `test_definition_change_after_capture_cannot_alter_current_build` — a deterministic
    seam flips the globally resolvable definition A→B the moment the build's governed
    capture finishes, i.e. between capture and L2S3 input loading: the current build still
    consumes captured A (served definition/instances == captured A), the default
    current-definition resolver is proven re-invoked ZERO times after capture (exactly one
    call total), provenance/fingerprint record A and the build stays ready;
  - T-001/REQ-003/REQ-004 (AC-002/AC-003 + scenario B):
    `test_build_rejects_changed_definition_before_l2s3_and_preserves_snapshot` — after a
    successful build, a current `SchemaDefinition` with the same id/version but a changed
    deterministic content hash (or a changed version) makes `build()` fail with the stable
    `schema_binding_mismatch` code BEFORE L2S3 execution (must-not-run L2S3 factory) and
    leaves the existing Wiki manifest/provenance byte-identical and ready (C-007);
  - T-001/C-007: `test_failed_build_snapshot_capture_leaves_existing_wiki_and_provenance_untouched`
    — a failed complete-snapshot capture never mutates Wiki/provenance state;
  - contract-v4 readiness: `test_partial_manifest_is_not_ready`, `test_failed_manifest_is_not_ready`,
    `test_non_complete_provenance_is_not_ready[partial|failed]`, `test_missing_vector_index_is_explicit_error`,
    `test_stale_vector_index_is_explicit_error`, `test_vector_index_incompatible_dimensions_is_error`,
    `test_vector_index_incomplete_coverage_is_error`, `test_complete_current_valid_wiki_is_ready_and_searchable`
    (AC-012), plus existing isolation/freshness/read-boundary regressions;
  - REQ-001/REQ-007 build gate (AC-011): `test_build_rejects_pointer_schema_hash_mismatch`,
    `test_build_rejects_run_manifest_schema_hash_mismatch`, `test_build_rejects_run_manifest_schema_version_mismatch`,
    `test_build_rejects_pointer_to_missing_referenced_run`, `test_build_rejects_corrupt_referenced_run`,
    `test_build_rejects_invalid_run_even_after_prior_successful_build` (the L2S3 composition is proven
    never constructed for invalid inputs via a must-not-run factory);
  - REQ-007/AC-011: `test_compatible_runs_still_build_through_workspace_composition`;
  - REQ-007 freshness (AC-011): `test_run_manifest_hash_tamper_invalidates_ready_wiki`,
    `test_run_manifest_version_mismatch_invalidates_ready_wiki`, `test_missing_current_run_invalidates_ready_wiki`,
    `test_corrupt_current_run_invalidates_ready_wiki` (all with `current.json` byte-identical, stable
    `schema_input_invalid` code, degraded `wiki_stale` reads), `test_restored_compatible_run_returns_the_same_wiki_to_ready`.
- `tests/test_l3s1_gateway_wiki_isolation.py`:
  `test_gateway_wiki_search_rejects_partial_manifest`, `test_gateway_wiki_search_rejects_missing_vector_index`,
  `test_gateway_wiki_search_rejects_stale_vector_index`, `test_gateway_wiki_search_rejects_incomplete_provenance`
  plus cross-Workspace isolation/no-schema regressions (AC-012/AC-014).
- `tests/test_l3s1_grounding_snapshot.py` (26 tests):
  six `test_grounding_wiki_read_false_for_*` + `test_grounding_wiki_read_true_only_for_complete_current_valid_wiki`
  (AC-012); pointer/run governance `test_grounding_non_ready_when_current_pointer_references_missing_run`,
  `test_grounding_non_ready_for_readable_run_with_version_mismatch`,
  `test_grounding_non_ready_for_pointer_hash_mismatch`, `test_grounding_ready_for_readable_binding_compatible_run`;
  REQ-007 consistency (AC-011):
  `test_grounding_wiki_read_false_when_contributing_run_binding_mismatched`,
  `test_grounding_wiki_read_false_when_contributing_run_missing` — same-snapshot
  `base_wiki.status != ready` + `wiki_read=false` with `schema_input_invalid` and matching recommended actions.
- `tests/test_l3s1_schema_workspace.py` (37 tests):
  - T-001/REQ-001/REQ-005 per-run capture: `test_capture_current_run_returns_instance_and_identity_of_same_run`
    — snapshot.run_id is the captured pointer's run A, snapshot.instance IS run A's
    persisted instance, the identity triple equals the captured run's triple and the
    binding, and `snapshot.identity` derives from this same snapshot;
    `test_capture_current_runs_bulk_deterministic_order` — bulk capture derives instance
    AND identity from the same snapshot collection in deterministic sorted Paper order,
    explicit subsets and empty set behave correctly; `test_capture_snapshot_is_frozen` —
    the snapshot object is frozen after capture (identity/instance can never be
    rewritten); `test_capture_snapshot_isolated_from_concurrent_current_switch` —
    a valid current switch A→B AFTER capture never retroactively rewrites the snapshot
    (C-003); `test_capture_current_runs_aborts_on_first_invalid_run` — a bulk capture with
    one invalid member never returns a partial collection, it aborts in deterministic
    order with the stable code (REQ-007/AC-011); `test_capture_requires_bound_workspace_and_member`
    — `schema_disabled` / `paper_not_member` boundaries;
  - T-001/REQ-003/REQ-004 complete build snapshot (AC-001..AC-007):
    `test_capture_build_snapshot_captures_exact_definition_and_runs` — for binding S/V/HA
    the snapshot freezes the exact current `SchemaDefinition` A (with
    `compute_schema_hash(A) == HA`), the binding triple, and every member's governed
    `ValidatedCurrentSchemaRun` in deterministic sorted order, with
    `snapshot.binding_identity == binding` (AC-001 / REQ-006);
    `test_capture_build_snapshot_is_frozen` — binding triple/definition/run collection
    can never be rewritten after capture; `test_capture_build_snapshot_rejects_same_id_version_changed_hash`
    — same id/version but different deterministic content hash rejects with
    `schema_binding_mismatch` and the run capture is PROVEN not to execute (REQ-003 /
    AC-002); `test_capture_build_snapshot_rejects_changed_version` (AC-003);
    `test_capture_build_snapshot_rejects_unresolvable_definition` (REQ-003);
    `test_capture_build_snapshot_paper_subset_and_empty` — explicit sorted subset / empty
    set with the same validated definition; `test_capture_build_snapshot_aborts_on_invalid_run`
    — one invalid member aborts the whole snapshot (no partial governed set, REQ-001);
    `test_capture_build_snapshot_requires_bound_workspace_and_member` — `schema_disabled` /
    `paper_not_member` boundaries;
  - T-001/REQ-007 capture rejection (AC-011): `test_capture_rejects_pointer_binding_mismatch`,
    `test_capture_rejects_run_manifest_binding_mismatch` (stable `schema_binding_mismatch`),
    `test_capture_rejects_missing_referenced_run`, `test_capture_rejects_corrupt_referenced_run`
    (stable `schema_missing`) — capture never softens the v5 boundary;
  - materialize binding rejection (`test_materialize_rejects_same_id_version_with_changed_hash`,
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
  provider/mutation paths introduced by the repairs, C-008).
- `tests/test_l2s3_package_b_service.py` (L2S3 suite): `test_readonly_vector_audit_wraps_wikistore_failures`
  — a `WikiStoreError`-derived failure during required Page/Entity loading surfaces a stable
  `WikiCorruptionError`, never a `NameError` (AC-013), and the same helper audits a valid complete
  index cleanly before the failure is injected (AC-013).

## 5. Acceptance criteria evidence (AC-001 .. AC-018)

Every criterion has passing automated-test evidence executed for this T-004 report. "E2E"
refers to `tests/integration/test_l3s1_*.py`.

| AC | Criterion summary | Evidence (test file / method) |
|----|-------------------|-------------------------------|
| AC-001 | for binding S/V/HA, the captured build snapshot contains SchemaDefinition A and proves `compute_schema_hash(A) == HA` | `test_l3s1_schema_workspace.py::test_capture_build_snapshot_captures_exact_definition_and_runs` (snapshot.definition == current definition; `compute_schema_hash(definition) == binding.schema_hash`; binding triple + every run frozen) + `test_l3s1_wiki_workspace.py::test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3` (fingerprint identity matches BOTH captured definition and binding) |
| AC-002 | same id/version but changed definition content hash → `schema_binding_mismatch` before L2S3, no Wiki/provenance mutation | `test_l3s1_schema_workspace.py::test_capture_build_snapshot_rejects_same_id_version_changed_hash` (run capture proven not to run) + `test_l3s1_wiki_workspace.py::test_build_rejects_changed_definition_before_l2s3_and_preserves_snapshot` + `::test_failed_build_snapshot_capture_leaves_existing_wiki_and_provenance_untouched` (manifest/provenance byte-identical, still ready) |
| AC-003 | current definition version differs from the binding → build fails before L2S3 | `test_l3s1_schema_workspace.py::test_capture_build_snapshot_rejects_changed_version` (+ unresolvable variant `::test_capture_build_snapshot_rejects_unresolvable_definition`) + `test_l3s1_wiki_workspace.py::test_build_rejects_changed_definition_before_l2s3_and_preserves_snapshot` (changed-version half) |
| AC-004 | Layer3-composed `WorkspaceWikiBuildService` receives the captured definition through `schema_definition_loader` and never the default current-definition resolver | `test_l3s1_wiki_workspace.py::test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3` (`load_build_inputs` served the captured definition) + `::test_definition_change_after_capture_cannot_alter_current_build` (guarded default resolver invoked exactly once — the build's own capture — never re-resolved) |
| AC-005 | per-Paper loader receives only captured-snapshot SchemaInstances | `test_l3s1_wiki_workspace.py::test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3` (`instances_by_paper == captured snapshot instances`) + `::test_definition_change_after_capture_cannot_alter_current_build` (served instances == captured A) |
| AC-006 | deterministic proof: L2S3 consumes captured definition A + captured run A from the same snapshot | `test_l3s1_wiki_workspace.py::test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3` (recorded build == captured snapshot set) + `::test_definition_change_after_capture_cannot_alter_current_build` |
| AC-007 | globally resolvable definition changes A→B after capture but before L2S3 loading → current build still consumes captured A | `test_l3s1_wiki_workspace.py::test_definition_change_after_capture_cannot_alter_current_build` (deterministic `_PostCaptureDefinitionSwap` seam between capture and L2S3 input loading; swapped flag true; resolver call count == 1; served == captured A) |
| AC-008 | all v6 A→B current-run race regressions remain green | `test_l3s1_wiki_workspace.py::test_current_switch_after_capture_keeps_build_and_provenance_on_a` (provenance/fingerprint remain A; status with current=B is stale, never ready; later rebuild over B records B and returns ready) + `test_l3s1_schema_workspace.py::test_capture_snapshot_isolated_from_concurrent_current_switch` + `test_l3s1_wiki_workspace.py::test_build_uses_one_governed_capture_for_content_and_identity` |
| AC-009 | `BuildProvenance.schema_runs` exactly matches the captured run identities consumed by L2S3 | `test_l3s1_wiki_workspace.py::test_provenance_schema_runs_match_governed_snapshot_identities` + `::test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3` (`recorded.schema_runs == captured identities`) |
| AC-010 | schema identity in the Wiki fingerprint exactly matches the captured SchemaDefinition and Workspace binding | `test_l3s1_wiki_workspace.py::test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3` (`expected_fingerprint == outcome.fingerprint == recorded.input_fingerprint` from `snapshot.binding_identity`) + `test_l3s1_schema_workspace.py::test_capture_build_snapshot_captures_exact_definition_and_runs` (`binding_identity == binding`) |
| AC-011 | v5 build rejection, validated freshness, `schema_input_invalid`, and Grounding `wiki_read=false` regressions remain green | 6 v5-era build-gate rejection tests in `test_l3s1_wiki_workspace.py` + 4 capture rejection tests in `test_l3s1_schema_workspace.py` (stable `schema_binding_mismatch` / `schema_missing`, L2S3 must not run) + 5 invalidation/round-trip tests (`test_run_manifest_hash_tamper_invalidates_ready_wiki`, `::test_run_manifest_version_mismatch_invalidates_ready_wiki`, `::test_missing_current_run_invalidates_ready_wiki`, `::test_corrupt_current_run_invalidates_ready_wiki`, `::test_restored_compatible_run_returns_the_same_wiki_to_ready`) + `test_l3s1_grounding_snapshot.py::test_grounding_wiki_read_false_when_contributing_run_binding_mismatched`, `::test_grounding_wiki_read_false_when_contributing_run_missing` |
| AC-012 | v4/v5 production-complete Wiki readiness regressions (provenance/manifest/index/dimensions/coverage) remain green | complete `tests/test_l3s1_wiki_workspace.py` + six `test_grounding_wiki_read_false_for_*` + `::test_grounding_wiki_read_true_only_for_complete_current_valid_wiki` + gateway `test_gateway_wiki_search_rejects_*` |
| AC-013 | read-only vector audit WikiStoreError failure → stable WikiCorruptionError, never NameError; valid complete indexes audit provider-free | `tests/test_l2s3_package_b_service.py::test_readonly_vector_audit_wraps_wikistore_failures` (clean audit before failure injection); `test_l3s1_wiki_workspace.py::test_complete_current_valid_wiki_is_ready_and_searchable` |
| AC-014 | isolation, no-schema no-fallback, read-only Grounding, revision/membership revalidation, archive, two-phase delete, global asset preservation, Layer1/L2 API independence proven | complete `tests/test_l3s1_*.py` + `tests/integration/test_l3s1_*.py` suites (lifecycle/isolation/readonly/no-agent modules; `test_l3s1_schema_workspace.py::test_l2s2_public_api_usable_independently_without_workspace_id`); perimeter subset run **123 passed** (see §6) |
| AC-015 | all new complete-build-snapshot regressions pass | targeted runs: **13 passed** (v6 run-snapshot build regressions) + **12 passed** (complete build snapshot closure regressions) — see §6 |
| AC-016 | complete L3S1 unit suite + complete integration suite pass | **225 + 12 = 237 passed** (see §6) |
| AC-017 | affected L2S2 and L2S3 suites introduce no new failures | L2S2 **629 passed**, L2S3 **99 passed**, database/Layer1 **59 passed** — only documented pre-existing L2S1 baseline findings remain (see §7) |
| AC-018 | freeze wording only after AC-001..AC-017 pass with actual executed evidence | this document §6/§7 executed evidence + §8 freeze declaration |

## 6. Verification runs (T-004 full regression revalidation, contract v7)

All runs executed with the repository virtualenv (Python 3.11.15, pytest 9.1.1) on the
isolated migrated SQLite database (`alembic upgrade head` → `e4f5a6b7c8d9`, conftest
session fixture), offline Layer2 guard active (`TRANSIT_SCHOLAR_BLOCK_NETWORK=true`, also
shipped by the project `.env`), isolated per-test data roots
(`temp/pytest-runs/`), `-p no:cacheprovider`.

| Suite | Command | Result |
|-------|---------|--------|
| Targeted v6 TOCTOU/build-snapshot regressions (13 tests: 10 capture + 3 build/race) | `pytest tests/test_l3s1_schema_workspace.py::test_capture_current_run_returns_instance_and_identity_of_same_run tests/test_l3s1_schema_workspace.py::test_capture_current_runs_bulk_deterministic_order tests/test_l3s1_schema_workspace.py::test_capture_snapshot_is_frozen tests/test_l3s1_schema_workspace.py::test_capture_snapshot_isolated_from_concurrent_current_switch tests/test_l3s1_schema_workspace.py::test_capture_rejects_pointer_binding_mismatch tests/test_l3s1_schema_workspace.py::test_capture_rejects_run_manifest_binding_mismatch tests/test_l3s1_schema_workspace.py::test_capture_rejects_missing_referenced_run tests/test_l3s1_schema_workspace.py::test_capture_rejects_corrupt_referenced_run tests/test_l3s1_schema_workspace.py::test_capture_current_runs_aborts_on_first_invalid_run tests/test_l3s1_schema_workspace.py::test_capture_requires_bound_workspace_and_member tests/test_l3s1_wiki_workspace.py::test_provenance_schema_runs_match_governed_snapshot_identities tests/test_l3s1_wiki_workspace.py::test_build_uses_one_governed_capture_for_content_and_identity tests/test_l3s1_wiki_workspace.py::test_current_switch_after_capture_keeps_build_and_provenance_on_a` | **13 passed in 4.50s** (AC-015) |
| Targeted complete build-snapshot closure regressions (12 tests: 8 definition/snapshot + 4 build) | `pytest tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_captures_exact_definition_and_runs tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_is_frozen tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_rejects_same_id_version_changed_hash tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_rejects_changed_version tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_rejects_unresolvable_definition tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_paper_subset_and_empty tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_aborts_on_invalid_run tests/test_l3s1_schema_workspace.py::test_capture_build_snapshot_requires_bound_workspace_and_member tests/test_l3s1_wiki_workspace.py::test_definition_change_after_capture_cannot_alter_current_build tests/test_l3s1_wiki_workspace.py::test_build_rejects_changed_definition_before_l2s3_and_preserves_snapshot tests/test_l3s1_wiki_workspace.py::test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3 tests/test_l3s1_wiki_workspace.py::test_failed_build_snapshot_capture_leaves_existing_wiki_and_provenance_untouched` | **12 passed in 6.23s** (AC-015) |
| Targeted v4/v5 repair + snapshot regressions (6 repaired unit files) | `pytest tests/test_l3s1_wiki_workspace.py tests/test_l3s1_gateway_wiki_isolation.py tests/test_l3s1_grounding_snapshot.py tests/test_l3s1_grounding_readonly.py tests/test_l3s1_schema_workspace.py tests/test_l3s1_gateway_schema_isolation.py` | **122 passed** (109 v5-era + 13 v6 snapshot) |
| Layer3 Stage1 unit suite (complete) | `pytest tests/test_l3s1_*.py` (16 files) | **225 passed in 35.16s** (200 v5-era + 13 v6 snapshot + 12 complete build snapshot) |
| Layer3 Stage1 integration suite (complete) | `pytest tests/integration/test_l3s1_*.py` (4 files) | **12 passed in 4.18s** |
| Layer3 Stage1 combined unit + integration | above two | **237 passed** |
| Lifecycle/isolation/no-fallback/read-only perimeter subset | `pytest tests/test_l3s1_lifecycle_archive.py tests/test_l3s1_lifecycle_delete.py tests/test_l3s1_lifecycle_membership_revocation.py tests/test_l3s1_lifecycle_revision_boundary.py tests/test_l3s1_storage_governance.py tests/test_l3s1_workspace_domain.py tests/test_l3s1_workspace_service.py tests/test_l3s1_gateway_schema_isolation.py tests/test_l3s1_gateway_wiki_isolation.py tests/test_l3s1_grounding_readonly.py tests/test_l3s1_knowledge_bound_gateway.py tests/test_l3s1_knowledge_evidence_delegation.py tests/test_l3s1_knowledge_stale_revalidation.py` | **123 passed in 11.69s** (AC-014) |
| Database/Layer1 regression (Workspace tables) | `pytest tests/test_stage1_database.py tests/test_database_lifecycle.py tests/test_stage5_citation.py` | **59 passed in 6.27s** |
| L2S2 regression (Layer3 composes over L2S2) | `pytest tests/test_l2s2_*.py` (19 files) | **629 passed in 32.21s** |
| L2S3 regression (Layer3 composes over L2S3; includes REQ-009 audit test) | `pytest tests/test_l2s3_*.py` (14 files) | **99 passed in 8.98s** |
| L2S1 regression (informational, pre-existing) | `pytest tests/test_l2s1_gate.py::test_gate_layer2_parse_code_imports_no_layer1_write_path tests/test_l2s1_parser.py::test_parser_no_llm_repair_or_voting_in_source tests/test_l2s1_safety.py::test_safety_no_scope_creep_source_scan` | **3 failed — pre-existing, reproduced unchanged, see §7** |

The complete lifecycle/isolation/no-fallback/read-only perimeter stays green inside the
L3S1 unit + integration suites: `test_l3s1_lifecycle_archive.py` (6), `test_l3s1_lifecycle_delete.py` (7),
`test_l3s1_lifecycle_membership_revocation.py` (5), `test_l3s1_lifecycle_revision_boundary.py` (6),
`test_l3s1_storage_governance.py` (28), `test_l3s1_workspace_domain.py` (13),
`test_l3s1_workspace_service.py` (21), `test_l3s1_gateway_schema_isolation.py` (7),
`test_l3s1_gateway_wiki_isolation.py` (8), `test_l3s1_grounding_readonly.py` (5),
`test_l3s1_knowledge_*.py` (17) — all passed (123), and the E2E isolation/governance/none-workspace/
no-agent-dependency integration modules (12) passed.

Static inspection (also asserted dynamically by
`tests/integration/test_l3s1_no_agent_dependency.py`): no Layer3 Stage1 source module
imports LangGraph, LangChain or any Agent-runtime package, and the Layer3 source tree
contains no ResearchPlan / Thought-Action / Agentic-Wiki / self-building vocabulary
(REQ-010). Grounding read-only guarantee is asserted by
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
`ImportError while importing test module 'tests/test_layer1_realset.py'` /
`from validate_layer1_realset import`). This
also reproduces at the baseline and is unrelated to Layer3 Stage1.

Neither finding is introduced by the repairs: the L2S2 (629), L2S3 (99) and
database/Layer1 (59) suites required by AC-017/AC-014 are fully green at the repaired tree.

## 8. Formal freeze declaration (AC-018)

All Contract v7 acceptance criteria AC-001 through AC-017 have passing executed evidence,
recorded in §6/§7 with actual commands and counts before any freeze wording:

- AC-001 (captured build snapshot contains SchemaDefinition A with
  `compute_schema_hash(A) == HA`): `test_capture_build_snapshot_captures_exact_definition_and_runs`
  + `test_capture_build_snapshot_is_frozen` + `test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3`
  in `test_l3s1_schema_workspace.py` / `test_l3s1_wiki_workspace.py`;
- AC-002/AC-003 (same id/version changed content hash, changed version, or unresolvable
  definition → stable `schema_binding_mismatch` before any run capture and before L2S3,
  existing Wiki/provenance untouched): `test_capture_build_snapshot_rejects_same_id_version_changed_hash`
  (run capture proven not to run), `::test_capture_build_snapshot_rejects_changed_version`,
  `::test_capture_build_snapshot_rejects_unresolvable_definition`,
  `test_build_rejects_changed_definition_before_l2s3_and_preserves_snapshot`
  (must-not-run L2S3 factory, manifest/provenance byte-identical, still ready),
  `test_failed_build_snapshot_capture_leaves_existing_wiki_and_provenance_untouched` (C-007);
- AC-004/AC-005 (Layer3-composed L2S3 receives ONLY the captured definition via
  `schema_definition_loader` and captured instances via `schema_instance_loader`):
  `test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3`
  (`load_build_inputs` == captured set) + `test_definition_change_after_capture_cannot_alter_current_build`
  (default resolver invoked exactly once, never re-resolved for that build);
- AC-006/AC-007 (deterministic definition A→B change after capture, before L2S3
  loading, cannot alter the current build; L2S3 consumes captured A):
  `test_definition_change_after_capture_cannot_alter_current_build`
  (deterministic `_PostCaptureDefinitionSwap` seam; served == captured A; provenance/
  fingerprint record A);
- AC-008 (v6 A→B current-run race regressions green): `test_current_switch_after_capture_keeps_build_and_provenance_on_a`
  (build consumes A, provenance/fingerprint record A, status with current=B is stale and
  never ready, later rebuild over B records B and returns ready) +
  `test_capture_snapshot_isolated_from_concurrent_current_switch`;
- AC-009 (BuildProvenance.schema_runs exactly matches the captured governed snapshot
  identities): `test_provenance_schema_runs_match_governed_snapshot_identities`
  + `test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3`;
- AC-010 (fingerprint identity matches BOTH the captured SchemaDefinition and the
  Workspace binding): `test_complete_build_snapshot_matches_recorded_build_and_feeds_l2s3`
  (`snapshot.binding_identity` drives the fingerprint) +
  `test_capture_build_snapshot_captures_exact_definition_and_runs`;
- AC-011 (v5 build-rejection, validated-freshness, `schema_input_invalid` and Grounding
  `wiki_read=false` regressions remain green): 6 v5-era build-gate rejection/re-build
  tests in `test_l3s1_wiki_workspace.py` + 4 capture rejection tests in
  `test_l3s1_schema_workspace.py` + 5 tamper/missing/corrupt/round-trip tests in
  `test_l3s1_wiki_workspace.py` + 2 same-snapshot tests in `test_l3s1_grounding_snapshot.py`
  — all green;
- AC-012 (v4/v5 production-complete Wiki readiness preserved): complete `test_l3s1_wiki_workspace.py` +
  grounding/gateway wiki-readiness counterparts — green;
- AC-013 (read-only vector audit fails safe on WikiStore errors, provider-free on
  valid wikis): `tests/test_l2s3_package_b_service.py::test_readonly_vector_audit_wraps_wikistore_failures`
  — green;
- AC-014 (safety/isolation/no-fallback/read-only/revalidation/archive/two-phase-delete/
  global-asset preservation, no mandatory workspace_id): complete L3S1 unit **225 passed**
  + integration **12 passed** suites; perimeter subset **123 passed**;
- AC-015: targeted build-snapshot regressions **13 + 12 = 25 passed** (v6 run-snapshot
  set + complete build snapshot closure set);
- AC-016: complete Layer3 Stage1 unit suite **225 passed**, integration suite **12 passed**
  (combined **237 passed**);
- AC-017: affected database/Layer1 **59 passed**, L2S2 **629 passed**, L2S3 **99 passed**
  — no new failures; the only failures anywhere are the three pre-existing L2S1 scan
  failures and the `test_layer1_realset.py` collection quirk, both reproducing unchanged on
  the pre-L3S1 baseline (§7);
- AC-018: this document records the actual executed counts above before any freeze wording.

**Layer3 Stage1 is therefore formally freeze-ready and is declared FROZEN as of this
report under contract v7 (REQ-001..REQ-011 / AC-001..AC-018), re-validated after the
complete authoritative Schema build snapshot closure (REQ-001..REQ-004/REQ-006).** The
blanket "frozen" label was removed when the governed-snapshot repair was introduced and is
re-asserted here only because the complete v7 acceptance set now has executed passing
evidence at the repaired tree. Any subsequent change to the Layer3 Stage1 sources or to
the covered regression suites requires a re-run of the AC-015/AC-016/AC-017 verification
gates before the freeze label may be re-asserted.

## 9. Operational notes

- Workspace-owned Schema/Wiki heavy artifacts stay file-backed under the Workspace-specific
  root; the database holds only the control plane (C-008).
- Stale/partial/failed/index-invalid Wiki content is never silently served as current:
  reads raise `wiki_stale` / `wiki_corrupt` / `wiki_missing` until a rebuild records a
  fresh complete provenance; a contributing Schema run that fails binding/readability
  validation also degrades the previously ready Wiki to `stale` with `schema_input_invalid`.
- Binding-incompatible Schema content is never silently usable: all Layer3 boundaries
  raise `schema_binding_mismatch`; the Wiki build/status/fingerprint derivation shares the
  same governed boundary as Schema reads (one source of truth for "this Workspace Schema
  run is consumable"), and the definition used by a build is proven against the same
  immutable binding by deterministic schema_hash before L2S3 runs.
- All integration tests run fully offline (fake parsers/LLM/embedding providers, isolated
  data roots, isolated SQLite databases), consistent with the Layer2 suite conventions.