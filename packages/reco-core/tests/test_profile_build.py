"""工单 09——build_profile：折叠黄金值、配置回退、置信度、缺口、重放（红测试，S3/S5-S9）。

期望值独立手算冻结（notes/09-freeze-goldens.py），不 import 实现内部复算（E4）。
时钟注入（E8）：now 与 occurred_at 全部显式。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reco_core.profile import (
    DEFAULT_HALF_LIFE_DAYS,
    PROFILE_VERSION,
    build_profile,
    profile_confidence,
    verify_replay,
)

BASE = datetime(2026, 11, 21, 0, 0, 0, tzinfo=timezone.utc)
NOW = BASE + timedelta(days=100)


def _iso(dt):
    return dt.isoformat()


def _ev(eid, etype, age_days, payload, book_id=1):
    return {
        "event_id": eid,
        "event_type": etype,
        "book_id": book_id,
        "payload": dict(payload),
        "occurred_at": _iso(BASE + timedelta(days=age_days)),
    }


def test_S3_build_golden_fold_and_top_tags():
    events = [
        _ev(1, "tag_hit", 70, {"axis": "subject.x", "tag_id": "subject.x", "confidence": 1.0}),
        _ev(2, "tag_hit", 40, {"axis": "subject.x", "tag_id": "subject.x", "confidence": 1.0}),
        _ev(3, "tag_hit", 10, {"axis": "plot.p", "tag_id": "plot.p", "confidence": 1.0}),
    ]
    p = build_profile(events, now=NOW)
    assert p.version == 1
    assert p.completeness == "full"
    # 事件1：age=30d → stable 档 180d，w=0.5^(30/180)=0.8908987181403393；virgin 位移被 clip 顶格 → 0.55
    # 事件2：age=60d → w=0.7937005259840998；Laplace 目标 2.6846/3.6846≈0.7285，位移 0.1285>0.05 → clip 顶格 → 0.6（语义 a：显示权重按 clip 步进演化）
    assert p.axis_weights["subject.x"] == pytest.approx(0.6, abs=1e-12)
    # plot.p：age=90d → tag 档 90d，w=0.5 → virgin clip 顶格 0.55
    assert p.axis_weights["plot.p"] == pytest.approx(0.55, abs=1e-12)
    assert p.top_tags[0][0] == "subject.x"
    assert p.top_tags[0][1] == pytest.approx(1.684599244124439, abs=1e-12)
    assert p.weights == (1.0, 0.0, 0.0)  # 小样本默认（§6.3 n_pos<10 → w_model=0）
    assert p.half_life_days == DEFAULT_HALF_LIFE_DAYS == {"tag": 90, "signal": 30, "stable": 180}
    assert len(p.generated_events) == 1
    ev = p.generated_events[0]
    assert ev["event_type"] == "profile_updated"
    assert ev["occurred_at"] == _iso(NOW)
    assert set(ev["payload"]) <= {
        "axis_weights_json", "sample_counts_json", "top_tags_json", "weights_json",
        "half_life_days_json", "reason", "event_id_high_water", "confidence",
        "profile_version",
    }  # B6：payload 白名单子集（confidence 供 profile_versions 的 NOT NULL 列）


def test_S5_older_feedback_counts_less_on_prewarmed_axis():
    # 预热：10 正 + 2 负，全部 age=0（w=1.0）；显示权重终态 0.8166666666666665，sums=(10,2)（冻结 trace）
    p0 = build_profile([], now=NOW)
    from reco_core.profile import update_profile
    p = p0
    for k in range(10):
        p = update_profile(p, _ev(10 + k, "tag_hit", 100, {"axis": "ax", "tag_id": "ax"}), now=NOW)
    for k in range(2):
        p = update_profile(p, _ev(30 + k, "rec_feedback", 100,
                                  {"axis": "ax", "kind": "dislike", "value": 1.0}), now=NOW)
    base = p.axis_weights["ax"]
    assert base == pytest.approx(0.8166666666666665, abs=1e-12)
    newer = update_profile(p, _ev(90, "tag_hit", 70, {"axis": "ax", "tag_id": "ax"}), now=NOW)
    older = update_profile(p, _ev(91, "tag_hit", 40, {"axis": "ax", "tag_id": "ax"}), now=NOW)
    # 同类同置信 hit：30d 前（w=0.5^(30/90)）比 60d 前（w=0.5^(60/90)）拉动更多（向目标收敛更快）
    assert newer.axis_weights["ax"] == pytest.approx(0.7972109821521187, abs=1e-12)
    assert older.axis_weights["ax"] == pytest.approx(0.7949413469103821, abs=1e-12)
    assert newer.axis_weights["ax"] > older.axis_weights["ax"]


def test_S6_invalid_half_life_falls_back_and_records():
    events = [_ev(1, "tag_hit", 99, {"axis": "ax", "tag_id": "ax"})]
    bad = {"tag": 0, "signal": 30, "stable": 180}
    p = build_profile(events, config={"half_life_days": bad}, now=NOW)
    assert p.half_life_days == DEFAULT_HALF_LIFE_DAYS  # 非法配置拒绝、回退默认（C4）
    reasons = " ".join(e["payload"].get("reason", "") for e in p.generated_events)
    assert "回退" in reasons
    p2 = build_profile(events, config={"half_life_days": {"tag": "NaN"}}, now=NOW)
    assert p2.half_life_days == DEFAULT_HALF_LIFE_DAYS


def test_S7_confidence_cap_and_display():
    """口径：value = cap×(0.4·样本因子+0.35·覆盖度+0.25·一致性)；一致性=1−|Δn|/Σn（冻结自 09-freeze-goldens 轮4）。"""
    from reco_core.profile import profile_confidence as conf
    p_empty = build_profile([], now=NOW)
    c0 = profile_confidence(p_empty)
    assert c0.value == 0.0
    assert c0.cap == 0.3
    assert "暂无正样本" in c0.display

    def fake(np_, nn_, per_axis):
        obj = type("P", (), {})()
        obj.sample_counts = {"tag_like": np_, "tag_dislike": nn_, "behavior": 0,
                             "per_axis": per_axis}
        return obj

    # (3,0) 三轴覆盖一轴：sf=3/40, cov=1/3, cons=0（全正无一致）→ 0.044（冻结）
    c1 = conf(fake(3, 0, {"ax": {"positive": 3, "negative": 0},
                          "b": {"positive": 0, "negative": 0},
                          "c": {"positive": 0, "negative": 0}}))
    assert c1.value == pytest.approx(0.044, abs=1e-12)
    assert c1.cap == 0.3
    assert "还不够准" in c1.display
    # (20,5) 单轴：sf=25/40, cov=1, cons=1−15/25=0.4 → 0.7（冻结）
    c2 = conf(fake(20, 5, {"a": {"positive": 20, "negative": 5}}))
    assert c2.value == pytest.approx(0.7, abs=1e-12)
    assert c2.cap == 1.0
    # (20,100)：0.8333333333333334（冻结）
    c3 = conf(fake(20, 100, {"a": {"positive": 20, "negative": 100}}))
    assert c3.value == pytest.approx(0.8333333333333334, abs=1e-12)
    # C6 硬门：n_pos=9（<10）时 cap=0.3 封顶——(9,100) → 0.23738532110091742（冻结）
    c4 = conf(fake(9, 100, {"a": {"positive": 9, "negative": 100}}))
    assert c4.cap == 0.3
    assert c4.value == pytest.approx(0.23738532110091742, abs=1e-12)
    assert "还不够准" in c4.display
    # cap 释放边界：n_pos=10 → cap=1.0，(10,9) → 0.776842105263158（冻结）
    c5 = conf(fake(10, 9, {"a": {"positive": 10, "negative": 9}}))
    assert c5.cap == 1.0
    assert c5.value == pytest.approx(0.776842105263158, abs=1e-12)
    # 极大样本饱和于 1.0（C5 相对归一，不溢出）
    c6 = conf(fake(1000, 1000, {"a": {"positive": 1000, "negative": 1000}}))
    assert c6.value == 1.0


def test_S8_event_gap_marks_partial():
    events = [
        _ev(1, "tag_hit", 99, {"axis": "a", "tag_id": "a"}),
        _ev(2, "tag_hit", 99, {"axis": "a", "tag_id": "a"}),
        _ev(3, "tag_hit", 99, {"axis": "b", "tag_id": "b"}),
        _ev(5, "tag_hit", 99, {"axis": "c", "tag_id": "c"}),  # 4 号缺失
    ]
    p = build_profile(events, now=NOW)
    assert p.completeness == "partial"
    assert p.event_id_high_water == 5
    p_full = build_profile(events[:3], now=NOW)
    assert p_full.completeness == "full"


def test_S9_replay_bitwise_and_tolerance():
    events = [
        _ev(1, "tag_hit", 70, {"axis": "subject.x", "tag_id": "subject.x", "confidence": 1.0}),
        _ev(2, "tag_hit", 40, {"axis": "subject.x", "tag_id": "subject.x"}),
        _ev(3, "rec_feedback", 95, {"axis": "plot.p", "kind": "dislike", "value": 1.0}),
    ]
    a = build_profile(events, now=NOW)
    b = build_profile(events, now=NOW)
    assert a.axis_weights == b.axis_weights  # 同流同配置逐位一致（C2）
    assert a.version == b.version == 1
    stored = export_profile(a) if False else {  # stored 版本行（store 投影形态）
        "version": a.version,
        "axis_weights": dict(a.axis_weights),
        "sample_counts": dict(a.sample_counts),
        "confidence_value": a.confidence.value,
        "completeness": a.completeness,
    }
    rep = verify_replay(events, stored, now=NOW)
    assert rep.ok and rep.diffs == ()
    tampered = dict(stored)
    tampered["axis_weights"] = dict(stored["axis_weights"])
    tampered["axis_weights"]["subject.x"] += 1e-6  # 超容差
    rep2 = verify_replay(events, tampered, now=NOW)
    assert not rep2.ok and rep2.diffs
