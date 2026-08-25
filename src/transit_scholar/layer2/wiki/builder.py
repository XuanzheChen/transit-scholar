"""Bounded, deterministic Package E WikiBuilder compiler."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from transit_scholar.layer2.schema_extraction.models import SchemaDefinition, SchemaInstance

from .field_cards import FieldCard, FieldCardValidationError, build_field_cards
from .models import PaperMetadata, WikiAuditReport, WikiEntity, WikiPage, WorkspaceContext
from .proposals import EntityProposal, EntityProposalRequest

MAX_PROPOSALS = 100


class BuildPhase(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    status: str
    error_code: str | None = None


class ProposalTrace(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_field_id: str
    canonical_name: str
    source_status: str | None = None
    confidence: float | None = None
    status: str
    error_code: str | None = None
    resolution: str | None = None
    resolution_reason: str | None = None
    entity_id: str | None = None
    link_id: str | None = None


class AuditTrace(BaseModel):
    model_config = ConfigDict(frozen=True)
    attempted: bool = False
    ok: bool = False
    issue_codes: tuple[str, ...] = ()
    object_ids: tuple[str, ...] = ()
    fingerprint: str | None = None


class PageTrace(BaseModel):
    model_config = ConfigDict(frozen=True)
    page_id: str | None = None
    paper_id: str
    schema_id: str
    schema_version: str
    build_status: str
    build_revision: int | None = None
    summary: str = ""


class PaperWikiBuildResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["complete", "incomplete", "failed"]
    paper_id: str
    page: PageTrace
    phases: tuple[BuildPhase, ...] = ()
    proposals: tuple[ProposalTrace, ...] = ()
    audit: AuditTrace = AuditTrace()
    error_codes: tuple[str, ...] = ()
    omitted_proposals: int = 0

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True, by_alias=True)


class WorkspaceWikiBuildResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: Literal["complete", "partial", "failed"]
    workspace_id: str
    schema_id: str
    schema_version: str
    papers: tuple[PaperWikiBuildResult, ...]
    complete_count: int
    incomplete_count: int
    failed_count: int

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True, by_alias=True)


def _code(exc: Exception, fallback: str) -> str:
    try:
        value = getattr(exc, "code", None)
    except Exception:
        value = None
    return value if isinstance(value, str) and value and value.isidentifier() else fallback


def _field_status(cards: tuple[FieldCard, ...], field_id: str) -> str:
    for card in cards:
        if card.field_id == field_id:
            return card.status
    return "unknown"


def _summary(metadata: PaperMetadata, cards: tuple[FieldCard, ...]) -> str:
    authors = ", ".join(metadata.authors)
    head = metadata.title
    if authors:
        head += f" | Authors: {authors}"
    if metadata.year is not None:
        head += f" | Year: {metadata.year}"
    values = []
    for card in cards:
        value = card.value
        if value is None:
            rendered = "null"
        else:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        values.append(f"{card.field_id}={rendered} [{card.status}]")
    return head + (" | Fields: " + "; ".join(values) if values else "")


def _safe_attr(value: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(value, name, default)
    except Exception:
        return default


def _safe_text(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _page_trace(page: WikiPage | None, context: Any, paper_id: str, status: str, summary: str = "") -> PageTrace:
    page_id = _safe_text(_safe_attr(page, "page_id"), "") if page is not None else None
    revision = _safe_attr(page, "build_revision") if page is not None else None
    if not isinstance(revision, int):
        revision = None
    return PageTrace(page_id=page_id, paper_id=_safe_text(paper_id, "invalid"),
                     schema_id=_safe_text(_safe_attr(context, "schema_id"), "invalid"),
                     schema_version=_safe_text(_safe_attr(context, "schema_version"), "invalid"),
                     build_status=status, build_revision=revision,
                     summary=summary if isinstance(summary, str) else "")


def _failure(context: WorkspaceContext, paper_id: str, code: str, phases: list[BuildPhase], *, page: WikiPage | None = None, summary: str = "", proposals: list[ProposalTrace] | None = None, audit: AuditTrace | None = None) -> PaperWikiBuildResult:
    return PaperWikiBuildResult(status="failed", paper_id=paper_id,
        page=_page_trace(page, context, paper_id, "failed", summary), phases=tuple(phases),
        proposals=tuple(proposals or ()), audit=audit or AuditTrace(), error_codes=(code,))


def _preflight(context: WorkspaceContext, definition: SchemaDefinition, instance: SchemaInstance, metadata: PaperMetadata, service: Any, proposal_runner: Any, resolver: Any, max_proposals: int) -> str | None:
    if not isinstance(context, WorkspaceContext) or not isinstance(definition, SchemaDefinition) or not isinstance(instance, SchemaInstance) or not isinstance(metadata, PaperMetadata): return "invalid_input"
    try:
        paper_ids = _safe_attr(context, "paper_ids", ())
        if not isinstance(paper_ids, (list, tuple)) or _safe_attr(metadata, "paper_id") not in paper_ids or _safe_attr(instance, "paper_id") != _safe_attr(metadata, "paper_id"): return "paper_mismatch"
        schema_id = _safe_attr(context, "schema_id")
        schema_version = _safe_attr(context, "schema_version")
        if _safe_attr(definition, "schema_id") != schema_id or _safe_attr(definition, "version") != schema_version or _safe_attr(instance, "schema_id") != schema_id or _safe_attr(instance, "schema_version") != schema_version: return "schema_mismatch"
        if _safe_attr(service, "context") != context or _safe_attr(_safe_attr(service, "store"), "workspace_context") != context: return "workspace_mismatch"
        if _safe_attr(resolver, "context", context) != context: return "workspace_mismatch"
        if not callable(_safe_attr(service, "ensure_paper_page")) or not callable(_safe_attr(proposal_runner, "run")) or not callable(_safe_attr(resolver, "resolve")): return "invalid_boundary"
    except Exception:
        return "invalid_input"
    try:
        if isinstance(max_proposals, bool) or not isinstance(max_proposals, int) or not 0 < max_proposals <= MAX_PROPOSALS: return "invalid_limit"
    except Exception:
        return "invalid_limit"
    return None


def build_wiki_for_paper(context: WorkspaceContext, definition: SchemaDefinition, instance: SchemaInstance, metadata: PaperMetadata, service: Any, proposal_runner: Any, resolver: Any, *, max_proposals: int = MAX_PROPOSALS) -> PaperWikiBuildResult:
    paper_id = _safe_text(_safe_attr(metadata, "paper_id"), "invalid")
    phases: list[BuildPhase] = [BuildPhase(name="validate_bindings", status="success")]
    error = _preflight(context, definition, instance, metadata, service, proposal_runner, resolver, max_proposals)
    if error:
        phases[0] = BuildPhase(name="validate_bindings", status="failed", error_code=error)
        return _failure(context, paper_id, error, phases)
    try:
        cards = tuple(build_field_cards(definition, instance))
        phases.append(BuildPhase(name="field_cards", status="success"))
    except Exception as exc:
        phases.append(BuildPhase(name="field_cards", status="failed", error_code=_code(exc, "cards_failure")))
        return _failure(context, paper_id, _code(exc, "cards_failure"), phases)
    page: WikiPage | None = None
    try:
        page = service.ensure_paper_page(metadata)
        phases.append(BuildPhase(name="ensure_page", status="success"))
    except Exception as exc:
        phases.append(BuildPhase(name="ensure_page", status="failed", error_code=_code(exc, "service_failure")))
        return _failure(context, paper_id, _code(exc, "service_failure"), phases, page=page)
    try:
        summary = _summary(metadata, cards)
        page = service.update_page_summary(page.page_id, summary)
        phases.append(BuildPhase(name="summary", status="success"))
    except Exception as exc:
        phases.append(BuildPhase(name="summary", status="failed", error_code=_code(exc, "service_failure")))
        return _failure(context, paper_id, _code(exc, "service_failure"), phases, page=page)
    traces: list[ProposalTrace] = []
    omitted = 0
    proposal_status = "provider_failure"
    try:
        request = EntityProposalRequest(cards=cards, paper_id=paper_id, schema_id=context.schema_id, schema_version=context.schema_version)
        result = proposal_runner.run(request)
        proposal_status = str(getattr(result, "status", "invalid"))
        proposals = tuple(getattr(result, "proposals", ()) or ())
        omitted = max(0, len(proposals) - max_proposals)
        for proposal in proposals[:max_proposals]:
            traces.append(ProposalTrace(source_field_id=proposal.source_field_id, canonical_name=proposal.canonical_name,
                source_status=_field_status(cards, proposal.source_field_id), confidence=proposal.confidence, status="proposed"))
        phases.append(BuildPhase(name="proposal", status=proposal_status, error_code=getattr(result, "error_code", None)))
    except Exception:
        phases.append(BuildPhase(name="proposal", status="provider_failure", error_code="proposal_failure"))
        proposal_status = "provider_failure"
        proposals = ()
    if proposal_status == "success":
        for index, proposal in enumerate(proposals[:max_proposals]):
            try:
                resolved = resolver.resolve(proposal)
                decision = getattr(resolved, "decision", getattr(resolved, "action", "ambiguous"))
                entity = getattr(resolved, "entity", None)
                trace = traces[index].model_copy(update={"resolution": decision, "resolution_reason": getattr(resolved, "reason_code", None)})
                traces[index] = trace
                if decision in {"reuse", "create"} and isinstance(entity, WikiEntity) and entity.workspace_id == context.workspace_id:
                    link = service.link_page_entity(page.page_id, entity.entity_id, source_field_id=proposal.source_field_id, source_status=_field_status(cards, proposal.source_field_id), confidence=proposal.confidence)
                    trace = trace.model_copy(update={"entity_id": entity.entity_id, "link_id": link.link_id})
                traces[index] = trace
            except Exception as exc:
                current = traces[index]
                traces[index] = current.model_copy(update={
                    "resolution_reason": _code(exc, "link_or_resolution_failure"),
                    "error_code": "link_failure" if current.resolution in {"reuse", "create"} else "resolution_failure",
                })
    audit = AuditTrace()
    try:
        report: WikiAuditReport = service.audit_page(page.page_id)
        issues = tuple(sorted({issue.code for issue in report.issues}))
        objects = tuple(sorted({issue.object_id for issue in report.issues if issue.object_id}))
        audit = AuditTrace(attempted=True, ok=bool(report.ok), issue_codes=issues, object_ids=objects, fingerprint=report.source_fingerprint)
        phases.append(BuildPhase(name="audit", status="success" if report.ok else "failed", error_code=None if report.ok else (issues[0] if issues else "audit_failed")))
    except Exception as exc:
        audit = AuditTrace(attempted=True, ok=False)
        phases.append(BuildPhase(name="audit", status="failed", error_code=_code(exc, "audit_failure")))
    degraded = proposal_status != "success" or any(t.resolution not in {None, "reuse", "create"} or (t.resolution in {"reuse", "create"} and not t.link_id) for t in traces) or not audit.ok
    status: Literal["complete", "incomplete", "failed"] = "incomplete" if degraded else "complete"
    try:
        page = service.update_page_build_status(page.page_id, status)
    except Exception:
        status = "failed"
        phases.append(BuildPhase(name="build_status", status="failed", error_code="status_failure"))
    return PaperWikiBuildResult(status=status, paper_id=paper_id, page=_page_trace(page, context, paper_id, status, page.summary if page else ""), phases=tuple(phases), proposals=tuple(traces), audit=audit, error_codes=tuple(sorted({p.error_code for p in phases if p.error_code})), omitted_proposals=omitted)


def build_wiki_for_workspace(context: WorkspaceContext, definition: SchemaDefinition, instances_by_paper: dict[str, SchemaInstance], metadata_by_paper: dict[str, PaperMetadata], service: Any, proposal_runner: Any, resolver: Any, *, max_proposals: int = MAX_PROPOSALS) -> WorkspaceWikiBuildResult:
    paper_ids = _safe_attr(context, "paper_ids", ())
    if not isinstance(paper_ids, (list, tuple)) or not all(isinstance(pid, str) for pid in paper_ids):
        paper_ids = ("invalid",)
    instances = instances_by_paper if isinstance(instances_by_paper, dict) else {}
    metadata = metadata_by_paper if isinstance(metadata_by_paper, dict) else {}
    results = tuple(build_wiki_for_paper(context, definition, instances.get(pid), metadata.get(pid), service, proposal_runner, resolver, max_proposals=max_proposals) if isinstance(instances.get(pid), SchemaInstance) and isinstance(metadata.get(pid), PaperMetadata) else _failure(context, pid, "missing_input", [BuildPhase(name="validate_bindings", status="failed", error_code="missing_input")]) for pid in paper_ids)
    complete = sum(r.status == "complete" for r in results); failed = sum(r.status == "failed" for r in results); incomplete = len(results) - complete - failed
    status = "complete" if complete == len(results) else "failed" if complete == 0 else "partial"
    return WorkspaceWikiBuildResult(status=status, workspace_id=_safe_text(_safe_attr(context, "workspace_id"), "invalid"), schema_id=_safe_text(_safe_attr(context, "schema_id"), "invalid"), schema_version=_safe_text(_safe_attr(context, "schema_version"), "invalid"), papers=results, complete_count=complete, incomplete_count=incomplete, failed_count=failed)
