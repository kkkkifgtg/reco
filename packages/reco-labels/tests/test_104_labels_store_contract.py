"""#104：LabelsStore 契约一致性——同一场景在内存实现与 SQLite 适配器上必须同结果。

两套实现曾各写各的：内存侧不校验提案状态、合并时连 manual 行一起搬、
折叠事件照记不误。这组用例把「同一个存储协议」当成唯一判据，任何一侧漂移
都会在这里红。
"""
from __future__ import annotations

import pytest

from reco_labels import InMemoryLabelsStore
from reco_labels.auto_tag import StoreAdapter
from reco_labels.store import TagRow

pytestmark = pytest.mark.integration


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryLabelsStore()
        return
    from reco_store import open_store

    raw = open_store(tmp_path / "labels.db")
    # book_tags 对 books 有外键：先落两本书（空库 → id 从 1 递增）
    for ino, name in ((1, "one.txt"), (2, "two.txt")):
        raw.append_event(
            "file_added",
            payload={"path": f"F:/date/adult-reco/data/liked/{name}", "zone": "liked",
                     "ext": ".txt", "size_bytes": 100, "mtime_ns": 1,
                     "st_dev": 1, "st_ino": ino, "display_name": name,
                     "normalized_name": name, "dedup_key": f"k-{ino}"},
            occurred_at="2026-01-01T00:00:00+00:00", config_fingerprint="t/104",
        )
    try:
        yield StoreAdapter(raw)
    finally:
        raw.close()


def _tag(tag_id, *, status="active"):
    return TagRow(tag_id=tag_id, axis="mechanism", label=tag_id.split(".")[-1],
                  aliases=("别名",), parent="mech", status=status, merged_into=None,
                  created_by="user", created_at="2026-01-01T00:00:00+00:00")


def _snapshot(store):
    tags = sorted((r.tag_id, r.status, tuple(r.aliases)) for r in store.iter_tags())
    rows = sorted((r.book_id, r.tag_id, r.source, round(float(r.confidence), 3))
                  for r in store.iter_book_tags())
    proposals = sorted((r.kind, r.status, r.candidate) for r in store.iter_proposals())
    return tags, rows, proposals


def test_merge_moves_every_row_and_preserves_source(store):
    at = "2026-01-01T00:00:00+00:00"
    store.upsert_tag(_tag("mech.甲"))
    store.upsert_tag(_tag("mech.乙"))
    store.record_book_tag(1, "mech.甲", source="auto", confidence=0.9,
                          basis_json="{}", updated_at=at)
    store.record_book_tag(2, "mech.甲", source="manual", confidence=0.5,
                          basis_json="{}", updated_at=at)

    assert store.redirect_book_tag_rows("mech.甲", "mech.乙") == 2

    rows = {(r.book_id, r.tag_id, r.source, round(float(r.confidence), 3))
            for r in store.iter_book_tags()}
    # 工单 06 验收：历史逐行改指向，source/confidence 原样保留（manual 也是）
    assert (1, "mech.乙", "auto", 0.9) in rows
    assert (2, "mech.乙", "manual", 0.5) in rows
    assert not any(r[1] == "mech.甲" for r in rows)


def test_proposal_status_guard_is_shared(store):
    pid = store.insert_proposal(kind="tag", candidate="词",
                                payload={"candidate": "词", "count": 9, "threshold": 3},
                                created_at="2026-01-01T00:00:00+00:00")
    store.update_proposal_status(pid, "accepted")
    assert [r.status for r in store.iter_proposals()] == ["accepted"]
    with pytest.raises(ValueError):
        store.update_proposal_status(pid, "archived")
    with pytest.raises(KeyError):
        store.update_proposal_status("nope-1" if isinstance(pid, str) and pid.startswith("prop-") else "999999", "accepted")


def test_unknown_event_kind_rejected_without_side_effects(store):
    # 事件流本身不在契约里（适配器不消费事件），但「未知事件必须拒绝且不留半条」
    # 两侧一致——拒绝之后投影一个字节都不许变。
    before = _snapshot(store)
    with pytest.raises(ValueError):
        store.record_label_event("not_a_labels_event", {}, occurred_at="2026-01-01T00:00:00+00:00")
    assert _snapshot(store) == before