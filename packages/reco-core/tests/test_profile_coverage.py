from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reco_core.profile import (
    build_profile,
    export_profile,
    profile_confidence,
    verify_replay,
)

BASE = datetime(2026, 11, 21, 0, 0, 0, tzinfo=timezone.utc)
NOW = BASE + timedelta(days=100)


def _ev(eid, etype, age_days, payload, book_id=1):
    return {
        "event_id": eid,
        "event_type": etype,
        "book_id": book_id,
        "payload": dict(payload),
        "occurred_at": (BASE + timedelta(days=age_days)).isoformat(),
    }


def test_S7b_coverage_denominator_is_declared_axis_total():
    """#110：覆盖度分母是分类法轴总数，不是 per_axis 的键数。"""
    fake = type("P", (), {})()
    fake.sample_counts = {
        "tag_like": 1,
        "tag_dislike": 0,
        "behavior": 0,
        "n_axes": 25,
        "per_axis": {"mech": {"positive": 1, "negative": 0}},
    }
    c = profile_confidence(fake)
    # cap=.3, sample_factor=1/40, coverage=1/25, consistency=0
    assert c.value == pytest.approx(0.3 * (0.4 / 40 + 0.35 / 25), abs=1e-12)


def test_S7b_build_profile_carries_declared_axes_through_update():
    """#110：build_profile 收声明轴集，update_profile 不丢。"""
    events = [_ev(1, "tag_hit", 0, {"axis": "mech", "tag_id": "mech.x", "confidence": 1.0})]
    axes = ("top", "mech", "tone", "ending")
    p = build_profile(events, axes=axes, now=NOW)
    assert p.sample_counts["n_axes"] == len(axes)
    c = profile_confidence(p)
    assert c.value == pytest.approx(0.3 * (0.4 / 40 + 0.35 / 4), abs=1e-12)

    from reco_core.profile import update_profile
    p2 = update_profile(p, _ev(2, "tag_hit", 0, {"axis": "tone", "tag_id": "tone.x", "confidence": 1.0}), now=NOW)
    assert p2.sample_counts["n_axes"] == len(axes)
    assert p2.sample_counts["per_axis"].keys() == {"mech", "tone"}
    c2 = profile_confidence(p2)
    # 两轴全正：一致性=1−|2−0|/2=0，只计样本因子与覆盖度
    assert c2.value == pytest.approx(0.3 * (0.4 * (2 / 40) + 0.35 * (2 / 4)), abs=1e-12)


def test_S7b_export_and_replay_keep_declared_axis_total():
    """#110：快照导出与重放校验必须带同一轴总数，否则 confidence 会假失败。"""
    axes = ("top", "mech", "tone", "ending")
    events = [_ev(1, "tag_hit", 0, {"axis": "mech", "tag_id": "mech.x", "confidence": 1.0})]
    p = build_profile(events, axes=axes, now=NOW)

    exported = export_profile(p)
    assert exported["sample_counts"]["n_axes"] == len(axes)

    report = verify_replay(events, p, now=NOW, axes=axes)
    assert report.ok, report.diffs
    # 不带声明轴集重放 → 覆盖度退回 1/1，confidence 对不上，说明 axes 必须贯穿
    assert not verify_replay(events, p, now=NOW).ok
