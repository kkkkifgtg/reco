"""RR1/t1 红测试：reading_progress 独立表 + Store API（GitHub #34）。

契约（#33「实现决定」+ RS1 §4.2 + RR1 交接 §2）：
- 表进 SCHEMA_SQL（book_id 主键 + char_offset / epub_spine_index / epub_block_pos /
  percent / updated_at / schema_version / config_fingerprint）；
- 照 ui_usage 先例：不进 COMPARE_TABLES、SCHEMA_VERSION 保持 "1"；
- upsert 走单写者队列（submit），get 走 read_conn 返回 dict 或 None；
- 进度零事件：保存前后 store.read_events() 条数不变、verify_replay 绿（§R4）。
"""
from __future__ import annotations

import pytest

from reco_store import dedup_key, open_store
from reco_store.store import COMPARE_TABLES, SCHEMA_VERSION

NOW = "2026-11-22T02:00:00+00:00"
CFG = "ui/reader/1"

_PROGRESS_COLUMNS = [
    "book_id",
    "char_offset",
    "epub_spine_index",
    "epub_block_pos",
    "percent",
    "updated_at",
    "schema_version",
    "config_fingerprint",
]


def _seed_book(store, *, name: str, ino: int, ext: str = ".txt", size: int = 100) -> int:
    """经 file_added 事件建书（真实投影路径），返回 book_id。"""
    key = dedup_key(size, name)
    store.append_event(
        "file_added",
        payload={
            "path": f"Z:/synthetic/{name}",
            "zone": "liked",
            "ext": ext,
            "size_bytes": size,
            "mtime_ns": 1000 + ino,
            "st_dev": 1,
            "st_ino": ino,
            "display_name": name,
            "normalized_name": name,
            "dedup_key": key,
            "mount_hint": "Z:",
        },
        occurred_at=NOW,
        config_fingerprint="t1/seed/1",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM books WHERE dedup_key = ?", (key,)).fetchone()[0])
    finally:
        conn.close()


def test_reading_progress_not_in_compare_tables():
    """隔离硬边界：位置表不参与投影重建逐表比对（照 ui_usage 先例）。"""
    assert "reading_progress" not in COMPARE_TABLES


def test_schema_version_stays_one():
    """CREATE IF NOT EXISTS 向后兼容：不为新表顺延 schema_version。"""
    assert SCHEMA_VERSION == "1"


def test_reading_progress_table_shape(store):
    conn = store.read_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(reading_progress)").fetchall()]
    finally:
        conn.close()
    assert cols == _PROGRESS_COLUMNS


def test_upsert_and_get_text_roundtrip(store):
    """text 格式往返：char_offset / percent / updated_at，epub 坐标为空。"""
    bid = _seed_book(store, name="novel.txt", ino=1)
    store.upsert_reading_progress(
        bid, ext="txt", char_offset=1234, percent=0.25, updated_at=NOW,
    )
    assert store.get_reading_progress(bid) == {
        "char_offset": 1234,
        "epub_spine_index": None,
        "epub_block_pos": None,
        "percent": 0.25,
        "updated_at": NOW,
    }


def test_upsert_and_get_epub_roundtrip(store):
    """epub 格式往返：spine_index + 章内 block 偏移 + percent，char_offset 为空。"""
    bid = _seed_book(store, name="book.epub", ino=2, ext=".epub")
    store.upsert_reading_progress(
        bid, ext="epub", epub_spine_index=2, epub_block_pos=7, percent=0.5, updated_at=NOW,
    )
    assert store.get_reading_progress(bid) == {
        "char_offset": None,
        "epub_spine_index": 2,
        "epub_block_pos": 7,
        "percent": 0.5,
        "updated_at": NOW,
    }


def test_upsert_overwrites_single_row(store):
    """同书二次保存是 upsert（book_id 主键单行覆写，不堆历史）。"""
    bid = _seed_book(store, name="again.txt", ino=3)
    store.upsert_reading_progress(bid, ext="txt", char_offset=10, percent=0.1, updated_at=NOW)
    later = "2026-11-22T03:00:00+00:00"
    store.upsert_reading_progress(bid, ext="txt", char_offset=900, percent=0.9, updated_at=later)
    got = store.get_reading_progress(bid)
    assert got is not None
    assert got["char_offset"] == 900
    assert got["percent"] == 0.9
    assert got["updated_at"] == later
    conn = store.read_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM reading_progress WHERE book_id = ?", (bid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_two_books_do_not_cross(store):
    """双书进度互不串（book_id 主键隔离）。"""
    a = _seed_book(store, name="a.txt", ino=4)
    b = _seed_book(store, name="b.epub", ino=5, ext=".epub")
    store.upsert_reading_progress(a, ext="txt", char_offset=42, percent=0.4, updated_at=NOW)
    store.upsert_reading_progress(
        b, ext="epub", epub_spine_index=3, epub_block_pos=11, percent=0.7, updated_at=NOW,
    )
    ga = store.get_reading_progress(a)
    gb = store.get_reading_progress(b)
    assert ga is not None and gb is not None
    assert ga["char_offset"] == 42
    assert ga["epub_spine_index"] is None
    assert gb["char_offset"] is None
    assert gb["epub_spine_index"] == 3
    assert gb["epub_block_pos"] == 11
    assert gb["percent"] == 0.7


def test_get_missing_returns_none(store):
    """未读过的书返回 None（open 时从头读的判定依据）。"""
    assert store.get_reading_progress(9999) is None


def test_progress_save_writes_no_events(store):
    """§R4：进度保存零事件——事件流条数不变，也不出现 progress 类事件。"""
    bid = _seed_book(store, name="quiet.txt", ino=6)
    before = len(store.read_events())
    store.upsert_reading_progress(bid, ext="txt", char_offset=1, percent=0.01, updated_at=NOW)
    store.upsert_reading_progress(bid, ext="txt", char_offset=2, percent=0.02, updated_at=NOW)
    assert len(store.read_events()) == before
    conn = store.read_conn()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type LIKE '%progress%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_verify_replay_ok_with_progress_data(store, store_module, tmp_path):
    """进度表有数据时 verify_replay 仍绿，且口味侧投影与无进度库逐位一致。"""
    bid = _seed_book(store, name="replay.txt", ino=7)
    clean = open_store(tmp_path / "clean.db")
    try:
        _seed_book(clean, name="replay.txt", ino=7)
        store.upsert_reading_progress(bid, ext="txt", char_offset=500, percent=0.5, updated_at=NOW)
        r1 = store.verify_replay()
        r2 = clean.verify_replay()
        assert r1.ok, r1.diffs
        assert r2.ok, r2.diffs
        # events 含 recorded_at（两库不同秒），口味侧投影逐位比对不含 events。
        keys = [k for k in r1.compare_rows if k != "events"]
        assert {k: r1.compare_rows[k] for k in keys} == {k: r2.compare_rows[k] for k in keys}
    finally:
        clean.close()


def test_invalid_inputs_rejected(store):
    """诚实校验：ext/percent/updated_at 非法即 ValueError（不写脏行）。"""
    bid = _seed_book(store, name="bad.txt", ino=8)
    with pytest.raises(ValueError):
        store.upsert_reading_progress(bid, ext="", char_offset=1, percent=0.1, updated_at=NOW)
    with pytest.raises(ValueError):
        store.upsert_reading_progress(bid, ext="txt", char_offset=1, percent="x", updated_at=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.upsert_reading_progress(bid, ext="txt", char_offset=1, percent=0.1, updated_at="")
    assert store.get_reading_progress(bid) is None
