"""池线程 worker 骨架（#113）：持活注册 + 销毁后安全发射，只此一份。

QThreadPool 只持 C++ 侧 QRunnable，不持 Python 包装引用；worker 必须由我们
持活到 emit 完成，否则窗口销毁后排队 worker 的包装会悬垂，退出期硬崩
（0xC0000409；conftest 的池泄洪是同一纪律的测试侧防线）。此前十个 worker
各抄一遍注册表与 try/except RuntimeError——语义相同，抄漏一处就是一次崩溃回归。
"""
from __future__ import annotations

from typing import ClassVar

from reco_ui.qt import QObject, QRunnable

__all__ = ["PoolWorker"]


class PoolWorker(QRunnable):
    """QRunnable 骨架：SIGNALS 造信号、SIGNAL_NAME 选信号、_emit 发射并释放。

    子类只写 __init__（先 super().__init__()，再存自己的入参）与 run()；
    发射一律 self._emit(*信号参数)，注册表由 __init_subclass__ 按子类各建一份。
    """

    SIGNALS: ClassVar[type] = QObject
    SIGNAL_NAME: ClassVar[str] = "done"

    _active: ClassVar[set] = set()

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        cls._active = set()  # 每个子类独立持活，互不干扰

    def __init__(self, signals=None) -> None:
        super().__init__()
        self.signals = signals if signals is not None else type(self).SIGNALS()
        type(self)._active.add(self)

    def _emit(self, *args) -> None:
        """按 SIGNAL_NAME 发射并释放持活（最常用的一条）。"""
        self._emit_on(type(self).SIGNAL_NAME, *args)

    def _emit_on(self, signal_name: str, *args) -> None:
        """按名字发射：多信号 worker（如 finished/failed）共用同一释放路径。"""
        try:
            getattr(self.signals, signal_name).emit(*args)
        except RuntimeError:
            pass  # 接收方已销毁：迟到结果静默丢弃
        finally:
            type(self)._active.discard(self)