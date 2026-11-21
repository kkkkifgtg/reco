"""W19 N2：file_moved payload 增补 zone → 投影同步 files.zone（跨专区移动）。

契约：zone 变更与路径变更同一事件同一事务；COALESCE 向后兼容——
无 zone 键的移动事件保持 zone 不变（旧事件流可逐位重放，ADR-0003）。
"""
from __future__ import annotations

CFG = "gui/move/1"


def _seed(store, store_module, path: str, zone: str) -> None:
    store.append_event("file_added", payload={
        "path": path, "zone": zone, "ext": ".txt", "size_bytes": 100,
        "mtime_ns": 111, "st_dev": 2, "st_ino": 10,
        "display_name": "a.txt", "normalized_name": "a.txt",
        "dedup_key": store_module.dedup_key(100, "a.txt"),
    }, occurred_at="2026-01-01T00:00:01", config_fingerprint=CFG)


def _rows(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT path, zone, moved_from_path FROM files WHERE present = 1").fetchall()
    finally:
        conn.close()


def test_file_moved_with_zone_updates_zone_column(store, store_module):
    """candidate→liked 跨专区移动：zone 列同步变更（验收「db zone 变更」）。"""
    _seed(store, store_module, "F:/candidate/a.txt", "candidate")
    store.append_event("file_moved", payload={
        "path": "F:/candidate/a.txt", "new_path": "F:/liked/a.txt", "zone": "liked",
    }, occurred_at="2026-01-01T00:00:02", config_fingerprint=CFG)
    assert _rows(store) == [("F:/liked/a.txt", "liked", "F:/candidate/a.txt")]


def test_file_moved_without_zone_keeps_zone(store, store_module):
    """无 zone 键（旧形态移动）：zone 保持不变（向后兼容，重放不漂移）。"""
    _seed(store, store_module, "F:/liked/a.txt", "liked")
    store.append_event("file_moved", payload={
        "path": "F:/liked/a.txt", "new_path": "F:/liked/sub/a.txt",
    }, occurred_at="2026-01-01T00:00:02", config_fingerprint=CFG)
    assert _rows(store) == [("F:/liked/sub/a.txt", "liked", "F:/liked/a.txt")]
