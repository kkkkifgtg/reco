"""W6-A（t12）——record_book_tag 接管语义对齐（audit-log 第 7 条裁定）。

裁定语义：D1 禁令的施动者是「自动提取」而非人工。
- auto 覆盖 manual/user → 拒绝（ManualTagProtected，不变）；
- 人工互可接管（manual→user、user→manual）→ 放行（人工优先语义的必要组成，
  否则 GUI 二次编辑在首次 manual 落位后即死锁）。
"""
from __future__ import annotations

import pytest

from reco_labels import InMemoryLabelsStore, ManualTagProtected


def _setup(store, source):
    store.record_book_tag(5, "mech.hypnosis", source=source, confidence=1.0,
                          basis_json='{"by":"' + source + '"}',
                          updated_at="2026-11-20T00:00:00")


def test_manual_to_user_takeover_allowed():
    """人工接管：manual 槽位可被 user 行接管（GUI 二次编辑路径）。"""
    store = InMemoryLabelsStore()
    _setup(store, "manual")
    store.record_book_tag(5, "mech.hypnosis", source="user", confidence=1.0,
                          basis_json='{"by":"user2"}',
                          updated_at="2026-11-21T00:00:00")
    row = next(r for r in store.iter_book_tags() if r.book_id == 5)
    assert row.source == "user"
    assert row.basis_json == '{"by":"user2"}'


def test_user_to_manual_takeover_allowed():
    """人工接管：user 槽位可被 manual 行接管（补标定路径）。"""
    store = InMemoryLabelsStore()
    _setup(store, "user")
    store.record_book_tag(5, "mech.hypnosis", source="manual", confidence=1.0,
                          basis_json='{"by":"manual2"}',
                          updated_at="2026-11-21T00:00:00")
    row = next(r for r in store.iter_book_tags() if r.book_id == 5)
    assert row.source == "manual"
    assert row.basis_json == '{"by":"manual2"}'


def test_auto_over_manual_still_rejected():
    """回归守卫：auto 覆盖 manual 仍被写入层拒绝（D1 主防线不松动）。"""
    store = InMemoryLabelsStore()
    _setup(store, "manual")
    with pytest.raises(ManualTagProtected):
        store.record_book_tag(5, "mech.hypnosis", source="auto", confidence=0.1,
                              basis_json="{}", updated_at="2026-11-21T00:00:00")


def test_auto_over_user_still_rejected():
    """回归守卫：auto 覆盖 user 仍被写入层拒绝。"""
    store = InMemoryLabelsStore()
    _setup(store, "user")
    with pytest.raises(ManualTagProtected):
        store.record_book_tag(5, "mech.hypnosis", source="auto", confidence=0.1,
                              basis_json="{}", updated_at="2026-11-21T00:00:00")


def test_auto_over_auto_updates_freely():
    """auto→auto 自更新不受影响（W4 自动提取增量更新路径）。"""
    store = InMemoryLabelsStore()
    _setup(store, "auto")
    store.record_book_tag(5, "mech.hypnosis", source="auto", confidence=0.9,
                          basis_json='{"total":2}',
                          updated_at="2026-11-21T01:00:00")
    row = next(r for r in store.iter_book_tags() if r.book_id == 5)
    assert row.source == "auto" and row.confidence == 0.9


def test_record_tag_hits_skips_manual_but_not_user_takeover_target():
    """record_tag_hits（auto 写入入口）对 manual/user 槽位仍双双跳过——
    接管只能由人工显式调用 store.record_book_tag 完成，自动管道语义不变。"""
    from reco_labels import TagHit, record_tag_hits

    store = InMemoryLabelsStore()
    _setup(store, "user")
    hit = TagHit(tag_id="mech.hypnosis", confidence=1.0,
                 basis={"axis": "mechanism", "matches": (), "total": 1, "evidence": ()})
    result = record_tag_hits(store, 5, (hit,), now="2026-11-21T02:00:00")
    assert result.skipped_manual == ("mech.hypnosis",)
    row = next(r for r in store.iter_book_tags() if r.book_id == 5)
    assert row.source == "user"  # 未被动
