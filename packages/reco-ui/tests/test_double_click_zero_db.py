"""#58 回归：双击打开阅读页不在 UI 线程查库（§11.1）。

原实现：_on_double_clicked 在 UI 线程调 zone_page_rows + _present_path（read_conn）。
修复后：行与源路径查询在池线程 worker 内完成，结果回 UI 线程决定是否发 reader_requested。
"""
from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "dbl.db")
    yield s
    s.close()


def _seed(store, path, zone="liked") -> int:
    from reco_store import dedup_key

    st = path.stat()
    store.append_event(
        "file_added",
        payload={
            "path": str(path), "zone": zone, "ext": path.suffix.lower(),
            "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
            "st_dev": 1, "st_ino": 1, "display_name": path.name,
            "normalized_name": path.name, "dedup_key": dedup_key(st.st_size, path.name),
        },
        occurred_at="2026-01-01T00:00:00+00:00", config_fingerprint="t/1", actor="user",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
    finally:
        conn.close()


def _wait():
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()


def test_double_click_does_not_read_db_on_ui_thread(qapp, store, tmp_path):
    from reco_ui.pages import ZonePage, zone_page_rows

    calls: list[str] = []
    import reco_ui.pages as pages

    real_rows = pages.zone_page_rows
    real_read_conn = store.read_conn

    def traced_rows(s, zone, **kw):
        calls.append("rows:" + threading.current_thread().name)
        return real_rows(s, zone, **kw)

    def traced_conn():
        calls.append("conn:" + threading.current_thread().name)
        return real_read_conn()

    pages.zone_page_rows = traced_rows
    store.read_conn = traced_conn
    try:
        book = tmp_path / "双击书.txt"
        book.write_text("正文", encoding="utf-8")
        book_id = _seed(store, book)
        page = ZonePage("liked", store)
        page.apply_rows(real_rows(store, "liked"))
        received: list[tuple] = []
        page.reader_requested.connect(lambda p, b: received.append((p, b)))
        calls.clear()

        page._table.doubleClicked.emit(page._model.index(0, 0))
        _wait()
    finally:
        pages.zone_page_rows = real_rows
        store.read_conn = real_read_conn

    ui_calls = [c for c in calls if c.endswith(":MainThread")]
    assert ui_calls == [], "双击在 UI 线程查了库：" + str(ui_calls)
    assert received == [(str(book), book_id)], "worker 完成后仍须正常打开阅读页"


def test_double_click_missing_file_reports_after_worker(qapp, store, tmp_path):
    from reco_ui.pages import ZonePage, zone_page_rows

    book = tmp_path / "已删书.txt"
    book.write_text("正文", encoding="utf-8")
    _seed(store, book, zone="candidate")
    book.unlink()
    page = ZonePage("candidate", store)
    page.apply_rows(zone_page_rows(store, "candidate"))
    received: list[tuple] = []
    page.reader_requested.connect(lambda p, b: received.append((p, b)))

    page._table.doubleClicked.emit(page._model.index(0, 0))
    _wait()

    assert received == []
    assert "文件缺失" in page._status.text()
