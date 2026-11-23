"""#82 回归（reco-core 侧）：rank_only 必须把 config 透传给唯一排序入口 rank。

缺陷（issue #82）：rank_only 曾以 `score_rank(candidates, sp, {}, ...)` 调用，
把调用方传入的 config 丢弃——`exploration_count` 永远落到 _DEFAULT_EXPLORATION=2，
于是推荐专区「生成篇数」控件形同虚设。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from reco_core.feedback import rank_only
from reco_core.profile import Confidence, ProfileData

NOW = datetime(2025, 11, 21, 10, 0, 0, tzinfo=timezone.utc)
QUERY = {10: 1.0}


def _profile():
    """v1 画像：单轴 mood.dark 显示权重 0.5，weights=(1,0,0)。"""
    return ProfileData(
        version=1, updated_at="2025-11-21T00:00:00+00:00",
        axis_weights={"mood.dark": 0.5},
        sample_counts={"tag_like": 0, "tag_dislike": 0, "behavior": 0,
                       "per_axis": {"mood.dark": {"positive": 0.0, "negative": 0.0}}},
        top_tags=(("mood.dark", 0.5),),
        weights=(1.0, 0.0, 0.0), weight_version="weights/1", weight_reason="seed",
        half_life_days={"tag": 90.0, "signal": 30.0, "stable": 180.0},
        behavior_signals={}, completeness="full", event_id_high_water=None,
        confidence=Confidence(value=0.5, n_pos=0, n_neg=0, cap=0.3, display="fresh"),
        generated_events=(),
    )


def _candidates(n=12):
    """n 本同向候选：相似度全并列 → 探索池取全体（够放 10 个探索位）。"""
    return [(f"B{i}", {10: 1.0}) for i in range(1, n + 1)]


def _exploration_count(rows):
    return sum(1 for r in rows if r.is_exploration)


@pytest.mark.parametrize("count", [1, 5, 10])
def test_rank_only_honours_exploration_count(count):
    """红：config 未透传 → 探索位恒 2（传 1/5/10 都不得 2）。"""
    out = rank_only(_candidates(), _profile(), positive_vectors=[QUERY],
                    config={"exploration_count": count}, now=NOW)
    assert _exploration_count(out["rank_rows"]) == count


def test_rank_only_default_exploration_is_two():
    """不传 config 时保持 §17 Q7 默认 2 篇（不回归）。"""
    out = rank_only(_candidates(), _profile(), positive_vectors=[QUERY], now=NOW)
    assert _exploration_count(out["rank_rows"]) == 2
