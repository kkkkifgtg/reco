"""T8（#40/#41/#42）红测试：store 事件白名单 / 投影契约修复。

契约（ADR-0003：事件流是唯一事实源，投影必须可从事件逐位重建）：
- #41：tag_merged 投影读 p["into"]（store.py tag_merged 分支），into 必须进
  白名单，否则合法合并事件不可构造、合并态重放不恢复。
- #40：proposal_created 投影读 p["payload_json"] / p["evidence_json"]，二者必须
  进白名单（标量 JSON 字符串——正文仍绝不入库，B2 内容纪律由调用方遵守），
  否则提案内容重放不恢复。
- #42：新增 proposal_updated 事件（白名单键 {proposal_id, status}），投影
  UPDATE proposals.status；提案状态变更从此经事件流落库、可重放恢复。
  修复前形态：append_event("proposal_updated", ...) 抛 ValueError「未知事件类型」。

proposals 不在 COMPARE_TABLES（既有隔离裁定不动），故其重放一致性用
rebuild_projections + 逐行显式比对钉死。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

CFG = "t8/contract"
NOW = "2026-11-22T00:00:00+00:00"


def _rows(store, sql: str, params=()):
    conn = store.read_conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _rows_of(db_path, table: str):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    finally:
        conn.close()


def _add_tag(store, tag_id: str, label: str) -> None:
    store.append_event(
        "tag_added",
        payload={"tag_id": tag_id, "axis": "背景", "label": label,
                 "created_by": "system"},
        occurred_at=NOW, config_fingerprint=CFG,
    )


# ---------------------------------------------------------------- #41 into

def test_tag_merged_event_admits_into_and_replays(store, store_module, tmp_path):
    """into 进白名单后 tag_merged 可构造；投影 + verify_replay + 逐位重建全绿。"""
    _add_tag(store, "t-a", "甲")
    _add_tag(store, "t-b", "乙")

    store.append_event(
        "tag_merged", book_id=None,
        payload={"tag_id": "t-a", "into": "t-b"},
        occurred_at=NOW, config_fingerprint=CFG,
    )  # 修复前：PayloadViolationError（into 白名单外键）

    status, merged_into = _rows(
        store, "SELECT status, merged_into FROM tags WHERE tag_id = 't-a'")[0]
    assert (status, merged_into) == ("merged", "t-b")
    # 合并别名照投影契约：源标签名成为目标的 merge 别名
    assert ("t-a", "t-b", "merge") in _rows(
        store, "SELECT alias, tag_id, source FROM tag_aliases")

    report = store.verify_replay()
    assert report.ok, report.diffs

    target = tmp_path / "rebuilt.db"
    store_module.rebuild_projections(target, store.read_events())
    assert _rows_of(target, "tags") == _rows_of(store.path, "tags")
    assert _rows_of(target, "tag_aliases") == _rows_of(store.path, "tag_aliases")


def test_tag_merged_into_must_be_scalar(store, store_module):
    """扩的是键的白名单，不是标量纪律：into 传结构值仍被拒（B6）。"""
    _add_tag(store, "t-a", "甲")
    with pytest.raises(store_module.PayloadViolationError):
        store.append_event(
            "tag_merged", payload={"tag_id": "t-a", "into": {"x": 1}},
            occurred_at=NOW, config_fingerprint=CFG,
        )


# ---------------------------------------------------------------- #40 json 列

PAYLOAD_JSON = json.dumps({"candidate": "夺舍", "count": 4, "threshold": 3},
                          ensure_ascii=False, sort_keys=True)
EVIDENCE_JSON = json.dumps({"count": 4, "threshold": 3},
                           ensure_ascii=False, sort_keys=True)


def test_proposal_created_event_carries_json_columns(store, store_module, tmp_path):
    """payload_json/evidence_json 进白名单后，提案内容随事件落库并逐位可重放。"""
    store.append_event(
        "proposal_created",
        payload={"kind": "tag", "payload_json": PAYLOAD_JSON,
                 "evidence_json": EVIDENCE_JSON},
        occurred_at=NOW, config_fingerprint=CFG,
    )  # 修复前：PayloadViolationError（payload_json 白名单外键）

    row = _rows(store, "SELECT proposal_id, kind, payload_json, evidence_json,"
                       " status, created_at FROM proposals")[0]
    assert row == (1, "tag", PAYLOAD_JSON, EVIDENCE_JSON, "pending", NOW)

    assert store.verify_replay().ok
    # proposals 不在 COMPARE_TABLES——重放一致性显式逐位钉死
    target = tmp_path / "rebuilt.db"
    store_module.rebuild_projections(target, store.read_events())
    assert _rows_of(target, "proposals") == _rows_of(store.path, "proposals")


def test_proposal_json_columns_missing_stay_optional(store, store_module):
    """只送 kind 的旧形态仍合法（投影列 NULL）——扩白名单不等于必填化。"""
    store.append_event("proposal_created", payload={"kind": "tag"},
                       occurred_at=NOW, config_fingerprint=CFG)
    row = _rows(store, "SELECT payload_json, evidence_json, status FROM proposals")[0]
    assert row == (None, None, "pending")


def test_proposal_json_columns_reject_nonscalar(store, store_module):
    """标量纪律：payload_json 传 dict（未序列化）必须被拒——列契约是 JSON 字符串。"""
    with pytest.raises(store_module.PayloadViolationError):
        store.append_event(
            "proposal_created",
            payload={"kind": "tag", "payload_json": {"candidate": "x"}},
            occurred_at=NOW, config_fingerprint=CFG,
        )


# ---------------------------------------------------------------- #42 proposal_updated

def test_proposal_updated_projects_status_and_replays(store, store_module, tmp_path):
    """proposal_updated 事件投影 UPDATE proposals.status，重放逐位恢复状态。"""
    store.append_event(
        "proposal_created",
        payload={"kind": "tag", "payload_json": PAYLOAD_JSON,
                 "evidence_json": EVIDENCE_JSON},
        occurred_at=NOW, config_fingerprint=CFG,
    )
    store.append_event(
        "proposal_updated", book_id=None,
        payload={"proposal_id": 1, "status": "rejected"},
        occurred_at=NOW, config_fingerprint=CFG,
    )  # 修复前：ValueError「未知事件类型: 'proposal_updated'」

    assert _rows(store, "SELECT status FROM proposals WHERE proposal_id = 1")[0][0] \
        == "rejected"
    # 状态变更不新增提案
    assert _rows(store, "SELECT COUNT(*) FROM proposals")[0][0] == 1

    assert store.verify_replay().ok
    target = tmp_path / "rebuilt.db"
    store_module.rebuild_projections(target, store.read_events())
    assert _rows_of(target, "proposals") == _rows_of(store.path, "proposals")


def test_proposal_updated_rejects_keys_outside_contract(store, store_module):
    """proposal_updated 的 payload 面仍受白名单纪律约束：未知键拒绝。"""
    with pytest.raises(store_module.PayloadViolationError):
        store.append_event(
            "proposal_updated", payload={"proposal_id": 1, "status": "accepted",
                                         "note": "随手塞的键"},
            occurred_at=NOW, config_fingerprint=CFG,
        )


def test_proposal_updated_missing_row_is_noop_not_crash(store, store_module):
    """投影层对不存在的 proposal_id 空转（与 tag 类 UPDATE 同形）；事件仍是真相源。"""
    store.append_event(
        "proposal_updated", payload={"proposal_id": 4242, "status": "accepted"},
        occurred_at=NOW, config_fingerprint=CFG,
    )
    assert _rows(store, "SELECT COUNT(*) FROM proposals")[0][0] == 0
    assert store.verify_replay().ok


def test_unknown_event_type_still_refused(store, store_module):
    """新增类型不放松词汇表守门：拼错的事件类型照旧 ValueError。"""
    with pytest.raises(ValueError, match="未知事件类型"):
        store.append_event("proposal_upsert", payload={},
                           occurred_at=NOW, config_fingerprint=CFG)
