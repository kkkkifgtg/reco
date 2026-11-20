"""reco-ui 测试共享夹具（PyQt 生命周期守护）。

两条铁律（0xC0000409 崩面根治实录）：
1. QApplication 必须被 Python 侧整场持有——局部引用释放会被 PyQt GC
   掉 C++ 对象，后续 globalInstance()=None、退出期硬崩。
2. 任何 QThreadPool worker 都不得跨测试边界存活——每测试后 waitForDone
   泄洪 + 泵事件；否则排队/运行中的 worker 会在下一个测试的对象销毁
   序列里触碰悬垂包装。
"""
from __future__ import annotations

import pytest

_app_holder: list = []


def _drain_pool(timeout_ms: int = 5000) -> None:
    from reco_ui.qt import QApplication, QThreadPool

    pool = QThreadPool.globalInstance()
    if pool is not None:
        pool.waitForDone(timeout_ms)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


@pytest.fixture(autouse=True, scope="session")
def _qapp_session():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    _app_holder.append(app)  # 整场会话持引用，防 PyQt GC 销毁 C++ 对象
    yield
    _drain_pool()


@pytest.fixture(autouse=True)
def _drain_after_each_test():
    yield
    _drain_pool(3000)
