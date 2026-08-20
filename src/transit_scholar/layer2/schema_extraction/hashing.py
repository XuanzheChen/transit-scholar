"""Schema definition hashing (FR-A-005).

The hash is derived from the canonical JSON representation of the schema
content (``model_dump_json`` preserves list order), so:

- equal content produces the same hash across independent loads;
- changing any content field (id, label, description, question, type,
  options, constraints, version, evidence_required, allow_inference, ...)
  changes the hash;
- reordering sections or fields changes the hash;
- no local path, load time, or machine information enters the payload.
"""

from __future__ import annotations

import hashlib

from .models import SchemaDefinition


def compute_schema_hash(definition: SchemaDefinition) -> str:
    """Return a stable content hash for a schema definition."""
    payload = definition.model_dump_json().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
