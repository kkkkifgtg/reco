"""GUI 主窗口骨架：三板块 + 画像页（工单 12 切片 1）。

线程模型（§11.1）：UI 线程只发起+渲染；扫描/打分/DB 全在 QThreadPool
worker（切片 2 起接 reco_ui.workers）。本切片落骨架、导航与按需加载
发起点；「已加载」标记仅在加载成功后置位（§11.2 坑 3、§11.7）。
"""
from __future__ import annotations

from reco_io.perf import perf_span
from reco_ui.qt import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QThreadPool,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from reco_ui.pages import FeedbackWorker, ProfileLoader, RankLoader, ZoneLoader, ZonePage
from reco_ui.tables import RankTableModel
from reco_ui.theme import apply_dark_theme

# 分级超时（§11.2 坑 1）：worker 内 zone 查询墙钟上限（慢库不冻结 UI）。
_LOAD_LIB_MS = 200.0

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
        self._inflight: set[str] = set()  # 坑 2：in-flight 去重
        self._loaders: dict[str, ZoneLoader] = {}  # 持有运行中 loader 的 Python 引用（QThreadPool 不持包装）
        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._stack = QStackedWidget()
        with perf_span("ui.first_frame"):
            self._build_skeleton()

    def _build_skeleton(self) -> None:
        self.setWindowTitle("reco —— 本地小说推荐")
        for pid in PAGE_ORDER:
            if pid == "recommend":
                page = RecommendPage(self._store)  # 真实推荐页（切片 2 接线）
            elif pid in ("liked", "candidate"):
                page = ZonePage(pid, self._store)  # 真实板块页（切片 3）
            elif pid == "profile":
                page = ProfilePage(self._store)  # 真实画像页（切片 4）
            else:
                page = PagePlaceholder(pid)
            self._pages[pid] = page
            self._stack.addWidget(page)
            QListWidgetItem(PAGE_LABELS[pid], self._nav)
        self._nav.currentRowChanged.connect(self._on_nav)
        self.page_requested.connect(self._start_load)  # UI 线程只发起（§11.1）
        self._nav.setCurrentRow(2)  # 默认推荐专区（恒热，§11.7）
        self.page_requested.emit("liked")  # 首屏自动预热 liked（异步，不阻塞骨架）
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
            # UI 线程只发起；真实加载在 QThreadPool worker（切片 3 接入）。
            self.page_requested.emit(page.page_id)

    # —— 按需加载机制（§11.1/§11.7；切片 3）——
    def _start_load(self, page_id: str) -> None:
        """发起加载：zone 查询/推荐装配/画像重放全在 QThreadPool worker（UI 线程零 DB，§11.1）。"""
        page = self._pages.get(page_id)
        if self._store is None:
            return
        if page_id in self._inflight:
            return  # 坑 2：同页并发请求只发一次
        if isinstance(page, ZonePage):
            self._inflight.add(page_id)
            loader = ZoneLoader(self._store, page_id, lib_ms=_LOAD_LIB_MS)
            loader.signals.finished.connect(lambda rows, pid=page_id: self._on_zone_loaded(pid, rows))
            loader.signals.failed.connect(lambda msg, pid=page_id: self._on_zone_failed(pid, msg))
            self._loaders[page_id] = loader  # 引用保活；完成回调里释放
            QThreadPool.globalInstance().start(loader)
        elif isinstance(page, RecommendPage):
            # W13 承接①：生产 rank 消费接线（RankLoader → apply_rank，零排序 A6）。
            self._inflight.add(page_id)
            loader = RankLoader(self._store)
            loader.signals.finished.connect(
                lambda out, err, pid=page_id: self._on_rank_loaded(pid, out, err))
            self._loaders[page_id] = loader
            QThreadPool.globalInstance().start(loader)
        elif isinstance(page, ProfilePage):
            self._inflight.add(page_id)
            loader = ProfileLoader(self._store)
            loader.signals.finished.connect(
                lambda data, err, pid=page_id: self._on_profile_loaded(pid, data, err))
            self._loaders[page_id] = loader
            QThreadPool.globalInstance().start(loader)

    def _on_rank_loaded(self, page_id: str, out, error: str) -> None:
        """rank worker 完成 → UI 线程渲染；失败不置位、可重试（坑 3）。"""
        self._inflight.discard(page_id)
        self._loaders.pop(page_id, None)
        page = self._pages.get(page_id)
        if not isinstance(page, RecommendPage):
            return
        if error:
            page.mark_failed(error)
            return
        page.apply_rank(out)
        page.mark_loaded()

    def _on_profile_loaded(self, page_id: str, data, error: str) -> None:
        self._inflight.discard(page_id)
        self._loaders.pop(page_id, None)
        page = self._pages.get(page_id)
        if not isinstance(page, ProfilePage):
            return
        if error:
            page._summary.setText(f"画像加载失败（可重试）：{error}")
            return
        page.apply_profile(data)
        page.loaded = True

    def _on_zone_loaded(self, page_id: str, rows) -> None:
        """worker 完成 → UI 线程渲染；「已加载」仅在成功后置位（坑 3）。"""
        self._inflight.discard(page_id)
        self._loaders.pop(page_id, None)
        page = self._pages.get(page_id)
        if isinstance(page, ZonePage):
            page.apply_rows(rows)

    def _on_zone_failed(self, page_id: str, message: str) -> None:
        """worker 失败 → 清 in-flight、不置位、提示可重试（坑 3）。"""
        self._inflight.discard(page_id)
        self._loaders.pop(page_id, None)
        page = self._pages.get(page_id)
        if isinstance(page, ZonePage):
            page.mark_failed(message)

    def closeEvent(self, event) -> None:  # noqa: N802（Qt 命名）
        """退出前泄洪线程池，避免销毁后信号回访已删对象（Qt 生命周期）。"""
        QThreadPool.globalInstance().waitForDone(3000)
        super().closeEvent(event)



