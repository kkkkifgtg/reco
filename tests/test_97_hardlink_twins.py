"""#97 —— 同 inode 的两条路径（硬链接孪生）必须都能落库。

落位主路径「同卷硬链接优先」保证源文件与副本同时存在，两者 st_ino 相同；
旧 uq_files_present(volume_id, st_ino) WHERE present=1 会把第二条 file_added 判成
UNIQUE 冲突，整条事件回滚，此后每轮刷新都在同一文件上继续失败。
"""
from __future__ import annotations

import pytest

T0 = "2026-11-21T00:00:00+00:00"

pytestmark = pytest.mark.integration


def _payload(path: str, *, ino: int = 424242, name: str = "sample.epub") -> dict:
    return {
        "path": path, "st_dev": 1, "st_ino": ino, "size_bytes": 1024,
        "mtime_ns": 111, "ext": "epub", "zone": "liked",
        "display_name": name, "normalized_name": name, "dedup_key": f"{name}:1024",
    }


def test_hardlink_twins_with_same_inode_both_land(tmp_path):
    from reco_store import open_store

    store = open_store(tmp_path / "twins.db")
    try:
        store.append_event("file_added", payload=_payload("F:/liked/src.epub"),
                           occurred_at=T0, config_fingerprint="t97/1")
        # 同卷硬链接副本：不同路径、同 (st_dev, st_ino)。
        store.append_event("file_added", payload=_payload("F:/liked/copy.epub"),
                           occurred_at=T0, config_fingerprint="t97/1")
        rows = store.read_conn().execute(
            "SELECT path, st_ino, present FROM files ORDER BY path").fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("F:/liked/copy.epub", 424242, 1),
            ("F:/liked/src.epub", 424242, 1),
        ]
        assert store.verify_replay().ok
    finally:
        store.close()