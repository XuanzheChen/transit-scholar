"""Gold query schema for retrieval evaluation (FR-014)."""

from __future__ import annotations

from transit_scholar.layer2.schema import GoldQuery

#: Recognized query types for gold annotations.
QUERY_TYPES = (
    "exact_term",
    "number_lookup",
    "method_description",
    "semantic_paraphrase",
    "formula_lookup",
    "table_result_lookup",
    "cross_language",
)

GOLD_FIELDS = ("paper_id", "query", "query_type", "gold_block_ids")


def validate_gold_query(data: dict[str, object]) -> GoldQuery:
    """Validate a gold query dict and return a ``GoldQuery``.

    ``gold_source_spans`` is optional; the four required fields are
    ``paper_id`` / ``query`` / ``query_type`` / ``gold_block_ids``.
    """
    for field in GOLD_FIELDS:
        if field not in data:
            raise ValueError(f"gold query missing required field {field!r}")
    query_type = str(data["query_type"])
    if query_type not in QUERY_TYPES:
        raise ValueError(f"gold query has unknown query_type {query_type!r}")
    return GoldQuery.from_dict(data)


def load_gold_queries(path) -> list[GoldQuery]:
    """Load a JSON array of gold queries from a file."""
    import json

    raw = json.loads(open(path, encoding="utf-8").read())
    if not isinstance(raw, list):
        raise ValueError("gold file root must be a JSON array")
    return [validate_gold_query(entry) for entry in raw]
