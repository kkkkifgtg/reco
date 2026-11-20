"""工单 05 红测试（三）：单写者队列 + 双连接并发读不阻塞写（验收项 2）。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_reader_not_blocked_by_open_write_txn(store):
    """写事务开着时，读连接照常拿到 pre-commit 快照（WAL 双连接）。"""
    started = threading.Event()
    release = threading.Event()

    def writer_job(conn):
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO signals(book_id, kind, value, occurred_at, source, session_id)"
            " VALUES(NULL, 'dwell', 1.0, '2026-01-01T00:00:00', 'cli', 's1')"
        )
        started.set()
        release.wait(5)
        conn.execute("COMMIT")

    fut = store.submit(writer_job)
    assert started.wait(5)
    rc = store.read_conn()
    try:
        before = rc.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    finally:
        rc.close()
    assert before == 0  # 快照读成功且看不到未提交行
    release.set()
    fut.result(timeout=5)
    rc = store.read_conn()
    try:
        after = rc.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    finally:
        rc.close()
    assert after == 1


def test_single_writer_queue_serializes(store):
    """多线程并发 append_event：单写者队列串行化，event_id 连续严格递增。"""

    def one(i):
        store.append_event(
            "signal_recorded",
            payload={"kind": "dwell", "value": float(i), "source": "cli", "session_id": "s"},
            occurred_at="2026-01-01T00:00:00",
            config_fingerprint="c",
        )

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, range(20)))

    events = store.read_events()
    assert [e.event_id for e in events] == list(range(1, 21))
    rc = store.read_conn()
    try:
        n = rc.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    finally:
        rc.close()
    assert n == 20
