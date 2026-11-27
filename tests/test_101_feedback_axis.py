"""#101 残余：反馈轴只有一个推导源，调用方不再各猜一个。

修复前 axis 有三个来源——UI 写死 "tag"（分类法里没有这个轴，会在画像
axis_weights 里造出一个假轴）、CLI 由用户传、core 缺省退化成 "unknown"。
现在 `reco_store.feedback.record_feedback` 在调用方没给轴时，按书的标签取
主导轴（行数降序、同数按最高置信度降序）；没有标签记 "unknown"——意图
落库，但不冒充任何一个真实轴。
"""
from __future__ import annotations

import json

import pytest

from reco_store import open_store
from reco_store.feedback import record_feedback
from reco_store.reads import dominant_axis_for_book

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


def _tag(store, tag_id: str, axis: str | None, label: str) -> None:
    store.append_event(
        "tag_added",
        payload={"tag_id": tag_id, "axis": axis or "", "label": label,
                 "parent": None, "created_by": "test"},
        occurred_at=NOW, config_fingerprint="t/101",
    )


def _label(store, book_id: int, tag_id: str, *, confidence: float = 0.9) -> None:
    store.append_event(
        "label_set", book_id=int(book_id),
        payload={"tag_id": tag_id, "source": "auto", "confidence": confidence,
                 "basis_json": "{}"},
        occurred_at=NOW, config_fingerprint="t/101",
    )


def _feedback_payload(store) -> dict:
    conn = store.read_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM events WHERE event_type = 'rec_feedback'"
            " ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "应落一条 rec_feedback 事件"
    return json.loads(row[0])


def _event_count(store) -> int:
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


def _tagged_book(store, ino=1):
    bid = _book(store, ino=ino)
    _tag(store, "mood.dark", "mood", "暗黑")
    _tag(store, "mood.lonely", "mood", "孤独")
    _tag(store, "mech.甲", "mechanism", "甲")
    _label(store, bid, "mood.dark", confidence=0.9)
    _label(store, bid, "mood.lonely", confidence=0.7)
    _label(store, bid, "mech.甲", confidence=0.95)
    return bid


def test_dominant_axis_prefers_more_tag_rows(tmp_path):
    """行数优先于单条置信度：两个 mood 标签压过一个高置信 mechanism 标签。"""
    store = open_store(tmp_path / "read.db")
    try:
        bid = _tagged_book(store)
        assert dominant_axis_for_book(store, bid) == "mood"
        assert dominant_axis_for_book(store, 999) is None
    finally:
        store.close()


def test_dislike_without_axis_derives_dominant_axis(tmp_path):
    store = open_store(tmp_path / "derive.db")
    try:
        bid = _tagged_book(store)
        receipt = record_feedback(store, "dislike", bid, now=NOW)
        assert receipt["axis"] == "mood"
        assert _feedback_payload(store)["axis"] == "mood"
        mutes = store.load_mutes()
        assert len(mutes) == 1 and mutes[0]["axis"] == "mood"
    finally:
        store.close()


def test_explicit_axis_is_an_override(tmp_path):
    store = open_store(tmp_path / "override.db")
    try:
        bid = _tagged_book(store)
        record_feedback(store, "dislike", bid, axis="plot.twist", now=NOW)
        assert _feedback_payload(store)["axis"] == "plot.twist"
        assert store.load_mutes()[0]["axis"] == "plot.twist"
    finally:
        store.close()


def test_like_without_axis_derives_and_does_not_mute(tmp_path):
    store = open_store(tmp_path / "like.db")
    try:
        bid = _tagged_book(store)
        receipt = record_feedback(store, "like", bid, now=NOW)
        assert receipt["axis"] == "mood"
        assert _feedback_payload(store)["axis"] == "mood"
        assert store.load_mutes() == []
    finally:
        store.close()


def test_dislike_without_tags_records_unknown_axis(tmp_path):
    """无标签可归：动作照常落库，轴记 unknown——不拒用户，也不冒充真实轴。"""
    store = open_store(tmp_path / "untagged.db")
    try:
        bid = _book(store)
        receipt = record_feedback(store, "dislike", bid, now=NOW)
        assert receipt["axis"] == "unknown"
        assert _feedback_payload(store)["axis"] == "unknown"
        assert store.load_mutes()[0]["axis"] == "unknown"
    finally:
        store.close()


def test_cli_feedback_derives_axis_when_flag_absent(tmp_path, capsys):
    """CLI 不带 --axis 也能写对轴（旧行为是 traceback 或退化成 unknown）。"""
    from reco_cli import main

    db = tmp_path / "cli.db"
    store = open_store(db)
    try:
        bid = _tagged_book(store)
    finally:
        store.close()

    assert main(["feedback", "dislike", "--db", str(db), "--book-id", str(bid)]) == 0
    out = capsys.readouterr().out
    assert "下调" in out

    store = open_store(db)
    try:
        assert store.load_mutes()[0]["axis"] == "mood"
        assert _feedback_payload(store)["axis"] == "mood"
    finally:
        store.close()

