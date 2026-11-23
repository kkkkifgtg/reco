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

def test_zone_scope_constants():
    assert AutoTagOutcome.ZONES == ("liked", "candidate")  # #83：candidate 也打标


def test_liked_book_with_known_alias_writes_book_tags_source_auto():
    """合成书带已知 alias 与值行显示名 → 各自命中写 book_tags(source=auto)。

    种子语义：mech.hypnosis 的 alias=（精神控制/洗脑/mind control）；#83 起值行
    显示名也参与匹配：「催眠」→ mech.催眠 同样命中。
    """
    store = _seeded()
    outcome = auto_tag_book(
        store, book_id=7, zone="liked", stats=_stats({"洗脑": 3, "催眠": 1}),
        now="2026-11-21T00:00:00",
    )
    # 洗脑（alias）与催眠（#83 显示名）都解析到 mech.hypnosis → 同标签合并一个命中
    assert outcome.inserted == ("mech.hypnosis",)
    assert outcome.updated == ()
    assert outcome.skipped_manual == ()
    rows = [r for r in store.iter_book_tags() if r.book_id == 7]
    assert {r.tag_id for r in rows} == {"mech.hypnosis"}
    assert all(r.source == "auto" for r in rows)
    assert all(r.confidence == 1.0 for r in rows)
    basis = json.loads(next(r for r in rows if r.tag_id == "mech.hypnosis").basis_json)
    assert basis["total"] == 4  # 手算：洗脑×3 + 催眠×1 → total 4
    assert basis["axis"] == "mechanism"
    assert {m["alias"] for m in basis["matches"]} == {"洗脑", "催眠"}
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
    """match 词表 = active 标签的 tag_id+aliases+值行显示名（#83；archived 不参与）。"""
    store = _seeded()
    aliases = aliases_for_match(store)
    # 身份/匹配键 = tag_id + aliases + 值行显示名（match_keys；#83）
    assert "洗脑" in aliases and "精神控制" in aliases and "mind control" in aliases
    assert "mech.hypnosis" in aliases   # tag_id 本身参与
    assert "top.1v1" in aliases          # 值行 tag_id 参与精确命中
    assert "催眠" in aliases              # #83：值行显示名参与匹配词表
    assert "攻" not in aliases and "HE" not in aliases  # 单字/ASCII 显示名不参与
    # archived 标签自身的键不再参与（D3）；同形词经各自存活值标签仍在（#83）
    from reco_labels import archive_tag
    archive_tag(store, "mech.hypnosis", now="2026-11-21T02:00:00")
    aliases2 = aliases_for_match(store)
    assert "mech.hypnosis" not in aliases2 and "mind control" not in aliases2
    assert "洗脑" in aliases2 and "精神控制" in aliases2  # mech.洗脑 / mech.精神控制 的显示名


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


def test_candidate_zone_tags_but_profile_folding_is_caller_side():
    """#83：candidate 也自动打标（book_tags(source=auto) + 提案）。

    画像折叠（tag_hit 事件）由调用方裁定——本层只落标注，不落 tag_hit。
    """
    store = _seeded()
    outcome = auto_tag_book(store, 10, "candidate", _stats({"洗脑": 9, "摄心术": 9}),
                            now="2026-11-21T00:00:00")
    assert set(outcome.inserted) == {"mech.hypnosis"}
    assert outcome.proposed == ("摄心术",)
    rows = [r for r in store.iter_book_tags() if r.book_id == 10]
    assert {r.tag_id for r in rows} == {"mech.hypnosis"}
    assert "tag_hit" not in [e.kind for e in store.iter_events()]  # 画像折叠不在本层


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


