"""Deterministic elimination scheduling tests (REQ-019..023, REQ-025)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from transit_scholar.layer3.rerank import (
    LLMFineRerankConfig,
    allocate_group_quotas,
    build_elimination_schedule,
    recompute_round_quota,
    regroup_candidates,
    validate_round_quotas,
)


def test_actual_count_controls_effective_entry_and_final_target():
    schedule = build_elimination_schedule(37, LLMFineRerankConfig(entry_candidates=50, final_top_k=10))

    assert schedule.effective_entry_count == 37
    assert schedule.effective_final_top_k == 10
    assert sum(schedule.round_quotas) == 27


def test_final_target_is_clamped_when_actual_candidates_are_fewer():
    schedule = build_elimination_schedule(8, LLMFineRerankConfig(entry_candidates=50, final_top_k=10))

    assert schedule.effective_entry_count == 8
    assert schedule.effective_final_top_k == 8
    assert schedule.total_eliminations == 0
    assert schedule.rounds == ()


def test_fifty_to_ten_in_three_rounds_eliminates_exactly_forty():
    schedule = build_elimination_schedule(
        50, LLMFineRerankConfig(entry_candidates=50, final_top_k=10, max_rounds=3)
    )

    assert schedule.round_quotas == (14, 13, 13)
    assert sum(schedule.round_quotas) == 40
    validate_round_quotas(schedule)


def test_round_quota_is_recomputed_from_actual_survivors():
    assert recompute_round_quota(37, 10, rounds_remaining=3) == 9
    assert recompute_round_quota(28, 10, rounds_remaining=2) == 9


def test_overshooting_manual_round_quota_is_rejected():
    schedule = build_elimination_schedule(
        50, LLMFineRerankConfig(entry_candidates=50, final_top_k=10, max_rounds=3)
    )
    overscheduled = schedule.__class__(
        **{**schedule.__dict__, "rounds": tuple(round_.__class__(round_.round_number, round_.survivor_count, 14) for round_ in schedule.rounds)}
    )

    with pytest.raises(ValueError, match="total budget"):
        validate_round_quotas(overscheduled)


def test_group_quotas_are_proportional_and_exact():
    quotas = allocate_group_quotas((20, 20, 10), 14)

    assert quotas == (6, 5, 3)
    assert sum(quotas) == 14


@pytest.mark.parametrize("group_sizes", [(1,), (2, 7), (3, 3, 3), (20, 20, 10), (4, 9, 13, 2)])
def test_group_quota_invariants(group_sizes):
    capacity = sum(size - 1 for size in group_sizes)
    for quota in range(capacity + 1):
        quotas = allocate_group_quotas(group_sizes, quota)
        assert sum(quotas) == quota
        assert all(0 <= allocated <= size - 1 for allocated, size in zip(quotas, group_sizes))


def test_group_quota_rejects_more_than_removable_capacity():
    with pytest.raises(ValueError, match="removable"):
        allocate_group_quotas((2, 2), 3)


def test_regrouping_is_seeded_and_interleaves_papers():
    candidates = [
        SimpleNamespace(candidate_id=f"{paper}-{rank}", paper_id=paper, local_rank=rank)
        for paper in ("paper-a", "paper-b", "paper-c")
        for rank in range(4)
    ]

    groups = regroup_candidates(candidates, group_size=3, seed=7)

    assert groups == regroup_candidates(candidates, group_size=3, seed=7)
    assert all(len({candidate.paper_id for candidate in group}) == len(group) for group in groups)
