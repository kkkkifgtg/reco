"""W5-F 修复红测试（t11）：F1 全表无正文列 schema 断言 + F2 book_tags 双保护。

契约：
- F1【blocker，W4 接条款】B2 的守护必须是「已提交测试」而非一次性探针：
  遍历 sqlite_master 全部表 × PRAGMA table_info，断言无 content/body/text/
  raw_text 类正文列。守卫自证：fixture 临时建一个含 body 列的表，
  守卫函数必须抓到（证明测试不是永真）。
- F2【medium】book_tags 人工优先双保护：source=manual 与 user 的行都不被
  label_cleared（auto 清理）与 label_set（auto 重算）覆盖——与 W6
  LabelsStore 协议收敛。
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def store_module():
    from reco_store import store

    return store


@pytest.fixture
def store(store_module, tmp_path):
    s = store_module.open_store(tmp_path / "store.db")
    yield s
    s.close()


# ----------------------------------------------------------- F1：全表无正文列

#: B2 正文列黑名单：这些列名模式出现在任何表即违规。
CONTENT_COLUMN_PATTERNS = ("content", "body", "text_body", "raw_text", "fulltext", "chapter")


def _content_column_violations(conn: sqlite3.Connection) -> list[str]:
    """扫描全部用户表的全部列，返回违规「表.列」清单。"""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    violations: list[str] = []
    for table in tables:
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
            col = row[1].lower()
            if any(pat in col for pat in CONTENT_COLUMN_PATTERNS):
                violations.append(f"{table}.{row[1]}")
    return violations


def test_no_content_columns_in_any_table(store):
    """F1：全部表无正文列（B2 已提交守护）。"""
    conn = store.read_conn()
    try:
        violations = _content_column_violations(conn)
    finally:
        conn.close()
    assert violations == []


def test_content_column_guard_self_proves(store, tmp_path):
    """守卫自证：注入一个含 body 列的表，守卫必须抓到（防永真测试）。"""
    evil = sqlite3.connect(tmp_path / "evil.db")
    try:
        evil.execute("CREATE TABLE smuggled(id INTEGER PRIMARY KEY, body TEXT)")
        evil.execute("CREATE TABLE innocent(id INTEGER PRIMARY KEY, note TEXT)")
        evil.commit()
        assert _content_column_violations(evil) == ["smuggled.body"]
    finally:
        evil.close()


# ----------------------------------------------------------- F2：book_tags 双保护

def _seed_book_and_tag(store, store_module):
    """种出 1 本书 + 1 个标签，返回 book_id。"""
    store.append_event(
        "file_added",
        payload={
            "path": "F:/liked/a.txt", "zone": "liked", "ext": ".txt",
            "size_bytes": 100, "mtime_ns": 111, "st_dev": 2, "st_ino": 10,
            "display_name": "a.txt", "normalized_name": "a.txt",
            "dedup_key": store_module.dedup_key(100, "a.txt"), "mount_hint": "F:",
        },
        occurred_at="2026-01-01T00:00:00", config_fingerprint="c",
    )
    store.append_event(
        "tag_added",
        payload={"tag_id": "t-x", "axis": "背景", "label": "X", "created_by": "system"},
        occurred_at="2026-01-01T00:00:01", config_fingerprint="c",
    )
    conn = store.read_conn()
    try:
        book_id = conn.execute("SELECT book_id FROM books LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    return int(book_id)


def _tag_source(store, book_id: int) -> str | None:
    conn = store.read_conn()
    try:
        row = conn.execute(
            "SELECT source FROM book_tags WHERE book_id = ? AND tag_id = 't-x'", (book_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def test_label_cleared_keeps_manual_row(store, store_module):
    book_id = _seed_book_and_tag(store, store_module)
    store.append_event("label_set", book_id=book_id, payload={"tag_id": "t-x", "source": "manual", "confidence": 1.0, "basis_json": "{}"}, occurred_at="2026-01-01T00:00:02", config_fingerprint="c")
    store.append_event("label_cleared", book_id=book_id, payload={"tag_id": "t-x"}, occurred_at="2026-01-01T00:00:03", config_fingerprint="c")
    assert _tag_source(store, book_id) == "manual"


def test_label_cleared_keeps_user_row(store, store_module):
    """F2 现状红：label_cleared 会删掉 source=user 行（W5 只保护 manual）。"""
    book_id = _seed_book_and_tag(store, store_module)
    store.append_event("label_set", book_id=book_id, payload={"tag_id": "t-x", "source": "user", "confidence": 1.0, "basis_json": "{}"}, occurred_at="2026-01-01T00:00:02", config_fingerprint="c")
    store.append_event("label_cleared", book_id=book_id, payload={"tag_id": "t-x"}, occurred_at="2026-01-01T00:00:03", config_fingerprint="c")
    assert _tag_source(store, book_id) == "user"


def test_auto_relabel_does_not_override_manual_or_user(store, store_module):
    """双保护第二半：label_set(auto) 不得覆盖既有 manual/user 行。"""
    book_id = _seed_book_and_tag(store, store_module)
    store.append_event("label_set", book_id=book_id, payload={"tag_id": "t-x", "source": "manual", "confidence": 1.0, "basis_json": "{}"}, occurred_at="2026-01-01T00:00:02", config_fingerprint="c")
    store.append_event("label_set", book_id=book_id, payload={"tag_id": "t-x", "source": "auto", "confidence": 0.5, "basis_json": "{}"}, occurred_at="2026-01-01T00:00:03", config_fingerprint="c")
    assert _tag_source(store, book_id) == "manual"

    store.append_event("label_cleared", book_id=book_id, payload={"tag_id": "t-x"}, occurred_at="2026-01-01T00:00:04", config_fingerprint="c")
    store.append_event("label_set", book_id=book_id, payload={"tag_id": "t-x", "source": "user", "confidence": 1.0, "basis_json": "{}"}, occurred_at="2026-01-01T00:00:05", config_fingerprint="c")
    store.append_event("label_set", book_id=book_id, payload={"tag_id": "t-x", "source": "auto", "confidence": 0.5, "basis_json": "{}"}, occurred_at="2026-01-01T00:00:06", config_fingerprint="c")
    assert _tag_source(store, book_id) == "user"


def test_auto_row_still_cleanable_by_label_cleared(store, store_module):
    """保护只属于人工来源：auto 行仍可被 label_cleared 清理（语义不扩大）。"""
    book_id = _seed_book_and_tag(store, store_module)
    store.append_event("label_set", book_id=book_id, payload={"tag_id": "t-x", "source": "auto", "confidence": 0.9, "basis_json": "{}"}, occurred_at="2026-01-01T00:00:02", config_fingerprint="c")
    store.append_event("label_cleared", book_id=book_id, payload={"tag_id": "t-x"}, occurred_at="2026-01-01T00:00:03", config_fingerprint="c")
    assert _tag_source(store, book_id) is None
