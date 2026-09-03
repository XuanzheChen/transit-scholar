"""Workspace-scoped, bounded retrieval of episodic memory."""
from __future__ import annotations
import re
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable
from .models import EpisodicMemoryRecord, MemorySourceKind

@dataclass(frozen=True)
class EpisodicMemoryCandidate:
    memory_id: str
    workspace_id: str
    agent_run_id: str
    relevance: float
    source_kind: str = MemorySourceKind.EPISODIC_MEMORY.value
    auxiliary: bool = True
    record: EpisodicMemoryRecord | None = None

class EpisodicMemoryStore:
    @classmethod
    def for_workspace(cls, workspace_id: str, *, base_dir: str | Path | None = None) -> "EpisodicMemoryStore":
        from ..storage.paths import workspace_layout
        layout = workspace_layout(workspace_id, base_dir=base_dir)
        return cls(storage_path=layout.derived_dir / "episodic_memory.json")

    def __init__(self, records: Iterable[EpisodicMemoryRecord] = (), *, storage_path: str | Path | None = None) -> None:
        self._records: dict[tuple[str, str], EpisodicMemoryRecord] = {}
        self._storage_path = Path(storage_path) if storage_path is not None else None
        if self._storage_path and self._storage_path.exists():
            for item in json.loads(self._storage_path.read_text(encoding="utf-8")):
                self.put(EpisodicMemoryRecord.model_validate(item), _persist=False)
        for record in records:
            self.put(record)

    def _persist(self) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [record.model_dump(mode="json") for record in self._records.values()]
        self._storage_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def put(self, record: EpisodicMemoryRecord, *, _persist: bool = True) -> EpisodicMemoryRecord:
        existing = self._records.get(record.canonical_episode_key)
        if existing is not None and existing != record:
            raise ValueError("an AgentRun already has a canonical episodic memory record")
        self._records[record.canonical_episode_key] = record
        if _persist:
            self._persist()
        return record

    def get(self, memory_id: str, *, workspace_id: str) -> EpisodicMemoryRecord:
        for record in self._records.values():
            if record.memory_id == memory_id:
                if record.workspace_id != workspace_id:
                    raise PermissionError("episodic memory belongs to another Workspace")
                return record
        raise PermissionError("episodic memory is not accessible in this Workspace")

    def get_for_run(
        self,
        *,
        workspace_id: str,
        agent_run_id: str,
    ) -> EpisodicMemoryRecord | None:
        return self._records.get((workspace_id, agent_run_id))

    def list(self, *, workspace_id: str) -> tuple[EpisodicMemoryRecord, ...]:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        return tuple(
            record
            for (workspace, _), record in self._records.items()
            if workspace == workspace_id
        )

    def delete_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        self._records = {
            key: record
            for key, record in self._records.items()
            if record.workspace_id != workspace_id
        }
        self._persist()

class EpisodicMemoryRetriever:
    def __init__(self, store: EpisodicMemoryStore) -> None: self.store = store
    def retrieve(self, *, workspace_id: str, query: str, top_k: int = 5) -> tuple[EpisodicMemoryCandidate, ...]:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if top_k <= 0: return ()
        terms = set(re.findall(r"\w+", query.casefold())); scored = []
        for record in self.store.list(workspace_id=workspace_id):
            words = set(re.findall(r"\w+", " ".join((record.user_goal_raw, record.goal_summary, record.research_summary, record.unresolved_summary)).casefold()))
            score = len(terms & words) / max(len(terms), 1)
            if score > 0 or not terms: scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].created_at, item[1].memory_id))
        return tuple(EpisodicMemoryCandidate(memory_id=r.memory_id, workspace_id=r.workspace_id, agent_run_id=r.agent_run_id, relevance=s, record=r) for s, r in scored[:top_k])

__all__ = ["EpisodicMemoryCandidate", "EpisodicMemoryStore", "EpisodicMemoryRetriever"]
