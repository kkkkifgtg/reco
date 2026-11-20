"""W12b 红测试（二）：热集判定纯函数（uses≥3 可配 / Top-K / 主面板恒热 / 确定性）。"""
from __future__ import annotations

from reco_ui.preload import (
    DEFAULT_HOT_THRESHOLD,
    ALWAYS_HOT_PAGES,
    compute_hot_set,
)


def _usage(uses_by_page):
    return {pid: {"uses": u, "last_used": "2026-11-21T00:00:00"} for pid, u in uses_by_page.items()}


def test_threshold_default_is_three():
    assert DEFAULT_HOT_THRESHOLD == 3


def test_always_hot_includes_recommend():
    assert "recommend" in ALWAYS_HOT_PAGES


def test_hot_set_threshold_rule():
    usage = _usage({"liked": 3, "candidate": 2, "profile": 5, "recommend": 0})
    hot = compute_hot_set(usage, threshold=3)
    assert "liked" in hot and "profile" in hot
    assert "candidate" not in hot


def test_hot_set_threshold_configurable():
    usage = _usage({"liked": 2, "candidate": 1})
    # 恒热 recommend 始终在场；阈值只决定阈值成员，Top-K 独立补位（默认 K=2）
    assert compute_hot_set(usage, threshold=2) == frozenset({"recommend", "liked", "candidate"})
    # threshold=3：两页均不过阈；默认 Top-K=2 按降序补 liked(2)>candidate(1)
    assert compute_hot_set(usage, threshold=3) == frozenset({"recommend", "liked", "candidate"})
    # Top-K=0 关闭补位：只剩恒热
    assert compute_hot_set(usage, threshold=3, top_k=0) == frozenset({"recommend"})


def test_hot_set_topk_adds_frequent_pages():
    """Top-K：未过阈值的高频页按 uses 降序补位（确定性：并列按 page_id 升序）。"""
    usage = _usage({"liked": 9, "candidate": 7, "profile": 7, "recommend": 1})
    hot = compute_hot_set(usage, threshold=3, top_k=3)
    assert hot == frozenset({"recommend", "liked", "candidate", "profile"})


def test_topk_ties_break_by_page_id():
    usage = _usage({"b": 7, "a": 7, "c": 1})
    hot = compute_hot_set(usage, threshold=100, top_k=2)
    # 并列按 page_id 升序取前 K；恒热 recommend 不占 K 名额（恒热与 Top-K 独立）
    assert hot == frozenset({"a", "b", "recommend"})


def test_recommend_always_hot_even_zero_uses():
    usage = _usage({"liked": 0, "recommend": 0})
    hot = compute_hot_set(usage, threshold=3, top_k=2)
    assert "recommend" in hot  # §11.7：主面板恒热，不进配置，不可关闭


def test_hot_set_deterministic_repeated_calls():
    usage = _usage({"liked": 4, "profile": 2, "recommend": 9})
    h1 = compute_hot_set(usage)
    h2 = compute_hot_set(usage)
    assert h1 == h2 == frozenset({"recommend", "liked"})
