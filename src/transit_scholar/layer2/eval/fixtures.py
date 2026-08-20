"""Deterministic synthetic fixtures for retrieval evaluation (FR-014).

Builds a small synthetic paper (as a fake-parser item stream) plus gold
queries -- including a cross-language (Chinese query -> English paper) entry --
so evaluation code can be validated offline with no network, heavy parsers or
API keys.
"""

from __future__ import annotations

from transit_scholar.layer2.parser.fake import make_item
from transit_scholar.layer2.schema import GoldQuery


def build_fixture_paper_items() -> list:
    """A small deterministic paper with sections, paragraphs and a table."""
    return [
        make_item(
            item_id="h1",
            item_type="heading",
            text="Introduction",
            order=0,
            page=1,
            level=1,
            bbox=[70.0, 100.0, 530.0, 120.0],
            font_size=14.0,
        ),
        make_item(
            item_id="p1",
            item_type="paragraph",
            text=(
                "Bus bunching occurs when two buses on the same route operate "
                "too close together, degrading headway regularity."
            ),
            order=1,
            page=1,
            bbox=[70.0, 130.0, 530.0, 150.0],
            font_size=10.0,
        ),
        make_item(
            item_id="h2",
            item_type="heading",
            text="Method",
            order=2,
            page=1,
            level=1,
            bbox=[70.0, 160.0, 530.0, 180.0],
            font_size=14.0,
        ),
        make_item(
            item_id="p2",
            item_type="paragraph",
            text=(
                "We formulate the holding control problem as a Markov decision "
                "process and solve it with deep reinforcement learning."
            ),
            order=3,
            page=1,
            bbox=[70.0, 190.0, 530.0, 210.0],
            font_size=10.0,
        ),
        make_item(
            item_id="t1",
            item_type="table",
            text="| Baseline | Avg Wait |\n| --- | --- |\n| No control | 8.2 |\n| DRL | 5.1 |",
            order=4,
            page=2,
            bbox=[70.0, 60.0, 530.0, 120.0],
            content={
                "label": "Table 1",
                "n_rows": 3,
                "n_cols": 2,
                "cells": [
                    {"row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "Baseline", "is_header": True},
                    {"row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "Avg Wait", "is_header": True},
                    {"row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "No control", "is_header": False},
                    {"row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "8.2", "is_header": False},
                    {"row": 2, "col": 0, "row_span": 1, "col_span": 1, "text": "DRL", "is_header": False},
                    {"row": 2, "col": 1, "row_span": 1, "col_span": 1, "text": "5.1", "is_header": False},
                ],
                "markdown": "| Baseline | Avg Wait |\n| --- | --- |\n| No control | 8.2 |\n| DRL | 5.1 |",
            },
        ),
        make_item(
            item_id="c1",
            item_type="caption",
            text="Table 1. Average passenger waiting time under each baseline.",
            order=5,
            page=2,
            bbox=[70.0, 130.0, 530.0, 145.0],
            font_size=9.0,
        ),
        make_item(
            item_id="h3",
            item_type="heading",
            text="Results",
            order=6,
            page=2,
            level=1,
            bbox=[70.0, 160.0, 530.0, 180.0],
            font_size=14.0,
        ),
        make_item(
            item_id="p3",
            item_type="paragraph",
            text=(
                "The deep reinforcement learning controller reduces average "
                "waiting time by 38 percent compared with no control."
            ),
            order=7,
            page=2,
            bbox=[70.0, 190.0, 530.0, 210.0],
            font_size=10.0,
        ),
    ]


def build_fixture_gold_queries(paper_id: str) -> list[GoldQuery]:
    """Gold queries over the synthetic fixture paper.

    Block ids are the ids of the canonical blocks produced from the fixture
    items above (in reading order: blk_00001...). Gold ids reference the
    blocks that contain the answer.
    """
    return [
        GoldQuery(
            paper_id=paper_id,
            query="bus bunching",
            query_type="exact_term",
            gold_block_ids=["blk_00002"],
        ),
        GoldQuery(
            paper_id=paper_id,
            query="What is the average waiting time reduction?",
            query_type="method_description",
            gold_block_ids=["blk_00008"],
        ),
        GoldQuery(
            paper_id=paper_id,
            query="公交车队控制与强化学习",
            query_type="cross_language",
            gold_block_ids=["blk_00004"],
        ),
    ]
