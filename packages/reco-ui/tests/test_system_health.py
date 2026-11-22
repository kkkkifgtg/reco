"""系统健康巡检（固化版）：把本轮实测的巡检项变成回归测试。

这些不是针对某个 bug 的回归，而是**系统级健康守卫**——
防止未来改动悄悄破坏并发安全、资源回收或边界处理。

来源：第 7 轮排查（并发/内存/边界三个方向实测通过）后固化。
"""
from __future__ import annotations

import os
import tempfile
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-01-01T00:00:00+00:00"


def _db(tmp_path, name="health.db"):
    from reco_store import open_store
    return open_store(tmp_path / name)


_ino_seq = {"n": 1000}


def _seed(store, n=1, zone="candidate"):
    """落 n 本书；st_ino 全局递增，避免与其它调用冲突（身份唯一性约束）。"""
    for i in range(1, n + 1):
        _ino_seq["n"] += 1
        ino = _ino_seq["n"]
        store.append_event("file_added", payload={
            "path": f"F:/data/{zone}/{ino}.txt", "zone": zone, "ext": ".txt",
            "size_bytes": 10, "mtime_ns": ino, "st_dev": 1, "st_ino": ino,
            "display_name": f"{ino}.txt", "normalized_name": f"{ino}.txt",
            "dedup_key": f"k{ino}"},
            occurred_at=NOW, config_fingerprint="t/1", actor="user")


# ─────────────────────── 并发安全 ───────────────────────

def test_concurrent_readers_do_not_fail(tmp_path):
    """多线程并发读不得报错（WAL 读不阻塞）。"""
    from reco_store import open_store

    db = tmp_path / "conc.db"
    s = open_store(db)
    _seed(s, 2)
    before = len(s.read_events())
    s.close()

    errors: list[str] = []

    def reader():
        try:
            st = open_store(db)
            for _ in range(10):
                st.read_events()
            st.close()
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"并发读失败: {errors}"
    st = open_store(db)
    assert len(st.read_events()) == before, "并发读不得改变事件数"
    st.close()


def test_concurrent_write_then_replay_consistent(tmp_path):
    """并发写后重放仍一致（单写者队列不丢事件）。"""
    from reco_store import open_store

    db = tmp_path / "w.db"
    s = open_store(db)
    _seed(s, 1)
    s.close()

    def writer(idx: int):
        st = open_store(db)
        for i in range(5):
            st.append_event(
                "signal_recorded",
                payload={"kind": "dwell", "value": float(i), "source": "gui",
                         "session_id": f"s{idx}"},
                occurred_at=NOW, config_fingerprint="t/1", actor="user",
            )
        st.close()

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    st = open_store(db)
    assert st.verify_replay().ok, "并发写后重放必须一致"
    st.close()


# ─────────────────────── 边界条件 ───────────────────────

def test_empty_db_rank_and_profile_do_not_crash(tmp_path):
    """空库上跑推荐/画像/重放都不得崩。"""
    from reco_store.recommend import load_profile, rank_candidates

    s = _db(tmp_path, "empty.db")
    try:
        assert not rank_candidates(s)["rank_rows"]  # 空库无推荐（空值可能是 tuple 或 list）
        assert load_profile(s) is not None
        assert s.verify_replay().ok
    finally:
        s.close()


def test_chunk_text_edge_inputs():
    """chunk_text 对空串/超长无空行文本的处理。"""
    from reco_ui.reader import chunk_text

    assert chunk_text("") == []
    single = chunk_text("字" * 500_000)
    assert len(single) >= 1
    assert "".join(single) == "字" * 500_000, "分块必须可无损还原"


def test_reader_error_is_honest_on_garbage(tmp_path):
    """坏 EPUB 只报文件名+枚举，不泄漏内容。"""
    from reco_ui.reader import ReaderError, parse_epub_spine

    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip at all")
    with pytest.raises(ReaderError) as exc:
        parse_epub_spine(bad)
    msg = str(exc.value)
    assert "corrupt_container" in msg
    assert "bad.epub" in msg


# ─────────────────────── 推荐算法不变量 ───────────────────────

def test_recommendation_invariants(tmp_path):
    """推荐输出的三条不变量：排名连续、分数降序、liked 不泄漏。"""
    import struct

    from reco_store.recommend import rank_candidates

    s = _db(tmp_path, "inv.db")
    try:
        # candidate 书 + 向量
        for i in range(1, 4):
            _seed(s, 1, zone="candidate")
            conn = s.read_conn()
            try:
                bid = int(conn.execute(
                    "SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
            finally:
                conn.close()
            blob = struct.pack(">2i", 1, 2) + struct.pack(">2d", 0.6, 0.8)
            s.append_event("vector_computed",
                           payload={"vector_blob_hex": blob.hex(), "dim": 2,
                                    "idf_version": "t/1", "norm": 1.0},
                           book_id=bid, occurred_at=NOW, config_fingerprint="t/1")
        # liked 书（不应出现在推荐里）
        _seed(s, 1, zone="liked")

        out = rank_candidates(s)
        rows = out["rank_rows"]
        assert rows, "应有推荐结果"

        ranks = [int(r.rank) for r in rows]
        assert ranks == list(range(1, len(ranks) + 1)), "rank 必须连续"

        scores = [float(r.score) for r in rows]
        assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1)), "分数必须降序"

        conn = s.read_conn()
        try:
            liked = {int(r[0]) for r in conn.execute(
                "SELECT DISTINCT book_id FROM files WHERE zone='liked' AND present=1")}
        finally:
            conn.close()
        rec_ids = {int(str(r.book_id).lstrip("b")) for r in rows}
        assert not (rec_ids & liked), "liked 书不得出现在推荐里"
    finally:
        s.close()
