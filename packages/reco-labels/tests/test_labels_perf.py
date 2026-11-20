"""工单 06——性能与 RECO_PERF（W2 打点纪律延续到标签热路径）。"""
from __future__ import annotations

import re
import time

import reco_io.perf as perf
from reco_labels import InMemoryLabelsStore, load_taxonomy, match_tags, record_tag_hits, TagHit
from reco_labels.seed import build_seed_rows


def _store(n_books=200):
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    for book in range(n_books):
        store.record_book_tag(book, "mech.hypnosis", source="auto", confidence=0.5,
                              basis_json="{}", updated_at="2026-11-20T00:00:00")
    return store


def test_match_tags_200_books_under_50ms():
    store = _store(200)
    taxonomy = load_taxonomy(store)
    stats = type("S", (), {"alias_counts": {"洗脑": 2, "mind control": 1}})()
    start = time.perf_counter()
    for _ in range(200):
        hits = match_tags(stats, taxonomy)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert hits[0].tag_id == "mech.hypnosis"
    assert elapsed_ms < 50.0, f"200 次匹配耗时 {elapsed_ms:.1f}ms"


def test_record_200_books_under_200ms():
    store = _store(0)
    taxonomy = load_taxonomy(store)
    hit = TagHit(tag_id="mech.hypnosis", confidence=1.0,
                 basis={"axis": "mechanism", "matches": (), "total": 1, "evidence": ()})
    start = time.perf_counter()
    for book in range(200):
        record_tag_hits(store, book, (hit,), now="2026-11-21T00:00:00")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 200.0, f"200 本书写标签耗时 {elapsed_ms:.1f}ms"


def test_perf_span_format_matches_w2_contract(capsys):
    perf.perf_enabled = True
    try:
        store = _store(50)
        with perf.perf_span("labels.load_taxonomy"):
            load_taxonomy(store)
    finally:
        perf.perf_enabled = False
    err = capsys.readouterr().err
    match = re.search(r"phase=labels\.load_taxonomy ms=(\d+\.\d{3})", err)
    assert match, f"W2 打点契约失效: {err!r}"
