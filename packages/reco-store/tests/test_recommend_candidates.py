"""T4 红测试：推荐候选只取 candidate 专区（liked 不进候选）。

语义最小改动：liked 仍是正样本（positive_vectors 口径不变），但不再作为推荐
候选；推荐专区（输出落位）同样不进候选。按 book_id 去重（同书多 files 行）。
"""

from __future__ import annotations

import math
import struct

from reco_store.recommend import rank_candidates

NOW = "2026-11-21T00:00:00+00:00"
CFG = "t4/candidates/1"


def _seed_book(store, *, zone: str, name: str, ino: int, size: int = 100, path=None):
    from reco_store import dedup_key

    key = dedup_key(size, name)
    store.append_event(
        "file_added",
        payload={
            "path": path or f"Z:/synthetic/{zone}/{name}", "zone": zone, "ext": ".txt",
            "size_bytes": size, "mtime_ns": 1000 + ino, "st_dev": 1, "st_ino": ino,
            "display_name": name, "normalized_name": name, "dedup_key": key,
            "mount_hint": "Z:",
        },
        occurred_at=NOW, config_fingerprint=CFG,
    )
    conn = store.read_conn()
    try:
        return int(conn.execute(
            "SELECT book_id FROM books WHERE dedup_key = ?", (key,)).fetchone()[0])
    finally:
        conn.close()


def _seed_vector(store, book_id: int, weights=(0.6, 0.8)) -> None:
    dim = len(weights)
    dims = list(range(dim))
    blob = struct.pack(f">{dim}i", *dims) + struct.pack(f">{dim}d", *weights)
    store.append_event(
        "vector_computed", book_id=book_id,
        payload={"vector_blob_hex": blob.hex(), "dim": dim, "idf_version": "t4/1",
                 "norm": math.sqrt(sum(w * w for w in weights))},
        occurred_at=NOW, config_fingerprint=CFG,
    )


def test_candidates_are_candidate_zone_only(store):
    """liked/推荐专区都不进候选；只有 candidate 区书出现在 rank_rows。"""
    liked = _seed_book(store, zone="liked", name="liked.txt", ino=1)
    cand = _seed_book(store, zone="candidate", name="cand.txt", ino=2)
    rec = _seed_book(store, zone="推荐专区", name="rec.txt", ino=3)
    for bid in (liked, cand, rec):
        _seed_vector(store, bid)

    out = rank_candidates(store)

    assert {row.book_id for row in out["rank_rows"]} == {f"b{cand}"}
    assert f"b{liked}" not in {row.book_id for row in out["rank_rows"]}
    assert out["n_candidates"] == 1
    # 正样本语义最小改动：liked 的向量仍计入库级向量数（不被丢弃）
    assert out["n_vectors"] == 3
    assert out["n_skipped"] == 0


def test_same_book_multiple_files_counted_once(store):
    """同书（同 dedup_key）同区两条 files 行 → 候选按 book_id 去重（T6 后同区版本）。"""
    cand = _seed_book(store, zone="candidate", name="same.txt", ino=1)
    copy = _seed_book(store, zone="candidate", name="same.txt", ino=2,
                      path="Z:/synthetic/candidate/same-copy.txt")
    assert cand == copy  # 同书（books 行唯一）
    _seed_vector(store, cand)

    out = rank_candidates(store)

    assert out["n_candidates"] == 1
    assert [row.book_id for row in out["rank_rows"]] == [f"b{cand}"]


def test_candidate_without_vector_not_ranked(store):
    """候选缺向量：如实计 skipped，不进 rank_rows（不静默当 0 分）。"""
    liked = _seed_book(store, zone="liked", name="liked.txt", ino=1)
    cand = _seed_book(store, zone="candidate", name="cand.txt", ino=2)
    _seed_vector(store, liked)  # liked 有向量（正样本），候选没有

    out = rank_candidates(store)

    assert list(out["rank_rows"]) == []
    assert out["n_candidates"] == 1
    assert out["n_vectors"] == 1  # 库级向量数（liked 正样本仍计）
    assert out["n_skipped"] == 1  # 候选缺向量数


def test_no_candidates_but_vectors_exist(store):
    """只有 liked：候选为空但库里有向量（空态可区分「候选为空」）。"""
    liked = _seed_book(store, zone="liked", name="liked.txt", ino=1)
    _seed_vector(store, liked)

    out = rank_candidates(store)

    assert list(out["rank_rows"]) == []
    assert out["n_candidates"] == 0
    assert out["n_vectors"] == 1

def test_dual_zone_book_excluded_from_candidates(store):
    """同书双区（liked + candidate 各一条 present 文件）不得进候选（T6 修复）。

    语义：liked 是正样本——只要该书拥有任一 present liked 文件，即使同时有
    candidate 文件也不得被推荐。
    """
    dual = _seed_book(store, zone="candidate", name="dual.txt", ino=1)
    liked = _seed_book(store, zone="liked", name="dual.txt", ino=2)
    assert dual == liked  # 同名同大小同 dedup_key → 同一本书
    pure = _seed_book(store, zone="candidate", name="pure.txt", ino=3)
    _seed_vector(store, dual)
    _seed_vector(store, pure)

    out = rank_candidates(store)

    ids = {row.book_id for row in out["rank_rows"]}
    assert f"b{dual}" not in ids, "同书双区（有 liked 文件）必须被排除"
    assert ids == {f"b{pure}"}
    assert out["n_candidates"] == 1
    assert out["n_vectors"] == 2  # 库级向量数：双区书的向量仍作正样本


def test_liked_file_removed_keeps_candidate(store):
    """liked 文件 present=0（已移除）不算「拥有 liked 文件」→ 该书仍是候选。"""
    dual = _seed_book(store, zone="candidate", name="dual.txt", ino=1)
    _seed_book(store, zone="liked", name="dual.txt", ino=2)
    _seed_vector(store, dual)
    store.append_event("file_removed", payload={"path": "Z:/synthetic/liked/dual.txt"},
                       occurred_at=NOW, config_fingerprint=CFG)

    out = rank_candidates(store)

    assert [row.book_id for row in out["rank_rows"]] == [f"b{dual}"]
    assert out["n_candidates"] == 1

