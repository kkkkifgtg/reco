"""工单 05 红测试（一）：库设置/append-only/指纹去重（契约 §7.1/§7.2/§9）。

红阶段策略：reco_store.store 由本票绿阶段实现——fixture 惰性 import，
ImportError 只让本文件逐测试转红，不产生 collection error 中止全队跑测
（工单 03 验证过的形态）。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3

import pytest


def test_open_store_settings(store, store_module):
    """C11：WAL/NORMAL/busy_timeout=5000/foreign_keys=ON，读写连接都成立。"""
    for conn in (store.write_conn, store.read_conn()):
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert mode == "wal"
        assert sync == store_module.Synchronous.NORMAL.value
        assert busy == 5000
        assert fk == 1
        conn.close()


def test_schema_meta_stored(store):
    """C13：schema_meta 记录 schema_version/created_at/app_version。"""
    conn = store.read_conn()
    try:
        rows = dict(conn.execute("SELECT key, value FROM schema_meta"))
    finally:
        conn.close()
    assert rows["schema_version"] == "1"
    assert rows["app_version"] == "0.1.0"
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", rows["created_at"])


def test_events_append_only_update_and_delete_refused(store):
    """C1：事件表只允许 INSERT——触发器硬保险拒绝 UPDATE/DELETE。"""
    store.append_event(
        "scan_started",
        payload={"path": "F:/liked"},
        occurred_at="2026-01-01T00:00:00",
        config_fingerprint="cfg-a",
    )
    conn = store.write_conn
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE events SET payload_json = '{}'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM events")


def test_payload_rejects_effective_weight(store, store_module):
    """B6：payload 白名单——effective_weight 必须被写入层拒绝（折叠时重算）。"""
    with pytest.raises(store_module.PayloadViolationError):
        store.append_event(
            "signal_recorded",
            book_id=None,
            payload={"kind": "dwell", "value": 1.0, "effective_weight": 0.5},
            occurred_at="2026-01-01T00:00:00",
            config_fingerprint="cfg-a",
        )


def test_payload_rejects_unknown_and_nonscalar(store, store_module):
    """B6：白名单外键、非标量值一律拒绝。"""
    with pytest.raises(store_module.PayloadViolationError):
        store.append_event("signal_recorded", payload={"mood": "好"}, occurred_at="2026-01-01T00:00:00", config_fingerprint="c")
    with pytest.raises(store_module.PayloadViolationError):
        store.append_event(
            "signal_recorded",
            payload={"kind": {"nested": "dict"}},
            occurred_at="2026-01-01T00:00:00",
            config_fingerprint="c",
        )


def test_normalize_book_name_rules(store_module):
    """§9 规范化规则（手算期望，E3）：
    ① NFKC ② casefold ③ 连续空白折叠+去首尾 ④ 全角→半角 ⑤ 括号与扩展名保留。
    """
    assert store_module.normalize_book_name("Ｈello　Ｗorld .txt") == "hello world .txt"
    assert store_module.normalize_book_name("BOOK（试）.TXT") == "book(试).txt"
    assert store_module.normalize_book_name("  A  B  ") == "a b"
    assert store_module.normalize_book_name("全角ＡＢ.txt") == "全角ab.txt"


def test_dedup_key_is_hash_of_size_and_normalized_name(store_module):
    """E4：期望值来自 hashlib 独立计算，不 import 实现内部函数。"""
    expected = hashlib.sha256("100\x1fhello world .txt".encode("utf-8")).hexdigest()
    assert store_module.dedup_key(100, "Ｈello　Ｗorld .txt") == expected


def test_books_dedup_partial_unique_index(store, store_module):
    """C9/F3：UNIQUE(dedup_key) 是判重的 schema 硬保险；投影层把同指纹文件
    归并成同一本书（硬链接孪生：源+落位副本=同一本书两路径）；同名不同大小不算重。
    """
    payload_a = {
        "path": "F:/liked/a.txt",
        "zone": "liked",
        "ext": ".txt",
        "size_bytes": 100,
        "mtime_ns": 111,
        "st_dev": 2,
        "st_ino": 10,
        "display_name": "Ｈello　Ｗorld .txt",
        "normalized_name": "hello world .txt",
        "dedup_key": store_module.dedup_key(100, "Ｈello　Ｗorld .txt"),
        "mount_hint": "F:",
    }
    store.append_event("file_added", payload=payload_a, occurred_at="2026-01-01T00:00:00", config_fingerprint="c")

    # 硬链接孪生：同指纹不同路径 → 归并同一本书，两个 files 行
    payload_b = dict(payload_a, path="F:/liked/b.txt", st_ino=11)
    store.append_event("file_added", payload=payload_b, occurred_at="2026-01-01T00:00:01", config_fingerprint="c")

    # 同名不同大小 → 另立一本书（不算重）
    payload_c = dict(payload_a, path="F:/liked/c.txt", st_ino=12, size_bytes=200, normalized_name="hello world .txt")
    payload_c["dedup_key"] = store_module.dedup_key(200, "hello world .txt")
    store.append_event("file_added", payload=payload_c, occurred_at="2026-01-01T00:00:02", config_fingerprint="c")

    conn = store.read_conn()
    try:
        n_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        book_ids = {r[0] for r in conn.execute("SELECT DISTINCT book_id FROM files")}
        n_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    finally:
        conn.close()
    assert n_books == 2  # 同名同大小归并一本；同名不同大小另立一本
    assert len(book_ids) == 2
    assert n_files == 3

    # schema 硬保险：绕过投影直插第二本同指纹书必须被 UNIQUE 拒绝
    raw = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO books(display_name, size_bytes, ext, dedup_key, normalized_name, status, first_seen_at)"
                " VALUES('dup', 100, '.txt', ?, 'hello world .txt', 'active', '2026-01-01T00:00:00')",
                (payload_a["dedup_key"],),
            )
    finally:
        raw.close()
