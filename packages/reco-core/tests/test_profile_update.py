"""工单 09——update_profile/trends/behavior/权重接缝/eval 门（红测试，S4/S10-S14）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reco_core.profile import (
    PROFILE_VERSION,
    axis_trends,
    build_profile,
    export_profile,
    merge_signal,
    persist_evaluation_gate,
    update_profile,
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


def test_S4_update_clip_band_and_golden():
    p0 = build_profile([], now=NOW)
    p = p0
    for k in range(5):  # 5×tag_hit age=0（w=1.0）：0.55→0.6→0.65→0.7→0.75（冻结 trace）
        p = update_profile(p, _ev(10 + k, "tag_hit", 100, {"axis": "ax", "tag_id": "ax"}), now=NOW)
    p = update_profile(p, _ev(20, "rec_feedback", 100, {"axis": "ax", "kind": "dislike", "value": 1.0}), now=NOW)
    # dislike 后 sums=(5,1)：Laplace 目标 6/8=0.75，与当前显示重合 → 位移≈0 → 保持 0.75（冻结）
    assert p.axis_weights["ax"] == pytest.approx(0.75, abs=1e-12)
    assert p.sample_counts["per_axis"]["ax"] == {"positive": 5.0, "negative": 1.0}
    hit = update_profile(p, _ev(30, "tag_hit", 40, {"axis": "ax", "tag_id": "ax"}), now=NOW)
    # +tag_hit age=60d（tag 档 w=0.6299605249474366）→ 目标 0.768249229620644（|Δ|=0.0182 不触 clip）
    assert hit.axis_weights["ax"] == pytest.approx(0.768249229620644, abs=1e-12)
    assert hit.version == p.version + 1
    virgin_up = update_profile(p0, _ev(40, "tag_hit", 100, {"axis": "v", "tag_id": "v"}), now=NOW)
    assert virgin_up.axis_weights["v"] == pytest.approx(0.55, abs=1e-12)  # clip 顶格
    virgin_dn = update_profile(p0, _ev(41, "rec_feedback", 100, {"axis": "v", "kind": "dislike", "value": 1.0}), now=NOW)
    assert virgin_dn.axis_weights["v"] == pytest.approx(0.45, abs=1e-12)  # clip 顶格


def test_S10_dislike_sequence_weighted_decay_never_excluded():
    p0 = build_profile([], now=NOW)
    p = p0
    seq = []
    for k in range(8):  # value=0.5 → w=0.5（age=0）；clip 步进序列（冻结轮3）
        p = update_profile(p, _ev(k, "rec_feedback", 100, {"axis": "ax", "kind": "dislike", "value": 0.5}), now=NOW)
        seq.append(p.axis_weights["ax"])
    frozen = [0.45, 0.4, 0.35000000000000003, 0.30000000000000004,
              0.25000000000000006, 0.20000000000000007, 0.18181818181818182, 0.16666666666666666]
    assert seq == pytest.approx(frozen, abs=1e-12)
    assert all(a > b for a, b in zip(seq, seq[1:]))  # 严格递减
    assert all(x > 0 for x in seq)  # 趋零不归零——永不构成硬排除（C7）


def test_S11_axis_trends():
    p = build_profile([], now=NOW)
    hist = [p]
    for k, w in enumerate((0.30, 0.35, 0.40, 0.45)):
        nxt = update_profile(p, _ev(50 + k, "tag_hit", 99, {"axis": "ax", "tag_id": "ax", "confidence": 1.0}), now=NOW)
        # 直接伪造历史快照以免依赖折叠恰好命中这些值
        nxt = type(nxt)(**{**nxt.__dict__, "axis_weights": {"ax": w}, "version": nxt.version + 1}) if False else nxt
        hist.append(nxt)
        p = nxt
    # 直接用导出形态构造干净历史（避免 clip 干扰趋势语义）
    snaps = []
    for i, w in enumerate((0.30, 0.35, 0.40, 0.45)):
        snaps.append({"version": i + 1, "axis_weights": {"ax": w, "other": 0.5}})
    trends = axis_trends(snaps)
    t = next(x for x in trends if x.axis == "ax")
    assert t.direction == "↑"
    assert t.delta_per_step == pytest.approx(0.05000000000000001, abs=1e-12)
    down = axis_trends([
        {"version": i + 1, "axis_weights": {"ax": w}} for i, w in enumerate((0.45, 0.40, 0.35, 0.30))
    ])
    td = next(x for x in down if x.axis == "ax")
    assert td.direction == "↓"
    assert td.delta_per_step == pytest.approx(-0.05, abs=1e-12)
    flat = axis_trends([{"version": 1, "axis_weights": {"ax": 0.4}}, {"version": 2, "axis_weights": {"ax": 0.4}}])
    assert next(x for x in flat if x.axis == "ax").direction == "→"


def test_S12_merge_signal_and_behavior_fold():
    # 直接探针：occurred=NOW（age=0，无衰减）→ strength 原样（dwell 200s 满强度口径）
    eff = merge_signal(_ev(1, "signal_recorded", 100, {"kind": "dwell", "value": 500.0}, book_id=7), now=NOW)
    assert eff[0]["book_id"] == 7 and eff[0]["weight"] == pytest.approx(1.0, abs=1e-12)
    eff2 = merge_signal(_ev(2, "signal_recorded", 100, {"kind": "dwell", "value": 100.0}, book_id=7), now=NOW)
    assert eff2[0]["weight"] == pytest.approx(0.2, abs=1e-12)
    # 信号档 30d 衰减：age=100d → strength×0.5^(100/30)=0.09921256574801246（冻结）
    eff3 = merge_signal(_ev(3, "signal_recorded", 0, {"kind": "dwell", "value": 500.0}, book_id=7), now=NOW)
    assert eff3[0]["weight"] == pytest.approx(0.09921256574801246, abs=1e-12)
    assert eff3[0]["age_days"] == pytest.approx(100.0, abs=1e-9)
    p = build_profile([
        _ev(1, "signal_recorded", 100, {"kind": "dwell", "value": 500.0}, book_id=7),
        _ev(2, "signal_recorded", 100, {"kind": "dwell", "value": 100.0}, book_id=7),
    ], now=NOW)  # 两条 age=0：w=1.0+0.2=1.2（冻结）
    assert p.behavior_signals[7] == pytest.approx(1.2, abs=1e-12)
    assert p.sample_counts["behavior"] == 2


def test_S13_weight_set_seam_echoed_into_event():
    ws = {
        "w_sim": 0.7133001217509216, "w_model": 0.03669987824907843, "w_behavior": 0.25,
        "version": "weights/1", "reason": "n_pos=20,n_neg=15,cv_auc=0.7,cap=0.5",
    }
    events = [_ev(1, "tag_hit", 99, {"axis": "a", "tag_id": "a"})]
    p = build_profile(events, now=NOW, weight_set=ws)
    assert p.weights == (0.7133001217509216, 0.03669987824907843, 0.25)
    assert p.weight_version == "weights/1"
    payload = p.generated_events[-1]["payload"]
    import json as _json
    echoed = _json.loads(payload["weights_json"])
    assert echoed["w_sim"] == pytest.approx(0.7133001217509216, abs=1e-12)
    assert echoed["version"] == "weights/1"
    assert echoed["reason"] == "n_pos=20,n_neg=15,cv_auc=0.7,cap=0.5"
    assert payload["profile_version"] == PROFILE_VERSION


def test_S14_evaluation_persistence_gate():
    class FakeStore:
        def __init__(self):
            self.calls = []

        def append_event(self, event_type, **kwargs):
            self.calls.append((event_type, kwargs))
            return len(self.calls)

    good = FakeStore()
    eid = persist_evaluation_gate(
        good, method="pairwise_cv", n_pos=12, n_neg=11, folds=5,
        auc=0.71, ci_low=0.6, ci_high=0.8, now=NOW, config_fingerprint="cfg-x",
    )
    assert eid is not None and good.calls and good.calls[0][0] == "eval_run"
    assert good.calls[0][1]["payload"]["n_pos"] == 12

    blocked = FakeStore()
    out = persist_evaluation_gate(
        blocked, method="pairwise_cv", n_pos=9, n_neg=11, folds=5,
        auc=0.71, ci_low=0.6, ci_high=0.8, now=NOW, config_fingerprint="cfg-x",
    )
    assert out is None and blocked.calls == []  # ≥10 门：不足不写（W8 接缝②强制）
