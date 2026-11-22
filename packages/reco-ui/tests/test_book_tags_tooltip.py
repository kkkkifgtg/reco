"""悬停显示标签：鼠标停在书上数毫秒即显示该书标签。

需求（用户）：标签需单独显示，悬停某本书时触发显示。
实现：表格模型提供 Qt.ToolTipRole，悬停即由 Qt 原生机制弹出，无需额外定时器。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-22T08:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "tags.db")
    yield s
    s.close()


def _落书与标签(store, tmp_path, name="带标签书.txt", labels=("催眠", "精神控制")):
    """落一本书并挂两个标签（label_set 事件投影到 book_tags）。"""
    from reco_store import dedup_key

    f = tmp_path / name
    f.write_text("正文。", encoding="utf-8")
    st = f.stat()
    store.append_event(
        "file_added",
        payload={
            "path": str(f), "zone": "liked", "ext": ".txt",
            "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
            "st_dev": 1, "st_ino": 1, "display_name": name,
            "normalized_name": name, "dedup_key": dedup_key(st.st_size, name),
        },
        occurred_at=NOW, config_fingerprint="t/1", actor="user",
    )
    conn = store.read_conn()
    try:
        book_id = int(conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
    finally:
        conn.close()

    for i, label in enumerate(labels):
        tag_id = f"t{i}"
        store.append_event(
            "tag_added",
            payload={"tag_id": tag_id, "axis": "mech", "label": label},
            occurred_at=NOW, config_fingerprint="t/1",
        )
        store.append_event(
            "label_set",
            payload={"tag_id": tag_id, "source": "manual", "confidence": 1.0,
                     "basis_json": "{}"},
            book_id=book_id, occurred_at=NOW, config_fingerprint="t/1", actor="user",
        )
    return book_id


def test_book_tags_reads_labels(store, tmp_path):
    """纯函数：读某本书的标签显示名列表。"""
    from reco_ui.pages import book_tags_display

    book_id = _落书与标签(store, tmp_path)
    labels = book_tags_display(store, book_id)
    assert "催眠" in labels
    assert "精神控制" in labels


def test_book_tags_empty_when_no_tag(store, tmp_path):
    """无标签的书：返回空列表（不伪造）。"""
    from reco_ui.pages import book_tags_display

    book_id = _落书与标签(store, tmp_path, name="无标签.txt", labels=())
    assert book_tags_display(store, book_id) == []


def test_zone_tooltip_shows_tags(qapp, store, tmp_path):
    """专区表格：悬停行返回含标签的 tooltip 文本。"""
    from reco_ui.pages import ZonePage, zone_page_rows
    from reco_ui.qt import Qt

    book_id = _落书与标签(store, tmp_path)
    page = ZonePage("liked", store)
    page.apply_rows(zone_page_rows(store, "liked"))
    assert page.row_count() == 1

    tip = page._model.data(page._model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
    assert tip, "悬停必须有 tooltip"
    assert "催眠" in tip and "精神控制" in tip


def test_zone_tooltip_without_tags_is_honest(qapp, store, tmp_path):
    """无标签时：tooltip 说明「暂无标签」，不假装有。"""
    from reco_ui.pages import ZonePage, zone_page_rows
    from reco_ui.qt import Qt

    _落书与标签(store, tmp_path, name="空标签书.txt", labels=())
    page = ZonePage("liked", store)
    page.apply_rows(zone_page_rows(store, "liked"))
    tip = page._model.data(page._model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
    assert tip is None or "暂无" in tip
