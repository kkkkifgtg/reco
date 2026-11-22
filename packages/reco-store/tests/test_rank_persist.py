"""#70 回归：推荐结果必须落库（rec_runs + recommendations）。

架构文档 ARCHITECTURE.md:721 明确要求「rank(...) → 写 rec_runs/recommendations；
GUI/CLI 均读同一 run_id」。修复前两表永远 0 行。
"""
from __future__ import annotations

import json

import pytest

NOW = "2026-11-22T10:00:00+00:00"


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store
    s = open_store(tmp_path / "rec.db")
    yield s
    s.close()


def _seed_books(store, n=3):
    """落 n 本 candidate 书并给向量（让 rank 有输出）。"""
    import struct
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
            bid = int(conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
        finally:
            conn.close()
        # 造一个 2 维向量（契约：>{dim}i 索引 + >{dim}d 权重）
        blob = struct.pack(">2i", 1, 2) + struct.pack(">2d", 0.6, 0.8)
        store.append_event(
            "vector_computed",
            payload={"vector_blob_hex": blob.hex(), "dim": 2,
                     "idf_version": "t/1", "norm": 1.0},
            book_id=bid, occurred_at=NOW, config_fingerprint="t/1",
        )


def _counts(store):
    conn = store.read_conn()
    try:
        runs = conn.execute("SELECT COUNT(*) FROM rec_runs").fetchone()[0]
        recs = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
        return runs, recs
    finally:
        conn.close()


def test_rank_writes_rec_runs_and_recommendations(store):
    """跑一次推荐 → rec_runs 1 行、recommendations 与结果行数一致。"""
    from reco_store.recommend import rank_candidates, persist_rank_run

    _seed_books(store, 3)
    out = rank_candidates(store)
    assert out["rank_rows"], "前置条件：应有推荐结果"

    persist_rank_run(store, out, now=NOW, actor="gui")

    runs, recs = _counts(store)
    assert runs == 1, f"应写入 1 条 rec_runs，实际 {runs}"
    assert recs == len(out["rank_rows"]), f"应写入 {len(out['rank_rows'])} 条 recommendations，实际 {recs}"


def test_persisted_rows_carry_rank_and_score(store):
    """落库内容与 rank 输出一致（可追溯）。"""
    from reco_store.recommend import rank_candidates, persist_rank_run

    _seed_books(store, 3)
    out = rank_candidates(store)
    run_id = persist_rank_run(store, out, now=NOW, actor="cli")

    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT book_id, rank, score, is_exploration FROM recommendations"
            " WHERE run_id=? ORDER BY rank", (run_id,)).fetchall()
        run = conn.execute(
            "SELECT actor, candidate_count, exploration_count FROM rec_runs WHERE run_id=?",
            (run_id,)).fetchone()
    finally:
        conn.close()

    assert len(rows) == len(out["rank_rows"])
    assert rows[0][1] == 1, "第一条 rank 应为 1"
    assert abs(float(rows[0][2]) - float(out["rank_rows"][0].score)) < 1e-9
    assert run[0] == "cli"
    assert run[1] == out["n_candidates"]


def test_persist_is_idempotent_on_same_run_id(store):
    """同一 run_id 重复写不产生重复行。"""
    from reco_store.recommend import rank_candidates, persist_rank_run

    _seed_books(store, 2)
    out = rank_candidates(store)
    run_id = persist_rank_run(store, out, now=NOW, actor="gui")

    runs1, recs1 = _counts(store)
    persist_rank_run(store, out, now=NOW, actor="gui", run_id=run_id)
    runs2, recs2 = _counts(store)

    assert (runs1, recs1) == (runs2, recs2), "重复写不得改变行数"


def test_gui_and_cli_produce_same_structure(store):
    """GUI 与 CLI 走同一写入路径，结构一致。"""
    from reco_store.recommend import rank_candidates, persist_rank_run

    _seed_books(store, 2)
    out = rank_candidates(store)
    gui_run = persist_rank_run(store, out, now=NOW, actor="gui")
    cli_run = persist_rank_run(store, out, now=NOW, actor="cli")

    conn = store.read_conn()
    try:
        for rid in (gui_run, cli_run):
            n = conn.execute("SELECT COUNT(*) FROM recommendations WHERE run_id=?", (rid,)).fetchone()[0]
            assert n == len(out["rank_rows"]), f"{rid} 行数不一致"
    finally:
        conn.close()
