"""W12b 红测试（一）：ui_usage 独立表 API + 口味事件流隔离硬边界（C12）。

契约（§11.7 + issue 12b 验收 1/2）：
- bump/get ui_usage 走 Store 单写者队列与 read_conn（性能数据，非口味数据）；
- ui_usage 含数据时 verify_replay 逐位不变、COMPARE_TABLES 排除（对照 pool_added 先例）；
- profile 折叠对 ui_usage 数据零反应（画像回放完全忽略它）。
"""
from __future__ import annotations

import json

from reco_store import open_store
from reco_store.store import COMPARE_TABLES

NOW = "2026-11-21T06:00:00+00:00"


def _seed_events(store):
    """最小口味事件种子：一条 pool_added + 一条 rec_feedback（投影/回放有实料）。

    book_id 走事件列（dict 顶层）；payload 只用白名单键（zone/path/kind/axis）。
    """
    store.append({
        "event_type": "pool_added",
        "book_id": 1,
        "payload": {"zone": "liked", "path": "liked/a.txt"},
        "occurred_at": NOW,
    })
    store.append({
        "event_type": "rec_feedback",
        "book_id": 1,
        "payload": {"kind": "like", "axis": "tone"},
        "occurred_at": NOW,
    })


def test_ui_usage_table_not_in_compare_tables():
    assert "ui_usage" not in COMPARE_TABLES  # 隔离硬边界：不参与投影重建比对


def test_bump_and_get_ui_usage_roundtrip(tmp_path):
    s = open_store(tmp_path / "u1.db")
    try:
        s.bump_ui_usage("liked", now=NOW)
        s.bump_ui_usage("liked", now=NOW + "+01:00")
        s.bump_ui_usage("recommend", now=NOW)
        rows = s.get_ui_usage()
        assert rows["liked"]["uses"] == 2
        assert rows["liked"]["last_used"] == NOW + "+01:00"
        assert rows["recommend"]["uses"] == 1
        assert "ghost" not in rows  # 未 bump 的 page_id 不出现
    finally:
        s.close()


def test_bump_write_goes_through_single_writer_queue(tmp_path):
    """bump 必须经 submit 单写者队列（W5-F 接缝纪律），读走 read_conn。"""
    s = open_store(tmp_path / "u2.db")
    try:
        s.bump_ui_usage("profile", now=NOW)
        report = s.verify_replay()
        assert report.ok  # 单写者路径不破坏 append-only/回放
        rows = s.get_ui_usage()
        assert rows["profile"]["uses"] == 1
    finally:
        s.close()


def test_verify_replay_bitwise_unchanged_with_ui_usage_data(tmp_path):
    """验收 2 硬边界：ui_usage 有数据 vs 空——回放/投影结果逐位不变。"""
    s1 = open_store(tmp_path / "u3a.db")
    s2 = open_store(tmp_path / "u3b.db")
    try:
        _seed_events(s1)
        _seed_events(s2)
        s1.bump_ui_usage("liked", now=NOW)
        s1.bump_ui_usage("liked", now=NOW)
        s1.bump_ui_usage("recommend", now=NOW)  # s1 带 ui_usage 数据；s2 不带
        r1 = s1.verify_replay()
        r2 = s2.verify_replay()
        assert r1.ok and r2.ok
        assert r1.compare_rows == r2.compare_rows  # 逐位：全部 COMPARE_TABLES 行一致
        assert r1.compare_rows is not None
    finally:
        s1.close()
        s2.close()


def test_profile_fold_ignores_ui_usage_completely(tmp_path):
    """画像折叠对 ui_usage 零反应：单独狂 bump 不产生任何口味语义变化。"""
    s = open_store(tmp_path / "u4.db")
    try:
        _seed_events(s)
        before = s.verify_replay()
        for i in range(50):
            s.bump_ui_usage(f"page-{i}", now=NOW)
        after = s.verify_replay()
        assert before.compare_rows == after.compare_rows  # 口味侧逐位不变
        assert len(s.get_ui_usage()) == 50  # 性能侧照常落表
    finally:
        s.close()


def test_events_table_never_contains_ui_usage_rows(tmp_path):
    """ui_usage 走独立表而非事件流——events 表永远不出现 ui_usage 记录。"""
    s = open_store(tmp_path / "u5.db")
    try:
        s.bump_ui_usage("liked", now=NOW)
        conn = s.read_conn()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type LIKE '%ui%'").fetchone()[0]
            assert n == 0
            usage = conn.execute("SELECT uses FROM ui_usage").fetchall()
            assert len(usage) == 1  # 数据只在自己的表里
        finally:
            conn.close()
    finally:
        s.close()
