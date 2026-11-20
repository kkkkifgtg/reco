"""工单 06——propose_tags：只产提案不建标签；显式确认才启用；冲突裁定事件落库。"""
from __future__ import annotations

from reco_labels import (
    InMemoryLabelsStore,
    accept_proposal,
    add_tag,
    archive_tag,
    propose_tags,
    reject_proposal,
    set_aliases,
)
from reco_labels.seed import build_seed_rows


def _store() -> InMemoryLabelsStore:
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def test_propose_writes_pending_only_never_creates_tag():
    """issue 06 验收 4：propose_tags 只产提案，落库需显式确认。"""
    store = _store()
    before_tags = {r.tag_id for r in store.iter_tags()}
    proposals = propose_tags(
        store,
        cooccurrence={"摄心术": 9, "夺舍": 4, "罕见词": 1},
        threshold=3,
        now="2026-11-21T00:00:00",
    )
    assert {p.candidate for p in proposals} == {"摄心术", "夺舍"}  # 1 < 3 被滤
    assert all(p.status == "pending" for p in proposals)
    assert all(p.kind == "tag" for p in proposals)
    after_tags = {r.tag_id for r in store.iter_tags()}
    assert after_tags == before_tags  # tags 表零改动——绝不自动创建


def test_propose_repropose_dedupes_pending():
    store = _store()
    propose_tags(store, {"摄心术": 9}, threshold=3, now="2026-11-21T00:00:00")
    second = propose_tags(store, {"摄心术": 12}, threshold=3, now="2026-11-21T01:00:00")
    assert second == ()  # pending 已存在 → 不重复写
    rows = [r for r in store.iter_proposals() if r.status == "pending"]
    assert len(rows) == 1


def test_propose_writes_event_with_payload_metadata_only():
    store = _store()
    propose_tags(store, {"摄心术": 9}, threshold=3, now="2026-11-21T00:00:00")
    events = [e for e in store.iter_events() if e.kind == "tag_proposed"]
    assert len(events) == 1
    assert events[0].payload["candidate"] == "摄心术"
    assert events[0].payload["count"] == 9
    assert events[0].payload["threshold"] == 3


def test_accept_proposal_creates_user_tag():
    """显式确认：提案 → 用户创建（created_by=user）→ 暂不自动识别（升级需 alias）。"""
    store = _store()
    propose_tags(store, {"摄心术": 9}, threshold=3, now="2026-11-21T00:00:00")
    tag = accept_proposal(store, "摄心术", axis="mechanism", now="2026-11-21T02:00:00")
    assert tag.created_by == "user"
    assert tag.aliases == ()  # user 新建不参与自动识别
    assert tag.status == "active"
    proposal = next(r for r in store.iter_proposals() if r.candidate == "摄心术")
    assert proposal.status == "accepted"
    assert next(r for r in store.iter_tags() if r.tag_id == tag.tag_id) is not None
    from reco_labels import load_taxonomy, normalize_tag

    assert normalize_tag("摄心术", load_taxonomy(store)) is None  # 无 alias 不识别


def test_accept_proposal_without_pending_raises():
    store = _store()
    try:
        accept_proposal(store, "从没提案过", axis="mechanism")
    except ValueError:
        pass
    else:
        raise AssertionError("无 pending 提案时必须拒绝")


def test_reject_proposal_rejected_status():
    store = _store()
    propose_tags(store, {"夺舍": 4}, threshold=3, now="2026-11-21T00:00:00")
    reject_proposal(store, "夺舍", now="2026-11-21T03:00:00")
    proposal = next(r for r in store.iter_proposals() if r.candidate == "夺舍")
    assert proposal.status == "rejected"


def test_tag_conflict_event_written_for_colliding_aliases():
    """冲突裁定：同 alias 两个 active 标签 → 事件落库（无正文），机器裁定+人工待裁。
    事件由写入层 record_tag_hits 产出（match_tags 是纯函数）。"""
    store = _store()
    set_aliases(store, "role.双性", ("双性属性",))
    set_aliases(store, "bpart.双性", ("双性属性",))
    from reco_labels import load_taxonomy, match_tags, record_tag_hits

    hits = match_tags(_s({"双性属性": 3}), load_taxonomy(store))
    assert [h.tag_id for h in hits] == ["bpart.双性"]
    record_tag_hits(store, 9, hits, now="2026-11-21T00:00:00")
    events = [e for e in store.iter_events() if e.kind == "tag_conflict"]
    assert len(events) == 1
    assert events[0].payload["winner"] == "bpart.双性"


def _s(counts):
    from types import SimpleNamespace

    return SimpleNamespace(alias_counts=counts)
