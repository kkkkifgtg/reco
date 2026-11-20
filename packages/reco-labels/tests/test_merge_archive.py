"""工单 06——merge（旧 id 重定向+历史不丢）/archive（不参与打分但数据完整）/D1 manual 写入层保护。"""
from __future__ import annotations

import json

import pytest

from reco_labels import (
    InMemoryLabelsStore,
    ManualTagProtected,
    RecordResult,
    add_tag,
    archive_tag,
    merge_tags,
    record_tag_hits,
    set_aliases,
)
from reco_labels.seed import build_seed_rows


def _store() -> InMemoryLabelsStore:
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def _book_rows(store, book_id, tag_id):
    return [r for r in store.iter_book_tags() if r.book_id == book_id and r.tag_id == tag_id]


# ---------- merge：重定向且历史不丢（issue 06 验收 1） ----------

def test_merge_redirects_rows_and_preserves_history():
    store = _store()
    add_tag(store, tag_id="mech.摄魂", axis="mechanism", label="摄魂", parent="mech")
    set_aliases(store, "mech.摄魂", ("soul capture",))
    # 两行历史：一行 auto、一行 manual（带独立 confidence/basis）
    store.record_book_tag(1, "mech.摄魂", source="auto", confidence=0.9,
                          basis_json=json.dumps({"k": "auto"}, ensure_ascii=False), updated_at="2026-11-20T01:00:00")
    store.record_book_tag(2, "mech.摄魂", source="manual", confidence=1.0,
                          basis_json=json.dumps({"k": "manual"}, ensure_ascii=False), updated_at="2026-11-20T02:00:00")
    store.record_book_tag(3, "mech.hypnosis", source="auto", confidence=0.8,
                          basis_json="{}", updated_at="2026-11-20T03:00:00")

    result = merge_tags(store, "mech.摄魂", "mech.hypnosis")
    assert result.redirected_rows == 2

    rows = store.iter_book_tags()
    assert not any(r.tag_id == "mech.摄魂" for r in rows)  # 旧行全部改指向
    moved_auto = _book_rows(store, 1, "mech.hypnosis")[0]
    moved_manual = _book_rows(store, 2, "mech.hypnosis")[0]
    assert moved_auto.source == "auto" and moved_auto.confidence == 0.9
    assert json.loads(moved_auto.basis_json) == {"k": "auto"}
    assert moved_manual.source == "manual" and moved_manual.confidence == 1.0  # manual 保留（D3）
    assert json.loads(moved_manual.basis_json) == {"k": "manual"}
    assert len(_book_rows(store, 3, "mech.hypnosis")) == 1  # 无关行不受扰

    src = store.iter_tags()
    src_row = next(r for r in src if r.tag_id == "mech.摄魂")
    assert src_row.status == "merged"
    assert src_row.merged_into == "mech.hypnosis"
    assert src_row.aliases == ("soul capture",)  # 别名原地保留（经链可解析）

    events = store.iter_events()
    merged_events = [e for e in events if e.kind == "tag_merged"]
    assert len(merged_events) == 1
    assert merged_events[0].payload["source"] == "mech.摄魂"
    assert merged_events[0].payload["target"] == "mech.hypnosis"
    assert merged_events[0].payload["redirected_rows"] == 2


def test_merge_old_aliases_still_resolvable():
    store = _store()
    add_tag(store, tag_id="mech.摄魂", axis="mechanism", label="摄魂", parent="mech")
    set_aliases(store, "mech.摄魂", ("soul capture",))
    merge_tags(store, "mech.摄魂", "mech.hypnosis")
    from reco_labels import load_taxonomy, normalize_tag

    taxonomy = load_taxonomy(store)
    assert normalize_tag("mech.摄魂", taxonomy) == "mech.hypnosis"
    assert normalize_tag("soul capture", taxonomy) == "mech.hypnosis"


def test_merge_target_must_be_active():
    store = _store()
    add_tag(store, tag_id="mech.摄魂", axis="mechanism", label="摄魂", parent="mech")
    archive_tag(store, "mech.调教")
    with pytest.raises(ValueError):
        merge_tags(store, "mech.摄魂", "mech.调教")  # 目标 archived
    with pytest.raises(ValueError):
        merge_tags(store, "mech.hypnosis", "mech.hypnosis")  # 自合并


def test_merge_chain_transitive():
    """a→b→c 链：normalize 经链传递解析到最终 active 标签。"""
    store = _store()
    add_tag(store, tag_id="mech.甲", axis="mechanism", label="甲", parent="mech")
    add_tag(store, tag_id="mech.乙", axis="mechanism", label="乙", parent="mech")
    merge_tags(store, "mech.甲", "mech.乙")
    merge_tags(store, "mech.乙", "mech.hypnosis")
    from reco_labels import load_taxonomy, normalize_tag

    assert normalize_tag("mech.甲", load_taxonomy(store)) == "mech.hypnosis"


# ---------- archive：不参与打分但数据完整（issue 06 验收 2） ----------

