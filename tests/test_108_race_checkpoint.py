"""#108 —— 反馈写入口必须回传「落库后」的事件 id，而不是时间戳占位。

修复前：persist_feedback 把 event_id 写成 int(now.timestamp())（十亿级），
而 store 会另外分配自增 id；调用方拿到的 id 在库里不存在，
checkpoint 只能回头读「库尾事件」推断，并发写时锚点会指错。
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration

T0 = "2026-11-21T00:00:00+00:00"


def _seed(store) -> None:
    from reco_ui.glue import seed_books

    seed_books(store, books=4, liked=2, candidate=2, now=T0)
    for axis, book in (("mechanism", 1), ("mood.dark", 2)):
        store.append_event(
            "tag_hit", book_id=book,
            payload={"axis": axis, "tag_id": axis, "confidence": 1.0},
            occurred_at=T0, config_fingerprint="t108/seed",
        )


class _RacingStore:
    """在 feedback 落库之后、checkpoint 读取之前插入一条外部事件。"""

    def __init__(self, inner):
        self._inner = inner
        self._injected = False

    def append(self, event):
        return self._inner.append(event)

    def append_event(self, event_type, **kwargs):
        return self._inner.append_event(event_type, **kwargs)

    def read_events(self):
        if not self._injected:
            self._injected = True
            self._inner.append_event(
                "pool_added", book_id=3, payload={"kind": "add_to_pool"},
                occurred_at=T0, config_fingerprint="t108/race",
            )
        return self._inner.read_events()


def _snapshot_rows(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT axis_weights_json, sample_counts_json, event_id_high_water"
            " FROM profile_versions ORDER BY version_id"
        ).fetchall()
    finally:
        conn.close()


def _max_event_id(store, *, before_type: str = "profile_updated") -> int:
    conn = store.read_conn()
    try:
        return int(conn.execute(
            "SELECT max(event_id) FROM events WHERE event_type != ?"
            " AND event_id < (SELECT max(event_id) FROM events WHERE event_type = ?)",
            (before_type, before_type),
        ).fetchone()[0])
    finally:
        conn.close()


def test_persist_feedback_returns_store_assigned_id(tmp_path):
    """落库后回传的 id 必须真实存在于事件表。"""
    from reco_core.feedback import persist_feedback
    from reco_store import open_store

    store = open_store(tmp_path / "id.db")
    try:
        _seed(store)
        result = persist_feedback("like", book_id=3, axis="mood.dark",
                                  value=1.0, store=store, now=T0)
        stored = store.read_events()[-1]
        assert result["event"]["event_id"] == stored.event_id
        assert stored.event_type == "rec_feedback"
    finally:
        store.close()


def test_snapshot_matches_replay_when_event_races_in(tmp_path):
    """外部事件插在反馈与 checkpoint 之间时，快照仍等于全量重放的折叠结果。"""
    from reco_core.feedback import record_feedback, rehydrate_from_events
    from reco_core.profile import export_profile
    from reco_store import open_store

    store = open_store(tmp_path / "race.db")
    try:
        _seed(store)
        record_feedback("like", book_id=3, axis="mood.dark", value=1.0,
                        store=_RacingStore(store), now=T0)
        rows = _snapshot_rows(store)
        assert len(rows) == 1
        counts = json.loads(rows[0][1])
        live = export_profile(rehydrate_from_events(store.read_events(), now=T0))
        assert counts == live["sample_counts"]
    finally:
        store.close()