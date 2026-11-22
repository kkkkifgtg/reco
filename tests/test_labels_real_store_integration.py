"""RR2/t6——StoreAdapter 真 Store 集成测试（根 tests/，跨包即集成：CODING-STANDARDS §2）。

真 Store = reco_store.open_store(临时 sqlite)：事件写入走 _validate_payload 白名单校验，
投影落 tags/tag_aliases/book_tags/proposals 真表。覆盖 PR #29 审核发现：
1) 新词 count>=3 → proposals(pending) 正常落库（不再 PayloadViolationError）；
2) proposal_created 事件 payload 只含白名单键；iter_proposals 从 payload_json 取 candidate；
3) labels 事件（tag_archived / redirect / proposal 状态）正确映射，不再误映射成
   proposal_created；
4) 已知 alias 命中路径行为不变（book_tags source=auto）。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from reco_labels import (
    accept_proposal,
    archive_tag,
    auto_tag_book,
    merge_tags,
)
from reco_labels.auto_tag import StoreAdapter
from reco_labels.seed import build_seed_rows
from reco_store import open_store
from reco_store.store import _ALLOWED_KEYS, dedup_key, rebuild_projections

NOW = "2026-11-21T00:00:00"


class _Stats:
    def __init__(self, alias_counts):
        self.alias_counts = alias_counts


def _open(tmp_path):
    raw = open_store(tmp_path / "reco.db")
    return raw, StoreAdapter(raw)


# 只落测试用到的种子标签（真库 schema/投影/白名单全走，避免 164 条种子事件拖慢套件）
_SEED_TAG_IDS = ("mech.hypnosis", "mech.调教", "mech.驯化")


def _seed(adapter) -> None:
    rows = [row for row in build_seed_rows() if row.tag_id in _SEED_TAG_IDS]
    assert len(rows) == len(_SEED_TAG_IDS)
    for row in rows:
        adapter.upsert_tag(row)


def _add_book(raw, *, ino: int, name: str = "book.epub") -> int:
    key = dedup_key(100 + ino, name)
    raw.append_event(
        "file_added",
        payload={
            "st_dev": 1, "st_ino": ino, "path": f"F:/lib/{ino}/{name}", "zone": "liked",
            "size_bytes": 100 + ino, "mtime_ns": ino, "ext": ".epub",
            "dedup_key": key, "display_name": name, "normalized_name": name,
        },
        occurred_at=NOW, config_fingerprint="rr2/test/1",
    )
    return _scalar(raw, "SELECT book_id FROM books WHERE dedup_key = ?", (key,))


def _db(raw, sql: str, params=()):
    conn = raw.read_conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _scalar(raw, sql: str, params=()):
    return _db(raw, sql, params)[0][0]


def _db_conn_rows(db_path, table: str):
    """逐位行快照（proposals/tags 不在 COMPARE_TABLES 时的显式重放比对）。"""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    finally:
        conn.close()


def _event_types(raw):
    return [row[0] for row in _db(raw, "SELECT event_type FROM events")]


def _proposal_count(raw) -> int:
    return _scalar(raw, "SELECT COUNT(*) FROM proposals")


# ---------------------------------------------------------------- ①提案落库
def test_auto_tag_new_words_land_pending_proposals_on_real_store(tmp_path):
    """修复前：insert_proposal → proposal_created 带白名单外键 → PayloadViolationError。"""
    raw, adapter = _open(tmp_path)
    _seed(adapter)
    book_id = _add_book(raw, ino=1)
    tags_before = {row.tag_id for row in adapter.iter_tags()}

    outcome = auto_tag_book(
        adapter, book_id, "liked",
        _Stats({"洗脑": 3, "摄心术": 5, "夺舍": 4, "罕见词": 1}), now=NOW,
    )

    # 已知 alias 路径行为不变 → book_tags(source=auto)；新词绝不建 tags 行
    assert outcome.inserted == ("mech.hypnosis",)
    assert sorted(outcome.proposed) == ["夺舍", "摄心术"]  # 阈值 3：罕见词被滤
    assert {row.tag_id for row in adapter.iter_tags()} == tags_before
    book_rows = [(r.tag_id, r.source) for r in adapter.iter_book_tags()
                 if r.book_id == book_id]
    assert book_rows == [("mech.hypnosis", "auto")]

    pending = [p for p in adapter.iter_proposals() if p.status == "pending"]
    assert {p.candidate for p in pending} == {"夺舍", "摄心术"}
    assert all(p.kind == "tag" for p in pending)
    assert all(p.proposal_id.isdigit() for p in pending)

    # proposal_created 事件 payload 只含白名单键；业务负载作为标量 JSON 字符串
    # 随事件一体落库（T8/#40：事件即真相源，提案内容重放可恢复）
    created = [json.loads(row[0]) for row in _db(
        raw, "SELECT payload_json FROM events WHERE event_type='proposal_created'")]
    assert len(created) == 2
    for payload in created:
        assert set(payload) <= _ALLOWED_KEYS
        assert set(payload) == {"kind", "payload_json", "evidence_json"}
        assert json.loads(payload["payload_json"])["candidate"] in {"夺舍", "摄心术"}
    assert raw.verify_replay().ok
    # 提案内容逐位可重建（proposals 不在 COMPARE_TABLES，显式比对）
    target = tmp_path / "rebuilt-proposals.db"
    rebuild_projections(target, raw.read_events())
    assert _db_conn_rows(target, "proposals") == _db_conn_rows(raw.path, "proposals")


def test_iter_proposals_reads_candidate_from_payload_json(tmp_path):
    """修复前：iter_proposals 查不存在的 candidate 列 → OperationalError。"""
    raw, adapter = _open(tmp_path)
    pid = adapter.insert_proposal("tag", "摄心术", {"count": 5, "threshold": 3}, NOW)

    rows = list(adapter.iter_proposals())
    assert [row.proposal_id for row in rows] == [pid]
    row = rows[0]
    assert row.candidate == "摄心术"
    assert row.kind == "tag" and row.status == "pending"
    assert dict(row.payload) == {"candidate": "摄心术", "count": 5, "threshold": 3}

    # payload_json 缺失/损坏的旧行降级为空 payload，读路径不抛异常
    raw.append_event("proposal_created", payload={"kind": "tag"},
                     occurred_at=NOW, config_fingerprint="rr2/test/1")
    degraded = list(adapter.iter_proposals())[-1]
    assert degraded.candidate == "" and dict(degraded.payload) == {}


# ---------------------------------------------------------------- ②labels 事件映射
def test_archive_tag_maps_to_tag_archived_without_proposal(tmp_path):
    """修复前：archive_tag → 误映射成 proposal_created → PayloadViolationError。"""
    raw, adapter = _open(tmp_path)
    _seed(adapter)

    archive_tag(adapter, "mech.hypnosis", now=NOW)

    tags = {row.tag_id: row for row in adapter.iter_tags()}
    assert tags["mech.hypnosis"].status == "archived"
    assert "tag_archived" in _event_types(raw)
    assert "proposal_created" not in _event_types(raw)  # 不得误映射
    assert _proposal_count(raw) == 0
    assert raw.verify_replay().ok


def test_redirect_moves_auto_rows_via_events(tmp_path):
    """修复前：redirect_book_tag_rows 是 NotImplementedError。"""
    raw, adapter = _open(tmp_path)
    _seed(adapter)
    book_id = _add_book(raw, ino=2)
    adapter.record_book_tag(book_id, "mech.调教", source="auto", confidence=1.0,
                            basis_json="{}", updated_at=NOW)

    moved = adapter.redirect_book_tag_rows("mech.调教", "mech.驯化")

    assert moved == 1
    rows = {(r.tag_id, r.source) for r in adapter.iter_book_tags()
            if r.book_id == book_id}
    assert rows == {("mech.驯化", "auto")}
    assert {"label_set", "label_cleared"} <= set(_event_types(raw))
    assert "proposal_created" not in _event_types(raw)
    assert raw.verify_replay().ok


def test_merge_tags_maps_merged_state_without_proposal(tmp_path):
    """merge_tags 全链路：redirect 事件搬迁 + 合并态经 tag_merged 事件投影（T8/#41）。

    into 已进 payload 白名单：合并态（tags.status/merged_into + 合并别名）由 store
    投影在同一事务完成，不再有队列旁路补写——verify_replay 与逐位重建都必须绿。
    """
    raw, adapter = _open(tmp_path)
    _seed(adapter)
    book_id = _add_book(raw, ino=3)
    adapter.record_book_tag(book_id, "mech.调教", source="auto", confidence=1.0,
                            basis_json="{}", updated_at=NOW)

    result = merge_tags(adapter, "mech.调教", "mech.驯化")

    assert result.redirected_rows == 1
    tags = {row.tag_id: row for row in adapter.iter_tags()}
    assert tags["mech.调教"].status == "merged"
    assert tags["mech.调教"].merged_into == "mech.驯化"
    assert ("mech.调教", "mech.驯化", "merge") in list(adapter.iter_aliases())
    rows = {(r.tag_id, r.source) for r in adapter.iter_book_tags()
            if r.book_id == book_id}
    assert rows == {("mech.驯化", "auto")}
    assert _proposal_count(raw) == 0
    assert "tag_merged" in _event_types(raw)  # #41 之前：合法事件不可构造
    assert raw.verify_replay().ok
    target = tmp_path / "rebuilt-merge.db"
    rebuild_projections(target, raw.read_events())
    assert _db_conn_rows(target, "tags") == _db_conn_rows(raw.path, "tags")
    assert _db_conn_rows(target, "tag_aliases") == _db_conn_rows(raw.path, "tag_aliases")


def test_update_proposal_status_and_accept_reject(tmp_path):
    """修复前：update_proposal_status 是 NotImplementedError（accept/reject 均断链）。"""
    raw, adapter = _open(tmp_path)
    _seed(adapter)
    book_id = _add_book(raw, ino=4)
    auto_tag_book(adapter, book_id, "liked", _Stats({"摄心术": 4, "夺舍": 5}), now=NOW)
    pending = {p.candidate: p for p in adapter.iter_proposals() if p.status == "pending"}
    assert set(pending) == {"摄心术", "夺舍"}

    adapter.update_proposal_status(pending["夺舍"].proposal_id, "rejected")
    assert {p.candidate: p.status for p in adapter.iter_proposals()}["夺舍"] == "rejected"

    created = accept_proposal(adapter, "摄心术", axis="mechanism", now=NOW)
    assert created.tag_id == "mech.摄心术" and created.created_by == "user"
    assert {p.candidate: p.status for p in adapter.iter_proposals()} == {
        "摄心术": "accepted", "夺舍": "rejected",
    }
    assert list(adapter.iter_book_tags()) == []  # accept 不写 book_tags
    assert _proposal_count(raw) == 2  # 状态变更不新增提案
    assert raw.verify_replay().ok

    with pytest.raises(ValueError):
        adapter.update_proposal_status(pending["摄心术"].proposal_id, "bogus")
    with pytest.raises(KeyError):
        adapter.update_proposal_status("999999", "accepted")