def test_archive_keeps_rows_and_stops_matching():
    store = _store()
    store.record_book_tag(7, "mech.hypnosis", source="auto", confidence=0.7,
                          basis_json="{}", updated_at="2026-11-20T04:00:00")
    archive_tag(store, "mech.hypnosis")

    row = next(r for r in store.iter_tags() if r.tag_id == "mech.hypnosis")
    assert row.status == "archived"
    assert row.aliases == ("精神控制", "洗脑", "mind control")  # 数据不删
    assert len(_book_rows(store, 7, "mech.hypnosis")) == 1      # 标注保留

    from reco_labels import load_taxonomy, match_tags, normalize_tag

    taxonomy = load_taxonomy(store)
    assert normalize_tag("洗脑", taxonomy) is None          # 不参与识别
    assert match_tags(_s({"洗脑": 2}), taxonomy) == ()      # 不参与打分输入
    events = [e for e in store.iter_events() if e.kind == "tag_archived"]
    assert len(events) == 1 and events[0].payload["tag_id"] == "mech.hypnosis"


def _s(counts):
    from types import SimpleNamespace

    return SimpleNamespace(alias_counts=counts)


# ---------- D1：manual/user 行写入层拒绝覆盖（拒绝路径测试必须有） ----------

def test_record_skips_existing_manual_row():
    store = _store()
    store.record_book_tag(5, "mech.hypnosis", source="manual", confidence=1.0,
                          basis_json=json.dumps({"by": "user"}, ensure_ascii=False),
                          updated_at="2026-11-20T05:00:00")
    hit = _hit("mech.hypnosis", 0.42, {"total": 1})
    result = record_tag_hits(store, 5, (hit,), now="2026-11-21T00:00:00")
    assert isinstance(result, RecordResult)
    assert result.skipped_manual == ("mech.hypnosis",)
    row = _book_rows(store, 5, "mech.hypnosis")[0]
    assert row.source == "manual"
    assert row.confidence == 1.0  # 未被覆盖
    assert json.loads(row.basis_json) == {"by": "user"}
    assert row.updated_at == "2026-11-20T05:00:00"


def test_record_store_layer_raises_on_manual_overwrite():
    """写入层结构化拒绝：直接调 store.record_book_tag 用 auto 覆盖 manual → 异常。"""
    store = _store()
    store.record_book_tag(5, "mech.hypnosis", source="manual", confidence=1.0,
                          basis_json="{}", updated_at="2026-11-20T05:00:00")
    with pytest.raises(ManualTagProtected):
        store.record_book_tag(5, "mech.hypnosis", source="auto", confidence=0.1,
                              basis_json="{}", updated_at="2026-11-21T00:00:00")
    row = _book_rows(store, 5, "mech.hypnosis")[0]
    assert row.source == "manual" and row.confidence == 1.0


def test_record_user_rows_also_protected():
    store = _store()
    store.record_book_tag(5, "mech.hypnosis", source="user", confidence=1.0,
                          basis_json="{}", updated_at="2026-11-20T05:00:00")
    with pytest.raises(ManualTagProtected):
        store.record_book_tag(5, "mech.hypnosis", source="auto", confidence=0.1,
                              basis_json="{}", updated_at="2026-11-21T00:00:00")


def test_record_updates_auto_and_inserts_new():
    store = _store()
    store.record_book_tag(5, "mech.hypnosis", source="auto", confidence=0.5,
                          basis_json="{}", updated_at="2026-11-20T05:00:00")
    h1 = _hit("mech.hypnosis", 1.0, {"total": 3})
    h2 = _hit("mech.调教", 1.0, {"total": 2})
    result = record_tag_hits(store, 5, (h1, h2), now="2026-11-21T06:00:00")
    assert result.inserted == ("mech.调教",)
    assert result.updated == ("mech.hypnosis",)
    assert result.skipped_manual == ()
    row = _book_rows(store, 5, "mech.hypnosis")[0]
    assert row.source == "auto" and row.confidence == 1.0
    assert json.loads(row.basis_json)["total"] == 3  # 锚定统计量；其余键为契约字段
    assert row.updated_at == "2026-11-21T06:00:00"


def test_record_writes_conflict_event_payload_metadata_only():
    """冲突裁定落事件：payload 只含 alias/tag_id/计数——绝无正文片段（B 类）。"""
    store = _store()
    set_aliases(store, "role.双性", ("双性属性",))
    set_aliases(store, "bpart.双性", ("双性属性",))
    from reco_labels import load_taxonomy, match_tags

    hits = match_tags(_s({"双性属性": 3}), load_taxonomy(store))
    record_tag_hits(store, 9, hits, now="2026-11-21T07:00:00")
    conflicts = [e for e in store.iter_events() if e.kind == "tag_conflict"]
    assert len(conflicts) == 1
    payload = conflicts[0].payload
    assert payload["winner"] == "bpart.双性"
    assert sorted(payload["candidates"]) == ["bpart.双性", "role.双性"]
    assert payload["alias"] == "双性属性"
    blob = repr(payload)
    assert "正文" not in blob and "片段" not in blob


def _hit(tag_id, confidence, basis_extra):
    from reco_labels import TagHit

    return TagHit(
        tag_id=tag_id,
        confidence=confidence,
        basis={"axis": tag_id.split(".")[0] if False else _axis_of(tag_id),
               "matches": ({"alias": "x", "count": 1},),
               "total": 1,
               "evidence": (),
               **basis_extra},
    )


def _axis_of(tag_id):
    return {
        "mech.hypnosis": "mechanism",
        "mech.调教": "mechanism",
    }[tag_id]
