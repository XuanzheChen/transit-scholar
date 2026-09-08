from datetime import datetime

from pydantic import BaseModel, Field


class SchemaSelectionRequest(BaseModel):
    schema_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    schema: SchemaSelectionRequest | None = None


class SchemaBindingResponse(BaseModel):
    schema_id: str
    schema_version: str
    schema_hash: str


class WorkspaceResponse(BaseModel):
    workspace_id: str
    name: str
    status: str
    schema_mode: str
    schema_binding: SchemaBindingResponse | None = None
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    items: list[WorkspaceResponse]


class WorkspacePaperRequest(BaseModel):
    paper_id: str = Field(min_length=1)


class WorkspacePaperResponse(BaseModel):
    workspace_id: str
    paper_id: str
    created_at: datetime | None = None
    already_member: bool = False


class WorkspacePaperListResponse(BaseModel):
    items: list[WorkspacePaperResponse]


class WorkspaceSchemaResponse(BaseModel):
    schema_mode: str
    binding: SchemaBindingResponse | None = None


class PaperSchemaStateResponse(BaseModel):
    workspace_id: str
    paper_id: str
    status: str
    error_code: str | None = None


class SchemaMaterializationResponse(BaseModel):
    workspace_id: str
    paper_id: str
    run_id: str | None = None
    status: str | None = None