class ProfilePage(QWidget):
    """画像页：轴权重/样本量/置信度/Top-N/漂移/版本（C6 诚实展示）。"""

    def __init__(self, store=None, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self.page_id = "profile"
        self.loaded = False
        layout = QVBoxLayout(self)
        title = QLabel("画像")
        title.setObjectName("page-title")
        layout.addWidget(title)
        self._summary = QLabel("")
        self._summary.setObjectName("dim")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        layout.addStretch(1)

    def refresh(self) -> None:
        """重读画像行并渲染（无画像事件 → 诚实降级，不伪造数值）。"""
        from reco_ui.pages import profile_rows

        self._summary.setText(self._format(profile_rows(self._store)))
        self.loaded = True

    def apply_profile(self, data: dict) -> None:
        """画像数据渲染（W13 承接②：ProfileLoader worker 产出 → 真实画像行）。"""
        self._summary.setText(self._format(data))
        self.loaded = True

    def summary_text(self) -> str:
        return self._summary.text()

    def _format(self, data: dict) -> str:
        if not data.get("has_profile"):
            return "\n".join([
                "画像：还不够准（样本不足，仅供参考）",
                "轴权重：—（暂无稳定轴）",
                "样本量：n_pos=0, n_neg=0",
                "置信度：0.00（样本 n 篇，仅供参考）",
                "Top-N：—",
                "漂移：—",
                "版本：—",
            ])
        weights = "，".join(f"{k}={v:.2f}" for k, v in dict(data.get("axis_weights") or {}).items())
        counts = dict(data.get("sample_counts") or {})
        conf = float(data.get("confidence") or 0.0)
        tops = "，".join(str(t) for t in tuple(data.get("top_tags") or ())) or "—"
        return "\n".join([
            f"轴权重：{weights or '—'}",
            f"样本量：n_pos={counts.get('n_pos', 0)}, n_neg={counts.get('n_neg', 0)}",
            f"置信度：{conf:.2f}（样本 n 篇，仅供参考）",
            f"Top-N：{tops}",
            f"漂移：{data.get('drift', '—')}",
            f"版本：v{data.get('version', 0)}",
        ])


class RecommendPage(QWidget):
    """推荐专区页：rank() 输出表格 + 三按钮（不合口味/标为喜欢/加入候选池）。

    UI 线程只发起+渲染：写操作经 reco_ui.glue → Store 单写者队列；
    表格模型零排序（A6，rank 唯一入口的输出直接渲染）。
    """

    page_id = "recommend"

    def __init__(self, store=None, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self.loaded = False
        self._model = RankTableModel()
        layout = QVBoxLayout(self)
        title = QLabel("推荐专区")
        title.setObjectName("page-title")
        layout.addWidget(title)
        gen_row = QHBoxLayout()
        gen_label = QLabel("生成篇数")
        gen_label.setObjectName("dim")
        self.generate_spin = QSpinBox()
        self.generate_spin.setRange(1, 10)
        self.generate_spin.setValue(2)  # §17 Q7：探索位默认 2 篇
        gen_row.addWidget(gen_label)
        gen_row.addWidget(self.generate_spin)
        gen_row.addStretch(1)
        layout.addLayout(gen_row)
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.table, 1)
        btns = QHBoxLayout()
        self.btn_dislike = QPushButton("不合口味")
        self.btn_like = QPushButton("标为喜欢")
        self.btn_pool = QPushButton("加入候选池")
        self.btn_dislike.setObjectName("danger")
        self.btn_like.setObjectName("primary")
        for b in (self.btn_dislike, self.btn_like, self.btn_pool):
            btns.addWidget(b)
        layout.addLayout(btns)
        self.btn_dislike.clicked.connect(lambda: self._feedback("dislike", "tag"))
        self.btn_like.clicked.connect(lambda: self._feedback("like", "tag"))
        self.btn_pool.clicked.connect(lambda: self._feedback("add_to_pool", None))
        self._status = QLabel("")
        self._status.setObjectName("dim")
        layout.addWidget(self._status)

    def set_rows(self, rows, names=None) -> None:
        self._model.set_rows(rows, names)

    def apply_rank(self, out) -> None:
        """rank 输出唯一消费面（W13 承接①）：RankRow 序列直接渲染，零排序（A6/§10）。"""
        self._model.set_rows(list(out["rank_rows"]), dict(out.get("names") or {}))

    def load_now(self) -> None:
        """同步装填（快照/探针口径）：与生产 worker 同一装配层 rank_candidates，零复制。

        本方法不做任何重排——快照一致性测试与 GUI_PROBE 经此路径取行序。
        """
        from reco_store.recommend import rank_candidates

        out = rank_candidates(self._store) if self._store is not None else {"rank_rows": (), "names": {}}
        self._model.set_rows(list(out["rank_rows"]), dict(out.get("names") or {}))
        self.mark_loaded()

    def mark_failed(self, message: str) -> None:
        self._status.setText(f"推荐加载失败（可重试）：{message}")

    def mark_loaded(self) -> None:
        self.loaded = True  # 「已加载」仅在按需/预热成功后置位（§11.2 坑 3）

    def _feedback(self, kind: str, axis) -> None:
        """发起反馈：写库投递线程池（UI 线程零阻塞，§11.1）；连点防护=反馈侧 in-flight 去重（坑 2）。"""
        if self._store is None:
            return
        book_id = self._model.book_id_at(self.table.selectionModel().currentIndex().row())
        if book_id is None:
            return
        for b in (self.btn_dislike, self.btn_like, self.btn_pool):
            b.setEnabled(False)  # in-flight：按钮禁用防双写
        worker = FeedbackWorker(self._store, kind, int(book_id), axis=axis)
        worker.signals.done.connect(self._on_feedback_done)
        QThreadPool.globalInstance().start(worker)

    def _on_feedback_done(self, event_id: int, error: str) -> None:
        for b in (self.btn_dislike, self.btn_like, self.btn_pool):
            b.setEnabled(True)
        self._status.setText("已记录" if not error else f"反馈失败（可重试）：{error}")


def run_tabs(win: "MainWindow", snapshot_path: str | None = None) -> int:
    """RECO_PERF_TABS=1 遍历入口：逐页渲染+打点，快照落盘后返回（离屏/CI 用）。"""
    import json
    from pathlib import Path

    snapshot = []
    for row, pid in enumerate(PAGE_ORDER):
        win._nav.setCurrentRow(row)
        page = win.page(pid)
        with perf_span(f"ui.tab.{pid}"):
            QApplication.processEvents()
            if hasattr(page, "refresh"):
                page.refresh()
            if hasattr(page, "load_now"):
                page.load_now()
            QApplication.processEvents()
        model = getattr(page, "_model", None)
        snapshot.append({"page": pid, "rows": int(model.rowCount()) if model is not None else 0})
    if snapshot_path:
        Path(snapshot_path).write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def run(argv=None) -> int:
    """事件循环入口；集中 QSS 在此挂载（美观不阻塞首帧骨架）。"""
    app = QApplication.instance() or QApplication(argv or [])
    apply_dark_theme(app)
    win = MainWindow()
    win.resize(1080, 720)
    win.show()
    return app.exec()
