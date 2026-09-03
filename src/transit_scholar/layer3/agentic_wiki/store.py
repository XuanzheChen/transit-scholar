from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..knowledge_evolution.models import AgenticWikiEntry


class AgenticWikiStore:
    """Workspace-scoped repository for promoted Agentic Wiki pages.

    ``for_workspace`` is the production constructor. The unbound in-memory
    form remains useful as an explicit test double.
    """

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        bound_workspace_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        if workspace_id is not None:
            if bound_workspace_id is not None and bound_workspace_id != workspace_id:
                raise ValueError("workspace_id and bound_workspace_id disagree")
            bound_workspace_id = workspace_id
        if bound_workspace_id is not None:
            self._validate_workspace_id(bound_workspace_id)
        self.bound_workspace_id = bound_workspace_id
        self._entries: dict[str, AgenticWikiEntry] = {}
        self._storage_path = Path(storage_path) if storage_path is not None else None
        self._cycles: set[tuple[str, str]] = set()
        if self._storage_path is not None and self._storage_path.exists():
            self._load()

    @classmethod
    def for_workspace(
        cls,
        workspace_id: str,
        *,
        base_dir: str | Path | None = None,
    ) -> "AgenticWikiStore":
        from ..storage.paths import workspace_layout

        layout = workspace_layout(workspace_id, base_dir=base_dir)
        return cls(
            storage_path=layout.derived_dir / "agentic_wiki.json",
            bound_workspace_id=workspace_id,
        )

    @property
    def is_durable(self) -> bool:
        return self._storage_path is not None

    @property
    def entries(self) -> Mapping[str, AgenticWikiEntry]:
        """Return an inspection view without exposing a durable write surface."""
        return MappingProxyType(
            {key: value.model_copy(deep=True) for key, value in self._entries.items()}
        )

    def snapshot(self) -> Mapping[str, AgenticWikiEntry]:
        """Return an immutable, deep-copied inspection view of entries."""
        return MappingProxyType(
            {key: value.model_copy(deep=True) for key, value in self._entries.items()}
        )

    def _load(self) -> None:
        assert self._storage_path is not None
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Agentic Wiki persistence is unreadable: {self._storage_path}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Agentic Wiki persistence must contain a JSON object")
        persisted_workspace = payload.get("workspace_id")
        if (
            persisted_workspace is not None
            and self.bound_workspace_id is not None
            and persisted_workspace != self.bound_workspace_id
        ):
            raise PermissionError("Agentic Wiki persistence belongs to another Workspace")
        entries = payload.get("entries", [])
        cycles = payload.get("cycles", [])
        if not isinstance(entries, list) or not isinstance(cycles, list):
            raise ValueError("Agentic Wiki persistence has an invalid shape")
        for item in entries:
            entry = AgenticWikiEntry.model_validate(item)
            self._check_workspace(entry.workspace_id)
            self._entries[entry.entry_id] = entry
        for item in cycles:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("invalid promotion cycle record")
            workspace_id, run_id = str(item[0]), str(item[1])
            self._check_workspace(workspace_id)
            self._cycles.add((workspace_id, run_id))

    @staticmethod
    def _validate_workspace_id(workspace_id: str) -> None:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id is required")

    def _check_workspace(self, workspace_id: str) -> None:
        self._validate_workspace_id(workspace_id)
        if self.bound_workspace_id is not None and workspace_id != self.bound_workspace_id:
            raise PermissionError("Agentic Wiki repository is bound to another Workspace")

    def _payload(
        self,
        entries: Iterable[AgenticWikiEntry] | None = None,
        cycles: Iterable[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        values = list(self._entries.values()) if entries is None else list(entries)
        cycle_values = self._cycles if cycles is None else set(cycles)
        for entry in values:
            self._check_workspace(entry.workspace_id)
        for workspace_id, _ in cycle_values:
            self._check_workspace(workspace_id)
        return {
            "schema_version": 1,
            "workspace_id": self.bound_workspace_id,
            "entries": [entry.model_dump(mode="json") for entry in values],
            "cycles": [list(item) for item in sorted(cycle_values)],
        }

    def _persist_payload(self, payload: dict[str, Any]) -> None:
        if self._storage_path is None:
            return
        target = self._storage_path
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        temporary_name: str | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
            temporary_name = None
            try:
                directory_fd = os.open(target.parent, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    try:
                        os.fsync(directory_fd)
                    except OSError:
                        pass
                finally:
                    os.close(directory_fd)
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def _persist(self) -> None:
        self._persist_payload(self._payload())

    def has_promotion_cycle(self, workspace_id: str, agent_run_id: str) -> bool:
        self._check_workspace(workspace_id)
        return self.is_durable and (workspace_id, str(agent_run_id)) in self._cycles

    def mark_promotion_cycle(self, workspace_id: str, agent_run_id: str) -> None:
        self._check_workspace(workspace_id)
        cycles = set(self._cycles)
        cycles.add((workspace_id, str(agent_run_id)))
        self._persist_payload(self._payload(cycles=cycles))
        self._cycles = cycles

    def commit_promotion(
        self,
        entries: Iterable[AgenticWikiEntry],
        workspace_id: str,
        agent_run_id: str,
    ) -> None:
        """Persist entry mutations and cycle completion as one durable commit."""

        self._check_workspace(workspace_id)
        run_key = (workspace_id, str(agent_run_id))
        if self.is_durable and run_key in self._cycles:
            return
        next_entries = dict(self._entries)
        for entry in entries:
            self._check_workspace(entry.workspace_id)
            if entry.workspace_id != workspace_id:
                raise PermissionError("entry belongs to another Workspace")
            next_entries[entry.entry_id] = entry
        next_cycles = set(self._cycles)
        next_cycles.add(run_key)
        self._persist_payload(self._payload(next_entries.values(), next_cycles))
        self._entries = next_entries
        self._cycles = next_cycles

    def put(self, entry: AgenticWikiEntry, workspace_id: str | None = None) -> AgenticWikiEntry:
        requested_workspace = entry.workspace_id if workspace_id is None else workspace_id
        self._check_workspace(requested_workspace)
        if entry.workspace_id != requested_workspace:
            raise PermissionError("entry belongs to another Workspace")
        next_entries = dict(self._entries)
        next_entries[entry.entry_id] = entry
        self._persist_payload(self._payload(next_entries.values()))
        self._entries = next_entries
        return entry

    update = put

    def delete(self, entry_id: str, workspace_id: str) -> AgenticWikiEntry:
        """Delete one entry after validating the bound Workspace."""
        self._check_workspace(workspace_id)
        entry = self._entries.get(entry_id)
        if entry is None or entry.workspace_id != workspace_id:
            raise PermissionError("entry is not accessible in this Workspace")
        next_entries = dict(self._entries)
        removed = next_entries.pop(entry_id)
        self._persist_payload(self._payload(next_entries.values()))
        self._entries = next_entries
        return removed.model_copy(deep=True)

    def get(self, entry_id: str, workspace_id: str) -> AgenticWikiEntry:
        self._check_workspace(workspace_id)
        entry = self._entries.get(entry_id)
        if entry is None or entry.workspace_id != workspace_id:
            raise PermissionError("entry is not accessible in this Workspace")
        return entry.model_copy(deep=True)

    def list(self, workspace_id: str, *, include_stale: bool = False) -> list[AgenticWikiEntry]:
        self._check_workspace(workspace_id)
        return [
            entry.model_copy(deep=True)
            for entry in self._entries.values()
            if entry.workspace_id == workspace_id
            and entry.status != "superseded"
            and (include_stale or entry.status == "active")
        ]

    def find_matching(
        self,
        workspace_id: str,
        title: str,
        *,
        include_stale: bool = True,
    ) -> AgenticWikiEntry | None:
        """Return the best deterministic same-topic page, when available."""

        self._check_workspace(workspace_id)
        query_terms = self._topic_terms(title)
        if not query_terms:
            return None
        candidates: list[tuple[float, AgenticWikiEntry]] = []
        for entry in self.list(workspace_id, include_stale=include_stale):
            terms = self._topic_terms(entry.title)
            if not terms:
                continue
            score = len(query_terms & terms) / max(len(query_terms | terms), 1)
            if entry.title.casefold().strip() == title.casefold().strip():
                score = 1.0
            if score >= 0.6:
                candidates.append((score, entry))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1].entry_id))
        return candidates[0][1]

    @staticmethod
    def _topic_terms(value: str) -> set[str]:
        return set(re.findall(r"\w+", str(value).casefold()))

    def delete_workspace(self, workspace_id: str) -> None:
        self._check_workspace(workspace_id)
        next_entries = {
            key: value for key, value in self._entries.items() if value.workspace_id != workspace_id
        }
        next_cycles = {key for key in self._cycles if key[0] != workspace_id}
        self._persist_payload(self._payload(next_entries.values(), next_cycles))
        self._entries = next_entries
        self._cycles = next_cycles

    def maintain(
        self,
        workspace_id: str,
        *,
        claims: Any = None,
        evidence: Any = None,
        papers: Any = None,
        claim_evidence_links: Any = None,
        source_versions: Any = None,
    ) -> list[AgenticWikiEntry]:
        """Run a deterministic provenance health pass for one Workspace."""

        self._check_workspace(workspace_id)

        def normalize(values: Any, kind: str) -> tuple[set[str] | None, dict[str, Any]]:
            if values is None:
                return None, {}
            result: set[str] = set()
            records: dict[str, Any] = {}
            if isinstance(values, dict):
                iterable = [values] if f"{kind}_id" in values or "id" in values else values.values()
            else:
                if isinstance(values, (str, bytes)):
                    iterable = [values]
                elif hasattr(values, f"{kind}_id") or hasattr(values, "id"):
                    iterable = [values]
                else:
                    try:
                        iter(values)
                    except TypeError:
                        iterable = [values]
                    else:
                        iterable = values
            for value in iterable:
                if isinstance(value, str):
                    result.add(value)
                    continue
                getter = value.get if isinstance(value, dict) else lambda key, default=None: getattr(value, key, default)
                ident = getter(f"{kind}_id", getter("id"))
                if ident is None:
                    continue
                ident = str(ident)
                result.add(ident)
                records[ident] = value
            return result, records

        claim_ids, claim_records = normalize(claims, "claim")
        evidence_ids, evidence_records = normalize(evidence, "evidence")
        paper_ids, paper_records = normalize(papers, "paper")
        if source_versions is not None and isinstance(source_versions, dict):
            for paper_id, version in source_versions.items():
                if str(paper_id) in paper_records:
                    paper_records[str(paper_id)] = {**(paper_records[str(paper_id)] if isinstance(paper_records[str(paper_id)], dict) else {}), "current_source_version": version}
                elif paper_ids is not None:
                    paper_records[str(paper_id)] = {"current_source_version": version}
                    paper_ids.add(str(paper_id))
        link_records = self._normalize_links(claim_evidence_links)
        now = datetime.now(timezone.utc)
        changed: list[AgenticWikiEntry] = []
        updates: dict[str, AgenticWikiEntry] = {}
        for entry in self._entries.values():
            if entry.workspace_id != workspace_id or entry.status == "superseded":
                continue
            invalid = (
                claim_ids is not None and not set(entry.source_claim_ids).issubset(claim_ids)
            ) or (
                evidence_ids is not None and not set(entry.evidence_refs).issubset(evidence_ids)
            )
            for ref in entry.evidence_refs:
                record = evidence_records.get(ref)
                if record is None:
                    continue
                getter = record.get if isinstance(record, dict) else lambda key, default=None: getattr(record, key, default)
                if self._provenance_flag(record, "provenance_resolvable", True) is False or self._provenance_flag(
                    record, "source_accessible", self._provenance_flag(record, "paper_accessible", True)
                ) is False:
                    invalid = True
                if self._provenance_flag(record, "source_version_valid", self._provenance_flag(record, "version_valid", True)) is False:
                    invalid = True
                paper_ref = getter("paper_id", getter("source_paper_id"))
                if paper_ref is None:
                    locator = getter("locator")
                    if locator is not None:
                        locator_getter = locator.get if isinstance(locator, dict) else lambda key, default=None: getattr(locator, key, default)
                        paper_ref = locator_getter("paper_id")
                if paper_ids is not None and paper_ref is not None and str(paper_ref) not in paper_ids:
                    invalid = True
                if self._provenance_flag(record, "paper_id", None) is not None and paper_ids is not None:
                    if str(self._provenance_flag(record, "paper_id", None)) not in paper_ids:
                        invalid = True
                if paper_ref is not None and paper_records:
                    paper_record = paper_records.get(str(paper_ref))
                    if paper_record is not None:
                        evidence_version = self._provenance_value(
                            record, "canonical_source_version", "parse_run_id", "source_version", "version", "source_version_id"
                        )
                        if evidence_version is None:
                            locator = getter("locator")
                            if locator is not None:
                                evidence_version = self._provenance_value(
                                    locator, "canonical_source_version", "parse_run_id", "source_version", "version", "source_version_id"
                                )
                        paper_version = self._provenance_value(
                            paper_record,
                            "current_source_version", "canonical_source_version", "parse_run_id",
                            "source_version",
                            "version",
                            "version_id",
                        )
                        if (
                            evidence_version is not None
                            and paper_version is not None
                            and str(evidence_version) != str(paper_version)
                        ):
                            invalid = True
            if paper_ids is not None:
                for paper_ref in getattr(entry, "paper_ids", ()):
                    if str(paper_ref) not in paper_ids:
                        invalid = True
            for claim_id in entry.source_claim_ids:
                record = claim_records.get(claim_id)
                if record is not None:
                    getter = record.get if isinstance(record, dict) else lambda key, default=None: getattr(record, key, default)
                    if getter("workspace_id", workspace_id) != workspace_id:
                        invalid = True
                    claim_status = str(getter("status", "accepted")).casefold()
                    if claim_status not in {"accepted", "supported"} or getter("eligible", True) is False:
                        invalid = True
            for evidence_id in entry.evidence_refs:
                record = evidence_records.get(evidence_id)
                if record is not None:
                    getter = record.get if isinstance(record, dict) else lambda key, default=None: getattr(record, key, default)
                    if getter("workspace_id", workspace_id) != workspace_id:
                        invalid = True
            if link_records is not None:
                supported = {
                    (claim_id, evidence_id)
                    for claim_id, evidence_id, relation in link_records
                    if relation == "supports"
                }
                if any(
                    not any((claim_id, evidence_id) in supported for evidence_id in entry.evidence_refs)
                    for claim_id in entry.source_claim_ids
                ):
                    invalid = True
            superseded_by = getattr(entry, "superseded_by", None)
            if superseded_by and (superseded_by == entry.entry_id or not any(
                other.entry_id == superseded_by
                and other.workspace_id == workspace_id
                and other.status == "active"
                for other in self._entries.values()
            )):
                invalid = True
            if invalid and entry.status == "active":
                updated = entry.model_copy(update={"status": "stale", "updated_at": now})
                updates[entry.entry_id] = updated
                changed.append(updated.model_copy(deep=True))
        for entry in self._entries.values():
            target = getattr(entry, "superseded_by", None)
            if (
                entry.workspace_id == workspace_id
                and entry.status == "active"
                and target in updates
                and entry.entry_id not in updates
            ):
                updated = entry.model_copy(update={"status": "stale", "updated_at": now})
                updates[entry.entry_id] = updated
                changed.append(updated.model_copy(deep=True))
        if updates:
            next_entries = dict(self._entries)
            next_entries.update(updates)
            self._persist_payload(self._payload(next_entries.values()))
            self._entries = next_entries
        return changed

    @staticmethod
    def _provenance_flag(record: Any, name: str, default: Any = None) -> Any:
        """Read a health flag from an evidence record or nested provenance."""
        getter = record.get if isinstance(record, dict) else lambda key, fallback=None: getattr(record, key, fallback)
        value = getter(name, None)
        if value is not None:
            return value
        for container_name in (
            "retrieval_provenance",
            "source_metadata",
            "paper_provenance",
            "locator",
            "provenance",
        ):
            container = getter(container_name, None)
            if isinstance(container, dict) and name in container:
                return container[name]
            if isinstance(container, dict):
                aliases = {
                    "provenance_resolvable": ("resolvable", "valid"),
                    "source_accessible": ("accessible",),
                    "paper_accessible": ("accessible",),
                    "source_version_valid": ("version_valid", "resolvable", "valid"),
                    "version_valid": ("valid", "resolvable"),
                }
                for alias in aliases.get(name, ()):
                    if alias in container:
                        return container[alias]
        return default

    @staticmethod
    def _provenance_value(record: Any, *names: str) -> Any:
        getter = record.get if isinstance(record, dict) else lambda key, fallback=None: getattr(record, key, fallback)
        for name in names:
            value = getter(name, None)
            if value is not None:
                return value
            for container_name in (
                "retrieval_provenance",
                "source_metadata",
                "paper_provenance",
                "locator",
                "provenance",
            ):
                container = getter(container_name, None)
                if isinstance(container, dict) and name in container:
                    return container[name]
        return None

    @staticmethod
    def _normalize_links(values: Any) -> set[tuple[str, str, str]] | None:
        if values is None:
            return None
        if isinstance(values, dict):
            if any(key in values for key in ("claim_id", "evidence_id")):
                values = [values]
            else:
                values = list(values.values())
        elif isinstance(values, (str, bytes)) or hasattr(values, "model_dump"):
            values = [values]
        else:
            try:
                values = list(values)
            except TypeError:
                values = [values]
        links: set[tuple[str, str, str]] = set()
        for value in values:
            getter = value.get if isinstance(value, dict) else lambda key, default=None: getattr(value, key, default)
            claim_id = getter("claim_id", None)
            evidence_id = getter("evidence_id", None)
            if claim_id is None or evidence_id is None:
                continue
            links.add((str(claim_id), str(evidence_id), str(getter("relation", "supports")).casefold()))
        return links
