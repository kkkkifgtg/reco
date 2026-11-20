"""W12b 红测试（三）：Preloader 集成——空闲档/成功才置位/可打断/perf span。

Qt 信号跨线程投递在测试里经 waitForDone + processEvents 收敛；窗口契约用
FakeWindow（page/preload_jobs/store 三方法，与 MainWindow 接线形状一致）。
"""
from __future__ import annotations

import contextlib
import io
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakePage:
    def __init__(self, page_id: str, loaded: bool = False):
        self.page_id = page_id
        self.loaded = loaded
        self.mark_calls = 0

    def mark_loaded(self) -> None:
        self.mark_calls += 1
        self.loaded = True


class FakeWindow:
    """MainWindow 预热契约的最小形状：page(pid) / preload_jobs() / store()。"""

    def __init__(self, store, jobs, pages):
        self._store = store
        self._jobs = jobs
        self._pages = pages

    def page(self, page_id):
        return self._pages[page_id]

    def preload_jobs(self):
        return self._jobs

    def store(self):
        return self._store


def _drain(qapp):
    from reco_ui.qt import QThreadPool

    QThreadPool.globalInstance().waitForDone(3000)
    qapp.processEvents()


def test_preloader_success_marks_loaded_failure_does_not(qapp, tmp_path):
    from reco_store import open_store
    from reco_ui.preload import Preloader

    s = open_store(tmp_path / "p1.db")
    s.bump_ui_usage("liked", now="2026-11-21T06:00:00")      # Top-K=1 补位
    s.bump_ui_usage("candidate", now="2026-11-21T06:00:00")  # Top-K=2 补位
    pages = {
        "liked": FakePage("liked"),
        "candidate": FakePage("candidate"),
        "recommend": FakePage("recommend", loaded=True),  # 已加载 → 不重复
        "profile": FakePage("profile"),  # 无 usage 行 → 不在热集（冷区）
    }
    calls = {"liked": 0, "candidate": 0, "profile": 0}

    def _mk(pid, ok):
        def job():
            calls[pid] += 1
            return ok
        return job

    win = FakeWindow(s, {
        "liked": _mk("liked", True),
        "candidate": _mk("candidate", False),  # 预热失败（坑 3：不置位）
        "profile": _mk("profile", True),
    }, pages)
    pre = Preloader(win)
    pre._warm_hot_set()  # 直接触发（跳过空闲延时）
    _drain(qapp)

    assert pages["liked"].loaded is True
    assert pages["candidate"].loaded is False  # 失败不置位，按需路径可重试
    assert pages["profile"].loaded is False    # 冷区（无 usage 行）未被预热
    assert pages["recommend"].mark_calls == 0  # 已加载页零扰动
    assert calls == {"liked": 1, "candidate": 1, "profile": 0}
    s.close()


def test_preloader_failure_page_still_on_demand_capable(qapp, tmp_path):
    """验收 5（逆向基相）：预热失败 → 未置位 + 按需加载照常成功。"""
    from reco_store import open_store
    from reco_ui.preload import Preloader

    s = open_store(tmp_path / "p2.db")
    s.bump_ui_usage("profile", now="2026-11-21T06:00:00")
    page = FakePage("profile")
    win = FakeWindow(s, {"profile": lambda: False}, {
        "profile": page,
        "recommend": FakePage("recommend", loaded=True),  # 恒热页在 fixtures 里就绪
    })
    pre = Preloader(win)
    pre._warm_hot_set()
    _drain(qapp)
    assert page.loaded is False
    # 按需路径（冷区兜底）：同步成功置位
    page.mark_loaded()
    assert page.loaded is True
    s.close()


def test_user_interrupt_skips_queued_jobs(qapp, tmp_path):
    from reco_store import open_store
    from reco_ui.preload import Preloader

    s = open_store(tmp_path / "p3.db")
    s.bump_ui_usage("liked", now="2026-11-21T06:00:00")
    page = FakePage("liked")
    ran = {"n": 0}

    def job():
        ran["n"] += 1
        return True

    win = FakeWindow(s, {"liked": job}, {"liked": page})
    pre = Preloader(win)
    pre.interrupt()  # 用户操作先到
    pre._warm_hot_set()
    _drain(qapp)
    assert ran["n"] == 0        # 排队任务全部跳过
    assert page.loaded is False  # 不置位（用户交互路径接管）
    s.close()


