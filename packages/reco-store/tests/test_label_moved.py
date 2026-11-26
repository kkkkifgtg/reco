"""#104：label_moved 投影——人工行的合并搬迁也要能重放。"""
from __future__ import annotations

import pytest

from reco_store import open_store

pytestmark = pytest.mark.integration

T0 = "2026-01-01T00:00:00+00:00"


def _book(store, ino, name):
    store.append_event(
        "file_added",
        payload={"path": f"F:/data/liked/{name}", "zone": "liked", "ext": ".txt",
                 "size_bytes": 1, "mtime_ns": 1, "st_dev": 1, "st_ino": ino,
                 "display_name": name, "normalized_name": name, "dedup_key": f"k{ino}"},
        occurred_at=T0, config_fingerprint="t/104",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM files WHERE st_ino = ?", (ino,)).fetchone()[0])
    finally:
        conn.close()


def test_label_moved_carries_manual_row_and_replays(tmp_path):
    store = open_store(tmp_path / "moved.db")
    try:
        bid = _book(store, 1, "one.txt")
        store.append_event("tag_added", payload={"tag_id": "mech.甲", "axis": "mechanism", "label": "甲", "created_by": "user"},
                           occurred_at=T0, config_fingerprint="t/104")
        store.append_event("tag_added", payload={"tag_id": "mech.乙", "axis": "mechanism", "label": "乙", "created_by": "user"},
                           occurred_at=T0, config_fingerprint="t/104")
        store.append_event("label_set", book_id=bid,
                           payload={"tag_id": "mech.甲", "source": "manual", "confidence": 0.7,
                                    "basis_json": "{\"k\": 1}"},
                           occurred_at=T0, config_fingerprint="t/104")
        store.append_event("label_moved", book_id=bid,
                           payload={"tag_id": "mech.甲", "into": "mech.乙"},
                           occurred_at=T0, config_fingerprint="t/104")

        rows = [(r.tag_id, r.source, r.confidence, r.basis_json) for r in _book_tags(store)]
        assert rows == [("mech.乙", "manual", 0.7, '{"k": 1}')]

        assert store.verify_replay().ok
    finally:
        store.close()


def _book_tags(store):
    from reco_labels.auto_tag import StoreAdapter

    return list(StoreAdapter(store).iter_book_tags())