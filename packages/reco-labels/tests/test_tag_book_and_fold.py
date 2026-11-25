"""#85——tag_book_and_fold：打标 + 仅 liked 折叠 tag_hit（真实 store 集成）。"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from reco_labels import load_taxonomy, match_tags
from reco_labels.auto_tag import StoreAdapter, append_tag_hit_events, tag_book_and_fold
from reco_labels.seed import build_seed_rows

pytestmark = pytest.mark.integration

COUNTS = {"催眠": 2, "调教": 1, "摄心术": 9}


def _store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "fold.db")
    adapter = StoreAdapter(s)
    for row in build_seed_rows():
        adapter.upsert_tag(row)
    return s, adapter


def _add_book(s, name: str, ino: int) -> int:
    s.append_event(
        "file_added",
        payload={"path": f"F:/date/adult-reco/data/liked/{name}", "zone": "liked",
                 "ext": ".txt", "size_bytes": 100, "mtime_ns": 1,
                 "st_dev": 1, "st_ino": ino, "display_name": name,
                 "normalized_name": name, "dedup_key": f"k-{ino}"},
        occurred_at="2026-11-20T00:00:00+00:00", config_fingerprint="t/fold")
    conn = s.read_conn()
    try:
        bid = int(conn.execute(
            "SELECT book_id FROM files WHERE st_ino = ?", (ino,)).fetchone()[0])
    finally:
        conn.close()
    return bid


def test_tag_book_and_fold_folds_only_for_liked(tmp_path):
    s, _adapter = _store(tmp_path)
    bid = _add_book(s, "one.txt", 1)
    outcome = tag_book_and_fold(s, bid, "liked", COUNTS,
                                now="2026-11-21T00:00:00+00:00")
    assert "mech.hypnosis" in outcome.inserted
    kinds = [e.event_type for e in s.read_events()]
    assert kinds.count("tag_hit") == len(outcome.hits)


def test_tag_book_and_fold_candidate_does_not_fold(tmp_path):
    s, _adapter = _store(tmp_path)
    bid = _add_book(s, "two.txt", 2)
    outcome = tag_book_and_fold(s, bid, "candidate", COUNTS,
                                now="2026-11-21T00:00:00+00:00")
    assert outcome.inserted
    assert "tag_hit" not in [e.event_type for e in s.read_events()]


def test_append_tag_hit_events_metadata_only(tmp_path):
    s, adapter = _store(tmp_path)
    bid = _add_book(s, "nine.txt", 9)
    hits = match_tags(SimpleNamespace(alias_counts={"催眠": 3}), load_taxonomy(adapter))
    n = append_tag_hit_events(s, hits, book_id=bid, now="2026-11-21T00:00:00+00:00")
    assert n == 1
    ev = [e for e in s.read_events() if e.event_type == "tag_hit"][0]
    assert "正文" not in repr(getattr(ev, "payload_json", ""))  # B 类：零正文
