"""工单 12 切片 2：GUI 反馈 glue（不合口味/标为喜欢/加入候选池）+ RankRow 表格模型。

- 接缝：所有写经 reco_store.Store.append_event（单写者队列），fingerprint=gui/*/1；
- C12：glue 只写 rec_feedback/pool_added/mute_upserted，绝不触碰 ui_usage；
- A6：表格模型只渲染 RankRow 序列，零排序逻辑；
- B4：理由渲染只含标签 id/统计量（axis+contribution+source），无正文。
红相（TDD）：reco_ui.glue / reco_ui.tables / RecommendPage 先红后绿。
"""
from __future__ import annotations

import json as _json
import os

os_environ_set = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from reco_core.score import RankRow, Reason


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "gui.db")
    yield s
    s.close()


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


NOW = "2026-11-21T04:00:00+00:00"


def test_seed_books_three_zones(store):
    """seed_books 落三专区 books/files（file_added），不写 ui_usage（C12 基线）。"""
    from reco_ui.glue import seed_books

    names = seed_books(store, books=6, liked=3, candidate=2, now=NOW)
    assert len(names) == 6
    conn = store.read_conn()
    try:
        zones = dict(conn.execute("SELECT zone, COUNT(*) FROM files WHERE present=1 GROUP BY zone").fetchall())
        usage = conn.execute("SELECT COUNT(*) FROM ui_usage").fetchone()[0]
        evs = [r[0] for r in conn.execute("SELECT event_type FROM events").fetchall()]
    finally:
        conn.close()
    assert zones == {"liked": 3, "candidate": 2, "推荐专区": 1}
    assert usage == 0
    assert set(evs) == {"file_added"}


def test_dislike_persists_rec_feedback_and_mute_not_ui_usage(store):
    """不合口味 → rec_feedback(kind=dislike) + mutes 激活行；ui_usage 计数不动（C12）。"""
    from reco_ui.glue import record_feedback, seed_books

    seed_books(store, books=4, liked=2, candidate=1, now=NOW)
    book_id = 4  # 推荐专区样例
    eid = record_feedback(store, "dislike", book_id, axis="tag", now=NOW)
    assert isinstance(eid, int)
    conn = store.read_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM events WHERE event_type='rec_feedback' AND book_id=?", (book_id,)
        ).fetchone()
        mute = conn.execute("SELECT kind FROM mutes WHERE book_id=?", (book_id,)).fetchone()
        usage = conn.execute("SELECT COUNT(*) FROM ui_usage").fetchone()[0]
    finally:
        conn.close()
    payload = _json.loads(row[0])
    assert payload["kind"] == "dislike" and payload["axis"] == "tag"
    assert mute == ("dislike",)
    assert usage == 0


def test_add_to_pool_persists_pool_added_not_taste_stream(store):
    """加入候选池 → pool_added（交互类，不进口味事件流的画像折叠）。"""
    from reco_ui.glue import record_feedback, seed_books

    seed_books(store, books=3, liked=1, candidate=1, now=NOW)
    book_id = 3
    record_feedback(store, "add_to_pool", book_id, now=NOW)
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT event_type FROM events WHERE book_id=?", (book_id,)
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["pool_added"]


def test_like_persists_rec_feedback_kind_like(store):
    """标为喜欢 → rec_feedback(kind=like)（回流正样本）。"""
    from reco_ui.glue import record_feedback, seed_books

    seed_books(store, books=3, liked=1, candidate=1, now=NOW)
    book_id = 3
    record_feedback(store, "like", book_id, axis="tag", now=NOW)
    conn = store.read_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM events WHERE event_type='rec_feedback' AND book_id=?", (book_id,)
        ).fetchone()
    finally:
        conn.close()
    payload = _json.loads(row[0])
    assert payload["kind"] == "like" and payload["axis"] == "tag"


def test_record_feedback_rejects_unknown_kind(store):
    from reco_ui.glue import record_feedback

    with pytest.raises(ValueError):
        record_feedback(store, "meh", 1, now=NOW)


GOLD_ROWS = [
    RankRow(book_id="2", score=0.9, rank=1,
            reasons=(Reason(axis="tag", contribution=0.5, source="similarity"),), is_exploration=False),
    RankRow(book_id="1", score=0.4, rank=2,
            reasons=(Reason(axis="relation", contribution=0.2, source="model"),), is_exploration=True),
]
GOLD_NAMES = {"1": "book-a.txt", "2": "book-b.txt"}


def test_rank_table_model_order_golden_and_no_prose(qapp):
    """A6/B4：模型保持 RankRow 输入顺序零重排；理由=axis+contribution 黄金渲染。"""
    from reco_ui.tables import EXPLORATION_MARK, RankTableModel

    model = RankTableModel(GOLD_ROWS, GOLD_NAMES)
    assert model.rowCount() == 2
    assert model.columnCount() == 5
    # 零重排：第一行 = 输入第一行
    assert model.index(0, 0).data() == "1"
    assert model.index(0, 1).data() == "book-b.txt"
    assert model.index(1, 4).data() == "relation +0.200 (model)"
    # 探索标记列
    assert model.index(0, 3).data() == ""
    assert model.index(1, 3).data() == EXPLORATION_MARK
    # B4：理由列无正文——只有 axis/数字/来源机制
    assert "正文" not in model.index(0, 4).data()


def test_rank_table_model_reset_is_batched(qapp):
    """model/view 批更新：set_rows 用 beginResetModel/endResetModel 包住。"""
    from reco_ui.tables import RankTableModel

    model = RankTableModel([], {})
    model.set_rows(GOLD_ROWS, GOLD_NAMES)
    assert model.rowCount() == 2
    assert model._reset_calls == 1  # 一次整批重置，非逐行 insert


def test_recommend_page_buttons_wire_glue(qapp, store):
    """推荐页三按钮真实接线：点击即经 glue 落事件（UI 线程只发起+渲染）。"""
    from reco_ui.app import RecommendPage
    from reco_ui.glue import seed_books

    seed_books(store, books=3, liked=1, candidate=1, now=NOW)
    page = RecommendPage(store)
    page.set_rows(GOLD_ROWS, GOLD_NAMES)
    page.table.selectRow(0)  # 选中 book 2
    page.btn_dislike.click()  # 写库在池线程：UI 线程零阻塞（§11.1）
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(3000)
    QApplication.processEvents()
    assert page.btn_dislike.isEnabled()  # 完成后按钮恢复（连点防护解除）
    assert page._status.text() == "已记录"
    conn = store.read_conn()
    try:
        kinds = [r[0] for r in conn.execute(
            "SELECT payload_json FROM events WHERE event_type='rec_feedback'").fetchall()]
        usage = conn.execute("SELECT COUNT(*) FROM ui_usage").fetchone()[0]
    finally:
        conn.close()
    assert any(_json.loads(k).get("kind") == "dislike" for k in kinds)
    assert usage == 0
