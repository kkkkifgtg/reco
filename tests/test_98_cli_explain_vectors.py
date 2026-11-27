"""#98：CLI explain 必须读该书的真实向量，且与 recommend 同一装配层。

修复前 cmd_explain 传的是空向量（`[(rank_id, {})]`），score 的 _synthesize 跳过空向量，
explain 抛 ValueError，命令恒输出 status=no_features——docstring 声称的「透传」从未发生。
"""
from __future__ import annotations

import json
import math
import struct

import pytest

from reco_cli import main

pytestmark = pytest.mark.integration

T0 = "2026-11-21T06:00:00+00:00"


def _vector(store, book_id: int, dims: int = 8) -> None:
    weights = [round(0.05 + ((book_id * 7 + d * 3) % 19) / 40.0, 4) for d in range(dims)]
    blob = struct.pack(f">{dims}i", *range(dims)) + struct.pack(f">{dims}d", *weights)
    store.append_event(
        "vector_computed", book_id=book_id,
        payload={"vector_blob_hex": blob.hex(), "dim": dims,
                 "idf_version": "t98-idf/1",
                 "norm": math.sqrt(sum(w * w for w in weights))},
        occurred_at=T0, config_fingerprint="t98/vectors/1")


def _seed(db):
    from reco_store import open_store
    from reco_ui.glue import seed_books

    store = open_store(db)
    try:
        seed_books(store, books=6, liked=2, candidate=4, now=T0)
        for book_id in range(1, 7):
            _vector(store, book_id)
    finally:
        store.close()
    return db


def _run(capsys, argv):
    code = main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_explain_reads_real_vector_and_matches_recommend(tmp_path, capsys):
    db = _seed(tmp_path / "explain.db")
    _, rec = _run(capsys, ["recommend", "--db", str(db)])
    item = rec["items"][0]
    rc, out = _run(capsys, ["explain", "--db", str(db), "--book-id", item["book_id"]])
    assert rc == 0
    assert out["status"] == "ok", "有向量的书不该报 no_features"
    assert out["reasons"], "有向量的书应给出理由行"
    assert out["reasons"] == item["reasons"], "与 recommend 同一装配层 → 理由逐位一致"


def test_explain_without_vector_is_honest(tmp_path, capsys):
    db = _seed(tmp_path / "no_feature.db")
    from reco_store import open_store
    from reco_ui.glue import seed_books

    store = open_store(db)
    try:
        seed_books(store, books=7, liked=2, candidate=5, now=T0)  # 只补第 7 本，不进向量
    finally:
        store.close()
    rc, out = _run(capsys, ["explain", "--db", str(db), "--book-id", "b7"])
    assert rc == 0
    assert out["status"] == "no_features"
    assert out["reasons"] == []


def test_explain_unknown_book_exits_nonzero(tmp_path, capsys):
    db = _seed(tmp_path / "unknown.db")
    rc, out = _run(capsys, ["explain", "--db", str(db), "--book-id", "b999"])
    assert rc == 1
    assert "error" in out