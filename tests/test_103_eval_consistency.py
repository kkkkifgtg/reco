"""#103：eval 口径与打分同源——一册一行、样本=特征向量、门控计数不手写键名。

修复前三处不一致：load_eval_samples 直接 JOIN files（同书多路径出现多行）；样本文本
取书名，而 §6.3 打分吃的是特征向量；门控把 tag_like/tag_dislike 手工翻译成 n_pos/n_neg，
每个调用方各翻一遍。
"""
from __future__ import annotations

import math
import struct

import pytest

from reco_store import dedup_key, normalize_book_name, open_store
from reco_store.recommend import load_eval_samples, load_vectors

pytestmark = pytest.mark.integration

T0 = "2026-11-21T06:00:00+00:00"
FP = "t103/seed/1"


def _vector(store, book_id: int, dims: int = 8) -> None:
    weights = [round(0.05 + ((book_id * 7 + d * 3) % 19) / 40.0, 4) for d in range(dims)]
    blob = struct.pack(f">{dims}i", *range(dims)) + struct.pack(f">{dims}d", *weights)
    store.append_event(
        "vector_computed", book_id=book_id,
        payload={"vector_blob_hex": blob.hex(), "dim": dims,
                 "idf_version": "t103-idf/1",
                 "norm": math.sqrt(sum(w * w for w in weights))},
        occurred_at=T0, config_fingerprint="t103/vectors/1")


def _add_file(store, book_id_display: str, zone: str, path_suffix: str) -> None:
    display = book_id_display
    store.append_event(
        "file_added",
        payload={"path": f"Z:/synthetic/{zone}/{path_suffix}", "zone": zone,
                 "ext": ".txt", "size_bytes": 1000, "mtime_ns": 1_000_000,
                 "st_dev": 1, "st_ino": 100 + len(path_suffix),
                 "display_name": display, "normalized_name": normalize_book_name(display),
                 "dedup_key": dedup_key(1000, display)},
        occurred_at=T0, config_fingerprint=FP, actor="system")


def _seed(db):
    from reco_ui.glue import seed_books

    store = open_store(db)
    try:
        seed_books(store, books=6, liked=2, candidate=4, now=T0)
        # 同一本书的第二条 liked 路径（硬链接/副本）：book 仍只有 1 本
        _add_file(store, "book-1.txt", "liked", "twin-book-1.txt")
        for book_id in range(1, 7):
            _vector(store, book_id)
    finally:
        store.close()
    return db


def test_one_row_per_book(tmp_path):
    db = _seed(tmp_path / "eval.db")
    store = open_store(db)
    try:
        samples = load_eval_samples(store)
    finally:
        store.close()
    ids = [book_id for book_id, _features, _label in samples]
    assert len(ids) == len(set(ids)) == 6, "同一本书多路径只该出现一次"


def test_sample_features_are_the_feature_vectors(tmp_path):
    db = _seed(tmp_path / "vectors.db")
    store = open_store(db)
    try:
        samples = load_eval_samples(store)
        vectors = load_vectors(store)
    finally:
        store.close()
    by_id = {book_id: (features, label) for book_id, features, label in samples}
    for book_id, vector in vectors.items():
        features, _label = by_id[f"b{book_id}"]
        assert features == vector, "样本特征必须就是打分用的向量，不是书名"
    assert by_id["b1"][1] == 1 and by_id["b3"][1] == 0, "liked=1，其余 0"


def test_gate_counts_come_from_profile_counts():
    from reco_core.profile import sample_counts_for_scoring

    counts = {"tag_like": 12, "tag_dislike": 9, "behavior": 3}
    assert sample_counts_for_scoring(counts) == {"n_pos": 12, "n_neg": 9}
    # score.Profile 自己的键名也接受（同源函数不再要求调用方先翻译）
    assert sample_counts_for_scoring({"n_pos": 12, "n_neg": 9}) == {"n_pos": 12, "n_neg": 9}
