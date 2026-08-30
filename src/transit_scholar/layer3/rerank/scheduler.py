"""Deterministic, provider-independent scheduling for LLM fine reranking.

This module deliberately decides only *how many* candidates each comparison
must eliminate.  A provider remains responsible for ranking a supplied group.
Keeping those concerns separate makes every elimination budget auditable before
an LLM is called.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from hashlib import blake2b
from math import floor
from typing import Any, Sequence


@dataclass(frozen=True)
class LLMFineRerankConfig:
    """Limits controlling multi-round LLM fine reranking."""

    entry_candidates: int = 50
    group_size: int = 10
    max_rounds: int = 3
    final_top_k: int = 10
    seed: int = 0

    def __post_init__(self) -> None:
        for name in ("entry_candidates", "group_size", "max_rounds", "final_top_k"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class EliminationRound:
    """The count-based budget for one elimination round."""

    round_number: int
    survivor_count: int
    elimination_quota: int


@dataclass(frozen=True)
class LLMEliminationSchedule:
    """An exact, actual-count-based elimination plan."""

    configured_entry_candidates: int
    actual_candidate_count: int
    effective_entry_count: int
    configured_final_top_k: int
    effective_final_top_k: int
    total_eliminations: int
    rounds: tuple[EliminationRound, ...]

    @property
    def round_quotas(self) -> tuple[int, ...]:
        return tuple(round_.elimination_quota for round_ in self.rounds)


def build_elimination_schedule(
    actual_candidate_count: int,
    config: LLMFineRerankConfig,
) -> LLMEliminationSchedule:
    """Build an exact schedule using only candidates that actually exist.

    The remaining budget is split across the remaining configured rounds.  This
    gives a deterministic front-loaded integer distribution while ensuring that
    a final round never overshoots the target (for example ``14, 13, 13`` for
    50 candidates reduced to 10 in three rounds).
    """
    if actual_candidate_count < 0:
        raise ValueError("actual_candidate_count must not be negative")

    effective_entry = min(config.entry_candidates, actual_candidate_count)
    effective_top_k = min(config.final_top_k, effective_entry)
    total_eliminations = effective_entry - effective_top_k
    survivor_count = effective_entry
    remaining_eliminations = total_eliminations
    rounds: list[EliminationRound] = []

    for round_number in range(1, config.max_rounds + 1):
        if remaining_eliminations == 0:
            break
        rounds_remaining = config.max_rounds - round_number + 1
        quota = recompute_round_quota(
            survivor_count, effective_top_k, rounds_remaining=rounds_remaining
        )
        rounds.append(
            EliminationRound(
                round_number=round_number,
                survivor_count=survivor_count,
                elimination_quota=quota,
            )
        )
        survivor_count -= quota
        remaining_eliminations -= quota

    return LLMEliminationSchedule(
        configured_entry_candidates=config.entry_candidates,
        actual_candidate_count=actual_candidate_count,
        effective_entry_count=effective_entry,
        configured_final_top_k=config.final_top_k,
        effective_final_top_k=effective_top_k,
        total_eliminations=total_eliminations,
        rounds=tuple(rounds),
    )


def recompute_round_quota(
    survivor_count: int, effective_final_top_k: int, *, rounds_remaining: int
) -> int:
    """Return the next exact quota from the survivors available right now."""
    if survivor_count < 0:
        raise ValueError("survivor_count must not be negative")
    if effective_final_top_k < 0:
        raise ValueError("effective_final_top_k must not be negative")
    if rounds_remaining < 1:
        raise ValueError("rounds_remaining must be positive")
    remaining_eliminations = max(0, survivor_count - effective_final_top_k)
    return -(-remaining_eliminations // rounds_remaining)


def validate_round_quotas(schedule: LLMEliminationSchedule) -> None:
    """Reject a schedule whose quotas do not preserve the exact budget."""
    quotas = schedule.round_quotas
    if any(quota < 0 for quota in quotas):
        raise ValueError("round elimination quotas must be non-negative")
    if sum(quotas) != schedule.total_eliminations:
        raise ValueError("round elimination quotas do not match the total budget")

    survivors = schedule.effective_entry_count
    for round_ in schedule.rounds:
        if round_.survivor_count != survivors:
            raise ValueError("round survivor counts must be recomputed from prior survivors")
        if round_.elimination_quota > survivors - schedule.effective_final_top_k:
            raise ValueError("round elimination quota exceeds the remaining budget")
        survivors -= round_.elimination_quota
    if survivors != schedule.effective_final_top_k:
        raise ValueError("schedule does not finish at the effective final target")


def allocate_group_quotas(group_sizes: Sequence[int], round_quota: int) -> tuple[int, ...]:
    """Allocate a round quota proportionally without emptying a group.

    Each group retains at least one candidate for its local comparison.  Whole
    quota units are allocated by largest fractional remainder; ties resolve by
    input order, so equivalent inputs always receive equivalent quotas.
    """
    if any(size < 1 for size in group_sizes):
        raise ValueError("group sizes must be positive")
    if round_quota < 0:
        raise ValueError("round_quota must be non-negative")
    if not group_sizes:
        if round_quota:
            raise ValueError("cannot allocate a quota without groups")
        return ()

    removable = [size - 1 for size in group_sizes]
    if round_quota > sum(removable):
        raise ValueError("round_quota exceeds removable group capacity")
    if round_quota == 0:
        return tuple(0 for _ in group_sizes)

    total_size = sum(group_sizes)
    exact = [round_quota * size / total_size for size in group_sizes]
    quotas = [min(floor(value), capacity) for value, capacity in zip(exact, removable)]
    remaining = round_quota - sum(quotas)
    priority = sorted(
        range(len(group_sizes)), key=lambda index: (-(exact[index] - floor(exact[index])), index)
    )
    while remaining:
        progressed = False
        for index in priority:
            if quotas[index] < removable[index]:
                quotas[index] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise ValueError("round_quota exceeds removable group capacity")
    return tuple(quotas)


def regroup_candidates(
    candidates: Sequence[Any], *, group_size: int, seed: int = 0
) -> tuple[tuple[Any, ...], ...]:
    """Deterministically interleave Papers before creating comparison groups.

    Candidates require a ``paper_id`` and preferably a stable ``candidate_id``;
    mappings with those keys are supported for lightweight callers and tests.
    """
    if group_size < 1:
        raise ValueError("group_size must be positive")

    by_paper: dict[str, list[Any]] = defaultdict(list)
    for candidate in candidates:
        paper_id = _candidate_value(candidate, "paper_id")
        if not paper_id:
            raise ValueError("each candidate must have a paper_id")
        by_paper[str(paper_id)].append(candidate)

    queues: dict[str, deque[Any]] = {}
    for paper_id, members in by_paper.items():
        queues[paper_id] = deque(
            sorted(members, key=lambda candidate: _seeded_key(candidate, seed))
        )
    paper_order = sorted(queues, key=lambda paper_id: _seeded_text_key(paper_id, seed))
    ordered: list[Any] = []
    while any(queues.values()):
        for paper_id in paper_order:
            if queues[paper_id]:
                ordered.append(queues[paper_id].popleft())
    return tuple(
        tuple(ordered[start : start + group_size])
        for start in range(0, len(ordered), group_size)
    )


def _candidate_value(candidate: Any, name: str) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(name)
    return getattr(candidate, name, None)


def _seeded_key(candidate: Any, seed: int) -> bytes:
    candidate_id = _candidate_value(candidate, "candidate_id")
    return _seeded_text_key(str(candidate_id) if candidate_id is not None else repr(candidate), seed)


def _seeded_text_key(value: str, seed: int) -> bytes:
    return blake2b(f"{seed}:{value}".encode(), digest_size=16).digest()


__all__ = [
    "EliminationRound",
    "LLMEliminationSchedule",
    "LLMFineRerankConfig",
    "allocate_group_quotas",
    "build_elimination_schedule",
    "recompute_round_quota",
    "regroup_candidates",
    "validate_round_quotas",
]
