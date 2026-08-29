"""Governed current-run build snapshot (T-001 / REQ-001).

One validated current persisted Workspace Schema run, captured as an
immutable snapshot that carries BOTH the exact ``SchemaInstance`` and the
exact run identity (run_id, Schema identity triple, current run status)
resolved from the SAME persisted run (REQ-001 / AC-001).

``WorkspaceSchemaService.capture_current_run`` /
``capture_current_runs`` implement the governed capture semantics:

1. read the Paper's ``current.json`` once;
2. capture the pointer's run_id (A);
3. validate the captured pointer A against the immutable Workspace binding;
4. read the persisted run A explicitly by the captured run_id;
5. validate L2S2 persistence integrity and binding compatibility of run A;
6. return the SchemaInstance read from that same run A together with
   identity A.

The snapshot therefore makes it impossible to build Wiki content from run A
while recording run B in fingerprint/provenance: content and identity are
derived from one captured run, and a concurrent ``current.json`` change after
capture can never retroactively change this snapshot's identity (AC-001 /
AC-002 / C-001..C-003). Invalid runs (missing/corrupt/unreadable pointer or
run, or a pointer/persisted run whose Schema identity disagrees with the
Workspace binding) keep failing with the exact stable codes of every other
governed read surface (``schema_missing`` / ``schema_binding_mismatch``,
REQ-004 / AC-009) — capturing never softens or weakens the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer2.schema_extraction.models import SchemaInstance


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


__all__ = ["ValidatedCurrentSchemaRun"]