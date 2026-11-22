"""RR1/t5 红测试：双击接线 + 主窗切页/返回 + 样式（工单 #38 / spec #33）。

契约：
- ZonePage 双击行 → 查 rows/源路径 → reader_requested(path, book_id)；
  无源文件行 → 状态行诚实提示（不猜名字映射）。
- MainWindow 建 ReaderPage 加入 _stack（不进 PAGE_ORDER/nav）；_open_reader 记录旧页 →
  切阅读页 + 标题 + ui_usage bump("reader")；返回 flush 进度并切回原页；closeEvent flush。
- 文件被删除后打开 → 阅读页状态行只报文件名 + 枚举（not_found），正文不上屏。
- theme.py 集中 QSS 追加 QPlainTextEdit/QTextBrowser 规则；reader.py/reader_page.py 无 setStyleSheet。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

CANARY = "金丝雀绝密正文"
NOW = "2026-11-22T03:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "wiring.db")
    yield s
    s.close()


def _seed_file(store, path: Path, *, zone: str = "liked", name: str | None = None, ino: int = 1) -> int:
    """落 file_added（真实 tmp 路径），返回 book_id。"""
    from reco_store import dedup_key

    display = name or path.name
    size = path.stat().st_size if path.exists() else 10
    key = dedup_key(size, display)
    store.append_event(
        "file_added",
        payload={
            "path": str(path),
            "zone": zone,
            "ext": path.suffix.lower(),
            "size_bytes": size,
            "mtime_ns": 1000 + ino,
            "st_dev": 1,
            "st_ino": ino,
            "display_name": display,
            "normalized_name": display,
            "dedup_key": key,
            "mount_hint": "C:",
        },
        occurred_at=NOW,
        config_fingerprint="t5/seed/1",
        actor="system",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM books WHERE dedup_key = ?", (key,)).fetchone()[0])
    finally:
        conn.close()


def _pump_until(predicate, timeout: float = 15.0) -> bool:
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(15000)
    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return predicate()


def _dispose(qapp, win) -> None:
    """显式销毁窗口：PyQt GC 若在后续测试事件循环里删 C++ 对象会硬崩（0xC0000409）。"""
    from reco_ui.qt import QThreadPool

    win.close()
    QThreadPool.globalInstance().waitForDone(15000)
    qapp.processEvents()
    win.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------- ZonePage 双击


def test_zone_double_click_emits_reader_requested(qapp, store, tmp_path):
    from reco_ui.pages import ZonePage, zone_page_rows

    book = tmp_path / "双击书.txt"
    book.write_text("第一章" + "\n\n" + "正文段落。" * 20, encoding="utf-8")
    book_id = _seed_file(store, book)
    page = ZonePage("liked", store)
    page.apply_rows(zone_page_rows(store, "liked"))
    assert page._model.rowCount() == 1

    received: list[tuple] = []
    page.reader_requested.connect(lambda path, bid: received.append((path, bid)))
    page._table.doubleClicked.emit(page._model.index(0, 0))
    qapp.processEvents()

    assert received == [(str(book), book_id)]
    page.deleteLater()
    qapp.processEvents()


def test_zone_double_click_without_source_reports_status(qapp, store, tmp_path):
    from reco_ui.pages import ZonePage, zone_page_rows

    book = tmp_path / "已删除书.txt"
    book.write_text("正文", encoding="utf-8")
    _seed_file(store, book, zone="candidate")
    book.unlink()  # 库里有行、磁盘上已缺失（用户移动/删除后的实况）
    page = ZonePage("candidate", store)
    page.apply_rows(zone_page_rows(store, "candidate"))
    assert page._model.rowCount() == 1
    received: list[tuple] = []
    page.reader_requested.connect(lambda path, bid: received.append((path, bid)))

    page._table.doubleClicked.emit(page._model.index(0, 0))
    qapp.processEvents()

    assert received == []
    assert "文件缺失" in page._status.text()
    assert "已删除书.txt" in page._status.text()
    page.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------- 阅读页诚实失败


def test_open_deleted_file_status_is_honest(qapp, store, tmp_path):
    from reco_ui.reader_page import ReaderPage

    ghost = tmp_path / "ghost.txt"
    book_id = _seed_file(store, ghost)
    page = ReaderPage(store)
    page.open_book(ghost, book_id)
    assert _pump_until(lambda: page.status_text.startswith("打开失败")), page.status_text

    assert "ghost.txt" in page.status_text
    assert "not_found" in page.status_text
    assert CANARY not in page.status_text
    page.close_book()
    page.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------- MainWindow 切页/返回


def test_main_window_open_reader_switches_stack_and_bumps_usage(qapp, store, tmp_path):
    from reco_ui.app import MainWindow

    win = MainWindow(store=store)
    book = tmp_path / "打开书.txt"
    book.write_text("第一段\n\n第二段", encoding="utf-8")
    book_id = _seed_file(store, book)

    win._open_reader(str(book), book_id)
    qapp.processEvents()

    assert win.reader_page is not None
    assert win._stack.currentWidget() is win.reader_page
    assert win.page_ids() == ["liked", "candidate", "recommend", "profile"]
    assert "打开书.txt" in win.windowTitle()
    assert "reader" not in win.page_ids()  # 阅读页不进入导航
    usage = store.get_ui_usage()
    assert usage["reader"]["uses"] == 1

    win._open_reader(str(book), book_id)  # 同一实例换书/重开仍只 bump
    assert store.get_ui_usage()["reader"]["uses"] == 2
    _dispose(qapp, win)


def test_close_reader_returns_to_previous_page_and_flushes(qapp, store, tmp_path):
    from reco_ui.app import MainWindow

    win = MainWindow(store=store)
    win._on_nav(1)  # 旧页 = candidate
    original = win._stack.currentWidget()
    book = tmp_path / "返回书.txt"
    book.write_text("正文", encoding="utf-8")
    book_id = _seed_file(store, book, zone="candidate")

    win._open_reader(str(book), book_id)
    flushed: list[int] = []
    win.reader_page.flush_progress = lambda: flushed.append(1)
    win.reader_page.back_requested.emit()
    qapp.processEvents()

    assert win._stack.currentWidget() is original
    assert flushed, "返回必须先 flush 进度"
    assert win.windowTitle() == "reco —— 本地小说推荐"
    _dispose(qapp, win)


def test_back_button_flushes_real_progress(qapp, store, tmp_path):
    from reco_ui.app import MainWindow
    from reco_ui.qt import QTextCursor

    win = MainWindow(store=store)
    text = "第一段" + "\n\n" + "第二段" * 100
    book = tmp_path / "进度书.txt"
    book.write_text(text, encoding="utf-8")
    book_id = _seed_file(store, book)

    win._open_reader(str(book), book_id)
    page = win.reader_page
    assert _pump_until(lambda: page.loaded), page.status_text
    page.viewer.moveCursor(QTextCursor.MoveOperation.End)

    page._back.click()
    qapp.processEvents()

    progress = store.get_reading_progress(book_id)
    assert progress is not None
    assert int(progress["char_offset"]) >= 1
    _dispose(qapp, win)


def test_close_event_flushes_reader_progress(qapp, store, tmp_path):
    from reco_ui.app import MainWindow

    win = MainWindow(store=store)
    book = tmp_path / "关窗书.txt"
    book.write_text("正文", encoding="utf-8")
    book_id = _seed_file(store, book)
    win._open_reader(str(book), book_id)
    flushed: list[int] = []
    win.reader_page.flush_progress = lambda: flushed.append(1)

    _dispose(qapp, win)
    qapp.processEvents()

    assert flushed, "closeEvent 必须 flush 阅读进度"


def test_zone_reader_requested_wired_to_main_window(qapp, store, tmp_path):
    from reco_ui.app import MainWindow
    from reco_ui.pages import zone_page_rows

    win = MainWindow(store=store)
    book = tmp_path / "信号书.txt"
    book.write_text("正文", encoding="utf-8")
    book_id = _seed_file(store, book)
    zone_page = win.page("liked")
    zone_page.apply_rows(zone_page_rows(store, "liked"))

    zone_page.reader_requested.emit(str(book), book_id)
    qapp.processEvents()

    assert win._stack.currentWidget() is win.reader_page
    _dispose(qapp, win)


# ---------------------------------------------------------- 样式


def test_reader_styles_live_in_central_qss():
    from reco_ui.theme import DARK_QSS

    assert "QPlainTextEdit" in DARK_QSS
    assert "QTextBrowser" in DARK_QSS
    src = Path(__file__).resolve().parents[1] / "src" / "reco_ui"
    for name in ("reader.py", "reader_page.py"):
        assert "setStyleSheet" not in (src / name).read_text(encoding="utf-8"), name
