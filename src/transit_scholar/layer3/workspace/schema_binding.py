"""Schema binding semantics for Layer3 Stage1 (REQ-003).

A Workspace is created in exactly one of two schema modes:

- ``bound`` — the Workspace is bound to a concrete SchemaDefinition identity;
  ``schema_id``, ``schema_version`` and a deterministic ``schema_hash`` over
  the exact SchemaDefinition structure are persisted at creation time;
- ``none`` — no Schema at all; all three binding fields are absent.

The deterministic hash reuses the L2S2 canonical hashing implementation
(``transit_scholar.layer2.schema_extraction.hashing.compute_schema_hash``) so
Layer3 never reimplements structure hashing and two loads of identical
SchemaDefinition content always produce the same hash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import InvalidWorkspaceInputError
from .models import SchemaBinding

if TYPE_CHECKING:  # pragma: no cover - typing only
    from transit_scholar.layer2.schema_extraction.models import SchemaDefinition

SCHEMA_MODE_BOUND = "bound"
SCHEMA_MODE_NONE = "none"
SCHEMA_MODES: frozenset[str] = frozenset({SCHEMA_MODE_BOUND, SCHEMA_MODE_NONE})


def compute_schema_hash(schema_definition: "SchemaDefinition") -> str:
    """Return the deterministic content hash of a ``SchemaDefinition``.

    Delegates to the existing L2S2 canonical hashing function; the payload is
    the canonical JSON dump of the definition (no paths, timestamps or
    machine information), so identical structure always hashes identically.
    """
    from transit_scholar.layer2.schema_extraction.hashing import (  # noqa: PLC0415
        compute_schema_hash as l2s2_compute_schema_hash,
    )

    return l2s2_compute_schema_hash(schema_definition)


def binding_for(schema_definition: "SchemaDefinition") -> SchemaBinding:
    """Validate a ``SchemaDefinition`` and derive its immutable binding triple.

    Raises ``InvalidWorkspaceInputError`` when the definition is missing or
    structurally invalid, so a bound Workspace can never be created with an
    incomplete binding.
    """
    if schema_definition is None:
        raise InvalidWorkspaceInputError(
            "bound schema mode requires a concrete SchemaDefinition"
        )
    schema_id = getattr(schema_definition, "schema_id", None)
    version = getattr(schema_definition, "version", None)
    if not schema_id or not version:
        raise InvalidWorkspaceInputError(
            "bound schema mode requires a SchemaDefinition with schema_id and version"
        )
    try:
        schema_hash = compute_schema_hash(schema_definition)
    except Exception as exc:  # noqa: BLE001 - surface as explicit invalid input
        raise InvalidWorkspaceInputError(
            f"could not compute deterministic schema_hash: {type(exc).__name__}: {exc}"
        ) from exc
    return SchemaBinding(
        schema_id=schema_id,
        schema_version=version,
        schema_hash=schema_hash,
    )


__all__ = [
    "SCHEMA_MODE_BOUND",
    "SCHEMA_MODE_NONE",
    "SCHEMA_MODES",
    "compute_schema_hash",
    "binding_for",
]