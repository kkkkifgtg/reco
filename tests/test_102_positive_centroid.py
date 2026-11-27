"""#102：相似度质心成员 = liked 正样本（CONTEXT 口径），不是全库有向量书。

修复前 rank_candidates 拿全库有向量书当质心，候选区一扩容质心就漂移——同一本书的
推荐分数会静默改变。裁定：质心只取 liked 专区（按 book_id 去重），与 CONTEXT.md
「正样本 = liked/ 中的作品，或被用户明确标为喜欢的作品」一致。
"""
from __future__ import annotations

import math
import struct

import pytest

from reco_store import dedup_key, normalize_book_name, open_store

pytestmark = pytest.mark.integration

T0 = "2026-11-21T06:00:00+00:00"
FP = "t102/seed/1"


def _vector(store, book_id: int, dims: int = 8) -> None:
    weights = [round(0.05 + ((book_id * 7 + d * 3) % 19) / 40.0, 4) for d in range(dims)]
    blob = struct.pack(f">{dims}i", *range(dims)) + struct.pack(f">{dims}d", *weights)
    store.append_event(
        "vector_computed", book_id=book_id,
        payload={"vector_blob_hex": blob.hex(), "dim": dims,
                 "idf_version": "t102-idf/1",
                 "norm": math.sqrt(sum(w * w for w in weights))},
        occurred_at=T0, config_fingerprint="t102/vectors/1")


def _add_book(store, book_id: int, zone: str) -> None:
    display = f"book-{book_id}.txt"
    store.append_event(
        "file_added",
        payload={"path": f"Z:/synthetic/{zone}/book-{book_id}.txt", "zone": zone,
                 "ext": ".txt", "size_bytes": 1000 + book_id,
                 "mtime_ns": 1_000_000_000 + book_id, "st_dev": 1, "st_ino": 100 + book_id,
                 "display_name": display, "normalized_name": normalize_book_name(display),
                 "dedup_key": dedup_key(1000 + book_id, display)},
        occurred_at=T0, config_fingerprint=FP, actor="system")


def _seed(db) -> None:
    from reco_ui.glue import seed_books

    store = open_store(db)
    try:
        seed_books(store, books=6, liked=2, candidate=4, now=T0)
        for book_id in range(1, 7):
            _vector(store, book_id)
    finally:
        store.close()


def _rank(db):
    from reco_store.recommend import rank_candidates

    store = open_store(db)
    try:
        return rank_candidates(store)
    finally:
        store.close()


def _scores(out) -> dict:
    return {row.book_id: float(row.score) for row in out["rank_rows"]}


def test_candidate_growth_does_not_move_centroid(tmp_path):
    db = tmp_path / "centroid.db"
    _seed(db)
    before = _scores(_rank(db))
    store = open_store(db)
    try:
        _add_book(store, 7, "candidate")  # 只是候选区扩容，不是新正样本
        _vector(store, 7)
    finally:
        store.close()
    after = _scores(_rank(db))
    for book_id, score in before.items():
        assert after[book_id] == score, f"{book_id} 的分数被候选扩容改动了 → 质心含候选区"


def test_liked_growth_moves_centroid(tmp_path):
    db = tmp_path / "liked.db"
    _seed(db)
    before = _scores(_rank(db))
    store = open_store(db)
    try:
        _add_book(store, 7, "liked")  # 新正样本，质心必须变
        _vector(store, 7)
    finally:
        store.close()
    after = _scores(_rank(db))
    assert any(after[b] != s for b, s in before.items()), "liked 增员没有影响质心 → 正样本没进质心"


def test_positive_count_reports_liked_only(tmp_path):
    db = tmp_path / "count.db"
    _seed(db)
    out = _rank(db)
    assert out["n_positive"] == 2, "正样本数 = liked 有向量书数"
    assert out["n_vectors"] == 6, "n_vectors 仍是库级有向量书数（空态文案依据）"