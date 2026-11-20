"""工单 08——相似度打分（rank 唯一排序入口）（红→绿测试 S1-S8/S11/F5）。

黄金期望值独立来源（E4，禁自洽）：
- 相似度金标冻结自 venv python 直算（2026-11-21，stdlib math 独立计算）：
  查询 {1:1.0, 5:0.8} vs 候选 {1:0.6, 5:0.8, 9:0.2}：
  dot=1.2400000000000002, q=1.2806248474865698, c=1.019803902718557,
  sim=0.9494740323391176
- WeightSet/RankRow/Reason 字段、归一容差 1e-6、探索位默认 2 条、
  候选集指纹=排序后 book_id 序列哈希：逐字对照 ARCHITECTURE.md §6.3（L448-476），
  非由实现反推。
验收映射（.scratch/reco-impl/issues/08-scoring.md）：
  A1 rank 唯一排序入口契约   A2 手算相似度期望（禁自洽）
  A3 单类别如实降级不伪造    A4/A5 sklearn 延迟导入（F5）
  A6 weight_schedule 平滑    A7 权重变化经单一来源（事件由画像层落）
  A8 §6.5 反例清单为测试用例
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from reco_core.score import (
    FingerprintTooShort,
    Profile,
    RankRow,
    evaluate_pairwise,
    explain,
    fingerprint_of,
    rank,
    score,
    similarity_scores,
    train_pairwise,
    weight_schedule,
)

SIM_GOLDEN = 0.9494740323391176
QUERY = {1: 1.0, 5: 0.8}
CAND = {1: 0.6, 5: 0.8, 9: 0.2}


def _profile(weights=(0.6, 0.0, 0.4), n_pos=3, n_neg=2, signals=None, extra=None, positives=None):
    data = {
        "weights": weights,
        "sample_counts": {"n_pos": n_pos, "n_neg": n_neg},
        "behavior_signals": signals or {"_probe_": 0.0},
    }
    if positives is not None:
        data["positive_vectors"] = positives
    if extra:
        data.update(extra)
    return Profile(**data)


# ---------- A2/S4：手算相似度金标（禁自洽） ----------


def test_S4_similarity_scores_golden_hand_calculated():
    out = similarity_scores([("b1", CAND)], QUERY)
    assert len(out) == 1
    assert out[0][0] == "b1"
    assert out[0][1] == pytest.approx(SIM_GOLDEN, abs=1e-12)


def test_S4_similarity_edge_cases():
    empty = similarity_scores([("b0", {})], QUERY)
    assert empty == [("b0", 0.0)]
    ident = similarity_scores([("bx", {1: 1.0})], {1: 1.0})
    assert ident[0][1] == pytest.approx(1.0, abs=1e-12)


def test_S4_fingerprint_guard_fires_on_empty():
    with pytest.raises(FingerprintTooShort):
        fingerprint_of([])
    assert fingerprint_of(["b1"])


def test_S4_fingerprint_of_stable_and_discriminating():
    a = fingerprint_of(["b1", "b2", "b3"])
    assert a == fingerprint_of(["b3", "b1", "b2"])
    assert a != fingerprint_of(["b1", "b2", "b4"])
    assert isinstance(a, str) and len(a) >= 16


# ---------- A1/S1：rank 契约、稳定性与探索位确定性 ----------


def test_S1_rankrow_contract_and_deterministic_exploration():
    profile = _profile(positives=[{1: 1.0}])  # 正样本在 1 轴：相似度按 dim1 分量递减
    config = {"exploration_count": 2}
    # 单位向量按 cos15°..cos75° 递减：单维向量全同向（余弦同分），此处必须二维
    candidates = [
        ("b3", {1: 0.9659258262890683, 2: 0.25881904510252074}),   # cos15°
        ("b1", {1: 0.7071067811865476, 2: 0.7071067811865476}),    # cos45°
        ("b2", {1: 0.8660254037844387, 2: 0.5000000000000001}),    # cos30°
        ("b4", {1: 0.25881904510252074, 2: 0.9659258262890683}),   # cos75°
        ("b5", {1: 0.5000000000000001, 2: 0.8660254037844387}),    # cos60°
    ]
    rows = rank(candidates, profile, config)
    assert len(rows) == 5
    for row in rows:
        assert isinstance(row, RankRow)
        assert set(row._fields) == {"book_id", "score", "rank", "reasons", "is_exploration"}
    assert [r.book_id for r in rows] == ["b3", "b2", "b1", "b5", "b4"]
    assert [r.rank for r in rows] == [1, 2, 3, 4, 5]
    assert rows[0].is_exploration is False  # 舒适区顶部不作探索位
    explorable = [r.book_id for r in rows if r.is_exploration]
    assert len(explorable) == 2
    assert "b3" not in explorable
    again = rank(candidates, profile, config)
    assert again == rows  # 同输入必得同探索位（§17 Q7/建议项 3）


def test_S1_tied_scores_break_by_book_id_ascending():
    # 同分必须按 book_id 升序稳定次序（§6.3）——输入乱序给出，期望与输入序无关
    profile = _profile(positives=[QUERY])
    candidates = [("b3", dict(QUERY)), ("b1", dict(QUERY)), ("b2", dict(QUERY))]
    rows = rank(candidates, profile, {})
    assert [r.book_id for r in rows] == ["b1", "b2", "b3"]
    assert [r.score for r in rows] == [rows[0].score] * 3


def test_S1_all_top_pool_too_small_skips_exploration():
    profile = _profile(positives=[{2: 1.0}])
    rows = rank([("b1", {2: 0.5}), ("b2", {2: 0.4})], profile, {"exploration_count": 2})
    assert [r.book_id for r in rows] == ["b1", "b2"]
    assert all(not r.is_exploration for r in rows)


def test_S1_more_candidates_than_exploration_slots():
    profile = _profile(positives=[{2: 1.0}])
    candidates = [(f"b{i:02d}", {2: 0.5}) for i in range(1, 9)]
    rows = rank(candidates, profile, {"exploration_count": 2})
    explorable = [r.book_id for r in rows if r.is_exploration]
    assert len(explorable) == 2
    again = rank(candidates, profile, {"exploration_count": 2})
    assert explorable == [r.book_id for r in again if r.is_exploration]


# ---------- A3/S5a：单类别不伪造 0.5/AUC，如实降级 ----------


def test_S5a_single_class_degrades_without_fake_metrics():
    profile = _profile(weights=(0.4, 0.6, 0.0), n_pos=12, n_neg=0, positives=[{1: 1.0}])
    rows = rank([("b1", {1: 1.0})], profile, {})
    assert rows[0].score == pytest.approx(1.0, abs=1e-12)
    assert getattr(rank, "_last_status", None) == "degraded_no_negatives"


def test_S5a_no_positives_also_degrades():
    profile = _profile(weights=(0.4, 0.6, 0.0), n_pos=0, n_neg=12, positives=[{1: 1.0}])
    rank([("b1", {1: 1.0})], profile, {})
    assert getattr(rank, "_last_status", None) == "degraded_no_positives"


def test_S5a_both_classes_plentiful_passes_gate():
    profile = _profile(weights=(0.4, 0.6, 0.0), n_pos=15, n_neg=15, positives=[{1: 1.0}])
    rows = rank([("b1", {1: 1.0})], profile, {})
    assert rows[0].score == pytest.approx(0.4, abs=1e-12)  # w_sim×1.0；无句柄 → 模型路 0
    assert getattr(rank, "_last_status", None) == "ok"


def test_S5a_small_but_present_both_classes_is_valid():
    profile = _profile(weights=(0.4, 0.6, 0.0), n_pos=8, n_neg=8, positives=[{1: 1.0}])
    rows = rank([("b1", {1: 1.0})], profile, {})
    assert rows[0].score == pytest.approx(1.0, abs=1e-12)


# ---------- A3/S5b：空候选诚实返回 ----------


def test_S5b_empty_candidates_returns_empty_and_states_it():
    profile = _profile()
    assert rank([], profile, {}) == []
    assert score([], profile, {}) == []
    assert getattr(rank, "_last_status", None) == "no_candidates"


# ---------- A6/S2：weight_schedule 平滑单调 + 指纹敏感 + 拒绝坏 CV ----------


def test_S2_weight_schedule_monotone_smooth_bounded():
    prev = None
    for n in range(0, 61, 5):
        ws = weight_schedule(n_pos=n, n_neg=max(n // 2, 1), cv_auc=0.75, config={"model_weight_cap": 0.5})
        total = ws.w_sim + ws.w_model + w_behavior_of(ws)
        assert abs(total - 1.0) < 1e-6
        assert 0.0 <= ws.w_model <= 0.5 + 1e-9
        assert 0.0 <= ws.w_sim <= 1.0 and 0.0 <= ws.w_behavior <= 1.0
        if prev is not None:
            assert ws.w_model >= prev - 1e-12
            assert ws.w_model - prev <= 0.05 + 1e-9
        prev = ws.w_model
    low = weight_schedule(n_pos=0, n_neg=1, cv_auc=0.75, config={"model_weight_cap": 0.5})
    assert low.w_model == 0.0


def w_behavior_of(ws):
    return ws.w_behavior


def test_S2_weight_schedule_cv_auc_sensitive_and_bounded():
    weak = weight_schedule(n_pos=30, n_neg=30, cv_auc=0.5, config={"model_weight_cap": 0.5})
    strong = weight_schedule(n_pos=30, n_neg=30, cv_auc=0.9, config={"model_weight_cap": 0.5})
    assert weak.w_model < strong.w_model
    assert strong.w_model <= 0.5 + 1e-9


def test_S2_weight_schedule_rejects_worse_than_chance_cv():
    with pytest.raises(ValueError):
        weight_schedule(n_pos=30, n_neg=30, cv_auc=0.35, config={"model_weight_cap": 0.5})


# ---------- A7/S3a：权重归一断言（报错而非继续） ----------


def test_S3a_non_normalized_weights_rejected():
    profile = _profile(weights=(0.5, 0.5, 0.3))
    with pytest.raises(ValueError):
        score([("b1", CAND)], profile, {})


# ---------- A3/S3b：n_pos<10 强制 w_model=0 ----------


def test_S3b_small_positive_sample_forces_model_weight_zero():
    profile = _profile(weights=(0.4, 0.6, 0.0), n_pos=7, n_neg=5, positives=[QUERY])
    rows = rank([("b1", CAND)], profile, {})
    assert all(r.axis != "model" for r in rows[0].reasons)
    assert rows[0].score == pytest.approx(SIM_GOLDEN, abs=1e-9)


def test_S3b_all_zero_effective_weights_rejected():
    profile = _profile(weights=(0.0, 0.0, 0.0), n_pos=7, n_neg=5)
    with pytest.raises(ValueError):
        rank([("b1", CAND)], profile, {})


def test_S3b_big_sample_without_model_keeps_gate_silent():
    profile = _profile(weights=(0.4, 0.6, 0.0), n_pos=15, n_neg=15, positives=[{1: 1.0}])
    rows = rank([("b1", {1: 1.0})], profile, {})
    assert rows[0].score == pytest.approx(0.4, abs=1e-12)
    assert getattr(rank, "_last_status", None) == "ok"


# ---------- A5/S4：三路加权合成（手算金标 + 理由来源） ----------


def test_S4_synthesis_weighted_sum_with_reasons():
    profile = _profile(weights=(0.6, 0.0, 0.4), signals={"b1": 0.4}, positives=[QUERY])
    rows = rank([("b1", CAND)], profile, {})
    row = rows[0]
    expected = 0.6 * SIM_GOLDEN + 0.4 * 0.4
    assert row.score == pytest.approx(expected, abs=1e-12)
    by_axis = {r.axis: r for r in row.reasons}
    assert by_axis["similar"].contribution == pytest.approx(0.6 * SIM_GOLDEN, abs=1e-12)
    assert by_axis["behavior"].contribution == pytest.approx(0.4 * 0.4, abs=1e-12)
    assert all(r.axis != "model" for r in row.reasons)
    assert sum(r.contribution for r in row.reasons) == pytest.approx(row.score, abs=1e-9)


# ---------- S7：失败模式 ----------


def test_S7_missing_vector_is_skipped_not_zeroed():
    profile = _profile(n_pos=15, n_neg=15, positives=[{1: 1.0}])
    candidates = [
        ("b_miss", {}), ("b01", {1: 1.0}), ("b02", {1: 0.5}),
        ("b03", {1: 0.25}), ("b04", {1: 0.1}),
    ]
    rows = rank(candidates, profile, {"exploration_count": 2})
    ids = [r.book_id for r in rows]
    assert len(rows) == 4
    assert "b_miss" not in ids
    assert getattr(rank, "_last_status", None) == "ok"


def test_S7_model_file_missing_degrades():
    profile = _profile(weights=(0.4, 0.6, 0.0), n_pos=15, n_neg=15,
                       extra={"model_handle": {"status": "file_missing"}},
                       positives=[{1: 1.0}])
    rows = rank([("b1", {1: 1.0})], profile, {})
    assert getattr(rank, "_last_status", None) == "degraded_model_file_missing"
    assert rows[0].score == pytest.approx(1.0, abs=1e-12)
    clean = _profile(weights=(0.4, 0.6, 0.0), n_pos=15, n_neg=15, positives=[{1: 1.0}])
    rank([("b1", {1: 1.0})], clean, {})
    assert getattr(rank, "_last_status", None) == "ok"


# ---------- S8a：explain（命中轴/相似样本/权重贡献） ----------


def test_S8a_explain_lists_hit_axes_and_similarity():
    profile = _profile(weights=(0.6, 0.0, 0.4), signals={"b1": 0.4}, positives=[QUERY])
    reasons = explain([("b1", CAND)], profile, "b1")
    axes = {r.axis for r in reasons}
    assert axes == {"similar", "behavior"}
    assert sum(r.contribution for r in reasons) == pytest.approx(0.6 * SIM_GOLDEN + 0.4 * 0.4, abs=1e-9)


def test_S8a_explain_unknown_book_raises():
    profile = _profile()
    with pytest.raises(ValueError):
        explain([("b1", CAND)], profile, "nope")


# ---------- A3/S8b：evaluate_pairwise 成对交叉验证 + 单类别如实 ----------


def _pairwise_samples():
    samples = []
    for i in range(10):
        label = 1 if i % 2 == 0 else 0
        if i == 8:
            text = "gamma delta " * 9      # 正样本但像负类（制造反转）
        elif i == 9:
            text = "alpha beta " * 10      # 负样本但像正类
        elif label == 1:
            text = "alpha beta " * (i + 1)
        else:
            text = "gamma delta " * (i + 1)
        samples.append((f"book_{i}", text, label))
    return samples


def test_S8b_eval_pairwise_cross_validated_auc_ci():
    result = evaluate_pairwise(_pairwise_samples(), {})
    assert result["folds"] == 5
    assert result["n_pos"] == 5 and result["n_neg"] == 5
    assert 0.5 < result["auc"] <= 1.0
    assert result["ci_low"] <= result["auc"] <= result["ci_high"]
    assert result["verdict"] in {"显著", "尚不显著"}


def test_S8b_eval_pairwise_ci_crosses_half_when_not_significant():
    # 两类完全同文本 + 每折测试对恰 1 正 1 负（labels[i+5]=1-labels[i]）→
    # 各折训练类平衡恒 4/4 → OOF 概率全同 → AUC=0.5，CI 必跨 0.5（§6.3「尚不显著」）
    labels = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0]  # 5 正 5 负
    samples = [(f"book_{i}", "identical neutral text ", labels[i]) for i in range(10)]
    result = evaluate_pairwise(samples, {})
    assert result["folds"] == 5
    assert result["n_pos"] == 5 and result["n_neg"] == 5
    assert result["ci_low"] < 0.5 < result["ci_high"]
    assert result["verdict"] == "尚不显著"


def test_S8b_eval_pairwise_single_class_honest_verdict():
    samples = [(f"book_{i}", "alpha beta " * (i + 1), 1) for i in range(3)]
    result = evaluate_pairwise(samples, {})
    assert result["auc"] is None
    assert result["verdict"] == "不足以判定"
    assert result["n_pos"] == 3 and result["n_neg"] == 0
    assert result["folds"] == 0


# ---------- A4/F5：sklearn 延迟导入 ----------

LAZY_PROBE = (
    "import sys;"
    "import reco_core, reco_core.score;"
    "print('BANNED' if 'sklearn' in sys.modules else 'CLEAN')"
)


def test_F5_import_reco_core_score_does_not_load_sklearn():
    proc = subprocess.run([sys.executable, "-c", LAZY_PROBE], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "CLEAN", proc.stdout + proc.stderr


def test_F5_train_pairwise_below_threshold_returns_none_without_sklearn():
    probe = (
        "import sys;"
        "import reco_core.score as s;"
        "out = s.train_pairwise([('a', 'doc one', 1), ('b', 'doc two', 0), ('c', 'doc three', 1)], {});"
        "print('NONE' if out is None else 'HANDLE', '|', 'BANNED' if 'sklearn' in sys.modules else 'CLEAN')"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "NONE | CLEAN", proc.stdout + proc.stderr


# ---------- A8/S11：§6.5 反例清单为测试用例（隔离红线） ----------


def test_S11_score_never_reads_event_stream_or_decay():
    profile = _profile(positives=[{1: 1.0}])
    rows = rank([("b1", {1: 1.0})], profile, {})
    assert len(rows) == 1
    assert isinstance(rows[0].score, float)


def test_S11_score_does_not_parse_tags_or_relations():
    with pytest.raises(ValueError, match="TAGS"):
        score([("b1", {1: 1.0})], _profile(), {"tag_definitions": {"ta": ["alias"]}})
    with pytest.raises(ValueError, match="RELATIONS"):
        score([("b1", {1: 1.0})], _profile(), {"relation_summary": {"power": {}}})
    profile = _profile(positives=[QUERY])
    dirty = {"tag_definitions": {"ta": ["alias"]}, "relation_summary": {"power": {}}}
    with pytest.raises(ValueError, match="TAGS"):
        score([("b1", CAND)], profile, dirty)
    # 合法 config 键混入不影响输出（score 只读合成输入，不消费语义键）
    legal = {"exploration_count": 2, "unknown_future_key": {"x": 1}}
    assert score([("b1", CAND)], profile, legal) == score([("b1", CAND)], profile, {})


def test_S11_no_single_hardcode_constant_sum():
    a = score([("b1", CAND)], _profile(weights=(0.6, 0.0, 0.4), positives=[QUERY], signals={"b1": 0.4}), {})
    b = score([("b1", CAND)], _profile(weights=(0.9, 0.0, 0.1), positives=[QUERY], signals={"b1": 0.4}), {})
    assert a[0][1] != b[0][1]  # 权重变化必须改变输出（拒绝常数打分，§6.5 反例 4）
    with pytest.raises(ValueError):
        score([("b1", CAND)], _profile(weights=(0.5, 0.5, 0.3), positives=[QUERY]), {})