def test_already_loaded_pages_not_rewarmed(qapp, tmp_path):
    from reco_store import open_store
    from reco_ui.preload import Preloader

    s = open_store(tmp_path / "p4.db")
    s.bump_ui_usage("liked", now="2026-11-21T06:00:00")
    page = FakePage("liked", loaded=True)
    ran = {"n": 0}

    def job():
        ran["n"] += 1
        return True

    win = FakeWindow(s, {"liked": job}, {
        "liked": page,
        "recommend": FakePage("recommend", loaded=True),
    })
    pre = Preloader(win)
    pre._warm_hot_set()
    _drain(qapp)
    assert ran["n"] == 0  # 已加载 → 零重复预热
    s.close()


def test_perf_preload_span_emitted(qapp, tmp_path):
    from reco_io import perf as perf_mod
    from reco_store import open_store
    from reco_ui.preload import Preloader

    s = open_store(tmp_path / "p5.db")
    s.bump_ui_usage("liked", now="2026-11-21T06:00:00")
    win = FakeWindow(s, {"liked": lambda: True}, {
        "liked": FakePage("liked"),
        "recommend": FakePage("recommend", loaded=True),
    })
    pre = Preloader(win)
    perf_mod.perf_enabled = True
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            pre._warm_hot_set()
            _drain(qapp)
    finally:
        perf_mod.perf_enabled = False
    match = re.search(r"phase=ui\.preload\.liked ms=(\d+\.\d{3})", err.getvalue())
    assert match, f"缺少 preload span: {err.getvalue()!r}"
    s.close()


def test_idle_timer_declared_and_scheduled(qapp):
    """§11.3：QTimer 声明式持有（防 GC 悬垂）；singleShot + 空闲档延时。"""
    from reco_store import open_store
    from reco_ui.preload import IDLE_DELAY_MS, Preloader
    from reco_ui.qt import QTimer

    s = open_store(tmp_path_none())
    try:
        win = FakeWindow(s, {}, {})
        pre = Preloader(win)
        assert pre._timer is None  # 未 start 不建 timer
        pre.start()
        assert isinstance(pre._timer, QTimer)
        assert pre._timer.isSingleShot()
        assert pre._timer.interval() == IDLE_DELAY_MS == 1500
        pre.interrupt()  # 收尾：停表防真实触发
    finally:
        s.close()


def tmp_path_none():
    import tempfile

    return tempfile.mkdtemp() + "/p6.db"


def test_mainwindow_first_frame_under_500ms(qapp, tmp_path):
    """首屏骨架 <500ms 不回归（F5 口径；ui.first_frame span 实测）。"""
    import time

    from reco_io import perf as perf_mod
    from reco_store import open_store
    from reco_ui.app import MainWindow

    s = open_store(tmp_path / "p7.db")
    perf_mod.perf_enabled = True
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            t0 = time.perf_counter()
            win = MainWindow(store=s)
            wall_ms = (time.perf_counter() - t0) * 1000.0
    finally:
        perf_mod.perf_enabled = False
    match = re.search(r"phase=ui\.first_frame ms=(\d+\.\d{3})", err.getvalue())
    assert match, err.getvalue()
    assert float(match.group(1)) < 500.0
    assert wall_ms < 500.0
    win.close()
    s.close()


def test_mainwindow_contract_methods_for_preloader(qapp, tmp_path):
    """MainWindow 预热契约三方法：page/preload_jobs/store（接线面）。"""
    from reco_store import open_store
    from reco_ui.app import MainWindow

    s = open_store(tmp_path / "p8.db")
    win = MainWindow(store=s)
    assert callable(win.preload_jobs)
    jobs = win.preload_jobs()
    assert set(jobs) == {"liked", "candidate"}  # zone 页可预热；recommend/profile 接线后扩
    assert win.store() is s
    assert win.page("liked").page_id == "liked"
    win.close()
    s.close()


def test_nav_click_bumps_ui_usage(qapp, tmp_path):
    """验收 1：导航点击 → ui_usage 计数递增（page_id, uses, last_used）。"""
    from reco_store import open_store
    from reco_ui.app import MainWindow

    s = open_store(tmp_path / "p9.db")
    win = MainWindow(store=s)
    win._on_nav(0)  # liked
    win._on_nav(3)  # profile
    win._on_nav(0)  # liked 第二次
    rows = s.get_ui_usage()
    assert rows["liked"]["uses"] == 2
    assert rows["profile"]["uses"] == 1
    assert rows["liked"]["last_used"] >= rows["profile"]["last_used"]
    win.close()
    s.close()
