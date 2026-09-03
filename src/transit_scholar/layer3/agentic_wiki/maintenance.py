"""Deterministic provenance maintenance for Agentic Wiki entries."""
from __future__ import annotations

import inspect
from typing import Any

from .store import AgenticWikiStore


class AgenticWikiMaintenance:
    """Runs one lightweight, Workspace-scoped health pass before Session use."""

    @classmethod
    def for_workspace(
        cls,
        workspace_id: str,
        *,
        base_dir: str | None = None,
        claims: Any = None,
        evidence: Any = None,
        papers: Any = None,
        claim_evidence_links: Any = None,
        workspace_service: Any = None,
        ledger_service: Any = None,
        execution_service: Any = None,
        session: Any = None,
        workspace_reader: Any = None,
        claim_reader: Any = None,
        evidence_reader: Any = None,
        paper_reader: Any = None,
        claims_resolver: Any = None,
        evidence_resolver: Any = None,
        papers_resolver: Any = None,
        links_resolver: Any = None,
        source_version_reader: Any = None,
    ) -> "AgenticWikiMaintenance":
        """Compose maintenance against the durable Workspace repository.

        ``workspace_service`` and ``ledger_service`` are the production
        composition seam.  When supplied, the maintenance pass resolves the
        current Workspace membership and all L3S4 records itself; callers do
        not need to provide test-only collections on every Session start.
        """
        from .store import AgenticWikiStore

        return cls(
            AgenticWikiStore.for_workspace(workspace_id, base_dir=base_dir),
            claims=claims,
            evidence=evidence,
            papers=papers,
            claim_evidence_links=claim_evidence_links,
            workspace_service=workspace_service,
            ledger_service=ledger_service,
            execution_service=execution_service,
            session=session,
            workspace_reader=workspace_reader,
            claim_reader=claim_reader,
            evidence_reader=evidence_reader,
            paper_reader=paper_reader,
            claims_resolver=claims_resolver,
            evidence_resolver=evidence_resolver,
            papers_resolver=papers_resolver,
            links_resolver=links_resolver,
            source_version_reader=source_version_reader,
        )

    def __init__(self, store: AgenticWikiStore, *, claims: Any = None,
                 evidence: Any = None, papers: Any = None,
                 claim_evidence_links: Any = None,
                 workspace_service: Any = None, ledger_service: Any = None,
                 execution_service: Any = None, session: Any = None,
                 workspace_reader: Any = None, claim_reader: Any = None,
                 evidence_reader: Any = None, paper_reader: Any = None,
                 claims_resolver: Any = None, evidence_resolver: Any = None,
                 papers_resolver: Any = None, links_resolver: Any = None,
                 source_version_reader: Any = None) -> None:
        self.store = store
        self.claims = claims
        self.evidence = evidence
        self.papers = papers
        self.claim_evidence_links = (
            claim_evidence_links if claim_evidence_links is not None else links_resolver
        )
        self.workspace_service = workspace_service or workspace_reader
        self.ledger_service = ledger_service
        self.execution_service = execution_service
        self.session = session or getattr(ledger_service, "session", None)
        self.claim_reader = claim_reader if claim_reader is not None else claims_resolver
        self.evidence_reader = (
            evidence_reader if evidence_reader is not None else evidence_resolver
        )
        self.paper_reader = paper_reader if paper_reader is not None else papers_resolver
        self.source_version_reader = source_version_reader
        self.calls = 0

    def __call__(self, workspace_id: str, **overrides: Any):
        self.calls += 1
        claims = self._resolve(overrides.get("claims", self.claims), workspace_id)
        evidence = self._resolve(overrides.get("evidence", self.evidence), workspace_id)
        papers = self._resolve(overrides.get("papers", self.papers), workspace_id)
        links = self._resolve(
            overrides.get("claim_evidence_links", self.claim_evidence_links), workspace_id
        )
        source_versions = self._resolve(self.source_version_reader, workspace_id)
        if source_versions is None:
            source_versions = self._authoritative_source_versions(workspace_id)
        if claims is None:
            claims = self._authoritative_records("claims", workspace_id)
        if evidence is None:
            evidence = self._authoritative_records("evidence", workspace_id)
        if papers is None:
            papers = self._authoritative_papers(workspace_id)
        if links is None:
            links = self._authoritative_links(workspace_id, claims)
        try:
            maintain_kwargs = dict(claims=claims, evidence=evidence, papers=papers, claim_evidence_links=links)
            if source_versions is not None:
                maintain_kwargs["source_versions"] = source_versions
            return self.store.maintain(workspace_id, **maintain_kwargs)
        except TypeError as exc:
            if "claim_evidence_links" not in str(exc):
                raise
            return self.store.maintain(
                workspace_id, claims=claims, evidence=evidence, papers=papers
            )

    @staticmethod
    def _resolve(source: Any, workspace_id: str) -> Any:
        if source is None:
            return None
        if callable(source):
            return AgenticWikiMaintenance._invoke_workspace(source, workspace_id)
        return source

    @staticmethod
    def _invoke_workspace(method: Any, workspace_id: str) -> Any:
        """Adapt supported reader signatures without swallowing implementation errors."""
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(workspace_id)
        parameters = tuple(signature.parameters.values())
        if "workspace_id" in signature.parameters:
            parameter = signature.parameters["workspace_id"]
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
                return method(workspace_id)
            return method(workspace_id=workspace_id)
        if any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters):
            return method(workspace_id=workspace_id)
        positional = [
            parameter
            for parameter in parameters
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if positional:
            return method(workspace_id)
        return method()

    def _authoritative_papers(self, workspace_id: str) -> Any:
        reader = self.paper_reader
        if reader is not None:
            return self._resolve(reader, workspace_id)
        service = self.workspace_service
        if service is None:
            return None
        for name in (
            "list_memberships",
            "list_papers",
            "list_workspace_papers",
            "papers_for_workspace",
        ):
            method = getattr(service, name, None)
            if method is None:
                continue
            return self._invoke_workspace(method, workspace_id)
        return None

    def _authoritative_source_versions(self, workspace_id: str) -> Any:
        service = self.workspace_service
        if service is None:
            return None
        for name in ("current_source_versions", "canonical_source_versions", "paper_source_versions"):
            method = getattr(service, name, None)
            if method is not None:
                return self._invoke_workspace(method, workspace_id)
        return None

    def _authoritative_records(self, kind: str, workspace_id: str) -> Any:
        reader = self.claim_reader if kind == "claims" else self.evidence_reader
        if reader is not None:
            return self._resolve(reader, workspace_id)
        service = self.ledger_service
        if service is None:
            return None
        method_names = {
            "claims": ("list_workspace_claims", "claims_for_workspace"),
            "evidence": ("list_workspace_evidence", "evidence_for_workspace"),
        }
        for name in method_names[kind]:
            method = getattr(service, name, None)
            if method is None:
                continue
            signature = inspect.signature(method)
            if "research_session_id" in signature.parameters and "workspace_id" not in signature.parameters:
                records: list[Any] = []
                for session_id in self._session_ids(workspace_id):
                    parameters = signature.parameters
                    if "research_session_id" in parameters:
                        records.extend(method(research_session_id=session_id))
                return records
            return self._invoke_workspace(method, workspace_id)

        method = getattr(service, f"list_{kind}", None)
        if method is not None:
            signature = inspect.signature(method)
            if "research_session_id" in signature.parameters and "workspace_id" not in signature.parameters:
                records: list[Any] = []
                for session_id in self._session_ids(workspace_id):
                    records.extend(method(research_session_id=session_id))
                return records
            return self._invoke_workspace(method, workspace_id)

        session_ids = self._session_ids(workspace_id)
        if method is None:
            return None
        records: list[Any] = []
        for session_id in session_ids:
            try:
                records.extend(method(research_session_id=session_id))
            except TypeError:
                try:
                    records.extend(method(session_id))
                except TypeError:
                    return None
        return records

    def _authoritative_links(self, workspace_id: str, claims: Any) -> Any:
        service = self.ledger_service
        if service is None:
            return None
        for name in ("list_workspace_claim_evidence", "claim_evidence_for_workspace"):
            method = getattr(service, name, None)
            if method is not None:
                return self._invoke_workspace(method, workspace_id)
        getter = getattr(service, "get_claim_evidence", None)
        if getter is None:
            return None
        claim_values = self._as_values(claims)
        links: list[Any] = []
        for claim in claim_values:
            claim_id = self._field(claim, "claim_id", self._field(claim, "id"))
            session_id = self._field(claim, "research_session_id")
            if claim_id is None:
                continue
            signature = inspect.signature(getter)
            kwargs = {"claim_id": claim_id}
            if session_id is not None and "research_session_id" in signature.parameters:
                kwargs["research_session_id"] = session_id
            links.extend(getter(**kwargs))
        return links

    def _session_ids(self, workspace_id: str) -> tuple[str, ...]:
        execution = self.execution_service
        if execution is not None:
            for name in ("list_workspace_research_sessions", "list_research_sessions"):
                method = getattr(execution, name, None)
                if method is None:
                    continue
                try:
                    values = method(workspace_id)
                except Exception:
                    try:
                        values = method(workspace_id=workspace_id)
                    except Exception:
                        continue
                if values is None:
                    continue
                ids = [self._field(value, "research_session_id", self._field(value, "id")) for value in values]
                return tuple(str(value) for value in ids if value)

        db_session = self.session or getattr(execution, "session", None) or getattr(
            self.workspace_service, "session", None
        )
        if db_session is None:
            return ()
        try:
            from sqlalchemy import select
            from transit_scholar.db.models import AgentRun, ResearchSession

            rows = db_session.execute(
                select(ResearchSession.id)
                .join(AgentRun, ResearchSession.agent_run_id == AgentRun.id)
                .where(AgentRun.workspace_id == workspace_id)
                .order_by(ResearchSession.created_at, ResearchSession.id)
            ).scalars().all()
        except Exception:
            return ()
        return tuple(str(value) for value in rows)

    @staticmethod
    def _field(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    @staticmethod
    def _as_values(values: Any) -> list[Any]:
        if values is None:
            return []
        if isinstance(values, dict):
            if any(key in values for key in ("id", "claim_id", "evidence_id")):
                return [values]
            return list(values.values())
        if isinstance(values, (str, bytes)) or hasattr(values, "model_dump"):
            return [values]
        try:
            return list(values)
        except TypeError:
            return [values]


def maintain_before_session_use(
    store: AgenticWikiStore | str,
    workspace_id: str | None = None,
    *,
    base_dir: str | None = None,
    **resolvers: Any,
):
    """Convenience boundary; never invokes an LLM or mutates other Workspaces."""
    if isinstance(store, str):
        workspace_id = workspace_id or store
        store = AgenticWikiStore.for_workspace(store, base_dir=base_dir)
    if workspace_id is None:
        workspace_id = store.bound_workspace_id
    if workspace_id is None:
        raise ValueError("workspace_id is required")
    return AgenticWikiMaintenance(store, **resolvers)(workspace_id)


__all__ = ["AgenticWikiMaintenance", "maintain_before_session_use"]
