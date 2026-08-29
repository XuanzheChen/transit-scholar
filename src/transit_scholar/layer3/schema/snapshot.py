"""Governed Workspace Wiki Schema build snapshots (T-001 / REQ-001).

Two frozen snapshot levels, both captured by ``WorkspaceSchemaService``
before any L2S3 Wiki build execution:

- ``ValidatedCurrentSchemaRun`` — ONE validated current persisted Workspace
  Schema run, carrying BOTH the exact ``SchemaInstance`` and the exact run
  identity (run_id, Schema identity triple, current run status) resolved
  from the SAME persisted run (REQ-001 / AC-001);
- ``WorkspaceWikiSchemaBuildSnapshot`` — ONE complete authoritative build
  snapshot: the Workspace binding triple, the exact binding-compatible
  ``SchemaDefinition`` used by the build, and every captured validated
  current run keyed by deterministic ``paper_id`` order (REQ-001 / AC-001 /
  AC-006).

``WorkspaceSchemaService.capture_current_run`` /
``capture_current_runs`` implement the governed per-run capture semantics:

1. read the Paper's ``current.json`` once;
2. capture the pointer's run_id (A);
3. validate the captured pointer A against the immutable Workspace binding;
4. read the persisted run A explicitly by the captured run_id;
5. validate L2S2 persistence integrity and binding compatibility of run A;
6. return the SchemaInstance read from that same run A together with
   identity A.

``WorkspaceSchemaService.capture_build_snapshot`` implements the complete
build-snapshot capture semantics on top of the per-run capture:

1. resolve the current ``SchemaDefinition`` for the bound schema_id;
2. compute its deterministic hash with the same canonical hashing used at
   Workspace creation;
3. require exact schema_id / schema_version / schema_hash equality with the
   immutable Workspace binding (``schema_binding_mismatch`` otherwise);
4. freeze that exact definition together with the binding triple;
5. capture every (or requested) Paper's validated current run through the
   governed per-run capture above.

The build snapshot therefore makes it impossible to build Wiki content from
run A while recording run B in fingerprint/provenance, and impossible to
build with a definition that is not byte-identical in identity to the
Workspace binding: content, identity and definition are derived from one
captured, mutually compatible set, and a concurrent ``current.json`` or
plugin-definition change after capture can never retroactively change this
snapshot (AC-001 / AC-002 / C-001..C-004). Invalid runs
(missing/corrupt/unreadable pointer or run, or a pointer/persisted run whose
Schema identity disagrees with the Workspace binding) and binding-incompatible
definitions (same id with changed version or content hash, or an
unresolvable definition) keep failing with the exact stable codes of every
other governed read surface (``schema_missing`` / ``schema_binding_mismatch``,
REQ-003 / REQ-004 / AC-009) — capturing never softens or weakens the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer2.schema_extraction.models import (
    SchemaDefinition,
    SchemaInstance,
)


class ValidatedCurrentSchemaRun(BaseModel):
    """One governed current persisted run captured with instance + identity.

    Frozen after capture: every field is resolved together from a single
    validated persisted run (REQ-001) and may not be mutated afterwards, so
    downstream consumers (L2S3 input composition, fingerprint/provenance
    recording) can rely on the same captured object for the whole build.

    ``schema_id`` / ``schema_version`` / ``schema_hash`` are the captured
    persisted run's identity triple; because capture validates BOTH the
    captured ``current.json`` pointer and the persisted run manifest against
    the same immutable Workspace binding, all three identities agree.
    ``status`` is the current run status recorded by the captured pointer.
    """

    model_config = ConfigDict(frozen=True)

    paper_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    instance: SchemaInstance
    schema_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    status: str = Field(min_length=1)

    @property
    def identity(self) -> dict[str, str]:
        """The provenance/fingerprint identity of the captured run (AC-002).

        Exactly the identity shape Wiki build provenance and the input
        fingerprint consume (``run_id`` / ``schema_hash`` / ``status``),
        derived directly from this snapshot's captured pointer + persisted
        run — never from a second current-run resolution.
        """
        return {
            "run_id": self.run_id,
            "schema_hash": self.schema_hash,
            "status": self.status,
        }


class WorkspaceWikiSchemaBuildSnapshot(BaseModel):
    """One frozen complete authoritative build snapshot (T-001 / REQ-001).

    Captured by ``WorkspaceSchemaService.capture_build_snapshot`` before any
    L2S3 Wiki build execution (C-001). It holds the immutable Workspace
    binding triple, the EXACT ``SchemaDefinition`` used by the build (proven
    to match the binding by schema_id / schema_version / deterministic
    schema_hash, REQ-003 / AC-001) and every captured validated current run
    (``ValidatedCurrentSchemaRun`` — the v6 run snapshot semantics, REQ-005)
    keyed by deterministic ``paper_id`` order.

    Frozen after capture: the binding triple, the definition and every run
    snapshot are resolved together from one mutually compatible set and may
    not be mutated afterwards (AC-001 / AC-006 / C-001..C-004). Because
    capture validates the definition against the SAME binding every run is
    validated against, definition and runs are always mutually compatible
    with one immutable Workspace binding (REQ-004).
    """

    model_config = ConfigDict(frozen=True)

    workspace_id: str = Field(min_length=1)
    schema_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    definition: SchemaDefinition
    runs_by_paper: dict[str, ValidatedCurrentSchemaRun]

    @property
    def binding_identity(self) -> dict[str, str]:
        """The Workspace binding identity consumed by fingerprint/provenance
        (REQ-006): exactly schema_id / schema_version / schema_hash, matching
        BOTH the captured ``definition`` and the Workspace binding.
        """
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
        }


__all__ = [
    "ValidatedCurrentSchemaRun",
    "WorkspaceWikiSchemaBuildSnapshot",
]