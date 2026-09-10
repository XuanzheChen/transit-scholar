"""Structured Workspace Wiki API endpoints."""
from typing import Literal

from fastapi import APIRouter, Depends, Request, Query

from transit_scholar.api.dependencies import get_product, exclusive_workspace_mutation
from transit_scholar.api.errors import ApiError, workspace_error_status, workspace_error_message
from transit_scholar.product.facade import WorkspaceBusyError
from transit_scholar.api.schemas.wiki import (
    AgenticWikiEntryListResponse, AgenticWikiEntryResponse,
    BaseWikiCapabilityResponse, BaseWikiStatusResponse, WikiBuildResponse,
    WikiEntityListResponse, WikiEntityResponse, WikiOverviewResponse,
    WikiPageListResponse, WikiPageResponse, WikiSearchResponse,
)
from transit_scholar.layer2.wiki.store import WikiNotFoundError
from transit_scholar.layer3.wiki.errors import WorkspaceWikiError
from transit_scholar.layer3.workspace.errors import WorkspaceError


router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/wiki")


def _status(record) -> BaseWikiStatusResponse:
    return BaseWikiStatusResponse.model_validate(record.model_dump())


def _wiki_error(exc: Exception, workspace_id: str) -> None:
    if isinstance(exc, WorkspaceError):
        code = getattr(exc, "code", "workspace_wiki_error")
        status_code = workspace_error_status(code)
        message = workspace_error_message(code, fallback="Wiki operation failed")
        raise ApiError(code.upper(), message, {"workspace_id": workspace_id}, status_code) from exc
    if isinstance(exc, WikiNotFoundError):
        raise ApiError("NOT_FOUND", "Wiki resource was not found", {"workspace_id": workspace_id}, 404) from exc
    if isinstance(exc, PermissionError):
        raise ApiError("NOT_FOUND", "Wiki resource was not found", {"workspace_id": workspace_id}, 404) from exc
    raise exc


@router.get("", response_model=WikiOverviewResponse)
def wiki_overview(workspace_id: str, product=Depends(get_product)):
    try:
        status = product.workspace_wiki_status(workspace_id)
        capability = product.workspace_wiki_capability(workspace_id)
        entries = product.list_workspace_agentic_entries(workspace_id, include_stale=True)
        return WikiOverviewResponse(
            workspace_id=workspace_id,
            base_wiki=_status(status),
            base_wiki_capability=BaseWikiCapabilityResponse(
                build_supported=capability.build_supported,
                read_supported=capability.read_supported,
                reason=capability.reason,
            ),
            agentic_wiki_entry_count=len(entries),
        )
    except (WorkspaceError, PermissionError) as exc:
        _wiki_error(exc, workspace_id)


@router.get("/status", response_model=BaseWikiStatusResponse)
def wiki_status(workspace_id: str, product=Depends(get_product)):
    try:
        return _status(product.workspace_wiki_status(workspace_id))
    except WorkspaceError as exc:
        _wiki_error(exc, workspace_id)


@router.post("/build", response_model=WikiBuildResponse)
def build_wiki(request: Request, workspace_id: str, product=Depends(get_product)):
    try:
        with exclusive_workspace_mutation(request, product, workspace_id):
            outcome = product.build_workspace_wiki(workspace_id)
        status = product.workspace_wiki_status(workspace_id)
        return WikiBuildResponse(
            workspace_id=workspace_id,
            status=_status(status),
            fingerprint=outcome.fingerprint,
            build_revision=outcome.provenance.build_revision,
        )
    except WorkspaceBusyError as exc:
        raise ApiError("WORKSPACE_BUSY", "Workspace has a non-terminal AgentRun", {"workspace_id": workspace_id}, 409) from exc
    except WorkspaceWikiError as exc:
        _wiki_error(exc, workspace_id)
    except WorkspaceError as exc:
        _wiki_error(exc, workspace_id)


@router.get("/pages", response_model=WikiPageListResponse)
def list_pages(workspace_id: str, product=Depends(get_product)):
    try:
        return WikiPageListResponse(items=[WikiPageResponse.model_validate(page.model_dump()) for page in product.list_workspace_wiki_pages(workspace_id)])
    except (WorkspaceWikiError, WorkspaceError, WikiNotFoundError) as exc:
        _wiki_error(exc, workspace_id)


@router.get("/pages/{page_id}", response_model=WikiPageResponse)
def get_page(workspace_id: str, page_id: str, product=Depends(get_product)):
    try:
        return WikiPageResponse.model_validate(product.get_workspace_wiki_page(workspace_id, page_id).model_dump())
    except (WorkspaceWikiError, WorkspaceError, WikiNotFoundError) as exc:
        _wiki_error(exc, workspace_id)


@router.get("/entities", response_model=WikiEntityListResponse)
def list_entities(workspace_id: str, product=Depends(get_product)):
    try:
        return WikiEntityListResponse(items=[WikiEntityResponse.model_validate(entity.model_dump()) for entity in product.list_workspace_wiki_entities(workspace_id)])
    except (WorkspaceWikiError, WorkspaceError, WikiNotFoundError) as exc:
        _wiki_error(exc, workspace_id)


@router.get("/entities/{entity_id}", response_model=WikiEntityResponse)
def get_entity(workspace_id: str, entity_id: str, product=Depends(get_product)):
    try:
        return WikiEntityResponse.model_validate(product.get_workspace_wiki_entity(workspace_id, entity_id).model_dump())
    except (WorkspaceWikiError, WorkspaceError, WikiNotFoundError) as exc:
        _wiki_error(exc, workspace_id)


@router.get("/agentic-entries", response_model=AgenticWikiEntryListResponse)
def list_agentic_entries(workspace_id: str, include_stale: bool = False, product=Depends(get_product)):
    try:
        entries = product.list_workspace_agentic_entries(workspace_id, include_stale=include_stale)
        return AgenticWikiEntryListResponse(items=[AgenticWikiEntryResponse.model_validate(entry.model_dump()) for entry in entries])
    except (WorkspaceError, PermissionError) as exc:
        _wiki_error(exc, workspace_id)


@router.get("/agentic-entries/{entry_id}", response_model=AgenticWikiEntryResponse)
def get_agentic_entry(workspace_id: str, entry_id: str, product=Depends(get_product)):
    try:
        return AgenticWikiEntryResponse.model_validate(product.get_workspace_agentic_entry(workspace_id, entry_id).model_dump())
    except (WorkspaceError, PermissionError) as exc:
        _wiki_error(exc, workspace_id)


@router.get("/search", response_model=WikiSearchResponse)
def search_wiki(
    workspace_id: str,
    query: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    mode: Literal["lexical", "semantic"] = "lexical",
    include_stale: bool = False,
    product=Depends(get_product),
):
    try:
        return WikiSearchResponse.model_validate(product.search_workspace_wiki(
            workspace_id, query, limit=limit, mode=mode, include_stale=include_stale
        ).model_dump())
    except (WorkspaceWikiError, WorkspaceError) as exc:
        _wiki_error(exc, workspace_id)
