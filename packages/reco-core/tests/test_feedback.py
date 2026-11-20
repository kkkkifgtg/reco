"""工单 10——不合口味反馈闭环（红→绿测试 S1-S7）。

黄金期望值独立来源（E4，禁自洽，2026-11-21 venv stdlib 直算冻结）：
- 余弦金标：cos(10°)=0.984807753012208、cos(30°)=0.8660254037844387、
  cos(45°)=0.7071067811865476、cos(60°)=0.5000000000000001、
  cos(75°)=0.25881904510252074；尾档 head×0.05=0.0492403876506104。
- 轴位移目标差（W9 Laplace (sp+1)/(sp+sn+2)，clip |Δ|≤0.05）：fresh 0.5 →
  dislike 目标 1/3（clip 后 -0.05 → 0.45）；like 目标 2/3（+0.05 → 0.55）；
  二次 dislike → 0.40。
- W9 decay 交叉验证：0.5^(30/90)=0.7937005259840998（tag 档 30 天）。
- 过期时刻：now=2025-11-21T10:00:00Z + 90d → 2026-02-19T10:00:00+00:00；
  +30d → 2025-12-21T10:00:00+00:00。
- 画像 fixture weights=(1,0,0)（Σ=1 合规）：score = w_sim × sim，尾档分即 TAIL。
验收映射（.scratch/reco-impl/issues/10-feedback.md 验收项）：
  A1 负反馈消失（降权 95%+，静音 30/90d 过期自动解除，非硬排除）
  A2 唯一硬过滤=去重；负样本仍进候选/特征库，只分数极低
  A3 事件 append-only + 重放含反馈逐位复现
  A4 「加入候选池/标为喜欢」回流入口
隔离红线：feedback 不做第二排序路径（必须走 W8 rank 唯一入口）。
W9 契约适配：build_profile 重放基线恒 v1；verify_replay(events, 同流重建 v1)
返回 ReplayReport(ok, diffs)；跨版本逐位复现改为内容等值断言（axis/计数/完整性）。
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

import reco_core.feedback as feedback_module
from reco_core.feedback import (
    FEEDBACK_MUTE_RATIO,
    FEEDBACK_VERSION,
    InvalidFeedback,
    MuteWindow,
    _score_feedback_aware,
    feedback_effects,
    handle_feedback,
    persist_feedback,
    rehydrate_from_events,
    rank_only,
)
from reco_core.profile import Confidence, ProfileData, build_profile, verify_replay

NOW = datetime(2025, 11, 21, 10, 0, 0, tzinfo=timezone.utc)
CFG = {"half_life_days": {"tag": 90.0, "signal": 30.0, "stable": 180.0}}

QUERY = {10: 1.0}
COS10 = 0.984807753012208
SIN10 = 0.17364817766693033
COS30 = 0.8660254037844387
SIN30 = 0.5
COS45 = 0.7071067811865476
SIN45 = 0.7071067811865476
COS60 = 0.5000000000000001
SIN60 = 0.8660254037844387
COS75 = 0.25881904510252074
SIN75 = 0.9659258262890683
TAIL = 0.048525574959967135          # 【换核演练】simE(cos10°)×0.05=1/(3-2cos10°)×0.05；余弦原值 0.0492403876506104


def _profile_now(axis_w=0.5):
    """v1 画像：单轴 mood.dark 显示权重 0.5、无事件历史（fresh）；weights=(1,0,0)。"""
    return ProfileData(
        version=1, updated_at="2025-11-21T00:00:00+00:00",
        axis_weights={"mood.dark": axis_w},
        sample_counts={"tag_like": 0, "tag_dislike": 0, "behavior": 0,
                       "per_axis": {"mood.dark": {"positive": 0.0, "negative": 0.0}}},
        top_tags=(("mood.dark", axis_w),),
        weights=(1.0, 0.0, 0.0), weight_version="weights/1", weight_reason="seed",
        half_life_days={"tag": 90.0, "signal": 30.0, "stable": 180.0},
        behavior_signals={}, completeness="full", event_id_high_water=None,
        confidence=Confidence(value=0.5, n_pos=0, n_neg=0, cap=0.3, display="fresh"),
        generated_events=(),
    )


def _dislike_event(axis="mood.dark", value=1.0, eid=7, occurred=None, book="B1"):
    return {
        "event_type": "rec_feedback", "event_id": eid,
        "occurred_at": (occurred or NOW).isoformat(),
        "payload": {"axis": axis, "kind": "dislike", "value": value},
        "book_id": book,
    }


def ids_of(rows):
    return [r.book_id for r in rows]


# ---------- S1：负反馈立即消失（降权 ≥95%），非硬排除 ----------


def test_S1_dislike_mutes_candidate_via_unique_rank():
    profile = _profile_now()
    candidates = [
        ("B1", {10: COS10, 11: SIN10}),   # 与正样本质心近同向 → 原第一
        ("B2", {10: COS60, 11: SIN60}),   # 中游
        ("B3", {10: COS45, 11: SIN45}),   # 原次席
    ]
    out = handle_feedback("dislike", "B1", candidates, profile, CFG,
                          positive_vectors=[QUERY], now=NOW)
    rows = out["rank_rows"]
    ids = ids_of(rows)
    assert "B1" in ids  # 非硬排除：仍在输出里
    assert ids[0] == "B3" and ids[1] == "B2"  # 无关候选相对次序不变
    assert ids[-1] == "B1"  # 立即沉底（从推荐头部消失）
    row_b1 = rows[-1]
    assert row_b1.score == pytest.approx(TAIL, abs=1e-12)  # 降权 95%+
    assert row_b1.is_exploration is False  # 静音候选不得被探索位捞回
    assert any(getattr(r, "source", "") == "muted" for r in row_b1.reasons)
    assert out["display"] == "已调整：mood.dark 维度权重已下调"
    assert out["profile"].version == 2  # 画像秒级更新（W9 update_profile）


def test_S1_mute_ratio_and_floor_positive():
    assert FEEDBACK_MUTE_RATIO == pytest.approx(0.05, abs=1e-12)  # 降权 95%+
    out = handle_feedback("dislike", "B1", [("B1", {10: COS10, 11: SIN10})],
                          _profile_now(), CFG, positive_vectors=[QUERY], now=NOW)
    assert out["rank_rows"][-1].score == pytest.approx(TAIL, abs=1e-12)  # >0：不归零


def test_S1_unrelated_candidates_untouched():
    out = handle_feedback("dislike", "B1",
                          [("B2", {10: COS60, 11: SIN60}), ("B3", {10: COS45, 11: SIN45})],
                          _profile_now(), CFG, positive_vectors=[QUERY], now=NOW)
    assert ids_of(out["rank_rows"]) == ["B3", "B2"]  # 无关候选排序不变
    assert all(not r.is_exploration for r in out["rank_rows"])  # 池不足以开探索


def test_S1_repeated_dislike_replaces_not_accumulates():
    profile = _profile_now()
    candidates = [("B1", {10: COS10, 11: SIN10}), ("B2", {10: COS60, 11: SIN60})]
    out = handle_feedback("dislike", "B1", candidates, profile, CFG,
                          positive_vectors=[QUERY], now=NOW)
    out2 = handle_feedback("dislike", "B1", candidates, out["profile"], CFG,
                           mute_by_key={"B1": out["mute_window"]},
                           positive_vectors=[QUERY], now=NOW)
    assert out2["mute_window"].remaining_days == pytest.approx(90.0, abs=1e-6)  # 替换而非累积
    assert ids_of(out2["rank_rows"])[-1] == "B1"


def test_S1_dislike_on_missing_book_is_noop_rank():
    out = handle_feedback("dislike", "NOPE", [("B2", {10: COS60, 11: SIN60})],
                          _profile_now(), CFG, positive_vectors=[QUERY], now=NOW)
    assert ids_of(out["rank_rows"]) == ["B2"]  # 候选不在场：只更新画像，不伪造排序变化


def test_S1_muted_never_returned_via_exploration_slot():
    # 转向夹具（指纹已冻结，E4）：顶席 C1（cos15°）不进池；BM=cos30° 是池内次席。
    # 无排除时池 [BM,C2,E7,F2] 选位必含 BM（F2 转向）；排除后池 [C2,E7,F2]
    # （3>配额 2，探索开启）选位无 BM。
    profile = _profile_now()
    candidates = [
        ("C1", {10: 0.9659258262890683, 11: 0.25881904510252074}),  # cos15°：顶席，池外
        ("BM", {10: COS30, 11: SIN30}),   # cos30°：静音（行为信号惩罚，非低相似）
        ("C2", {10: COS45, 11: SIN45}),   # cos45°：池内
        ("E7", {10: 0.05, 11: 0.99}),     # 近正交：池内（转向 id）
        ("F2", {10: 0.04, 11: 0.98}),     # 近正交：池内（F2 转向 id）
    ]
    out = handle_feedback("dislike", "BM", candidates, profile, CFG,
                          mute_by_key={"BM": MuteWindow("dislike", "2026-02-19T10:00:00+00:00", 90.0, "mood.dark")},
                          positive_vectors=[QUERY], now=NOW)
    rows = out["rank_rows"]
    explorers = [r.book_id for r in rows if r.is_exploration]
    assert sorted(explorers) == ["C2", "E7"]  # 排除态（指纹冻结转向）：BM 不被捞回
    assert all(not r.is_exploration for r in rows if r.book_id == "BM")


# ---------- S2：静音窗过期自动解除（30/90 天分档） ----------


def test_S2_mute_window_expiry_90d_tag_axis():
    effects = feedback_effects(_dislike_event(), NOW)
    assert effects["book_id"] == "B1"
    assert effects["axis"] == "mood.dark"
    assert effects["mute_expired_at"] == "2026-02-19T10:00:00+00:00"
    assert effects["mute_window"].remaining_days == pytest.approx(90.0, abs=1e-6)
    assert effects["fold_weight"] == pytest.approx(1.0, abs=1e-12)  # 0.5^(0/90)，age=0
    assert effects["display"] == "已调整：mood.dark 维度权重已下调"


def test_S2_mute_window_expiry_30d_signal_axis():
    effects = feedback_effects(_dislike_event(axis="signal.dwell", eid=9), NOW)
    assert effects["mute_expired_at"] == "2025-12-21T10:00:00+00:00"
    assert effects["mute_window"].remaining_days == pytest.approx(30.0, abs=1e-6)


def test_S2_expired_mute_lifts_penalty_within_rank():
    profile = _profile_now()
    candidates = [("B1", {10: COS10, 11: SIN10}), ("B2", {10: COS60, 11: SIN60})]
    out = handle_feedback("dislike", "B1", candidates, profile, CFG,
                          positive_vectors=[QUERY], now=NOW)
    assert ids_of(out["rank_rows"])[-1] == "B1"
    # 91 天后同一批候选重排：静音已过期 → B1 回到第一（无残留）
    out2 = rank_only(candidates, out["profile"],
                     mute={"B1": out["mute_window"]},
                     positive_vectors=[QUERY], now=NOW + timedelta(days=91))
    assert ids_of(out2["rank_rows"])[0] == "B1"
    assert out2["rank_rows"][0].score == pytest.approx(0.9705114991993427, abs=1e-9)  # 【换核演练】simE(cos10°)


# ---------- S3：画像秒级更新（W9 折叠对接） ----------


def test_S3_profile_axis_weight_drops_on_dislike():
    out = handle_feedback("dislike", "B1", [("B1", {10: 1.0})], _profile_now(axis_w=0.5),
                          CFG, positive_vectors=[QUERY], now=NOW)
    p2 = out["profile"]
    assert p2.version == 2
    assert p2.axis_weights["mood.dark"] == pytest.approx(0.45, abs=1e-12)  # 0.5 + clip(-0.05)
    assert p2.sample_counts["tag_dislike"] == 1
    assert out["display"] == "已调整：mood.dark 维度权重已下调"


def test_S3_like_raises_axis_weight_and_reflow():
    out = handle_feedback("like", "B2", [("B2", {10: COS60, 11: SIN60})],
                          _profile_now(axis_w=0.5), CFG, positive_vectors=[QUERY], now=NOW)
    assert out["profile"].axis_weights["mood.dark"] == pytest.approx(0.55, abs=1e-12)
    assert out["profile"].sample_counts["tag_like"] == 1
    assert out["display"] == "已调整：mood.dark 维度权重已上调"
    assert out["rank_rows"][0].book_id == "B2"  # 「标为喜欢」回流：仍在排序内且靠前


# ---------- S4：事件落库 append-only + 重放复现 ----------


class FakeAppendOnlyStore:
    def __init__(self):
        self.events = []
        self._next_id = 6

    def append(self, event):
        if int(event["event_id"]) <= self._next_id:
            raise InvalidFeedback("append-only 违规：追溯插入被拒绝")
        self.events.append(dict(event))
        self._next_id = int(event["event_id"])

    def read_events(self):
        return list(self.events)


def test_S4_append_only_and_replay_reproduces():
    store = FakeAppendOnlyStore()
    p1 = build_profile([], config=CFG, now=NOW)
    assert p1.version == 1
    rec = persist_feedback("dislike", "B1", axis="mood.dark", store=store,
                           event_id=7, now=NOW)
    assert rec["event"]["event_type"] == "rec_feedback"
    assert rec["event"]["payload"]["kind"] == "dislike"
    assert rec["appended"] is True
    with pytest.raises(InvalidFeedback):
        store.append(_dislike_event(eid=4, book="B0"))  # 追溯插入拒绝（append-only）
    rebuilt = rehydrate_from_events(store.read_events(), config=CFG, now=NOW)
    assert rebuilt.version == 1  # W9 契约：重放基线恒 v1
    assert rebuilt.axis_weights["mood.dark"] == pytest.approx(0.45, abs=1e-9)  # 独立冻结期望
    assert rebuilt.completeness == "full"


def test_S4_replay_includes_feedback_bitwise():
    store = FakeAppendOnlyStore()
    persist_feedback("dislike", "B1", axis="mood.dark", store=store, event_id=7, now=NOW)
    rebuilt_v1 = rehydrate_from_events(store.read_events(), config=CFG, now=NOW)
    assert rebuilt_v1.axis_weights["mood.dark"] == pytest.approx(0.45, abs=1e-9)
    persist_feedback("dislike", "B1", axis="mood.dark", store=store, event_id=8, now=NOW)
    candidates = [("B1", {10: COS10, 11: SIN10}), ("B2", {10: COS60, 11: SIN60})]
    out = handle_feedback("dislike", "B1", candidates, rebuilt_v1, CFG,
                          positive_vectors=[QUERY], now=NOW)  # 折叠第二条反馈 → v2
    assert out["profile"].version == 2
    assert out["profile"].axis_weights["mood.dark"] == pytest.approx(0.40, abs=1e-9)  # 0.45 - clip
    assert out["profile"].sample_counts["tag_dislike"] == 2
    # 同流重放与增量链内容逐位一致（跨版本复现；版本基线按 W9 契约恒 v1）
    full = rehydrate_from_events(store.read_events(), config=CFG, now=NOW)
    assert full.axis_weights["mood.dark"] == pytest.approx(
        out["profile"].axis_weights["mood.dark"], abs=1e-9)
    assert full.sample_counts["tag_dislike"] == out["profile"].sample_counts["tag_dislike"]
    assert full.completeness == out["profile"].completeness == "full"
    report = verify_replay(store.read_events(), full, config=CFG, now=NOW)  # W9 重放校验
    assert report.ok is True
    assert report.diffs == ()


def test_S4_persist_requires_axis_for_dislike():
    with pytest.raises(InvalidFeedback):
        persist_feedback("dislike", "B1", axis=None, store=None, now=NOW)


# ---------- S5：静音判定与身份（去重键） ----------


def test_S5_identity_rule_same_dedup_key_muted():
    a = ("X1", {"dedup_key": "hash:aa", "vector": {10: 1.0}})
    b = ("X2", {"dedup_key": "hash:aa", "vector": {10: 1.0}})  # 同名同大小同去重键
    c = ("X3", {"vector": {10: 1.0}})  # 无去重键，仅 id 不同
    mute = {"hash:aa": MuteWindow("dislike", "2026-02-19T10:00:00+00:00", 90.0, "mood.dark")}
    out = rank_only([a, b, c], _profile_now(), mute=mute, positive_vectors=[QUERY], now=NOW)
    ids = ids_of(out["rank_rows"])
    assert ids == ["X3", "X1", "X2"]  # X1/X2 共享静音（同去重键=同一本书），沉底
    assert out["muted"] == ("X1", "X2")  # 静音 id 集合（探索位排除依据）
    assert out["rank_rows"][-1].score == pytest.approx(0.05, abs=1e-12)  # 0.05×w_sim×1.0（sim=1）
    assert out["rank_rows"][0].score == pytest.approx(0.95, abs=1e-12)  # X3 未静音：0.95×1.0


def test_S5_missing_dedup_key_falls_back_to_book_id():
    mute = {"B7": MuteWindow("dislike", "2026-02-19T10:00:00+00:00", 90.0, "mood.dark")}
    out = rank_only(
        [("B7", {10: 1.0}), ("B8", {10: COS45, 11: SIN45})],  # B8 部分对齐：sim=cos45°
        _profile_now(), mute=mute, positive_vectors=[QUERY], now=NOW)
    ids = ids_of(out["rank_rows"])
    assert ids == ["B8", "B7"]  # 部分对齐（0.6717）> 静音尾档（0.0492）
    assert out["rank_rows"][0].score == pytest.approx(0.95 * 0.6306019374818708, abs=1e-12)  # B8 不受静音影响【换核演练】0.95×simE(cos45°)
    assert out["rank_rows"][-1].score == pytest.approx(0.05, abs=1e-12)  # 0.05×w_sim×1.0（sim=1）


# ---------- S5b：回流入口（加入候选池不进口味事件流） ----------


class FakeAppendOnlyPool:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(dict(event))
        return dict(event)


def test_S5b_add_to_pool_not_a_ranking_event():
    store = FakeAppendOnlyPool()
    out = persist_feedback("add_to_pool", "B9", axis=None, store=store,
                           event_id=7, now=NOW)
    assert out["appended"] is True
    rec = out["event"]
    assert rec["event_type"] == "pool_added"  # 不进口味事件流（§7 隔离）
    assert rec["book_id"] == "B9"
    profile = rehydrate_from_events(store.events, config=CFG, now=NOW)
    assert profile.version == 1  # pool_added 不被画像折叠（空口味流 → v1）


def test_S5b_invalid_feedback_rejected():
    with pytest.raises(InvalidFeedback):
        persist_feedback("meh", "B1", axis="mood.dark", store=None, now=NOW)


# ---------- S6：隔离红线：不做第二排序路径 + 零跨包引用 ----------


def test_S6_no_second_ranking_path():
    src = inspect.getsource(feedback_module)
    assert "reco_core.score" in src  # 唯一排序入口复用（W8）
    for banned in ("def _rank", "def rank(", "def score(", "sorted(rows"):
        assert banned not in src  # 本模块内不得出现第二条排序实现


def test_S6_feedback_never_references_foreign_packages():
    src = inspect.getsource(feedback_module)
    for banned in ("reco_store", "reco_io", "reco_relations", "reco_labels", "sklearn"):
        assert banned not in src  # 零跨包引用（依赖方向 §4.2）；重型依赖禁触（F5）


# ---------- S7：满饱和（§13）：全静音仍输出、分数 >0 ----------


def test_S7_full_saturation_floor_not_zero():
    profile = _profile_now()
    candidates = [
        ("B1", {10: COS30, 11: SIN30}),
        ("B2", {10: COS60, 11: SIN60}),
        ("B3", {10: COS75, 11: SIN75}),
    ]
    out = handle_feedback("dislike", "B1", candidates, profile, CFG,
                          positive_vectors=[QUERY], now=NOW)
    mute = {"B1": out["mute_window"]}
    out = handle_feedback("dislike", "B2", candidates, out["profile"], CFG,
                          mute_by_key=mute, positive_vectors=[QUERY], now=NOW)
    mute = {**mute, "B2": out["mute_window"]}
    out = handle_feedback("dislike", "B3", candidates, out["profile"], CFG,
                          mute_by_key=mute, positive_vectors=[QUERY], now=NOW)
    rows = out["rank_rows"]
    assert len(rows) == 3  # 不硬排除：全体仍在输出
    assert min(r.score for r in rows) > 0.0  # 最低权重不为 0（§13 负样本饱和）
    assert out["display"] == "口味可能已变：多个候选已被静音"


def test_S10_version_exported():
    assert FEEDBACK_VERSION == "feedback/1"
