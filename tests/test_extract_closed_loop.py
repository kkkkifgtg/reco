"""T2 闭环集成（根 tests/，全仓安装环境）：CLI extract → 推荐非空。

R8 全合成夹具：临时 Store + tmp_path 合成 txt + file_added 事件 → CLI extract
→ reco_store.recommend.rank_candidates 返回非空 rank_rows（修复前 feature_vectors
为空 → rank_rows 恒空）。幂等：第二遍 extract 后两表行数/行值不变。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from reco_cli import main

NOW = "2026-11-21T00:00:00+00:00"
CFG = "t2/closed-loop"


def _seed_book(store, path: Path, *, ino: int, dedup: str) -> int:
    store.append_event(
        "file_added",
        payload={
            "path": str(path), "zone": "candidate", "ext": ".txt",
            "size_bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
            "st_dev": 1, "st_ino": ino,
            "display_name": path.name, "normalized_name": path.name,
            "dedup_key": dedup,
        },
        occurred_at=NOW, config_fingerprint=CFG,
    )
    conn = store.read_conn()
    try:
        return int(conn.execute(
            "SELECT book_id FROM books WHERE dedup_key = ?", (dedup,)
        ).fetchone()[0])
    finally:
        conn.close()


def _rows(db: Path, table: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    finally:
        conn.close()


def _build_store(db: Path, tmp_path: Path) -> None:
    from reco_store import open_store

    store = open_store(db)
    try:
        liked = tmp_path / "liked.txt"
        liked.write_text("窗外起风了，他把书翻到最后一页，合上。" * 12, encoding="utf-8")
        cand = tmp_path / "candidate.txt"
        cand.write_text("长夜将尽，灯火在河面上碎成一片金色。" * 12, encoding="utf-8")
        _seed_book(store, liked, ino=10, dedup="closed-liked")
        _seed_book(store, cand, ino=11, dedup="closed-cand")
    finally:
        store.close()


def test_cli_extract_then_rank_rows_nonempty(tmp_path, capsys):
    from reco_store import open_store
    from reco_store.recommend import rank_candidates

    db = tmp_path / "loop.db"
    _build_store(db, tmp_path)

    store = open_store(db)
    try:
        before = rank_candidates(store)  # 修复前：无向量 → rank_rows 空
    finally:
        store.close()
    assert not before["rank_rows"]  # 修复前：feature_vectors 空 → 排序输入为空
    assert before["n_vectors"] == 0

    assert main(["extract", "--db", str(db)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {"total": 2, "ok": 2, "failed": 0, "vectors": 2}

    store = open_store(db)
    try:
        after = rank_candidates(store)
    finally:
        store.close()
    assert after["n_vectors"] == 2
    assert len(after["rank_rows"]) == 2
    assert {row.book_id for row in after["rank_rows"]} == {"b1", "b2"}


def test_cli_extract_is_idempotent(tmp_path, capsys):
    db = tmp_path / "loop.db"
    _build_store(db, tmp_path)

    assert main(["extract", "--db", str(db)]) == 0
    first_summary = json.loads(capsys.readouterr().out)
    first_features = _rows(db, "features")
    first_vectors = _rows(db, "feature_vectors")

    assert main(["extract", "--db", str(db)]) == 0
    second_summary = json.loads(capsys.readouterr().out)

    assert second_summary == first_summary
    # 特征向量（纯函数）逐行相等；features.computed_at 是「本次计算时刻」，随事件推进
    assert _rows(db, "feature_vectors") == first_vectors
    second_features = _rows(db, "features")
    assert len(second_features) == len(first_features) == 2
    assert [
        (r[0], r[1], r[2], r[3], r[4], r[6]) for r in second_features
    ] == [(r[0], r[1], r[2], r[3], r[4], r[6]) for r in first_features]
    assert len(first_vectors) == 2
