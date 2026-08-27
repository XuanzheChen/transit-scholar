"""Run one real-provider, persisted-input L2S3 production smoke build.

This command deliberately has no fake-provider fallback. It loads a current
persisted L2S2 SchemaInstance and persisted Paper metadata, then exercises
the application composition root and writes only a sanitized JSON result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from transit_scholar.layer2.schema_extraction.api import get_schema
from transit_scholar.layer2.schema_extraction.llm import (
    OpenAICompatibleLLMClient,
    resolve_runtime_llm_client,
)
from transit_scholar.layer2.schema_extraction.loader import get_schema_definition
from transit_scholar.layer2.wiki import (
    WikiStore,
    WorkspaceContext,
    WorkspaceWikiBuildService,
    create_production_wiki_composition,
    resolve_wiki_embedding_provider,
)
from transit_scholar.layer2.retrieval.providers import CloudEmbeddingProvider
from transit_scholar.metadata.service import read_paper_metadata


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="workspace identifier")
    parser.add_argument("--paper", required=True, help="one persisted paper identifier")
    parser.add_argument("--schema", required=True, help="persisted L2S2 schema identifier")
    parser.add_argument(
        "--schema-storage-root",
        type=Path,
        default=None,
        help="optional L2S2 schema storage root (defaults to configured data root)",
    )
    parser.add_argument(
        "--wiki-storage-root",
        type=Path,
        default=None,
        help="optional isolated root for derived Wiki smoke artifacts",
    )
    parser.add_argument(
        "--result-path",
        type=Path,
        default=None,
        help="optional path for the sanitized JSON smoke result",
    )
    return parser.parse_args(argv)


def _result(
    *,
    success: bool,
    checks: dict[str, bool],
    error_code: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "smoke": "l2s3_production_single_paper",
        "success": success,
        "error_code": error_code,
        "checks": checks,
    }
    if details:
        result["details"] = details
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    checks: dict[str, bool] = {}
    try:
        definition = get_schema_definition(args.schema)
        context = WorkspaceContext(
            workspace_id=args.workspace,
            schema_id=definition.schema_id,
            schema_version=definition.version,
            paper_ids=[args.paper],
        )
        client = resolve_runtime_llm_client()
        checks["real_llm_client"] = isinstance(client, OpenAICompatibleLLMClient) and not client.is_fake
        if not checks["real_llm_client"]:
            raise RuntimeError("real_llm_client_required")
        embedding = resolve_wiki_embedding_provider()
        checks["real_embedding_provider"] = isinstance(embedding, CloudEmbeddingProvider) and bool(
            embedding.available and embedding.info is not None
        )
        if not checks["real_embedding_provider"]:
            raise RuntimeError("real_embedding_provider_required")

        def load_instance(paper_id: str, schema_id: str):
            return get_schema(paper_id, schema_id, storage_root=args.schema_storage_root)

        composition_by_workspace: dict[str, Any] = {}

        def compose(workspace: WorkspaceContext, store: WikiStore):
            composition = create_production_wiki_composition(
                workspace,
                store,
                llm_client=client,
                embedding_provider=embedding,
            )
            composition_by_workspace[workspace.workspace_id] = composition
            return composition

        service = WorkspaceWikiBuildService(
            schema_definition_loader=get_schema_definition,
            schema_instance_loader=load_instance,
            paper_metadata_loader=read_paper_metadata,
            composition_factory=compose,
            wiki_storage_root=args.wiki_storage_root,
        )
        result = service.build_wiki_for_workspace(context)
        composition = composition_by_workspace[context.workspace_id]
        store = WikiStore(context, args.wiki_storage_root)
        links = store.list_links()
        pages = store.list_pages()
        instance = load_instance(args.paper, args.schema)
        index_path = store.index_path / "package_b_index.json"
        index_payload = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {}
        proposal_phase = next(
            (phase for phase in result.build.papers[0].phases if phase.name == "proposal"), None
        )
        proposal_traces = result.build.papers[0].proposals
        accepted = [trace for trace in proposal_traces if trace.status == "linked"]

        valid_proposals = [trace for trace in proposal_traces if trace.status != "invalid"]
        checks.update({
            "paper_page_created": len(pages) == 1 and pages[0].paper_id == args.paper,
            "real_entity_proposal_executed": proposal_phase is not None and proposal_phase.status in {"success", "success_empty"},
            "real_resolver_executed_when_proposals_exist": not proposal_traces or (
                bool(valid_proposals) and all(trace.resolution is not None for trace in valid_proposals)
            ),
            "accepted_links_traceable": all(
                link.paper_id == args.paper
                and link.schema_id == context.schema_id
                and link.schema_version == context.schema_version
                and link.source_field_id in instance.fields
                and link.source_status == instance.fields[link.source_field_id].status
                for link in links
            ) and all(trace.link_id is not None for trace in accepted),
            "manifest_written": result.manifest.build_status == "complete" and store.manifest_path.is_file(),
            "persistent_vectors_built": (
                result.index.status == "rebuilt"
                and index_payload.get("vector_metadata") is not None
                and any(vector.get("kind") == "page" for vector in index_payload.get("vectors", []))
                and (
                    not store.list_entities()
                    or any(vector.get("kind") == "entity" for vector in index_payload.get("vectors", []))
                )
            ),
            "final_audit_no_blocking_error": result.audit.ok,
            "application_composition_used": composition.proposal_provider.client is client and composition.embedding_provider is embedding,
        })
        success = all(checks.values()) and result.build.status == "complete"
        payload = _result(
            success=success,
            checks=checks,
            details={
                "workspace_id": context.workspace_id,
                "paper_id": args.paper,
                "schema_id": context.schema_id,
                "schema_version": context.schema_version,
                "build_status": result.build.status,
                "proposal_count": len(proposal_traces),
                "proposal_phase_status": (
                    proposal_phase.status if proposal_phase is not None else None
                ),
                "accepted_link_count": len(accepted),
                "audit_issue_codes": sorted({issue.code for issue in result.audit.issues}),
            },
        )
        _write_result(args.result_path, payload)
        print(json.dumps(payload, sort_keys=True))
        return 0 if success else 2
    except Exception as error:
        code = getattr(error, "code", None)
        if not isinstance(code, str) or not code:
            code = str(error) if str(error) in {
                "real_llm_client_required",
                "real_embedding_provider_required",
            } else "production_smoke_failed"
        payload = _result(
            success=False,
            checks=checks,
            error_code=code,
            details={"workspace_id": args.workspace, "paper_id": args.paper, "schema_id": args.schema},
        )
        _write_result(args.result_path, payload)
        print(json.dumps(payload, sort_keys=True))
        return 2


def _write_result(path: Path | None, payload: dict[str, Any]) -> None:
    """Persist only the sanitized smoke result when explicitly requested."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
