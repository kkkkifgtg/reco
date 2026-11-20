"""GUI 主窗口骨架：三板块 + 画像页（工单 12 切片 1）。

线程模型（§11.1）：UI 线程只发起+渲染；扫描/打分/DB 全在 QThreadPool
worker（切片 2 起接 reco_ui.workers）。本切片落骨架、导航与按需加载
发起点；「已加载」标记仅在加载成功后置位（§11.2 坑 3、§11.7）。
"""
from __future__ import annotations

from reco_io.perf import perf_span
from reco_ui.qt import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from reco_ui.theme import apply_dark_theme

PAGE_ORDER = ("liked", "candidate", "recommend", "profile")
PAGE_LABELS = {
    "liked": "喜欢专区",
    "candidate": "候选池专区",
    "recommend": "推荐专区",
    "profile": "画像",
}

class PagePlaceholder(QWidget):
    """板块骨架容器：真实数据视图由切片 2-4 填充。"""

    def __init__(self, page_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page_id = page_id
        self.loaded = False
        layout = QVBoxLayout(self)
        title = QLabel(PAGE_LABELS[page_id])
        title.setObjectName("page-title")
        layout.addWidget(title)
        hint = QLabel("骨架占位：数据视图按需加载")
        hint.setObjectName("dim")
        layout.addWidget(hint)
        layout.addStretch(1)

    def mark_loaded(self) -> None:
        """「已加载」标记：仅按需/预热成功后置位（§11.2 坑 3）。"""
        self.loaded = True
        for hint in self.findChildren(QLabel, "dim"):
            hint.setText("已加载")

class MainWindow(QMainWindow):
    """三板块 + 画像页导航骨架；首帧骨架 <500ms（F5 口径，RECO_PERF 打点）。"""

    page_requested = pyqtSignal(str)

    def __init__(self, store=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._pages: dict[str, PagePlaceholder] = {}
        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._stack = QStackedWidget()
        with perf_span("ui.first_frame"):
            self._build_skeleton()

    def _build_skeleton(self) -> None:
        self.setWindowTitle("reco —— 本地小说推荐")
        for pid in PAGE_ORDER:
            page = PagePlaceholder(pid)
            self._pages[pid] = page
            self._stack.addWidget(page)
            QListWidgetItem(PAGE_LABELS[pid], self._nav)
        self._nav.currentRowChanged.connect(self._on_nav)
        self._nav.setCurrentRow(2)  # 默认推荐专区（恒热，§11.7）
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_layout.addWidget(self._nav)
        side_layout.addStretch(1)
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.addWidget(side, 0)
        body_layout.addWidget(self._stack, 1)
        self.setCentralWidget(body)

    # —— 对外契约（切片 2-4 与测试使用）——
    def page_ids(self) -> list[str]:
        return list(PAGE_ORDER)

    def page_label(self, page_id: str) -> str:
        return PAGE_LABELS[page_id]

    def page(self, page_id: str) -> PagePlaceholder:
        return self._pages[page_id]

    def _on_nav(self, row: int) -> None:
        if row < 0 or row >= len(PAGE_ORDER):
            return
        page = self._pages[PAGE_ORDER[row]]
        if not page.loaded:
            # UI 线程只发起；真实加载在 QThreadPool worker（切片 2 接入）。
            self.page_requested.emit(page.page_id)

def run(argv=None) -> int:
    """事件循环入口；集中 QSS 在此挂载（美观不阻塞首帧骨架）。"""
    app = QApplication.instance() or QApplication(argv or [])
    apply_dark_theme(app)
    win = MainWindow()
    win.resize(1080, 720)
    win.show()
    return app.exec()
