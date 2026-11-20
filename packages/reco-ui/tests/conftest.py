"""reco-ui 测试共享夹具。

关键点（PyQt 生命周期）：QApplication 必须被 Python 侧整场持有——
局部引用释放会被 PyQt GC 掉 C++ 对象，导致后续 globalInstance()=None
乃至退出期 0xC0000409 崩溃。收尾统一泄洪线程池（防迟到信号回访）。
"""
from __future__ import annotations

import pytest

_app_holder: list = []


@pytest.fixture(autouse=True, scope="session")
def _qapp_session():
    from reco_ui.qt import QApplication, QThreadPool

    app = QApplication.instance() or QApplication([])
    _app_holder.append(app)  # 整场会话持引用，防 PyQt GC 销毁 C++ 对象
    yield
    pool = QThreadPool.globalInstance()
    if pool is not None:
        pool.waitForDone(5000)
    if app is not None:
        app.processEvents()
