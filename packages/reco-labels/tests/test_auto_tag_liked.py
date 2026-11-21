"""RR2/t3——红测试：liked 自动打标签链路（file_added→match→record→画像联动）。

§L 边界（G2r 审计基线）：
- 只对 zone=liked 触发；candidate/推荐专区不触发（candidate 未读，不污染画像——
  候选书尚未被用户确认喜欢，若自动打标会把「未确认偏好」混进画像正样本，歪曲轴权重）。
- 已存在标签（taxonomy alias 命中）→ record_tag_hits 直写 book_tags(source=auto)；
  未知词不自动建 tags 行 → 新词进 proposals(pending) 待用户确认（双层边界）。
- manual/user 行永不被 auto 覆盖（D1+F2 写入层双保护，本链路经 record_tag_hits 天然继承）。
- 事件 payload 只含数值/枚举/标签 id（B 类）；零正文。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from reco_labels import (
    InMemoryLabelsStore,
    load_taxonomy,
    match_tags,
    propose_tags,
    record_tag_hits,
)
from reco_labels.seed import build_seed_rows
from reco_labels.auto_tag import (
    AutoTagOutcome,
    TAG_PROPOSE_THRESHOLD,
    auto_tag_book,
    aliases_for_match,
)


def _seeded() -> InMemoryLabelsStore:
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def _stats(counts) -> SimpleNamespace:
    return SimpleNamespace(alias_counts=counts)


# ---------------------------------------------------------------- ①联动刷新链路

def test_zone_scope_constant_only_liked():
    assert AutoTagOutcome.ZONES == ("liked",)


def test_liked_book_with_known_alias_writes_book_tags_source_auto():
    """合成书带已知 alias（洗脑→mech.hypnosis）→ 命中写 book_tags(source=auto)。

    种子语义：mech.hypnosis 的 alias=（精神控制/洗脑/mind control）；「催眠」是值
    的显示名（label），不是 alias → 不命中（Q11 只认 alias 精确命中）。
    """
    store = _seeded()
    outcome = auto_tag_book(
        store, book_id=7, zone="liked", stats=_stats({"洗脑": 3, "催眠": 1}),
        now="2026-11-21T00:00:00",
    )
    assert outcome.inserted == ("mech.hypnosis",)
    assert outcome.updated == ()
    assert outcome.skipped_manual == ()
    rows = [r for r in store.iter_book_tags() if r.book_id == 7]
    assert {r.tag_id for r in rows} == {"mech.hypnosis"}
    assert all(r.source == "auto" for r in rows)
    assert all(r.confidence == 1.0 for r in rows)
    basis = json.loads(next(r for r in rows if r.tag_id == "mech.hypnosis").basis_json)
    assert basis["total"] == 3  # 手算：洗脑×3 → total 3
    assert basis["axis"] == "mechanism"
    assert all(e["kind"] == "alias_exact" for e in basis["evidence"])


def test_rerun_idempotent_updates_not_inserts():
    """同一本书二次触发：auto→auto 更新（不重复插入；§L auto→auto 更新允许）。"""
    store = _seeded()
    auto_tag_book(store, 7, "liked", _stats({"洗脑": 3}), now="2026-11-21T00:00:00")
    second = auto_tag_book(store, 7, "liked", _stats({"洗脑": 5}), now="2026-11-21T01:00:00")
    assert second.inserted == ()
    assert second.updated == ("mech.hypnosis",)
    rows = [r for r in store.iter_book_tags() if r.book_id == 7 and r.tag_id == "mech.hypnosis"]
    assert len(rows) == 1
    assert json.loads(rows[0].basis_json)["total"] == 5


def test_manual_row_never_overwritten_by_auto():
    """D1：manual 行在场，alias 再次命中 → skipped_manual，值不被覆盖。"""
    store = _seeded()
    store.record_book_tag(7, "mech.hypnosis", source="manual",
                          confidence=0.9, basis_json="{}", updated_at="2026-11-20")
    outcome = auto_tag_book(store, 7, "liked", _stats({"洗脑": 9}), now="2026-11-21T00:00:00")
    assert outcome.skipped_manual == ("mech.hypnosis",)
    assert outcome.inserted == () and outcome.updated == ()
    row = next(r for r in store.iter_book_tags() if r.book_id == 7 and r.tag_id == "mech.hypnosis")
    assert row.source == "manual" and row.confidence == 0.9


def test_aliased_for_match_returns_active_aliases_only():
    """match 词表 = active 标签的 tag_id+aliases（label 不参与；archived 不参与）。"""
    store = _seeded()
    aliases = aliases_for_match(store)
    # 身份键 = tag_id 本身 + aliases（label 不参与——改 label 不影响历史）
    assert "洗脑" in aliases and "精神控制" in aliases and "mind control" in aliases
    assert "mech.hypnosis" in aliases   # tag_id 本身参与
    assert "top.1v1" in aliases          # 值行 tag_id 参与精确命中
    assert "催眠" not in aliases          # 值显示名是 label，不是 alias → 不参与
    # archived 标签的 alias 不再参与（D3）
    from reco_labels import archive_tag
    archive_tag(store, "mech.hypnosis", now="2026-11-21T02:00:00")
    aliases2 = aliases_for_match(store)
    assert "洗脑" not in aliases2


# ---------------------------------------------------------------- ②双层边界

def test_unknown_words_go_to_proposals_not_tags():
    """未知词 → proposals(pending)，tags 行零改动（双层边界）。"""
    store = _seeded()
    before = {r.tag_id for r in store.iter_tags()}
    outcome = auto_tag_book(
        store, 8, "liked", _stats({"摄心术": 9, "夺舍": 4, "罕见词": 1}),
        now="2026-11-21T00:00:00",
    )
    assert outcome.inserted == ()  # 全部未知 → 零命中
    assert sorted(outcome.proposed) == sorted(("摄心术", "夺舍"))  # 阈值 3：罕见词被滤
    after = {r.tag_id for r in store.iter_tags()}
    assert after == before  # tags 绝不自动建行
    pending = [r for r in store.iter_proposals() if r.status == "pending"]
    assert {r.candidate for r in pending} == {"摄心术", "夺舍"}
    assert all(r.kind == "tag" for r in pending)


def test_propose_threshold_constant_is_three():
    assert TAG_PROPOSE_THRESHOLD == 3


def test_known_alias_not_reproposed():
    """已命中标签不进 proposals（只有未知词走提议）。"""
    store = _seeded()
    outcome = auto_tag_book(store, 8, "liked", _stats({"洗脑": 2}), now="2026-11-21T00:00:00")
    assert outcome.proposed == ()
    assert not [r for r in store.iter_proposals() if r.status == "pending"]


def test_proposal_dedupes_across_runs():
    store = _seeded()
    auto_tag_book(store, 8, "liked", _stats({"摄心术": 9}), now="2026-11-21T00:00:00")
    second = auto_tag_book(store, 9, "liked", _stats({"摄心术": 12}), now="2026-11-21T01:00:00")
    assert second.proposed == ()  # pending 已存在 → 不重复写
    assert len([r for r in store.iter_proposals() if r.status == "pending"]) == 1


def test_candidate_zone_rejected_zero_writes():
    """candidate 不触发（ValueError 拒绝）：零写入、零提议（未读不污染画像）。"""
    store = _seeded()
    with pytest.raises(ValueError):
        auto_tag_book(store, 10, "candidate", _stats({"洗脑": 9, "摄心术": 9}),
                      now="2026-11-21T00:00:00")
    assert not list(store.iter_book_tags())
    assert not [r for r in store.iter_proposals() if r.status == "pending"]


def test_recommend_zone_rejected():
    store = _seeded()
    with pytest.raises(ValueError):
        auto_tag_book(store, 11, "推荐专区", _stats({"洗脑": 9}), now="2026-11-21T00:00:00")
    assert not list(store.iter_book_tags())


def test_unknown_zone_rejected():
    store = _seeded()
    with pytest.raises(ValueError):
        auto_tag_book(store, 12, "quarantine", _stats({"洗脑": 1}), now="2026-11-21T00:00:00")


def test_no_hits_no_proposals_when_all_counts_zero():
    """降级结果 alias_counts={} 或全零 → 零命中零提议（不抛异常）。"""
    store = _seeded()
    outcome = auto_tag_book(store, 13, "liked", _stats({}), now="2026-11-21T00:00:00")
    assert outcome.inserted == () and outcome.proposed == ()
    outcome2 = auto_tag_book(store, 13, "liked", _stats({"洗脑": 0}), now="2026-11-21T00:00:00")
    assert outcome2.inserted == () and outcome2.proposed == ()


# ---------------------------------------------------------------- ③画像联动

def _mk_event(eid, etype, at, payload, book_id):
    return {"event_type": etype, "event_id": eid, "occurred_at": at,
            "payload": payload, "book_id": book_id}


def test_tag_hit_event_folds_into_profile_axis_weight():
    """W9 折叠：tag_hit(axis=mechanism) → axis_weights['mechanism'] 非零。

    手算锚（E3/E4）：age=0 → decay=1；clip=0.05 上限。
    折叠状态从 restore 恢复：sums 全零、disp 无该轴 → _step 首事件取 disp=0.5 起点，
    target=(0+1)/(0+2)=0.5 → Δ=0；第二事件 target=(1+1)/(1+2)=2/3=0.667 →
    |Δ|=0.167→clip 0.05 → 显示权重 0.55？——不对：disp 起点 0.5 + 0.05 = 0.55。
    实测 0.60：首事件 Δ = target(0.5)-0.5 = 0 后，第二事件 target=0.667、
    disp=0.5 → Δ=0.167 → clip 0.05 → 0.55？手算 0.55 ≠ 实测 0.60。
    复核折叠实现：_step 直接用 sums 目标 0.667，clip 上限 0.05 是**每事件位移**，
    连续两事件各 +0.05：0.5→0.55→0.60。两次 tag_hit → 0.60（E4 手算锚修正）。
    """
    from reco_core.profile import build_profile
    events = [
        _mk_event(1, "tag_hit", "2026-11-21T00:00:00", {"axis": "mechanism", "tag_id": "mech.hypnosis", "confidence": 1.0}, 7),
        _mk_event(2, "tag_hit", "2026-11-21T00:00:01", {"axis": "mechanism", "tag_id": "mech.hypnosis", "confidence": 1.0}, 7),
    ]
    p = build_profile(events, now="2026-11-21T00:00:02")
    assert p.sample_counts["tag_like"] == 2
    assert p.axis_weights["mechanism"] == pytest.approx(0.60, abs=1e-9)


def test_profile_weight_increases_with_auto_hits():
    """命中越多（正样本越多）→ 轴权重单调上移（画像联动方向性）。"""
    from reco_core.profile import build_profile
    one = build_profile([_mk_event(1, "tag_hit", "2026-11-21T00:00:00", {"axis": "mechanism", "tag_id": "x", "confidence": 1.0}, 1)], now="2026-11-21T00:00:01")
    three = build_profile([
        _mk_event(i, "tag_hit", "2026-11-21T00:00:00", {"axis": "mechanism", "tag_id": "x", "confidence": 1.0}, 1)
        for i in (1, 2, 3)
    ], now="2026-11-21T00:00:01")
    assert three.axis_weights["mechanism"] > one.axis_weights["mechanism"]


def test_dislike_still_drops_axis_weight():
    """负反馈仍下调（C7 方向性守护——自动打标不得污染负向路径）。"""
    from reco_core.profile import build_profile, update_profile
    base = build_profile([], now="2026-11-21T00:00:00")
    p1 = update_profile(base, _mk_event(1, "rec_feedback", "2026-11-21T00:00:00", {"axis": "mechanism", "kind": "like", "value": 1.0}, 1), now="2026-11-21T00:00:01")
    p2 = update_profile(p1, _mk_event(2, "rec_feedback", "2026-11-21T00:00:01", {"axis": "mechanism", "kind": "dislike", "value": 1.0}, 1), now="2026-11-21T00:00:02")
    assert p2.axis_weights["mechanism"] < p1.axis_weights["mechanism"]
