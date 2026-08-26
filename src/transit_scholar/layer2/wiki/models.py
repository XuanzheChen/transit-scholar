"""Schema-neutral, workspace-scoped data models for the base wiki."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
PageBuildStatus = Literal["pending", "complete", "incomplete", "failed"]
ManifestBuildStatus = Literal["complete", "partial", "failed"]


def validate_identifier(value: str) -> str:
    """Return a safe identifier, rejecting values that could escape storage."""
    if not isinstance(value, str):
        raise ValueError("identifier must be a string")
    value = value.strip()
    if (
        not value
        or value in {".", ".."}
        or ".." in value
        or "/" in value
        or "\\" in value
        or _DRIVE_PREFIX.match(value)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("identifier is empty or unsafe for a path")
    return value


_PRESENTATION_SEPARATORS = re.compile(r"[\u00b7\u2022\u2027\u30fb]+")
_DASH_VARIANTS = re.compile(r"[\u2010-\u2015\u2212]")


def normalize_entity_name(value: str) -> str:
    """Return a generic, deterministic key suitable for entity identity.

    Presentation-only separators collapse to whitespace, while hyphens and
    version punctuation remain meaningful.  A blank result represents an
    invalid entity name and is deliberately not usable for identity creation.
    """
    if not isinstance(value, str):
        raise ValueError("entity name must be a string")
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = _DASH_VARIANTS.sub("-", normalized)
    normalized = _PRESENTATION_SEPARATORS.sub(" ", normalized)
    normalized = " ".join(normalized.split())
    return normalized if any(character.isalnum() for character in normalized) else ""


def utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _stable_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join((kind, *parts)).encode("utf-8")
    return f"{kind}_{hashlib.sha256(payload).hexdigest()}"


def page_id_for(workspace_id: str, paper_id: str) -> str:
    return _stable_id("page", workspace_id, paper_id)


def entity_id_for(workspace_id: str, canonical_name: str) -> str:
    normalized = normalize_entity_name(canonical_name)
    if not normalized:
        raise ValueError("canonical entity name must contain an alphanumeric character")
    return _stable_id("entity", workspace_id, normalized)


def link_id_for(
    workspace_id: str,
    page_id: str,
    entity_id: str,
    source_field_id: str,
    source_status: str,
    schema_id: str,
    schema_version: str,
) -> str:
    return _stable_id(
        "link",
        workspace_id,
        page_id,
        entity_id,
        source_field_id,
        source_status,
        schema_id,
        schema_version,
    )


class _UtcModel(BaseModel):
    @field_validator("created_at", "updated_at", "timestamp", mode="before", check_fields=False)
    @classmethod
    def validate_timestamps(cls, value: datetime | str) -> datetime:
        return _utc_datetime(value)


class WorkspaceContext(BaseModel):
    workspace_id: str
    schema_id: str
    schema_version: str
    paper_ids: list[str]

    @field_validator("workspace_id", "schema_id", "schema_version")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("paper_ids")
    @classmethod
    def validate_paper_ids(cls, values: list[str]) -> list[str]:
        unique: list[str] = []
        for value in values:
            value = validate_identifier(value)
            if value not in unique:
                unique.append(value)
        if not unique:
            raise ValueError("paper_ids must not be empty")
        return unique


class PaperMetadata(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None

    @field_validator("paper_id")
    @classmethod
    def validate_paper_id(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("title must not be empty")
        return value.strip()

    @field_validator("year")
    @classmethod
    def validate_year(cls, value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or not 1000 <= value <= 3000):
            raise ValueError("year must be a reasonable integer")
        return value


class WikiPage(_UtcModel):
    page_id: str
    workspace_id: str
    paper_id: str
    title: str
    summary: str = ""
    schema_id: str
    schema_version: str
    build_status: PageBuildStatus = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    build_revision: int = Field(default=0, ge=0)

    @field_validator("page_id", "workspace_id", "paper_id", "schema_id", "schema_version")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("title must not be empty")
        return value.strip()


class WikiEntity(_UtcModel):
    entity_id: str
    workspace_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    kind: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("entity_id", "workspace_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("canonical_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not isinstance(value, str) or not normalize_entity_name(value):
            raise ValueError("canonical_name must not be empty")
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                raise ValueError("aliases must contain strings")
            value = value.strip()
            normalized = normalize_entity_name(value)
            if value and normalized and normalized not in seen:
                cleaned.append(value)
                seen.add(normalized)
        return cleaned

    def model_post_init(self, __context: object) -> None:
        canonical = normalize_entity_name(self.canonical_name)
        self.aliases = [
            alias for alias in self.aliases if normalize_entity_name(alias) != canonical
        ]


class PageEntityLink(_UtcModel):
    link_id: str
    workspace_id: str
    page_id: str
    entity_id: str
    paper_id: str
    relation: str = "associated_with"
    schema_id: str
    schema_version: str
    source_field_id: str
    source_status: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "link_id", "workspace_id", "page_id", "entity_id", "paper_id", "schema_id", "schema_version"
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("relation", "source_field_id", "source_status")
    @classmethod
    def validate_trace(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("link trace values must not be empty")
        return value.strip()


class WikiManifest(_UtcModel):
    workspace_id: str
    schema_id: str
    schema_version: str
    paper_ids: list[str]
    builder_version: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    build_status: ManifestBuildStatus = "partial"

    @field_validator("workspace_id", "schema_id", "schema_version", "builder_version")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("paper_ids")
    @classmethod
    def validate_paper_ids(cls, values: list[str]) -> list[str]:
        return WorkspaceContext.validate_paper_ids(values)


class WikiSearchHit(BaseModel):
    type: Literal["page", "entity"]
    object_id: str
    title: str
    score: float
    snippet: str
    retrieval_mode: Literal["lexical", "semantic"]


class WikiSearchResult(BaseModel):
    status: Literal["ok", "degraded", "error"] = "ok"
    hits: list[WikiSearchHit] = Field(default_factory=list)
    error_code: str | None = None


class PageEntityResult(BaseModel):
    page: WikiPage
    entities: list[WikiEntity] = Field(default_factory=list)


class RelatedPageResult(BaseModel):
    page: WikiPage
    shared_entity_ids: list[str]
    shared_entity_count: int


class UnlinkResult(BaseModel):
    link_id: str
    status: Literal["removed"] = "removed"


class AuditIssue(BaseModel):
    code: str
    object_id: str | None = None
    message: str
    severity: Literal["error", "warning"] = "error"


class WikiAuditReport(BaseModel):
    ok: bool
    audited_at: datetime = Field(default_factory=utc_now)
    issues: list[AuditIssue] = Field(default_factory=list)
    page_id: str | None = None
    source_fingerprint: str | None = None


class IndexRebuildResult(BaseModel):
    status: Literal["rebuilt", "failed"] = "rebuilt"
    source_fingerprint: str
    index_version: int
    error_code: str | None = None
    rebuilt_at: datetime = Field(default_factory=utc_now)


class RawSnapshotAsset(BaseModel):
    path: str
    exists: bool
    sha256: str | None = None
    content: bytes | None = None


class RawSnapshot(BaseModel):
    manifest: RawSnapshotAsset
    pages: RawSnapshotAsset
    entities: RawSnapshotAsset
    links: RawSnapshotAsset
