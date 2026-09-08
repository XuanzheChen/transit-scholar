from typing import Any
from pydantic import BaseModel, Field


class SchemaDraftRequest(BaseModel):
    schema_id: str
    version: str
    sections: list[dict[str, Any]]
    name: str | None = None
    description: str | None = None
    status_semantics: dict[str, Any] | None = None


class SchemaValidationResponse(BaseModel):
    valid: bool
    issues: list[dict[str, Any]] = Field(default_factory=list)


class SchemaResponse(BaseModel):
    schema_id: str
    version: str
    name: str | None = None
    description: str | None = None
    schema_hash: str
    definition: dict[str, Any]
