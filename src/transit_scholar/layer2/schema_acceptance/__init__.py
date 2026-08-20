"""L2S2 Package E: real-paper schema acceptance framework.

Package E is the V1 pre-freeze acceptance package for the L2S2 schema
extraction layer. It adds only a data format, a runner, metrics, reports and
a deterministic offline test loop; it adds no extraction, retrieval,
embedding, parser, OCR, database or Web capability.

Important position statement: Package E never declares the L2S2 V1 freeze.
Reports carry ``freeze.declared_frozen = false`` and the fixed wording that
freezing is decided by the user/Planner based on the report
(``FREEZE_MESSAGE`` in ``report.py``).

Public API (AC-E-04):

- ``load_schema_gold(path)``
- ``validate_schema_gold(gold)``
- ``evaluate_schema_instance(instance, gold_entry, *, validation_report=None,
  canonical_reader=None)``
- ``evaluate_schema_gold(gold, *, storage_root=None,
  schema_id="bus_control_rl", run_id=None, canonical_reader=None)``
- ``write_acceptance_report(report, output_dir)``

Offline contract: importing this package only pulls stdlib, pydantic, and
the stable public exports of ``transit_scholar.layer2.schema_extraction``.
No network, no LLM, no PDF, no ``data/**`` IO happens on the default paths.
"""

from __future__ import annotations

from .evaluate import (
    AcceptanceIssue,
    FieldEvaluation,
    PaperEvaluation,
    evaluate_schema_gold,
    evaluate_schema_instance,
)
from .gold import (
    GoldBenchmark,
    GoldField,
    GoldLoadError,
    GoldPaper,
    load_schema_gold,
    validate_schema_gold,
)
from .metrics import AggregateMetrics, FieldAggregate
from .report import (
    REPORT_SCHEMA_VERSION,
    AcceptanceReport,
    AcceptanceReportError,
    FreezeBlock,
    GoldReviewItem,
    TraceabilityBlock,
    write_acceptance_report,
)

__all__ = [
    # public functions (AC-E-04)
    "load_schema_gold",
    "validate_schema_gold",
    "evaluate_schema_instance",
    "evaluate_schema_gold",
    "write_acceptance_report",
    # gold models
    "GoldBenchmark",
    "GoldPaper",
    "GoldField",
    "GoldLoadError",
    # evaluation models
    "AcceptanceIssue",
    "FieldEvaluation",
    "PaperEvaluation",
    # metrics models
    "AggregateMetrics",
    "FieldAggregate",
    # report models
    "AcceptanceReport",
    "AcceptanceReportError",
    "FreezeBlock",
    "GoldReviewItem",
    "TraceabilityBlock",
    "REPORT_SCHEMA_VERSION",
]
