"""RR2/t3——tag_hit → 画像折叠的跨包集成测试（根 tests/，CODING-STANDARDS §2）。

从 packages/reco-labels/tests/test_auto_tag_liked.py 移入：import reco_core.profile
属跨包，单包测试只允许 import 本包模块（ADR-0001「包独立可测」）。
"""
from __future__ import annotations

import pytest


def _mk_event(eid, etype, at, payload, book_id):
    return {"event_type": etype, "event_id": eid, "occurred_at": at,
            "payload": payload, "book_id": book_id}


def test_tag_hit_event_folds_into_profile_axis_weight():
    """W9 折叠：tag_hit(axis=mechanism) → axis_weights['mechanism'] 非零。

    手算锚（E3/E4）：age=0 → decay=1；clip 0.05 为**每事件**位移上限。
    空画像 disp 起点 0.5：首事件 target=0.5 → Δ=0；第二事件 target=2/3 →
    Δ≈0.167 → clip 0.05 → 0.55…连续两事件实测 0.60（0.50→0.55→0.60）。
    """
    from reco_core.profile import build_profile
    events = [
        _mk_event(1, "tag_hit", "2026-11-21T00:00:00",
                  {"axis": "mechanism", "tag_id": "mech.hypnosis", "confidence": 1.0}, 7),
        _mk_event(2, "tag_hit", "2026-11-21T00:00:01",
                  {"axis": "mechanism", "tag_id": "mech.hypnosis", "confidence": 1.0}, 7),
    ]
    profile = build_profile(events, now="2026-11-21T00:00:02")
    assert profile.sample_counts["tag_like"] == 2
    assert profile.axis_weights["mechanism"] == pytest.approx(0.60, abs=1e-9)


def test_profile_weight_increases_with_auto_hits():
    """命中越多（正样本越多）→ 轴权重单调上移（画像联动方向性）。"""
    from reco_core.profile import build_profile
    one = build_profile(
        [_mk_event(1, "tag_hit", "2026-11-21T00:00:00",
                   {"axis": "mechanism", "tag_id": "x", "confidence": 1.0}, 1)],
        now="2026-11-21T00:00:01")
    three = build_profile(
        [_mk_event(i, "tag_hit", "2026-11-21T00:00:00",
                   {"axis": "mechanism", "tag_id": "x", "confidence": 1.0}, 1)
         for i in (1, 2, 3)],
        now="2026-11-21T00:00:01")
    assert three.axis_weights["mechanism"] > one.axis_weights["mechanism"]


def test_dislike_still_drops_axis_weight():
    """负反馈仍下调（C7 方向性守护——自动打标不得污染负向路径）。"""
    from reco_core.profile import build_profile, update_profile
    base = build_profile([], now="2026-11-21T00:00:00")
    p1 = update_profile(
        base,
        _mk_event(1, "rec_feedback", "2026-11-21T00:00:00",
                  {"axis": "mechanism", "kind": "like", "value": 1.0}, 1),
        now="2026-11-21T00:00:01")
    p2 = update_profile(
        p1,
        _mk_event(2, "rec_feedback", "2026-11-21T00:00:01",
                  {"axis": "mechanism", "kind": "dislike", "value": 1.0}, 1),
        now="2026-11-21T00:00:02")
    assert p2.axis_weights["mechanism"] < p1.axis_weights["mechanism"]
