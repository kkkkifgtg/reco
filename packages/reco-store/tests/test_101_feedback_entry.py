"""#101：反馈入口收成一个深模块——effects、静音落库、文案都在这里一次做完。"""
from __future__ import annotations

import pytest

from reco_store import open_store
from reco_store.feedback import record_feedback

pytestmark = pytest.mark.integration

NOW = "2026-03-01T00:00:00+00:00"


def _book(store, ino=1, name="one.txt"):
    store.append_event(
        "file_added",
        payload={"path": f"F:/data/liked/{name}", "zone": "liked", "ext": ".txt",
                 "size_bytes": 1, "mtime_ns": 1, "st_dev": 1, "st_ino": ino,
                 "display_name": name, "normalized_name": name, "dedup_key": f"k{ino}"},
        occurred_at=NOW, config_fingerprint="t/101",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM files WHERE st_ino = ?", (ino,)).fetchone()[0])
    finally:
        conn.close()


def _kinds(store):
    return [e.event_type for e in store.read_events()]


def test_dislike_writes_event_profile_snapshot_and_mute_window(tmp_path):
    store = open_store(tmp_path / "fb.db")
    try:
        bid = _book(store)
        receipt = record_feedback(store, "dislike", bid, axis="mood.dark",
                                  value=1.0, now=NOW)
        assert "下调" in receipt["display"]
        assert receipt["event_id"] and receipt["profile_event_id"]
        kinds = _kinds(store)
        assert "rec_feedback" in kinds and "mute_upserted" in kinds
        assert "profile_updated" in kinds

        mutes = store.load_mutes()
        assert len(mutes) == 1
        row = mutes[0]
        assert row["axis"] == "mood.dark" and row["kind"] == "dislike"
        # 分档时长（tag 90d）由显式 now 决定，不读墙钟
        assert row["expired_at"].startswith("2026-05-30")
        assert abs(row["remaining_days"] - 90.0) < 1e-6
    finally:
        store.close()


def test_repeat_dislike_replaces_window_without_duplicate_row(tmp_path):
    store = open_store(tmp_path / "fb2.db")
    try:
        bid = _book(store)
        record_feedback(store, "dislike", bid, axis="mood.dark", now=NOW)
        record_feedback(store, "dislike", bid, axis="mood.dark",
                        now="2026-03-02T00:00:00+00:00")
        assert len(store.load_mutes()) == 1
        assert store.load_mutes()[0]["expired_at"].startswith("2026-05-31")
    finally:
        store.close()


def test_like_only_folds_and_add_to_pool_stays_out_of_taste_stream(tmp_path):
    store = open_store(tmp_path / "fb3.db")
    try:
        bid = _book(store)
        like = record_feedback(store, "like", bid, axis="mood.dark", now=NOW)
        assert "上调" in like["display"]
        assert store.load_mutes() == []

        before = len(_kinds(store))
        pool = record_feedback(store, "add_to_pool", bid, now=NOW)
        assert pool["mute_window"] is None and pool["display"] == ""
        added = _kinds(store)[before:]
        assert added == ["pool_added"]
    finally:
        store.close()