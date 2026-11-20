"""W5-N（t19）：键名-编码契约测试——payload 键名承诺的编码必须与解码一致。

历史缺陷（W13 发现上报）：vector_blob_b64 键名暗示 base64，投影器却用
bytes.fromhex 解码——行为自洽（写入方也按 hex 写）但名实不符，真实向量
接线（W12/W13）与换核（W14）必踩坑。裁定（队长预裁）：统一 hex，
唯一合法键名 vector_blob_hex，解码 bytes.fromhex；旧键名入白名单外键。
"""

from __future__ import annotations

import pytest

HEX_BLOB = "0001027f80ff"  # 6 字节，含 0x00/0x7f/0x80/0xff 边界值


def _seed_book(store, store_module):
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
    conn = store.read_conn()
    try:
        book_id = conn.execute("SELECT book_id FROM books LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    return int(book_id)


def _vector_payload(**overrides):
    payload = {"vector_blob_hex": HEX_BLOB, "dim": 6, "idf_version": "idf-1", "norm": 1.0}
    payload.update(overrides)
    return payload


def test_vector_blob_hex_key_roundtrips(store, store_module):
    """键名承诺 hex：解码结果必须逐字节等于 hex 原值（键名-编码耦合）。"""
    book_id = _seed_book(store, store_module)
    store.append_event(
        "vector_computed", book_id=book_id, payload=_vector_payload(),
        occurred_at="2026-01-01T00:00:01", config_fingerprint="c",
    )
    conn = store.read_conn()
    try:
        blob, dim, norm = conn.execute(
            "SELECT vector_blob, dim, norm FROM feature_vectors WHERE book_id = ?", (book_id,)
        ).fetchone()
    finally:
        conn.close()
    assert blob == bytes.fromhex(HEX_BLOB)
    assert (dim, norm) == (6, 1.0)


def test_old_b64_key_name_rejected(store, store_module):
    """旧键名 vector_blob_b64 已废：不在白名单，写入层拒绝（防名实再劈叉）。"""
    book_id = _seed_book(store, store_module)
    with pytest.raises(store_module.PayloadViolationError):
        store.append_event(
            "vector_computed", book_id=book_id, payload=_vector_payload(vector_blob_b64=HEX_BLOB),
            occurred_at="2026-01-01T00:00:02", config_fingerprint="c",
        )
