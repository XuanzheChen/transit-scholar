#!/usr/bin/env python
"""Controlled one-paper × one-field real L2S2 runtime smoke."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="l2s2_runtime_smoke",
        description=(
            "Real L2S2 structured-output smoke for exactly one paper and "
            "one existing schema field."
        ),
    )
    parser.add_argument(
        "--paper",
        required=True,
        help="paper id with existing L2S1 parse and retrieval data",
    )
    parser.add_argument(
        "--field",
        required=True,
        help="one existing field id from the selected schema",
    )
    parser.add_argument("--schema", default="bus_control_rl")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--rerank",
        action="store_true",
        help="opt in to reranking (off by default for the minimal smoke)",
    )
    return parser.parse_args(argv)


def _network_blocked() -> bool:
    value = os.environ.get("TRANSIT_SCHOLAR_BLOCK_NETWORK", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _find_field(definition: Any, field_id: str) -> Any | None:
    for section in definition.sections:
        for field in section.fields:
            if field.id == field_id:
                return field
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from transit_scholar.config import ensure_project_dotenv

    ensure_project_dotenv()

    from transit_scholar.layer2.schema_extraction import (
        LLMCapabilityError,
        LLMConfig,
        LLMInvalidOutputError,
        LLMRequestError,
        LLMUnavailableError,
        OpenAICompatibleLLMClient,
        StructuredSemanticVerifier,
        extract_field_instance_in_memory,
        get_schema_definition,
        resolve_runtime_llm_client,
    )
    from transit_scholar.layer2.schema_extraction.retrieval import (
        HybridRetrievalWrapper,
    )
    from transit_scholar.layer2.retrieval.api import read_blocks

    definition = get_schema_definition(args.schema)
    field = _find_field(definition, args.field)
    if field is None:
        print(
            f"L2S2 runtime smoke: unknown field {args.field!r} for schema "
            f"{args.schema!r}",
            file=sys.stderr,
        )
        return 2

    try:
        client = resolve_runtime_llm_client()
    except LLMUnavailableError as exc:
        print("L2S2 runtime smoke: runtime_unavailable", file=sys.stderr)
        print(f"  reason={exc}", file=sys.stderr)
        return 3
    if not isinstance(client, OpenAICompatibleLLMClient) or bool(
        getattr(client, "is_fake", False)
    ):
        print("L2S2 runtime smoke: real OpenAI-compatible client required", file=sys.stderr)
        return 3

    config = getattr(client, "config", LLMConfig())
    print("L2S2 runtime smoke (one paper x one field)")
    print(f"  provider={getattr(client, 'provider_name', 'unknown')}")
    print(f"  model={getattr(client, 'model_name', 'unknown')}")
    print(f"  client_class={type(client).__name__}")
    print(f"  allow_network={bool(getattr(config, 'allow_network', False))}")
    print(f"  block_network={_network_blocked()}")
    print(f"  paper_id={args.paper}")
    print(f"  field_id={args.field}")

    retrieval = HybridRetrievalWrapper(top_k=args.top_k, rerank=args.rerank)
    run = extract_field_instance_in_memory(
        args.paper,
        args.schema,
        args.field,
        llm_client=client,
        retrieval=retrieval,
        top_k=args.top_k,
        canonical_reader=read_blocks,
    )
    if run.instance is None or not run.manifest.fields:
        print("  failure_class=extraction_runtime", file=sys.stderr)
        return 4

    trace = run.manifest.fields[0]
    if trace.error_code:
        failure_class = {
            "llm_structured_output_unsupported": "capability",
            "llm_invalid_output": "invalid_output",
        }.get(trace.error_code, "extraction_runtime")
        print(f"  failure_class={failure_class}", file=sys.stderr)
        print(f"  error_code={trace.error_code}", file=sys.stderr)
        return 5

    result = run.instance.fields[args.field]
    if result.status in {"not_found", "unclear"}:
        print("  failure_class=no_extractable_value", file=sys.stderr)
        print(f"  extraction_status={result.status}", file=sys.stderr)
        return 6

    verifier = StructuredSemanticVerifier(client)
    try:
        verdict = verifier(field, result)
    except LLMCapabilityError as exc:
        print("  failure_class=capability", file=sys.stderr)
        print(f"  error_code={exc.error_code}", file=sys.stderr)
        return 7
    except LLMInvalidOutputError as exc:
        print("  failure_class=invalid_output", file=sys.stderr)
        print(f"  error_code={exc.error_code}", file=sys.stderr)
        return 8
    except (LLMRequestError, LLMUnavailableError) as exc:
        print("  failure_class=verifier_runtime", file=sys.stderr)
        print(f"  error_code={exc.error_code}", file=sys.stderr)
        return 9

    print(f"  extraction_status={result.status}")
    print(f"  semantic_decision={verdict.decision}")
    print("  final_success=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
