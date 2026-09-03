"""Workspace-scoped, bounded retrieval of episodic memory."""
from __future__ import annotations
import re
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
    def __init__(self, records: Iterable[EpisodicMemoryRecord] = ()) -> None:
        self._records: dict[tuple[str, str], EpisodicMemoryRecord] = {}
        for record in records:
            self.put(record)

    def put(self, record: EpisodicMemoryRecord) -> EpisodicMemoryRecord:
        existing = self._records.get(record.canonical_episode_key)
        if existing is not None and existing != record:
            raise ValueError("an AgentRun already has a canonical episodic memory record")
        self._records[record.canonical_episode_key] = record
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
