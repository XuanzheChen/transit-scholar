from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
import json
from pathlib import Path
from types import MappingProxyType

from ..knowledge_evolution.models import AgenticWikiEntry


class AgenticWikiStore:
    """In-memory, Workspace-isolated page store for promoted knowledge."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._entries: dict[str, AgenticWikiEntry] = {}
        self._storage_path = Path(storage_path) if storage_path is not None else None
        self._cycles: set[tuple[str, str]] = set()
        if self._storage_path and self._storage_path.exists():
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            for item in payload.get("entries", []):
                self._entries[item["entry_id"]] = AgenticWikiEntry.model_validate(item)
            self._cycles = {tuple(item) for item in payload.get("cycles", [])}

    @classmethod
    def for_workspace(cls, workspace_id: str, *, base_dir: str | Path | None = None) -> "AgenticWikiStore":
        from ..storage.paths import workspace_layout
        layout = workspace_layout(workspace_id, base_dir=base_dir)
        return cls(storage_path=layout.derived_dir / "agentic_wiki.json")

    @property
    def entries(self):
        return self._entries if self._storage_path is None else MappingProxyType(self._entries)

    def _persist(self) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(json.dumps({
            "entries": [entry.model_dump(mode="json") for entry in self._entries.values()],
            "cycles": [list(item) for item in sorted(self._cycles)],
        }, sort_keys=True), encoding="utf-8")

    def has_promotion_cycle(self, workspace_id: str, agent_run_id: str) -> bool:
        return self._storage_path is not None and (workspace_id, agent_run_id) in self._cycles

    def mark_promotion_cycle(self, workspace_id: str, agent_run_id: str) -> None:
        self._cycles.add((workspace_id, agent_run_id))
        self._persist()

    def _check(self, workspace_id: str) -> None:
        if not workspace_id:
            raise ValueError("workspace_id is required")

    def put(self, entry: AgenticWikiEntry, workspace_id: str | None = None) -> AgenticWikiEntry:
        workspace_id = workspace_id or entry.workspace_id
        self._check(workspace_id)
        if entry.workspace_id != workspace_id:
            raise PermissionError("entry belongs to another Workspace")
        self._entries[entry.entry_id] = entry
        self._persist()
        return entry

    def get(self, entry_id: str, workspace_id: str) -> AgenticWikiEntry:
        self._check(workspace_id)
        entry = self._entries.get(entry_id)
        if entry is None or entry.workspace_id != workspace_id:
            raise PermissionError("entry is not accessible in this Workspace")
        return entry

    def list(self, workspace_id: str, *, include_stale: bool = False) -> list[AgenticWikiEntry]:
        self._check(workspace_id)
        return [e for e in self._entries.values() if e.workspace_id == workspace_id and e.status != "superseded" and (include_stale or e.status == "active")]

    def delete_workspace(self, workspace_id: str) -> None:
        self._check(workspace_id)
        self._entries = {k: v for k, v in self._entries.items() if v.workspace_id != workspace_id}
        self._cycles = {key for key in self._cycles if key[0] != workspace_id}
        self._persist()

    def maintain(self, workspace_id: str, *, claims: Any = None, evidence: Any = None, papers: Any = None) -> list[AgenticWikiEntry]:
        """Run a deterministic provenance health pass for one Workspace.

        Resolver inputs may be iterables of identifiers or records/mappings. A
        record can expose ``id``/typed identifiers, accessibility and version
        flags. Missing resolver inputs are treated as unknown (not invalid).
        """
        self._check(workspace_id)
        def normalize(values: Any, kind: str) -> tuple[set[str] | None, dict[str, Any]]:
            if values is None:
                return None, {}
            result: set[str] = set(); records: dict[str, Any] = {}
            iterable = values.values() if isinstance(values, dict) else values
            for value in iterable:
                if isinstance(value, str):
                    result.add(value); continue
                getter = value.get if isinstance(value, dict) else lambda k, d=None: getattr(value, k, d)
                ident = getter(f"{kind}_id", getter("id"))
                if ident is None: continue
                ident = str(ident); result.add(ident); records[ident] = value
            return result, records
        claim_ids, claim_records = normalize(claims, "claim")
        evidence_ids, evidence_records = normalize(evidence, "evidence")
        paper_ids, paper_records = normalize(papers, "paper")
        now = datetime.now(timezone.utc)
        changed = []
        for entry in list(self._entries.values()):
            if entry.workspace_id != workspace_id or entry.status == "superseded":
                continue
            invalid = (claim_ids is not None and not set(entry.source_claim_ids).issubset(claim_ids)) or (evidence_ids is not None and not set(entry.evidence_refs).issubset(evidence_ids))
            for ref in entry.evidence_refs:
                record = evidence_records.get(ref)
                if record is not None:
                    getter = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
                    if getter("provenance_resolvable", True) is False or getter("source_accessible", getter("paper_accessible", True)) is False:
                        invalid = True
                    paper_ref = getter("paper_id", getter("source_paper_id"))
                    if paper_ids is not None and paper_ref is not None and str(paper_ref) not in paper_ids:
                        invalid = True
                    if getter("source_version_valid", getter("version_valid", True)) is False:
                        invalid = True
            if paper_ids is not None:
                for record in evidence_records.values():
                    getter = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
                    if any(str(getter(k)) in paper_ids for k in ("paper_id", "source_paper_id")):
                        continue
                # Entries may directly carry paper references in provenance.
                if any(ref not in paper_ids for ref in getattr(entry, "paper_ids", ())):
                    invalid = True
            for claim_id in entry.source_claim_ids:
                record = claim_records.get(claim_id)
                if record is not None:
                    getter = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
                    if getter("status", "accepted") in {"rejected", "ineligible"} or getter("eligible", True) is False:
                        invalid = True
            superseded_by = getattr(entry, "superseded_by", None)
            if superseded_by and not any(e.entry_id == superseded_by and e.workspace_id == workspace_id and e.status != "stale" for e in self._entries.values()):
                invalid = True
            if invalid and entry.status == "active":
                updated = entry.model_copy(update={"status": "stale", "updated_at": now})
                self._entries[entry.entry_id] = updated
                self._persist()
                changed.append(updated)
        return changed
