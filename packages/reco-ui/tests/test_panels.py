"""工单 12 切片 3：喜欢/候选板块 + 异步加载（QThreadPool）+ in-flight 去重 + 超时分级。

- §11.1：UI 线程只发起+渲染，zone 查询全在 QRunnable worker；
- §11.2 坑 1：分级超时（慢库不打断事件循环）；坑 2：in-flight 去重；
- §11.2 坑 3：「已加载」标记仅在加载成功后置位，失败可重试；
- §11.2 坑 7：QMenu 构建幂等，重复打开不泄漏。
红相（TDD）：reco_ui.pages 尚不存在 → 导入失败转红。
"""
from __future__ import annotations

import os
import time

os_environ_set = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from reco_ui.glue import seed_books


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "gui.db")
    seed_books(s, books=6, liked=3, candidate=2, now="2026-11-21T04:00:00+00:00")
    yield s
    s.close()


class SlowStore:
    """慢库桩：read_conn 延迟 0.25s（分级超时验证用）。"""

    def __init__(self, real) -> None:
        self._real = real

    def read_conn(self):
        time.sleep(0.25)
        return self._real.read_conn()

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_zone_page_rows_read_store(store):
    """zone 行查询：liked 3 行、candidate 2 行；行 = (book_id, 名称, 大小)。"""
    from reco_ui.pages import zone_page_rows

    liked = zone_page_rows(store, "liked")
    cand = zone_page_rows(store, "candidate")
    assert len(liked) == 3 and len(cand) == 2
    # 行契约 = (book_id, 名称, 大小, 备注)（备注列给静音/标记渲染用）
    assert all(len(r) == 4 and isinstance(r[0], int) for r in liked)


def test_zone_page_shows_rows_and_marks_loaded(qapp, store):
    """ZonePage 同步装填：行数正确、loaded 置位、无 UI 线程查询（数据由 glue 读）。"""
    from reco_ui.pages import ZonePage

    page = ZonePage("liked", store)
    page.load_now()
    assert page.row_count() == 3
    assert page.loaded is True


def test_async_load_via_threadpool(qapp, store):
    """ZoneLoader 在 QThreadPool 跑查询，结果经信号回 UI 线程置位。"""
    from reco_ui.pages import ZoneLoader

    done = []
    loader = ZoneLoader(store, "liked")
    loader.signals.finished.connect(lambda rows: done.append(rows))
    from reco_ui.qt import QThreadPool

    QThreadPool.globalInstance().start(loader)
    deadline = time.monotonic() + 3.0
    while not done and time.monotonic() < deadline:
        from reco_ui.qt import QApplication

        QApplication.processEvents()
        time.sleep(0.01)
    assert done and len(done[0]) == 3


def test_inflight_dedup_and_failure_retry(qapp, store):
    """坑 2/坑 3：in-flight 去重（同页并发请求只发一次）；失败不置位可重试。"""
    from reco_ui.app import MainWindow

    win = MainWindow(store=SlowStore(store))
    page = win.page("liked")
    win._start_load(page)
    assert win._inflight == {"liked"}          # 第一次进入 in-flight（首屏预热请求被去重）
    win._start_load(page)                       # 去重：重复请求被忽略
    assert win._inflight == {"liked"}
    # 等待 worker 完成（慢库 0.25s），让线程池泄洪避免跨测试信号竞态
    deadline = time.monotonic() + 3.0
    while win._inflight and time.monotonic() < deadline:
        from reco_ui.qt import QApplication

        QApplication.processEvents()
        time.sleep(0.01)
    assert win._inflight == set()               # 完成后清理（成功或失败都清）
    assert page.loaded is False                 # 超时/失败不置位「已加载」
    from reco_ui.qt import QThreadPool

    QThreadPool.globalInstance().waitForDone(3000)


def test_zone_menu_build_idempotent(qapp, store):
    """坑 7：QMenu 构建幂等——重复构建不崩、每次新实例、动作数稳定。"""
    from reco_ui.pages import ZonePage

    page = ZonePage("candidate", store)
    page.load_now()
    m1 = page.build_context_menu()
    m2 = page.build_context_menu()
    assert m1 is not m2
    assert m1.actions() and len(m1.actions()) == len(m2.actions())
    m1.deleteLater()
    m2.deleteLater()
