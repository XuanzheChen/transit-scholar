from fastapi import APIRouter, Depends, status

from transit_scholar.api.dependencies import get_product
from transit_scholar.api.errors import ApiError, workspace_error_status, workspace_error_message
from transit_scholar.api.schemas.workspaces import (
    PaperSchemaStateResponse, SchemaMaterializationResponse,
    WorkspaceCreateRequest, WorkspaceListResponse, WorkspacePaperListResponse,
    WorkspacePaperRequest, WorkspacePaperResponse, WorkspaceResponse,
    WorkspaceSchemaResponse,
)
from transit_scholar.layer2.schema_catalog import SchemaNotFoundError
from transit_scholar.layer3.schema.errors import SchemaDisabledError, WorkspaceSchemaError
from transit_scholar.layer3.workspace.errors import WorkspaceError
from transit_scholar.product.facade import WorkspaceBusyError

router = APIRouter(prefix="/api/v1/workspaces")


def _workspace(record):
    return WorkspaceResponse.model_validate(record.model_dump())


def _workspace_error(exc: WorkspaceError, workspace_id: str):
    code = "NOT_FOUND" if exc.code == "workspace_not_found" else exc.code.upper()
    status_code = workspace_error_status(exc.code)
    message = workspace_error_message(exc.code)
    raise ApiError(code, message, {"workspace_id": workspace_id}, status_code) from exc


def _busy(exc: WorkspaceBusyError, workspace_id: str):
    raise ApiError("WORKSPACE_BUSY", "Workspace has a non-terminal AgentRun", {"workspace_id": workspace_id}, 409) from exc


@router.get("", response_model=WorkspaceListResponse)
def list_workspaces(product=Depends(get_product)):
    return WorkspaceListResponse(items=[_workspace(item) for item in product.list_workspaces()])


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreateRequest, product=Depends(get_product)):
    try:
        selection = payload.schema
        return _workspace(product.create_workspace(payload.name, selection.schema_id if selection else None, selection.version if selection else None))
    except SchemaNotFoundError as exc:
        raise ApiError("NOT_FOUND", "Schema not found", {}, 404) from exc
    except WorkspaceError as exc:
        _workspace_error(exc, "")


@router.get("/{workspace_id}/papers", response_model=WorkspacePaperListResponse)
def list_papers(workspace_id: str, product=Depends(get_product)):
    try:
        return WorkspacePaperListResponse(items=[WorkspacePaperResponse.model_validate(item.model_dump()) for item in product.list_workspace_papers(workspace_id)])
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(workspace_id: str, product=Depends(get_product)):
    try:
        return _workspace(product.get_workspace(workspace_id))
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.post("/{workspace_id}/archive", response_model=WorkspaceResponse)
def archive_workspace(workspace_id: str, product=Depends(get_product)):
    try:
        return _workspace(product.archive_workspace(workspace_id))
    except WorkspaceBusyError as exc:
        _busy(exc, workspace_id)
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.delete("/{workspace_id}", response_model=WorkspaceResponse)
def delete_workspace(workspace_id: str, product=Depends(get_product)):
    try:
        return _workspace(product.delete_workspace(workspace_id))
    except WorkspaceBusyError as exc:
        _busy(exc, workspace_id)
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.post("/{workspace_id}/papers", response_model=WorkspacePaperResponse)
def add_paper(workspace_id: str, payload: WorkspacePaperRequest, product=Depends(get_product)):
    try:
        result = product.add_workspace_paper(workspace_id, payload.paper_id)
        data = result.membership.model_dump()
        data["already_member"] = result.already_member
        return WorkspacePaperResponse.model_validate(data)
    except WorkspaceBusyError as exc:
        _busy(exc, workspace_id)
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.delete("/{workspace_id}/papers/{paper_id}", response_model=WorkspaceResponse)
def remove_paper(workspace_id: str, paper_id: str, product=Depends(get_product)):
    try:
        return _workspace(product.remove_workspace_paper(workspace_id, paper_id).workspace)
    except WorkspaceBusyError as exc:
        _busy(exc, workspace_id)
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.get("/{workspace_id}/schema", response_model=WorkspaceSchemaResponse)
def workspace_schema(workspace_id: str, product=Depends(get_product)):
    try:
        record = product.get_workspace(workspace_id)
        binding = product.workspace_schema(workspace_id)
        return WorkspaceSchemaResponse(
            schema_mode=record.schema_mode,
            binding=binding.model_dump() if binding is not None else None,
        )
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.get("/{workspace_id}/papers/{paper_id}/schema", response_model=PaperSchemaStateResponse)
def paper_schema(workspace_id: str, paper_id: str, product=Depends(get_product)):
    try:
        record = product.get_workspace(workspace_id)
        if record.schema_mode == "none":
            memberships = product.list_workspace_papers(workspace_id)
            if not any(member.paper_id == paper_id for member in memberships):
                raise ApiError("NOT_FOUND", "Paper not found or not a workspace member", {"paper_id": paper_id}, 404)
            return PaperSchemaStateResponse(workspace_id=workspace_id, paper_id=paper_id, status="disabled", error_code="schema_disabled")
        readiness = product.workspace_schema_readiness(workspace_id, paper_id)[paper_id]
        return PaperSchemaStateResponse(workspace_id=workspace_id, paper_id=paper_id, **readiness.model_dump())
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)


@router.post("/{workspace_id}/papers/{paper_id}/schema/materialize", response_model=SchemaMaterializationResponse)
def materialize_schema(workspace_id: str, paper_id: str, product=Depends(get_product)):
    try:
        result = product.materialize_workspace_schema(workspace_id, paper_id)
        return SchemaMaterializationResponse(workspace_id=workspace_id, paper_id=paper_id, run_id=getattr(result, "run_id", None), status=result.run_manifest.status)
    except WorkspaceBusyError as exc:
        _busy(exc, workspace_id)
    except WorkspaceSchemaError as exc:
        _workspace_error(exc, workspace_id)
    except WorkspaceError as exc:
        _workspace_error(exc, workspace_id)
