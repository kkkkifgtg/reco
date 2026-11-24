"""#82 回归（reco-store 侧）：生成篇数（探索位配额）必须能贯穿 rank_candidates。

先前 rank_candidates(store) 无 config 形参，UI 控件值根本无处安放；本测试锚定
「调用方配额 → rank 输出探索位行数」与「rec_runs.exploration_count 落库一致」。
"""
from __future__ import annotations

import struct

import pytest

NOW = "2026-11-22T10:00:00+00:00"


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "rec.db")
    yield s
    s.close()


def _seed_books(store, n=12):
    """落 n 本 candidate 书并给同向向量（让探索池有 n 本可选）。"""
    for i in range(1, n + 1):
        name = f"书{i}.txt"
        store.append_event(
            "file_added",
            payload={"path": f"F:/data/candidate/{name}", "zone": "candidate", "ext": ".txt",
                     "size_bytes": 100, "mtime_ns": i, "st_dev": 1, "st_ino": i,
                     "display_name": name, "normalized_name": name, "dedup_key": f"k{i}"},
            occurred_at=NOW, config_fingerprint="t/1", actor="user",
        )
        conn = store.read_conn()
        try:
            bid = int(conn.execute(
                "SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
        finally:
            conn.close()
        blob = struct.pack(">2i", 1, 2) + struct.pack(">2d", 0.6, 0.8)
        store.append_event(
            "vector_computed",
            payload={"vector_blob_hex": blob.hex(), "dim": 2,
                     "idf_version": "t/1", "norm": 1.0},
            book_id=bid, occurred_at=NOW, config_fingerprint="t/1",
        )


def _exploration_count(rows):
    return sum(1 for r in rows if getattr(r, "is_exploration", False))


@pytest.mark.parametrize("count", [1, 5, 10])
def test_rank_candidates_honours_exploration_count(store, count):
    """红：rank_candidates 不接 config → TypeError（控件值无处传入）。"""
    from reco_store.recommend import rank_candidates

    _seed_books(store, 12)
    out = rank_candidates(store, config={"exploration_count": count})
    assert out["rank_rows"], "前置条件：应有推荐结果"
    assert _exploration_count(out["rank_rows"]) == count


def test_rank_candidates_default_exploration_is_two(store):
    """不传 config 时默认 2 篇（§17 Q7，不回归）。"""
    from reco_store.recommend import rank_candidates

    _seed_books(store, 12)
    out = rank_candidates(store)
    assert _exploration_count(out["rank_rows"]) == 2


def test_persisted_run_records_exploration_count(store):
    """落库的 rec_runs.exploration_count 与控件配额一致（可追溯）。"""
    from reco_store.recommend import persist_rank_run, rank_candidates

    _seed_books(store, 12)
    out = rank_candidates(store, config={"exploration_count": 5})
    run_id = persist_rank_run(store, out, now=NOW, actor="gui")
    conn = store.read_conn()
    try:
        got = conn.execute(
            "SELECT exploration_count FROM rec_runs WHERE run_id=?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert got is not None, "应写入 rec_runs"
    assert int(got[0]) == 5, f"落库 exploration_count 应与配额一致，实际 {got[0]}"
