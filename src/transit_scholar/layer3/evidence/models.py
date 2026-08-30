"""Framework-neutral provenance contracts for authoritative evidence sources."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceSpan(BaseModel):
    """A half-open character range within an evidence source."""

    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_order(self) -> "EvidenceSpan":
        if self.end < self.start:
            raise ValueError("span end must not precede span start")
        return self


class EvidenceLocator(BaseModel):
    """Source provenance only; it carries no Claim or semantic conclusion."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    paper_id: str | None = Field(default=None, min_length=1)
    block_id: str | None = Field(default=None, min_length=1)
    pages: list[int] | None = None
    span: EvidenceSpan | None = None

    @model_validator(mode="after")
    def _validate_source_identity(self) -> "EvidenceLocator":
        if self.source_kind.casefold() == "paper" and self.paper_id is None:
            raise ValueError("paper evidence requires paper_id")
        if self.pages is not None and any(page < 1 for page in self.pages):
            raise ValueError("page numbers must be positive")
        return self


__all__ = ["EvidenceLocator", "EvidenceSpan"]
