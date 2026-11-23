"""proposals 待确认页（#48）：列出自动打标签产生的新词提案，供用户采纳/拒绝。

设计要点：
- 只读 pending；candidate/count 来自 proposal payload（proposals 表无 candidate 列）；
- 采纳 → accept_proposal（建 user 标签；#83 起显示名参与自动识别）；
- 拒绝 → reject_proposal（不建标签）；
- 库写与列表重查都在池线程 worker 内完成，UI 线程只渲染（§11.1）；
- 无提案时诚实提示「暂无待确认提案」，不显示空表冒充有数据。
"""
from __future__ import annotations

from reco_ui.qt import (
    QHBoxLayout,
    QLabel,
    QObject,
    QPushButton,
    QRunnable,
    QTableWidget,
    QTableWidgetItem,
    QThreadPool,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)

_ACTIVE_PROPOSAL_WORKERS: set = set()


class ProposalActionSignals(QObject):
    """提案操作信号：done(rows, message)。"""

    done = pyqtSignal(list, str)


class ProposalActionWorker(QRunnable):
    """池线程执行「采纳/拒绝 + 重查 pending 列表」（§11.1 UI 线程零 DB）。"""

    def __init__(self, store, action: str, candidate: str, axis: str = "tag") -> None:
        super().__init__()
        self._store = store
        self._action = action
        self._candidate = candidate
        self._axis = axis
        self.signals = ProposalActionSignals()
        _ACTIVE_PROPOSAL_WORKERS.add(self)

    def run(self) -> None:
        from reco_labels.auto_tag import StoreAdapter
        from reco_labels.proposals import accept_proposal, reject_proposal

        adapter = StoreAdapter(self._store)
        try:
            if self._action == "accept":
                accept_proposal(adapter, self._candidate, axis=self._axis)
                message = f"已采纳：{self._candidate}"
            else:
                reject_proposal(adapter, self._candidate)
                message = f"已拒绝：{self._candidate}"
        except Exception as exc:  # UI 只收字符串
            self._emit([], f"{type(exc).__name__}: {exc}")
            return
        try:
            from reco_ui.pages import proposal_rows

            rows = proposal_rows(self._store)
        except Exception as exc:
            self._emit([], f"{message}（列表刷新失败：{type(exc).__name__}）")
            return
        self._emit(rows, message)

    def _emit(self, rows: list, message: str) -> None:
        try:
            self.signals.done.emit(list(rows), message)
        except RuntimeError:
            pass  # 接收方已销毁
        finally:
            _ACTIVE_PROPOSAL_WORKERS.discard(self)


class ProposalsPage(QWidget):
    """待确认提案页：列表 + 采纳/拒绝按钮。"""

    back_requested = pyqtSignal()  # 返回进入前的页面（MainWindow 接线）

    def __init__(self, store=None, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self.page_id = "proposals"
        self.loaded = False
        self._rows: list[dict] = []
        layout = QVBoxLayout(self)
        title = QLabel("待确认提案")
        title.setObjectName("page-title")
        layout.addWidget(title)
        self._status = QLabel("待加载")
        self._status.setObjectName("dim")
        layout.addWidget(self._status)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["候选词", "共现次数", "阈值"])
        layout.addWidget(self._table, 1)
        btns = QHBoxLayout()
        self.btn_accept = QPushButton("采纳")
        self.btn_accept.setObjectName("primary")
        self.btn_reject = QPushButton("拒绝")
        self.btn_reject.setObjectName("danger")
        self.btn_refresh = QPushButton("刷新")
        self.btn_accept.clicked.connect(lambda: self.accept_row(self.current_row()))
        self.btn_reject.clicked.connect(lambda: self.reject_row(self.current_row()))
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_back = QPushButton("返回")
        self.btn_back.clicked.connect(self.back_requested.emit)
        btns.insertWidget(0, self.btn_back)
        for b in (self.btn_accept, self.btn_reject, self.btn_refresh):
            btns.addWidget(b)
        btns.addStretch(1)
        layout.addLayout(btns)

    # —— 对外契约（测试与 MainWindow 使用）——
    def row_count(self) -> int:
        return self._table.rowCount()

    def table_text(self) -> str:
        cells = []
        for r in range(self._table.rowCount()):
            for c in range(self._table.columnCount()):
                item = self._table.item(r, c)
                if item is not None:
                    cells.append(item.text())
        return " ".join(cells)

    def status_text(self) -> str:
        return self._status.text()

    def current_row(self) -> int:
        return self._table.currentRow()

    def apply_rows(self, rows: list) -> None:
        """渲染行（UI 线程只渲染）。"""
        self._rows = list(rows)
        self._table.setRowCount(len(self._rows))
        for i, row in enumerate(self._rows):
            values = [str(row.get("candidate", "")), str(row.get("count", 0)),
                      str(row.get("threshold", 0))]
            for c, v in enumerate(values):
                self._table.setItem(i, c, QTableWidgetItem(v))
        self.loaded = True
        if self._rows:
            self._status.setText(f"待确认 {len(self._rows)} 条")
            self.btn_accept.setEnabled(True)
            self.btn_reject.setEnabled(True)
        else:
            self._status.setText("暂无待确认提案")
            self.btn_accept.setEnabled(False)
            self.btn_reject.setEnabled(False)

    def refresh(self) -> None:
        """同步读一次（首屏/刷新按钮）：库读在调用方线程，仅用于已加载后的轻查询。"""
        from reco_ui.pages import proposal_rows

        self.apply_rows(proposal_rows(self._store))

    def accept_row(self, row: int) -> None:
        self._act(row, "accept")

    def reject_row(self, row: int) -> None:
        self._act(row, "reject")

    def _act(self, row: int, action: str) -> None:
        if self._store is None or not (0 <= row < len(self._rows)):
            return
        candidate = str(self._rows[row].get("candidate") or "")
        if not candidate:
            return
        self._status.setText("采纳中…" if action == "accept" else "拒绝中…")
        worker = ProposalActionWorker(self._store, action, candidate)
        worker.signals.done.connect(self._on_action_done)
        QThreadPool.globalInstance().start(worker)

    def _on_action_done(self, rows: list, message: str) -> None:
        self.apply_rows(rows)
        self._status.setText(message + ("" if rows else "（暂无待确认提案）"))
