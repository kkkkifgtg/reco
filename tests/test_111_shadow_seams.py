"""#111：影子接缝清点——每条 seam 要么接上，要么删掉。

清点结果：
1. char_ngram_bucket_tfidf 无消费者，且 ARCHITECTURE/ADR-0004 明说生产不计算 IDF
   → 删除模块与测试（删掉接缝，保留「不落 n-gram 原文」这条仍在生效的约束）。
2. proposals 不在 verify_replay 比对面 → 加进 COMPARE_TABLES，漂移可见。
3. release_mute 无生产调用方 → 由 reco_store.feedback.release_feedback 接上，
   CLI unmute 是它的第一个调用方（用户「改主意」不必等过期）。
4. 「人工覆盖三套」核验后不是同一件事的三份实现：book_tags 的 manual/user 行
   归属标签权威，relation_fields 的 manual 归属逐字段真相，而 manual_set 只是
   标签人工写入的事件形态（投影到同一张 book_tags）。两套权威、一条事件路径，
   合并会抹掉领域边界——保留，理由记在 #119。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from reco_labels import propose_tags
from reco_labels.auto_tag import StoreAdapter
from reco_labels.seed import build_seed_rows
from reco_store import open_store
from reco_store.feedback import record_feedback, release_feedback
from reco_store.store import COMPARE_TABLES

NOW = "2026-03-01T00:00:00+00:00"


def _book(store, ino=1, name="one.txt"):
    store.append_event(
        "file_added",
        payload={"path": f"F:/data/liked/{name}", "zone": "liked", "ext": ".txt",
                 "size_bytes": 1, "mtime_ns": 1, "st_dev": 1, "st_ino": ino,
                 "display_name": name, "normalized_name": name, "dedup_key": f"k{ino}"},
        occurred_at=NOW, config_fingerprint="t/111",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM files WHERE st_ino = ?", (ino,)).fetchone()[0])
    finally:
        conn.close()


def test_proposals_are_compared_on_replay(tmp_path):
    assert "proposals" in COMPARE_TABLES
    store = open_store(tmp_path / "seam.db")
    try:
        adapter = StoreAdapter(store)
        for row in build_seed_rows():
            adapter.upsert_tag(row)
        propose_tags(adapter, {"摄心术": 9}, threshold=3, now=NOW)
        assert store.verify_replay().ok

        # 直接改投影（不改事件）：修复前 proposals 不在比对面 → 这里会静默绿
        conn = sqlite3.connect(store.path)
        try:
            conn.execute("UPDATE proposals SET status = 'rejected'")
            conn.commit()
        finally:
            conn.close()
        report = store.verify_replay()
        assert not report.ok
        assert any("proposals" in d for d in report.diffs)
    finally:
        store.close()


def test_release_feedback_clears_mute_and_replays(tmp_path):
    store = open_store(tmp_path / "release.db")
    try:
        bid = _book(store)
        record_feedback(store, "dislike", bid, axis="mood.dark", now=NOW)
        assert len(store.load_mutes()) == 1

        receipt = release_feedback(store, bid, now=NOW)
        assert receipt["kind"] == "unmute" and receipt["event_id"]
        assert store.load_mutes() == []
        assert [e.event_type for e in store.read_events()][-1] == "mute_released"
        assert store.verify_replay().ok
    finally:
        store.close()


def test_release_feedback_is_idempotent_on_a_book_without_mute(tmp_path):
    store = open_store(tmp_path / "release2.db")
    try:
        bid = _book(store)
        release_feedback(store, bid, now=NOW)
        assert store.load_mutes() == []
        assert store.verify_replay().ok
    finally:
        store.close()


def test_cli_unmute_prints_json_and_clears_mute(tmp_path, capsys):
    from reco_cli import main

    store = open_store(tmp_path / "cli.db")
    try:
        bid = _book(store)
        record_feedback(store, "dislike", bid, axis="mood.dark", now=NOW)
    finally:
        store.close()

    rc = main(["unmute", "--db", str(tmp_path / "cli.db"), "--book-id", f"b{bid}"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok" and payload["book_id"] == f"b{bid}"

    reopened = open_store(tmp_path / "cli.db")
    try:
        assert reopened.load_mutes() == []
        assert reopened.verify_replay().ok
    finally:
        reopened.close()


def test_cli_unmute_rejects_bad_book_id(tmp_path, capsys):
    from reco_cli import main

    rc = main(["unmute", "--db", str(tmp_path / "x.db"), "--book-id", "abc"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"


def test_dead_tfidf_seam_is_gone():
    with pytest.raises(ImportError):
        __import__("reco_core.vectorize")