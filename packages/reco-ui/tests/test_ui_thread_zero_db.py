"""#47 回归：UI 线程零 DB——导入完成回调与「标为喜欢」发起路径。

背景：§11.1 规定 UI 线程零 DB。issue #47 点名两处：
- ZonePage._on_import_done（导入完成回调）曾调 zone_page_rows(self._store, ...)
- 「标为喜欢」发起路径曾在 UI 线程 read_conn() 查源路径
  （#80 之后该入口迁到阅读界面 ReaderPage，源查询在 ReaderLikeMoveWorker 内）
本测试用「记录库读发生线程」的探针，钉死这两处不再在 UI 线程读库。

范围说明：双击打开阅读页走用户即时手势（单行点查），不在 #47 范围，保持同步。
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

    s = open_store(tmp_path / "ui-thread.db")
    yield s
    s.close()


@pytest.fixture()
def db_probe(store, monkeypatch):
    """记录每次库读发生的线程名（UI 线程 = main_thread）。

    打桩点：pages.zone_page_rows（页面层唯一行查询入口）与 store.read_conn。
    """
    calls: list[str] = []
    import reco_ui.pages as pages

    real_rows = pages.zone_page_rows

    def traced_rows(store_, zone, **kw):
        calls.append("zone_rows:" + threading.current_thread().name)
        return real_rows(store_, zone, **kw)

    monkeypatch.setattr(pages, "zone_page_rows", traced_rows)
    real_read_conn = store.read_conn

    def traced_read_conn():
        calls.append("read_conn:" + threading.current_thread().name)
        return real_read_conn()

    monkeypatch.setattr(store, "read_conn", traced_read_conn)
    return calls


def _wait():
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()


def _ui_thread_reads(calls: list[str]) -> list[str]:
    """筛出发生在 UI 线程（main_thread）的库读。"""
    return [c for c in calls if c.endswith(":main_thread")]


def _seed_candidate(store, tmp_path):
    """candidate 目录真实文件 + file_added 落库。"""
    zone_dir = tmp_path / "data" / "candidate"
    zone_dir.mkdir(parents=True)
    dst = zone_dir / "novel-a.txt"
    dst.write_text("正文", encoding="utf-8")
    st = dst.stat()
    store.append_event(
        "file_added",
        payload={
            "path": str(dst),
            "zone": "candidate",
            "ext": ".txt",
            "size_bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "st_dev": 1,
            "st_ino": 7,
            "display_name": dst.name,
            "normalized_name": dst.name,
            "dedup_key": "k1",
        },
        occurred_at="2026-01-01T00:00:00+00:00",
        config_fingerprint="test/1",
        actor="user",
    )


def test_probe_detects_worker_thread_read(qapp, store, tmp_path, db_probe):
    """探针自检：走真实导入 worker，库读发生在 worker 线程（钉死测试有效）。"""
    from reco_ui.pages import ImportWorker

    src = tmp_path / "导入书.txt"
    src.write_text("正文", encoding="utf-8")
    worker = ImportWorker(store, "candidate", str(tmp_path / "data"), [str(src)])
    db_probe.clear()
    worker.run()
    _wait()

    worker_reads = [c for c in db_probe if not c.endswith(":main_thread")]
    assert worker_reads, "探针失效：未捕捉到 worker 线程库读"


def test_import_done_does_not_read_db_on_ui_thread(qapp, store, tmp_path, db_probe):
    """导入完成回调：行查询在 worker 线程，UI 只渲染。"""
    from reco_ui.pages import ZonePage

    page = ZonePage("candidate", store, data_root=str(tmp_path / "data"))
    page.load_now()
    db_probe.clear()

    page._on_import_done(1, 0, 0, [], "")
    _wait()

    assert _ui_thread_reads(db_probe) == [], (
        "导入完成回调在 UI 线程查了库：" + str(_ui_thread_reads(db_probe))
    )


def test_reader_like_move_does_not_read_db_on_ui_thread(qapp, store, tmp_path, db_probe):
    """#80：阅读页点「标为喜欢」→ 源查询与移动全在 worker 线程（UI 线程零 DB）。"""
    from reco_ui.reader_page import ReaderPage

    _seed_candidate(store, tmp_path)
    page = ReaderPage(store, data_root=str(tmp_path / "data"))
    conn = store.read_conn()
    try:
        book_id = int(conn.execute(
            "SELECT book_id FROM files WHERE present=1 LIMIT 1").fetchone()[0])
    finally:
        conn.close()
    page.open_book(str(tmp_path / "data" / "candidate" / "novel-a.txt"), book_id,
                   from_zone="candidate")
    db_probe.clear()

    page.like_button.click()
    _wait()

    assert _ui_thread_reads(db_probe) == [], (
        "发起移动在 UI 线程查了库：" + str(_ui_thread_reads(db_probe))
    )
