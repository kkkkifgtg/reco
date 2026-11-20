"""板块页与异步加载 worker（工单 12 切片 3）。

§11.1：UI 线程只发起+渲染——zone 查询在 QThreadPool 的 QRunnable 里跑；
§11.2 坑 1：分级超时（lib_ms 上限保护，慢库超时不冻结 UI）；
§11.2 坑 2：in-flight 去重在 MainWindow 统一裁决；
§11.2 坑 3：「已加载」仅在成功置位，失败可重试；
§11.2 坑 7：QMenu 每次按需构建、用后即弃（幂等）。
"""
from __future__ import annotations

import time

from reco_io.perf import perf_span
from reco_ui.qt import (
    QAbstractTableModel,
    QLabel,
    QMenu,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QTableView,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)

_ZONE_HEADERS = ("book_id", "书名", "大小", "备注")


def zone_page_rows(store, zone: str, *, limit: int = 200, lib_ms: float = 800.0) -> list[tuple]:
    """专区行查询：行 = (book_id, 名称, 大小, 备注)。

    分级超时（坑 1）：查询墙钟超 lib_ms 上限 → TimeoutError（worker 线程内，
    不冻结 UI）。lib_ms 由调用方按库规模分级传入。
    """
    from reco_ui.glue import zone_rows

    t0 = time.perf_counter()
    raw = zone_rows(store, zone, limit=limit)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if elapsed_ms > lib_ms:
        raise TimeoutError(f"zone={zone!r} 查询 {elapsed_ms:.0f}ms 超出分级超时上限 {lib_ms:.0f}ms")
    return [(r[0], r[1], r[2], "") for r in raw]


def profile_rows(store) -> dict:
    """画像行（无画像事件时返回空壳；数值与标签 id，无正文，B4/C6）。"""
    conn = store.read_conn()
    try:
        n_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='rec_feedback'").fetchone()[0]
    finally:
        conn.close()
    if n_events == 0:
        return {"has_profile": False}
    return {
        "has_profile": True,
        "axis_weights": {"tag": 1.0},
        "sample_counts": {"n_pos": 0, "n_neg": 0},
        "confidence": 0.0,
        "top_tags": (),
        "drift": "flat",
        "version": 0,
    }


class ZoneTableModel(QAbstractTableModel):
    """专区表格模型（book_id/书名/大小/备注）；批更新走整批重置（坑 6）。"""

    def __init__(self, rows=(), parent=None) -> None:
        super().__init__(parent)
        self._rows: list[tuple] = list(rows)
        self._reset_calls = 0

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(_ZONE_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(_ZONE_HEADERS):
                return _ZONE_HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        col = index.column()
        return "" if col >= len(row) else str(row[col])

    def set_rows(self, rows) -> None:
        """整批替换（§11.2 坑 6：model/view 批更新，非逐行 insert）。"""
        self.beginResetModel()
        self._rows = list(rows)
        self._reset_calls += 1
        self.endResetModel()


class LoaderSignals(QObject):
    finished = pyqtSignal(list)
    failed = pyqtSignal(str)


class ZoneLoader(QRunnable):
    """QRunnable zone 查询：结果/异常经信号回 UI 线程（UI 线程零 DB，§11.1）。"""

    def __init__(self, store, zone: str, *, lib_ms: float = 800.0) -> None:
        super().__init__()
        self._store = store
        self._zone = zone
        self._lib_ms = lib_ms
        self.signals = LoaderSignals()

    def run(self) -> None:
        try:
            with perf_span(f"ui.load.{self._zone}"):
                rows = zone_page_rows(self._store, self._zone, lib_ms=self._lib_ms)
        except Exception as exc:  # UI 只收字符串消息，不收异常对象
            self._emit_failed(f"{type(exc).__name__}: {exc}")
            return
        self._emit_finished(rows)

    def _emit_finished(self, rows) -> None:
        try:
            self.signals.finished.emit(rows)
        except RuntimeError:
            pass  # UI 已销毁（测试收尾/窗口关闭）：迟到发射静默丢弃，结果本已无人接收

    def _emit_failed(self, message: str) -> None:
        try:
            self.signals.failed.emit(message)
        except RuntimeError:
            pass  # 同上：接收方已不存在，迟到失败上报无意义


class ZonePage(QWidget):
    """喜欢/候选板块：表格 + 状态行；菜单按需构建（坑 7 幂等）。"""

    def __init__(self, zone: str, store=None, parent=None) -> None:
        super().__init__(parent)
        self.zone = zone
        self.page_id = zone  # 与 PAGE_ORDER 对齐（liked/candidate）
        self.loaded = False
        self._store = store
        layout = QVBoxLayout(self)
        title = QLabel("喜欢专区" if zone == "liked" else "候选池专区")
        title.setObjectName("page-title")
        layout.addWidget(title)
        self._table = QTableView()
        self._model = ZoneTableModel()
        self._table.setModel(self._model)
        layout.addWidget(self._table, 1)
        self._status = QLabel("待加载")
        self._status.setObjectName("dim")
        layout.addWidget(self._status)

    def load_now(self) -> None:
        """同步装填（分级超时默认档；成功后才置「已加载」，坑 3）。"""
        try:
            with perf_span(f"ui.load.{self.zone}"):
                rows = zone_page_rows(self._store, self.zone)
        except TimeoutError as exc:
            self._status.setText(f"加载超时：{exc}")
            return
        self._model.set_rows(rows)
        self.loaded = True
        self._status.setText("已加载")

    def apply_rows(self, rows) -> None:
        """worker 回调装填（UI 线程：只渲染）。"""
        self._model.set_rows(rows)
        self.loaded = True
        self._status.setText("已加载")

    def mark_failed(self, message: str) -> None:
        """失败不置「已加载」，提示可重试（坑 3）。"""
        self._status.setText(f"加载失败（可重试）：{message}")

    def row_count(self) -> int:
        return self._model.rowCount()

    def build_context_menu(self) -> QMenu:
        """每次新构建（不缓存实例 → 无悬挂 QMenu，坑 7 幂等）。"""
        menu = QMenu(self)
        if self.zone == "candidate":
            menu.addAction("标为喜欢")
            menu.addAction("不合口味")
        else:
            menu.addAction("移到候选池")
        return menu
