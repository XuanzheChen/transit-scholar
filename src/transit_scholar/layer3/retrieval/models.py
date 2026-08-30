"""Query-level, framework-neutral retrieval contracts for Layer3 Stage3."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class ResearchQuery(BaseModel):
    """An already-formed query belonging to one research session.

    This contract deliberately contains no session query-generation, sufficiency,
    or reformulation policy.  Each instance is independently executable.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    query_text: str = Field(
        min_length=1,
        validation_alias=AliasChoices("query_text", "text", "query"),
    )

    @property
    def text(self) -> str:
        """A readable alias for callers that use ``text`` terminology."""
        return self.query_text


class RetrievalAction(BaseModel):
    """Common identity and ordering fields for source-specific actions."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action_id: str = Field(min_length=1)
    source_query: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1)
    depends_on: list[str] = Field(default_factory=list)


class SchemaRetrievalAction(RetrievalAction):
    """A structured Schema lookup; it is not textual Paper evidence."""

    source_kind: Literal["schema"] = "schema"
    field_ids: list[str] = Field(default_factory=list)
    paper_ids: list[str] = Field(default_factory=list)


class WikiRetrievalAction(RetrievalAction):
    """A Base Wiki navigation or discovery lookup."""

    source_kind: Literal["wiki"] = "wiki"
    mode: Literal["lexical", "semantic"] = "lexical"
    discover_paper_ids: bool = False


class RagRetrievalAction(RetrievalAction):
    """A source-grounded textual retrieval action over Workspace Papers."""

    source_kind: Literal["rag"] = "rag"
    scope: Literal["workspace", "papers"] = "workspace"
    paper_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_paper_scope(self) -> "RagRetrievalAction":
        if self.scope == "papers" and not self.paper_ids and not self.depends_on:
            raise ValueError(
                "paper-scoped RAG requires paper_ids or a discovery dependency"
            )
        if self.scope == "workspace" and self.paper_ids:
            raise ValueError("workspace-scoped RAG must not specify paper_ids")
        return self


RetrievalActionContract = Annotated[
    SchemaRetrievalAction | WikiRetrievalAction | RagRetrievalAction,
    Field(discriminator="source_kind"),
]


class RetrievalStrategy(BaseModel):
    """Validated ordered source actions for exactly one ``ResearchQuery``."""

    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1)
    actions: list[RetrievalActionContract] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_ordering(self) -> "RetrievalStrategy":
        action_ids = [action.action_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("retrieval action_ids must be unique")
        known_ids: set[str] = set()
        for action in self.actions:
            unknown = set(action.depends_on) - known_ids
            if unknown:
                raise ValueError(
                    "retrieval action dependencies must reference earlier actions: "
                    f"{sorted(unknown)!r}"
                )
            known_ids.add(action.action_id)
        return self


class SchemaResult(BaseModel):
    """Structured Schema output retained without conversion to evidence."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    paper_id: str | None = Field(default=None, min_length=1)
    field_id: str = Field(min_length=1)
    value: object | None = None
    provenance: dict[str, object] = Field(default_factory=dict)


class WikiNavigationResult(BaseModel):
    """Wiki navigation/discovery output retained without conversion to evidence."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    discovered_paper_ids: list[str] = Field(default_factory=list)
    navigation: dict[str, object] = Field(default_factory=dict)


class RetrievalDiagnostic(BaseModel):
    """A non-semantic operational diagnostic for one retrieval action."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action_id: str | None = Field(default=None, min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    status: Literal["info", "skipped", "degraded", "failed"] = "info"
    details: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "RagRetrievalAction",
    "ResearchQuery",
    "RetrievalAction",
    "RetrievalActionContract",
    "RetrievalDiagnostic",
    "RetrievalStrategy",
    "SchemaResult",
    "SchemaRetrievalAction",
    "WikiNavigationResult",
    "WikiRetrievalAction",
]
