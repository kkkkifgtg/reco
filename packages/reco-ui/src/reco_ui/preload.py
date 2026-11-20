"""自适应热加载（W12b；架构 §11.7）。

- 热集判定（纯函数、确定性）：阈值 uses≥3（可配）或 Top-K 高频补位；
  推荐专区主面板恒热（ALWAYS_HOT_PAGES，不进配置不可关闭）。
- 空闲档预热：首帧骨架渲染完成后 QTimer.singleShot(~1.5s) 起 Preloader；
  worker 走 QThreadPool（低优先级 QRunnable）；user_interrupt 置位后排队任务
  全部跳过（用户操作可打断、不抢首帧）；QTimer 声明式持有（无原生 QTimer 引用）。
- 「已加载」标记只在预热成功后置位（坑 3）；失败/被中断 → 保持未加载，
  冷区按需路径随时可用、可重试。
- RECO_PERF：每个预热 span 输出 phase=ui.preload.<page_id> ms=<float>（§11.3 契约）。
"""
from __future__ import annotations

from reco_io.perf import perf_span
from reco_ui.qt import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal

DEFAULT_HOT_THRESHOLD = 3
DEFAULT_TOP_K = 2
IDLE_DELAY_MS = 1500  # §11.7：首帧后约 T+1.5s 空闲档

# 主面板恒热：不进配置、不可关闭（§11.7）
ALWAYS_HOT_PAGES = frozenset({"recommend"})


def compute_hot_set(ui_usage, *, threshold: int = DEFAULT_HOT_THRESHOLD,
                    top_k: int = DEFAULT_TOP_K) -> frozenset:
    """{page_id: {uses, last_used}} → 热集（uses≥threshold ∪ Top-K ∪ 恒热）。

    确定性：Top-K 按 uses 降序、并列按 page_id 升序（可复现，C8 同源纪律）。
    """
    hot: set[str] = set(ALWAYS_HOT_PAGES)
    if not isinstance(ui_usage, dict):
        return frozenset(hot)
    over = [pid for pid, row in ui_usage.items()
            if isinstance(row, dict) and row.get("uses", 0) >= threshold]
    hot.update(over)
    ranked = sorted(
        ((pid, row.get("uses", 0)) for pid, row in ui_usage.items()
         if isinstance(row, dict)),
        key=lambda kv: (-int(kv[1]), kv[0]),
    )
    hot.update(pid for pid, _ in ranked[: max(0, int(top_k))])
    return frozenset(hot)


class _PreloadSignals(QObject):
    finished = pyqtSignal(str, bool)  # (page_id, ok)


class _PreloadRunner(QRunnable):
    """单页预热 worker：可打断（运行前查 user_interrupt）；perf span 内执行。"""

    def __init__(self, page_id: str, job, signals: _PreloadSignals,
                 user_interrupt: list[bool]) -> None:
        super().__init__()
        self._page_id = page_id
        self._job = job
        self._signals = signals
        self._interrupt = user_interrupt
        _ACTIVE_PRELOADS.add(self)

    def run(self) -> None:  # noqa: N802（Qt 命名）
        ok = False
        try:
            if not self._interrupt[0]:
                with perf_span(f"ui.preload.{self._page_id}"):
                    ok = bool(self._job())
        except Exception:
            ok = False  # 失败不置位（坑 3），按需路径兜底
        try:
            self._signals.finished.emit(self._page_id, ok)
        except RuntimeError:
            pass  # 接收方已销毁
        finally:
            _ACTIVE_PRELOADS.discard(self)


# 预热 worker 注册表（QThreadPool 不持 Python 包装；run() finally 释放——
# 与 pages.py _ACTIVE_LOADERS 同款生命周期防线，防退出期 0xC0000409）。
_ACTIVE_PRELOADS: set = set()


class Preloader(QObject):
    """空闲档预热协调器：set_hot_set 后逐页入池；interrupt 后排队任务全跳过。"""

    def __init__(self, window, *, idle_delay_ms: int = IDLE_DELAY_MS,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self._jobs = window.preload_jobs()
        self._signals = _PreloadSignals()
        self._signals.finished.connect(self._on_finished)
        self._interrupt: list[bool] = [False]  # 可变槽：worker 运行前可读
        self._timer: QTimer | None = None
        self._idle_delay_ms = int(idle_delay_ms)
        self._started = False

    def start(self) -> None:
        """首帧骨架后空闲档启动（QTimer 声明式持有；singleShot 语义经 start(1) 模拟）。"""
        if self._started:
            return
        self._started = True
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._warm_hot_set)
        self._timer.start(self._idle_delay_ms)

    def interrupt(self) -> None:
        """用户操作 → 打断：排队任务全部跳过（不抢用户、不抢首帧）。"""
        self._interrupt[0] = True
        if self._timer is not None:
            self._timer.stop()

    def _warm_hot_set(self) -> None:
        if self._interrupt[0]:
            return
        store = self._window.store()
        usage = store.get_ui_usage() if store is not None else {}
        hot = compute_hot_set(usage)
        pool = QThreadPool.globalInstance()
        for pid in sorted(hot):
            page = self._window.page(pid)
            if page.loaded:
                continue  # 已加载（含按需路径先到）→ 不重复预热
            job = self._jobs.get(pid)
            if job is None:
                continue
            pool.start(_PreloadRunner(pid, job, self._signals, self._interrupt))

    def _on_finished(self, page_id: str, ok: bool) -> None:
        """worker 回 UI 线程：成功才置「已加载」（坑 3）；失败留待按需/重试。"""
        if ok and not self._interrupt[0]:
            page = self._window.page(page_id)
            if not page.loaded:
                page.mark_loaded()
