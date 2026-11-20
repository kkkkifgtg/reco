"""工单 11 强制必办（auditor W10 前瞻接缝）：真库投影器扩展与静音表持久化。

① pool_added 进 _apply_event pass-through 记账清单（与 scan_started/perf_sample
   同列）：pool 回流在真库直接落库成功，不抛「未知事件类型」；事件流重放零漂移。
② 静音表持久化形态（schema 在 store 层）：mutes 表 + mute_upserted/mute_released
   事件投影 + Store.record_mute/release_mute/load_mutes；重启（重新 open_store）
   后静音窗/到期时刻仍生效（D2/工单 10 语义的持久化载体）。
"""

from __future__ import annotations

import pytest

from reco_store import Event, open_store, rebuild_projections

CFG_FP = "t15/probe"
NOW = "2026-11-21T05:00:00+00:00"


def _add_book(store, book_name="合成样本甲"):
    store.append_event(
        "file_added",
        payload={
            "path": f"\\\\synth\\\\{book_name}.txt",
            "zone": "candidate",
            "ext": "txt",
            "size_bytes": 1024,
            "mtime_ns": 1_000,
            "st_dev": 11,
            "st_ino": 22,
            "display_name": book_name,
            "normalized_name": book_name,
            "dedup_key": "t15-dedup",
        },
        occurred_at=NOW,
        config_fingerprint=CFG_FP,
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM books LIMIT 1").fetchone()[0])
    finally:
        conn.close()


def test_S1_payload_whitelist_admits_mute_keys_with_real_row(tmp_path):
    # 白名单回归（历史教训：空表探针测不出白名单行为）：真实行写入 + 读取验证
    store = open_store(tmp_path / "probe.db")
    try:
        book_id = _add_book(store)
        event_id = store.record_mute(
            book_id, dedup_key="real-k", axis="mood.dark", kind="dislike",
            expired_at="2027-02-19T00:00:00+00:00", remaining_days=90.0,
            occurred_at=NOW, config_fingerprint=CFG_FP,
        )  # 修复前：PayloadViolationError（dedup_key/remaining_days 白名单外）
        assert event_id >= 1
        rows = store.load_mutes()
        assert rows[0]["dedup_key"] == "real-k"
        assert rows[0]["remaining_days"] == 90.0
        assert store.verify_replay().ok
    finally:
        store.close()


def test_S1_pool_added_passes_real_store_projection(tmp_path):
    store = open_store(tmp_path / "probe.db")
    try:
        book_id = _add_book(store)
        event_id = store.append_event(
            "pool_added", book_id=book_id, payload={"kind": "add_to_pool"},
            occurred_at=NOW, config_fingerprint=CFG_FP,
        )  # 修复前：抛 ValueError「未知事件类型」
        assert event_id >= 1
        types = [ev.event_type for ev in store.read_events()]
        assert types[-1] == "pool_added"
        report = store.verify_replay()
        assert report.ok, report.diffs  # 重放零漂移
    finally:
        store.close()


def test_S1_pool_added_stays_out_of_taste_stream(tmp_path):
    # pool_added 是交互类事件：不落任何口味投影（signals/profile 零变化）
    store = open_store(tmp_path / "probe.db")
    try:
        book_id = _add_book(store)
        store.append_event("pool_added", book_id=book_id,
                           payload={"kind": "add_to_pool"}, occurred_at=NOW,
                           config_fingerprint=CFG_FP)
        conn = store.read_conn()
        try:
            assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM profile_versions").fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        store.close()


def test_S2_mute_roundtrip_survives_restart(tmp_path):
    store = open_store(tmp_path / "probe.db")
    book_id = _add_book(store)
    store.record_mute(
        book_id, dedup_key="t15-dedup", axis="mood.dark", kind="dislike",
        expired_at="2027-02-19T00:00:00+00:00", remaining_days=90.0,
        occurred_at=NOW, config_fingerprint=CFG_FP,
    )
    store.close()
    # 重启：重新 open_store 后静音窗/到期时刻仍生效（持久化形态定义）
    reopened = open_store(tmp_path / "probe.db")
    try:
        mutes = reopened.load_mutes()
        assert len(mutes) == 1
        row = mutes[0]
        assert row["book_id"] == book_id
        assert row["dedup_key"] == "t15-dedup"
        assert row["axis"] == "mood.dark"
        assert row["kind"] == "dislike"
        assert row["expired_at"] == "2027-02-19T00:00:00+00:00"
        assert row["remaining_days"] == 90.0
    finally:
        reopened.close()


def test_S2_mute_replace_and_release(tmp_path):
    store = open_store(tmp_path / "probe.db")
    try:
        book_id = _add_book(store)
        store.record_mute(book_id, dedup_key="k", axis="mood.dark", kind="dislike",
                          expired_at="2027-01-01T00:00:00+00:00", remaining_days=30.0,
                          occurred_at=NOW, config_fingerprint=CFG_FP)
        store.record_mute(book_id, dedup_key="k", axis="mood.dark", kind="dislike",
                          expired_at="2027-03-01T00:00:00+00:00", remaining_days=90.0,
                          occurred_at=NOW, config_fingerprint=CFG_FP)  # 重复反馈=替换窗口
        assert len(store.load_mutes()) == 1
        assert store.load_mutes()[0]["expired_at"] == "2027-03-01T00:00:00+00:00"
        store.release_mute(book_id, occurred_at=NOW, config_fingerprint=CFG_FP)
        assert store.load_mutes() == []  # 过期解除：零残留（工单 10 语义）
    finally:
        store.close()


def test_S2_mute_projection_replays_bitwise(tmp_path):
    src = open_store(tmp_path / "src.db")
    try:
        book_id = _add_book(src)
        src.record_mute(book_id, dedup_key="k", axis="mood.dark", kind="dislike",
                        expired_at="2027-02-19T00:00:00+00:00", remaining_days=90.0,
                        occurred_at=NOW, config_fingerprint=CFG_FP)
        events = src.read_events()
    finally:
        src.close()
    target = tmp_path / "replay.db"
    rebuild_projections(target, events)  # 修复前：未知事件类型（静音事件未投影）
    conn = __import__("sqlite3").connect(target)
    try:
        rows = conn.execute("SELECT book_id, dedup_key, axis, kind, expired_at, remaining_days FROM mutes").fetchall()
        assert len(rows) == 1
        assert rows[0][2] == "mood.dark"
    finally:
        conn.close()
