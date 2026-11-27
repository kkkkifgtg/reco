"""T8/#42——提案状态变更事件化的真 Store 集成测试（根 tests/，跨包即集成：CODING-STANDARDS §2）。

修复前（issue #42）：StoreAdapter.update_proposal_status 经单写者队列直改投影，
事件流里没有任何状态事件——投影重建后 proposals.status 一律回到 pending，
accept/reject 的裁定不可重放。

契约（ADR-0003：事件流是唯一事实源）：状态变更必须经 proposal_updated 事件落库，
重放逐位恢复 status；非法状态/不存在的提案在事件面零污染（不写事件就报错）。
"""
from __future__ import annotations

import sqlite3

import pytest

from reco_labels.auto_tag import StoreAdapter
from reco_store import open_store
from reco_store.store import rebuild_projections

NOW = "2026-11-22T00:00:00+00:00"
CFG_FP = "t8/adapter"


def _open(tmp_path):
    raw = open_store(tmp_path / "reco.db")
    return raw, StoreAdapter(raw)


def _payloads(raw, event_type: str):
    conn = raw.read_conn()
    try:
        import json
        return [json.loads(r[0]) for r in conn.execute(
            "SELECT payload_json FROM events WHERE event_type = ? ORDER BY event_id",
            (event_type,)).fetchall()]
    finally:
        conn.close()


def _proposal_cols(db_path):
    """状态列的重放一致性显式逐位比对（#111 起 proposals 也在 COMPARE_TABLES 内）。"""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT proposal_id, kind, status, created_at FROM proposals"
            " ORDER BY rowid").fetchall()
    finally:
        conn.close()


def test_status_change_lands_proposal_updated_event(tmp_path):
    """修复前：状态变更只改投影，事件流里没有 proposal_updated。"""
    raw, adapter = _open(tmp_path)
    try:
        pid = adapter.insert_proposal("tag", "夺舍", {"count": 4, "threshold": 3}, NOW)
        before = len(raw.read_events())

        adapter.update_proposal_status(pid, "rejected")

        assert _payloads(raw, "proposal_updated") == [
            {"proposal_id": int(pid), "status": "rejected"}]
        assert len(raw.read_events()) == before + 1  # 不多写事件
        conn = raw.read_conn()
        try:
            row = conn.execute(
                "SELECT status FROM proposals WHERE proposal_id = ?", (int(pid),),
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "rejected"
    finally:
        raw.close()


def test_status_change_restores_on_replay(tmp_path):
    """重放恢复 status：重建库里提案状态与当前库逐位一致（#42 验收）。"""
    raw, adapter = _open(tmp_path)
    try:
        pid_a = adapter.insert_proposal("tag", "夺舍", {"count": 4, "threshold": 3}, NOW)
        pid_b = adapter.insert_proposal("tag", "摄心术", {"count": 5, "threshold": 3}, NOW)
        adapter.update_proposal_status(pid_a, "rejected")
        adapter.update_proposal_status(pid_b, "accepted")
        assert raw.verify_replay().ok  # events 侧零漂移

        target = tmp_path / "rebuilt.db"
        rebuild_projections(target, raw.read_events())
        assert _proposal_cols(target) == _proposal_cols(raw.path)
        assert [r[2] for r in _proposal_cols(target)] == ["rejected", "accepted"]
    finally:
        raw.close()


def test_invalid_status_rejected_without_event(tmp_path):
    """非法状态在事件面零污染：先校验再落事件（枚举守卫不外溢成坏事件）。"""
    raw, adapter = _open(tmp_path)
    try:
        pid = adapter.insert_proposal("tag", "夺舍", {"count": 4, "threshold": 3}, NOW)
        before = len(raw.read_events())
        with pytest.raises(ValueError):
            adapter.update_proposal_status(pid, "bogus")
        assert len(raw.read_events()) == before
    finally:
        raw.close()


def test_missing_proposal_raises_keyerror_without_event(tmp_path):
    """不存在的提案：KeyError 照旧（向后兼容），且不留孤立状态事件。"""
    raw, adapter = _open(tmp_path)
    try:
        before = len(raw.read_events())
        with pytest.raises(KeyError):
            adapter.update_proposal_status("999999", "accepted")
        assert len(raw.read_events()) == before
        assert _payloads(raw, "proposal_updated") == []
    finally:
        raw.close()


def test_accept_reject_helpers_land_status_events(tmp_path):
    """accept_proposal / reject_proposal 经 update_proposal_status 自动事件化。"""
    from reco_labels import accept_proposal, reject_proposal

    raw, adapter = _open(tmp_path)
    try:
        adapter.insert_proposal("tag", "夺舍", {"count": 4, "threshold": 3}, NOW)
        adapter.insert_proposal("tag", "摄心术", {"count": 5, "threshold": 3}, NOW)
        accept_proposal(adapter, "夺舍", axis="mechanism", now=NOW)
        reject_proposal(adapter, "摄心术", now=NOW)
        assert _payloads(raw, "proposal_updated") == [
            {"proposal_id": 1, "status": "accepted"},
            {"proposal_id": 2, "status": "rejected"},
        ]
        target = tmp_path / "rebuilt.db"
        rebuild_projections(target, raw.read_events())
        assert _proposal_cols(target) == _proposal_cols(raw.path)
    finally:
        raw.close()
