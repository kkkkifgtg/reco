"""回归：推荐专区表格双击 → 打开阅读页。

用户实测 bug：在推荐列表里双击某一项，既不打开阅读、也没加入任何专区。
根因：doubleClicked 只接在 ZonePage（liked/candidate），RecommendPage 的 table 未接线。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-22T07:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "rec-dbl.db")
    yield s
    s.close()


def _seed_recommend_book(store, tmp_path, name="推荐书.txt"):
    """落一本「推荐专区」目录下的书（rank 行来自推荐专区来源）。"""
    from reco_store import dedup_key

    zone_dir = tmp_path / "data" / "推荐专区"
    zone_dir.mkdir(parents=True, exist_ok=True)
    f = zone_dir / name
    f.write_text("正文内容。", encoding="utf-8")
    st = f.stat()
    store.append_event(
        "file_added",
        payload={
            "path": str(f), "zone": "推荐专区", "ext": ".txt",
            "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
            "st_dev": 1, "st_ino": 1, "display_name": name,
            "normalized_name": name, "dedup_key": dedup_key(st.st_size, name),
        },
        occurred_at=NOW, config_fingerprint="t/1", actor="user",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
    finally:
        conn.close()


def _feed_rank_row(page, book_id: int, name: str):
    """给推荐页喂一行 rank 数据（避开真实向量依赖，聚焦双击接线）。"""
    from reco_core.score import RankRow, Reason

    row = RankRow(book_id=f"b{book_id}", score=0.9, rank=1,
                  reasons=(Reason(axis="tag", contribution=0.5, source="similarity"),),
                  is_exploration=False)
    page.apply_rank({"rank_rows": [row], "names": {f"b{book_id}": name}})


def test_recommend_double_click_emits_reader_requested(qapp, store, tmp_path):
    """双击推荐行 → 打开阅读页（reader_requested 信号）。"""
    from reco_ui.app import RecommendPage
    from reco_ui.qt import QThreadPool

    book_id = _seed_recommend_book(store, tmp_path)
    page = RecommendPage(store, data_root=str(tmp_path / "data"))
    _feed_rank_row(page, book_id, "推荐书.txt")
    assert page.row_count() == 1

    received: list = []
    page.reader_requested.connect(lambda p, b: received.append((p, b)))
    page.table.doubleClicked.emit(page._model.index(0, 0))
    QThreadPool.globalInstance().waitForDone(8000)
    qapp.processEvents()

    assert len(received) == 1, "推荐页双击必须触发阅读请求"
    path, bid = received[0]
    assert path.endswith("推荐书.txt")
    assert int(bid) == book_id


def test_recommend_double_click_missing_source_is_honest(qapp, store, tmp_path):
    """源文件已不在磁盘：不打开阅读页，状态条诚实提示。"""
    from reco_ui.app import RecommendPage
    from reco_ui.qt import QThreadPool

    book_id = _seed_recommend_book(store, tmp_path, name="已删推荐书.txt")
    (tmp_path / "data" / "推荐专区" / "已删推荐书.txt").unlink()
    page = RecommendPage(store, data_root=str(tmp_path / "data"))
    _feed_rank_row(page, book_id, "已删推荐书.txt")

    received: list = []
    page.reader_requested.connect(lambda p, b: received.append((p, b)))
    page.table.doubleClicked.emit(page._model.index(0, 0))
    QThreadPool.globalInstance().waitForDone(8000)
    qapp.processEvents()

    assert received == []
    assert "无法打开" in page._status.text() or "缺失" in page._status.text()
