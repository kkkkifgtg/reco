"""#72 回归：推荐页三个反馈按钮必须能用（book_id 的 b 前缀需剥离）。

修复前：_feedback 直接 int("b203") → ValueError，点击即崩。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-22T11:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store
    s = open_store(tmp_path / "fb.db")
    yield s
    s.close()


def _seed(store, n=2):
    """落 n 本 candidate 书 + 向量，让 rank 有输出。"""
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
        blob = struct.pack(">2i", 1, 2) + struct.pack(">2d", 0.6, 0.8)
        store.append_event(
            "vector_computed",
            payload={"vector_blob_hex": blob.hex(), "dim": 2, "idf_version": "t/1", "norm": 1.0},
            book_id=bid, occurred_at=NOW, config_fingerprint="t/1",
        )


def _count(store, kind):
    conn = store.read_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM events WHERE event_type=?", (kind,)).fetchone()[0]
    finally:
        conn.close()


def _ready_page(store, qapp):
    from reco_ui.app import RecommendPage
    page = RecommendPage(store, data_root="F:/data")
    from reco_store.recommend import rank_candidates
    page.apply_rank(rank_candidates(store))
    return page


def test_dislike_button_writes_rec_feedback(store, qapp):
    """点「不合口味」→ 产生 rec_feedback 事件（不崩溃）。"""
    from reco_ui.qt import QApplication, QThreadPool

    _seed(store)
    page = _ready_page(store, qapp)
    assert page.row_count() > 0, "前置条件：应有推荐行"
    page.table.selectRow(0)

    before = _count(store, "rec_feedback")
    page.btn_dislike.click()
    QThreadPool.globalInstance().waitForDone(15000)
    QApplication.processEvents()

    assert _count(store, "rec_feedback") > before, "点不合口味后应有 rec_feedback 事件"


def test_pool_button_writes_pool_added(store, qapp):
    """点「加入候选池」→ 产生 pool_added 事件（不崩溃）。"""
    from reco_ui.qt import QApplication, QThreadPool

    _seed(store)
    page = _ready_page(store, qapp)
    page.table.selectRow(0)

    before = _count(store, "pool_added")
    page.btn_pool.click()
    QThreadPool.globalInstance().waitForDone(15000)
    QApplication.processEvents()

    assert _count(store, "pool_added") > before, "点加入候选池后应有 pool_added 事件"


def test_bad_book_id_reports_instead_of_crashing(store, qapp):
    """书号无法解析时给状态提示，不抛异常。"""
    from reco_core.score import RankRow, Reason
    from reco_ui.app import RecommendPage

    page = RecommendPage(store, data_root="F:/data")
    bad = RankRow(book_id="不是书号", score=0.5, rank=1,
                  reasons=(Reason(axis="tag", contribution=0.5, source="similarity"),),
                  is_exploration=False)
    page.apply_rank({"rank_rows": [bad], "names": {}})
    page.table.selectRow(0)

    page.btn_dislike.click()  # 不应抛出
    assert "无法解析" in page._status.text() or "失败" in page._status.text()
