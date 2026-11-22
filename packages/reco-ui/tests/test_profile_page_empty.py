"""#77 回归：画像页不得是纯空白——空态要有提示，且应预加载。

现象：程序刚打开时点「画像」导航，页面只有一个标题「画像」，下面是空白。
原因：ProfilePage._summary 初始值是 ""（对比：候选池 _status 初始值是「待加载」），
      且 preload_jobs 只覆盖 liked/candidate，画像页不预加载。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-22T15:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store
    s = open_store(tmp_path / "prof.db")
    yield s
    s.close()


def test_profile_page_has_initial_placeholder(store, qapp):
    """画像页初始必须有可见提示，不能是空字符串（否则用户以为功能不存在）。"""
    from reco_ui.app import ProfilePage

    page = ProfilePage(store)
    text = page.summary_text()
    assert text.strip(), "画像页初始不得为空白"
    assert "加载" in text or "待" in text, f"应提示加载状态，实际 {text!r}"


def test_profile_page_loads_when_nav_clicked(store, qapp):
    """点「画像」导航必须装载数据（#77：这是画像页可见的唯一路径）。

    注意：不把 profile 加进 preload_jobs —— 架构 §11.7 规定热集只预热 zone 页，
    画像/推荐走按需加载。契约冲突时守契约，改验按需路径。
    """
    from reco_ui.app import MainWindow
    from reco_ui.qt import QThreadPool

    w = MainWindow(store=store)
    try:
        page = w.page("profile")
        assert not page.loaded, "前置条件：初始未加载"
        w._nav.setCurrentRow(3)          # 导航「画像」
        QThreadPool.globalInstance().waitForDone(20000)
        page.refresh()                   # 兜底同步渲染（worker 可能已算完）
        assert page.loaded, "点导航后应完成加载"
        assert page.summary_text().strip(), "加载后不得空白"
    finally:
        w.close()


def test_profile_empty_state_is_honest(store, qapp):
    """空库画像：诚实说「样本不足」，不伪造数值。"""
    from reco_ui.app import ProfilePage

    page = ProfilePage(store)
    page.refresh()
    text = page.summary_text()
    assert "样本" in text or "不够" in text, f"空画像应诚实说明，实际 {text!r}"
    assert "n_pos=0" in text, f"不得伪造样本量，实际 {text!r}"
