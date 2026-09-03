"""Workspace-scoped, bounded retrieval of episodic memory."""
from __future__ import annotations
import re
import json
import os
import tempfile
import errno
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable
from .models import EpisodicMemoryRecord, MemorySourceKind

_UNSUPPORTED_DIRECTORY_DURABILITY_ERRNOS = frozenset({
    errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS,
})

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
        return cls(storage_path=layout.derived_dir / "episodic_memory.json", bound_workspace_id=workspace_id)

    def __init__(self, records: Iterable[EpisodicMemoryRecord] = (), *, storage_path: str | Path | None = None,
                 bound_workspace_id: str | None = None) -> None:
        self._records: dict[tuple[str, str], EpisodicMemoryRecord] = {}
        self._storage_path = Path(storage_path) if storage_path is not None else None
        self.bound_workspace_id = bound_workspace_id
        if self.bound_workspace_id is not None and not str(self.bound_workspace_id).strip():
            raise ValueError("bound_workspace_id must be a non-empty string")
        if self._storage_path and self._storage_path.exists():
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("episodic memory persistence must contain a JSON list")
            for item in payload:
                self.put(EpisodicMemoryRecord.model_validate(item), _persist=False)
        for record in records:
            self.put(record)

    def _persist(self, records: dict[tuple[str, str], EpisodicMemoryRecord] | None = None) -> None:
        if self._storage_path is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        source_records = self._records if records is None else records
        payload = [record.model_dump(mode="json") for record in source_records.values()]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self._storage_path.name}.", suffix=".tmp",
            dir=str(self._storage_path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self._storage_path)
            try:
                directory_fd = os.open(self._storage_path.parent, os.O_RDONLY)
            except OSError as error:
                directory_open_unsupported = error.errno in _UNSUPPORTED_DIRECTORY_DURABILITY_ERRNOS
                if os.name == "nt" and error.errno == errno.EACCES:
                    directory_open_unsupported = True
                if not directory_open_unsupported:
                    raise
                directory_fd = None
            if directory_fd is not None:
                try:
                    try:
                        os.fsync(directory_fd)
                    except OSError as error:
                        if error.errno not in _UNSUPPORTED_DIRECTORY_DURABILITY_ERRNOS:
                            raise
                finally:
                    os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
    def _check_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if self.bound_workspace_id is not None and workspace_id != self.bound_workspace_id:
            raise PermissionError("episodic memory repository is bound to another Workspace")

    def put(self, record: EpisodicMemoryRecord, *, _persist: bool = True) -> EpisodicMemoryRecord:
        self._check_workspace(record.workspace_id)
        existing = self._records.get(record.canonical_episode_key)
        if existing is not None and existing != record:
            raise ValueError("an AgentRun already has a canonical episodic memory record")
        next_records = dict(self._records)
        next_records[record.canonical_episode_key] = record
        if _persist:
            self._persist(next_records)
        self._records = next_records
        return record

    def get(self, memory_id: str, *, workspace_id: str) -> EpisodicMemoryRecord:
        self._check_workspace(workspace_id)
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
        self._check_workspace(workspace_id)
        return self._records.get((workspace_id, agent_run_id))

    def list(self, *, workspace_id: str) -> tuple[EpisodicMemoryRecord, ...]:
        self._check_workspace(workspace_id)
        return tuple(
            record
            for (workspace, _), record in self._records.items()
            if workspace == workspace_id
        )

    def delete_workspace(self, workspace_id: str) -> None:
        self._check_workspace(workspace_id)
        next_records = {
            key: record
            for key, record in self._records.items()
            if record.workspace_id != workspace_id
        }
        self._persist(next_records)
        self._records = next_records

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
