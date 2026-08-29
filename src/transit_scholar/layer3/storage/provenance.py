"""Workspace-owned build provenance (Base Wiki input fingerprints).

REQ-007/AC-010..11 require freshness to be derived from a recorded
deterministic input fingerprint rather than a persisted boolean readiness
flag. Each successful Base Wiki build records its input fingerprint plus the
included Schema run identities, a build revision and a build timestamp in
``<wiki_root>/provenance.json`` — inside the Workspace's own Wiki storage
boundary, so provenance is Workspace-owned, travels with the Wiki, and is
removed with it. It is the only durable build provenance record; Grounding
never persists ``wiki_ready``/``wiki_stale`` booleans anywhere.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

#: Provenance file name inside a Workspace's Wiki root.
PROVENANCE_FILE = "provenance.json"

#: Provenance model version; bumped only when the record schema changes.
PROVENANCE_VERSION = 1


class BuildProvenanceError(ValueError):
    """The persisted provenance record is missing required data or is corrupt."""


class BuildProvenance(BaseModel):
    """Durable provenance of one successful Base Wiki build (REQ-007).

    ``input_fingerprint`` is the deterministic fingerprint over Workspace
    identity, Schema binding, membership, and current Workspace Schema run
    identities; a later recomputation that differs means the Wiki inputs have
    changed and the Wiki is stale. ``build_revision`` is a monotonic per-Wiki
    build counter; ``built_at`` is the UTC build timestamp.
    """

    artifact_type: Literal["base_wiki"] = "base_wiki"
    provenance_version: int = PROVENANCE_VERSION
    workspace_id: str = Field(min_length=1)
    input_fingerprint: str = Field(min_length=1)
    schema_runs: dict[str, Any] = Field(default_factory=dict)
    build_status: str = Field(default="partial")
    build_revision: int = Field(default=1, ge=1)
    built_at: str = Field(min_length=1)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_raw(wiki_root: Path) -> dict[str, Any] | None:
    path = wiki_root / PROVENANCE_FILE
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildProvenanceError(
            f"build provenance {path} is not readable JSON: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BuildProvenanceError(f"build provenance {path} is not a JSON object")
    return data


def read_build_provenance(wiki_root: Path | str) -> BuildProvenance | None:
    """Read the recorded provenance; ``None`` when no build was ever recorded.

    A present-but-unreadable record raises ``BuildProvenanceError`` so the
    caller distinguishes "never built" from "provenance corrupted".
    """
    root = Path(wiki_root)
    data = _read_raw(root)
    if data is None:
        return None
    try:
        return BuildProvenance.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError surfaces
        raise BuildProvenanceError(
            f"build provenance {root / PROVENANCE_FILE} is invalid: {exc}"
        ) from exc


def record_build_provenance(
    wiki_root: Path | str,
    *,
    input_fingerprint: str,
    schema_runs: dict[str, Any],
    build_status: str,
    build_revision: int,
    workspace_id: str,
) -> BuildProvenance:
    """Atomically record one build's provenance inside the Workspace Wiki root."""
    root = Path(wiki_root)
    provenance = BuildProvenance(
        workspace_id=workspace_id,
        input_fingerprint=input_fingerprint,
        schema_runs=schema_runs,
        build_status=build_status,
        build_revision=int(build_revision),
        built_at=_utc_now_iso(),
    )
    payload = (provenance.model_dump_json(indent=2) + "\n").encode("utf-8")
    try:
        root.mkdir(parents=True, exist_ok=True)
        target = root / PROVENANCE_FILE
        tmp_path = root / f".{PROVENANCE_FILE}.tmp-{uuid.uuid4().hex[:12]}"
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, target)
    except OSError as exc:
        raise BuildProvenanceError(
            f"could not record build provenance under {root}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return provenance


__all__ = [
    "PROVENANCE_FILE",
    "BuildProvenance",
    "BuildProvenanceError",
    "read_build_provenance",
    "record_build_provenance",
]