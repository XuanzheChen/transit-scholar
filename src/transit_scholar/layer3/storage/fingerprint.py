"""Deterministic Base Wiki input fingerprinting (REQ-007 / AC-010..11).

The fingerprint reflects every authoritative input a Base Wiki build depends
on:

- the Workspace identity;
- the bound Schema identity/version and deterministic structure hash;
- the current Paper membership (deterministically ordered);
- the current Workspace-owned Schema run identity for every participating
  Paper (current pointer run id / schema hash / status).

The same Workspace inputs always produce the same fingerprint; any change to
membership or to any current Workspace Schema run identity produces a
different fingerprint, so Grounding can derive "stale" vs "current" purely
from the recorded vs recomputed fingerprint — no persisted boolean readiness
flag (REQ-007).
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer2.schema_extraction.persistence import (
        SchemaRunStorage,
    )

#: JSON separators used for the canonical payload so hashes are byte-stable.
_JSON_SEPARATORS = (",", ":")


def current_schema_run_identities(
    storage: "SchemaRunStorage",
    paper_ids: list[str] | tuple[str, ...],
) -> dict[str, dict[str, str] | None]:
    """Per-Paper current Workspace Schema run identity, or ``None`` when absent.

    Reads the current pointer of the *Workspace-specific* ``SchemaRunStorage``
    (never a global or another Workspace's storage). A Paper without a current
    run contributes ``None``, which still participates deterministically in
    the fingerprint.
    """
    from transit_scholar.layer2.schema_extraction.persistence import (  # noqa: PLC0415
        SchemaCurrentNotFoundError,
    )

    identities: dict[str, dict[str, str] | None] = {}
    for paper_id in sorted(paper_ids):
        try:
            pointer = storage.read_current(paper_id)
        except SchemaCurrentNotFoundError:
            identities[paper_id] = None
        else:
            identities[paper_id] = {
                "run_id": pointer.run_id,
                "schema_hash": pointer.schema_hash,
                "status": pointer.status,
            }
    return identities


def compute_wiki_input_fingerprint(
    *,
    workspace_id: str,
    schema_id: str,
    schema_version: str,
    schema_hash: str,
    paper_ids: list[str] | tuple[str, ...],
    schema_run_identities: dict[str, dict[str, str] | None],
) -> str:
    """SHA-256 fingerprint over the canonical build-input payload.

    Membership and per-Paper run identities are sorted into a deterministic
    order, and the whole payload is JSON-serialized with sorted keys, so the
    hash is stable for identical inputs and changes exactly when an
    authoritative input changes.
    """
    canonical_payload = json.dumps(
        {
            "workspace_id": workspace_id,
            "schema": {
                "id": schema_id,
                "version": schema_version,
                "hash": schema_hash,
            },
            "membership": sorted(paper_ids),
            "schema_runs": {
                paper_id: identity
                for paper_id, identity in sorted(schema_run_identities.items())
            },
        },
        sort_keys=True,
        separators=_JSON_SEPARATORS,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


__all__ = ["compute_wiki_input_fingerprint", "current_schema_run_identities"]