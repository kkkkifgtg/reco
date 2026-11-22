"""GUI 主窗口骨架：三板块 + 画像页（工单 12 切片 1）。

线程模型（§11.1）：UI 线程只发起+渲染；扫描/打分/DB 全在 QThreadPool
worker（切片 2 起接 reco_ui.workers）。本切片落骨架、导航与按需加载
发起点；「已加载」标记仅在加载成功后置位（§11.2 坑 3、§11.7）。
"""
from __future__ import annotations

from pathlib import Path

from reco_io.perf import perf_span
from reco_ui.qt import (
    QAbstractItemView,
    QApplication,
    QIcon,
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
    QTimer,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from reco_ui.pages import (
    FeedbackWorker,
    ProfileLoader,
    RankLoader,
    ScanWorker,
    ZoneLoader,
    ZonePage,
    recommend_empty_text,
    zone_page_rows,
)
from reco_ui.preload import Preloader
from reco_ui.proposals_page import ProposalsPage
from reco_ui.reader_page import ReaderPage
from reco_ui.tables import RankTableModel
from reco_ui.theme import apply_dark_theme

# 分级超时（§11.2 坑 1）：worker 内 zone 查询墙钟上限（慢库不冻结 UI）。
# W18 U2：200ms 对真实库规模过激（0.3s 慢库即死页）——放宽到 1.5s；
# 仍远小于无响应感知阈值，失败路径有「重试」入口兜底。
_LOAD_LIB_MS = 1500.0

# W16：数据根目录配置（用户可经 config 注入覆盖；当前默认目录可能不存在，
# 「打开文件夹」时由 ensure_zones 创建——F2 契约）。
DEFAULT_DATA_ROOT = "F:/date/adult-reco/data"

# N1：定时自动扫描默认关闭（config["scan_interval_s"]>0 才启动；QTimer 周期
# 触发后台重扫，UI 零阻塞——语义与手动「刷新」同一条 sync 落库路径）。
DEFAULT_SCAN_INTERVAL_S = 0
_AUTO_SCAN_ZONES = ("liked", "candidate", "推荐专区")

PAGE_ORDER = ("liked", "candidate", "recommend", "profile")
PAGE_LABELS = {
    "liked": "喜欢专区",
    "candidate": "候选池专区",
    "recommend": "推荐专区",
    "profile": "画像",
}


def _zone_page_id(zone: str) -> str:
    """专区名 → page_id（推荐专区页 zone 与 page_id 异名映射）。"""
    return "recommend" if zone == "推荐专区" else zone


def _sync_summary(added: int, removed: int, moved: int, changed: int) -> str:
    """再扫描摘要文案（数值/枚举，无正文；全零 = 无变化）。"""
    parts = []
    if added:
        parts.append(f"新增 {added}")
    if removed:
        parts.append(f"移除 {removed}")
    if moved:
        parts.append(f"移动 {moved}")
    if changed:
        parts.append(f"更新 {changed}")
    return "，".join(parts) if parts else "无变化"

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

    def __init__(self, store=None, parent: QWidget | None = None, config=None) -> None:
        super().__init__(parent)
        self._store = store
        self._config = dict(config or {})  # N1：配置快照（定时自动扫描参数读取）
        self._data_root = self._config.get("data_root", DEFAULT_DATA_ROOT)  # W16 配置注入
        self._pages: dict[str, PagePlaceholder] = {}
        self._inflight: set[str] = set()  # 坑 2：in-flight 去重
        self._preloader = None  # W12b：骨架构建完成后在 _build_skeleton 末尾启动
        self._loaders: dict[str, ZoneLoader] = {}  # 持有运行中 loader 的 Python 引用（QThreadPool 不持包装）
        # RR1 T5：阅读页常驻实例（换书复用；不进 PAGE_ORDER/导航）+ 来源页记录。
        self._reader = None
        self._reader_return_widget = None
        self._reader_return_title = ""
        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._stack = QStackedWidget()
        # N1：再扫描同步状态——在途计数（防重入）+ 定时器（默认关，配置开启）。
        self._sync_inflight = 0
        self._auto_pending = 0
        self._auto_scan = None  # QTimer | None；_build_skeleton 末尾按配置启动
        with perf_span("ui.first_frame"):
            self._build_skeleton()

    def _build_skeleton(self) -> None:
        self.setWindowTitle("reco —— 本地小说推荐")
        _icon = Path(__file__).parent / "assets" / "reco.ico"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        for pid in PAGE_ORDER:
            if pid == "recommend":
                page = RecommendPage(self._store)  # 真实推荐页（切片 2 接线）
            elif pid in ("liked", "candidate"):
                page = ZonePage(pid, self._store, data_root=self._data_root)  # 板块页（W16 F1/F2）
            elif pid == "profile":
                page = ProfilePage(self._store)  # 真实画像页（切片 4）
            else:
                page = PagePlaceholder(pid)
            self._pages[pid] = page
            self._stack.addWidget(page)
            QListWidgetItem(PAGE_LABELS[pid], self._nav)
        # RR1 T5：阅读页加入 stack（不进 PAGE_ORDER/nav），back_requested → 返回原页。
        self._reader = ReaderPage(self._store)
        self._stack.addWidget(self._reader)
        self._reader.back_requested.connect(self._close_reader)
        self._pages["reader"] = self._reader  # ui_usage("reader") 预热热集可解析（PAGE_ORDER 仍 4 页）
        # #48：待确认提案页常驻 stack，从画像页进入（不进 PAGE_ORDER/nav，保持 4 页契约）
        self._proposals = ProposalsPage(self._store)
        self._stack.addWidget(self._proposals)
        self._pages["proposals"] = self._proposals
        self._proposals.back_requested.connect(self._close_proposals)
        self._pages["profile"].proposals_requested.connect(self._open_proposals)
        self._nav.currentRowChanged.connect(self._on_nav)
        self.page_requested.connect(self._start_load)  # UI 线程只发起（§11.1）
        self._nav.setCurrentRow(2)  # 默认推荐专区（恒热，§11.7）
        self.page_requested.emit("liked")  # 首屏自动预热 liked（异步，不阻塞骨架）
        # W12b §11.7：首帧骨架渲染完成后空闲档预热热集（QTimer singleShot ~1.5s，
        # 用户操作可打断；「已加载」只在预热成功后置位——坑 3）。
        self._preloader = Preloader(self)
        self._preloader.start()
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 8, 8, 8)
        side_layout.addWidget(self._nav)
        side_layout.addStretch(1)
        # W16 F3：候选页专属「开始推荐」→ 触发 RankLoader（W13 生产接线复用）+ 切页
        self._pages["candidate"].recommend_requested.connect(self._start_recommend)
        # W18 U2：「重试」入口——失败页重新发起加载（点击无响应的兜底出口）
        for pid in ("liked", "candidate"):
            self._pages[pid].reload_requested.connect(
                lambda checked=False, p=pid: self._start_load(p))
        # N1：「刷新」接线——zone 页同步+重查；推荐页同步+rank 唯一装配重绘。
        for pid in ("liked", "candidate"):
            self._pages[pid].refresh_requested.connect(
                lambda checked=False, p=pid: self._start_sync_refresh(p))
        rec_page = self._pages.get("recommend")
        if hasattr(rec_page, "btn_refresh"):
            rec_page.refresh_requested.connect(
                lambda checked=False: self._start_sync_refresh("推荐专区"))
        # W19 N2：「我喜欢」真移动跨页联动——发起页 apply_rows 后，对侧专区页重查
        self._pages["liked"].zone_changed = self._on_zone_changed
        self._pages["candidate"].zone_changed = self._on_zone_changed
        # RR1 T5：双击作品 → 打开阅读页（ZonePage 只发信号，主窗决定切页）。
        for pid in ("liked", "candidate"):
            self._pages[pid].reader_requested.connect(self._open_reader)
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.addWidget(side, 0)
        body_layout.addWidget(self._stack, 1)
        self.setCentralWidget(body)
        # N1：定时自动扫描（默认关；scan_interval_s>0 才启动，零阻塞后台重扫）。
        interval_s = int(self._config.get("scan_interval_s", DEFAULT_SCAN_INTERVAL_S))
        if interval_s > 0 and self._store is not None:
            timer = QTimer(self)
            timer.setInterval(interval_s * 1000)
            timer.timeout.connect(self._on_auto_scan)
            timer.start()
            self._auto_scan = timer

    # —— 对外契约（切片 2-4 与测试使用）——
    def page_ids(self) -> list[str]:
        return list(PAGE_ORDER)

    def page_label(self, page_id: str) -> str:
        return PAGE_LABELS[page_id]

    def page(self, page_id: str) -> PagePlaceholder:
        return self._pages[page_id]

    @property
    def reader_page(self):
        """RR1：阅读页实例（测试/验收取用；不进 PAGE_ORDER/导航）。"""
        return self._reader

    # —— RR1 T5：双击 → 阅读页 → 返回原页 ——
    def _open_reader(self, path, book_id) -> None:
        """进入阅读页：记录来源页/标题 → 切 stack + 标题 + ui_usage bump("reader")。"""
        from datetime import datetime, timezone

        if self._reader is None:
            return
        if self._reader_return_widget is None:  # 嵌套重开保留最初的来源页
            self._reader_return_widget = self._stack.currentWidget()
            self._reader_return_title = self.windowTitle()
        self._stack.setCurrentIndex(self._stack.indexOf(self._reader))
        self.setWindowTitle(f"{Path(path).name} · 阅读")
        if self._store is not None:
            try:  # 性能统计走独立表（C12）；失败绝不挡住阅读
                self._store.bump_ui_usage(
                    "reader", now=datetime.now(timezone.utc).isoformat())
            except Exception:
                pass
        self._reader.open_book(path, book_id)

    def _open_proposals(self) -> None:
        """#48：从画像页进入待确认提案页（记录来源页，返回时切回）。"""
        self._proposals_origin = self._nav.currentRow()
        self._proposals.refresh()
        self._stack.setCurrentWidget(self._proposals)

    def _close_proposals(self) -> None:
        """返回进入前的页面。"""
        row = getattr(self, "_proposals_origin", 2)
        if 0 <= row < len(PAGE_ORDER):
            self._stack.setCurrentWidget(self._pages[PAGE_ORDER[row]])

    def _close_reader(self) -> None:
        """返回按钮：close_book 内 flush 进度，再切回来源页并恢复标题。"""
        if self._reader is None:
            return
        self._reader.close_book()
        target = self._reader_return_widget
        index = self._stack.indexOf(target) if target is not None else -1
        if index < 0 and self._nav.currentRow() >= 0:
            index = self._stack.indexOf(self._pages[PAGE_ORDER[self._nav.currentRow()]])
        self._stack.setCurrentIndex(max(0, index))
        self.setWindowTitle(self._reader_return_title or "reco —— 本地小说推荐")
        self._reader_return_widget = None
        self._reader_return_title = ""

    def _on_nav(self, row: int) -> None:
        if row < 0 or row >= len(PAGE_ORDER):
            return
        if self._reader_return_widget is not None:
            # 阅读中点左侧导航：先落进度并退出阅读上下文（标题不残留）。
            if self._reader is not None:
                self._reader.flush_progress()
            title = self._reader_return_title or "reco —— 本地小说推荐"
            self._reader_return_title = ""
            self._reader_return_widget = None
            self.setWindowTitle(title)
        page = self._pages[PAGE_ORDER[row]]
        self._stack.setCurrentIndex(self._stack.indexOf(page))  # W18b：切右栏（缺此行=点导航不换页）
        # W12b 验收 1：导航/功能点击 → ui_usage 计数递增（独立表，C12 不进口味流）。
        if self._store is not None:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            self._store.bump_ui_usage(page.page_id, now=now)
            if self._preloader is not None:
                self._preloader.interrupt()  # 用户操作可打断空闲档预热（§11.7）
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
            page.mark_loading()  # W18 U2：点击立即有可见反馈（「加载中…」+隐藏重试按钮）
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

    def _start_recommend(self) -> None:
        """W16 F3：「开始推荐」→ RankLoader（W13 生产接线复用）+ 自动切推荐专区页。"""
        self._start_load("recommend")
        self._nav.setCurrentRow(2)

    def _on_zone_changed(self, changed_page) -> None:
        """W19 N2：真移动跨页联动——对侧专区页重查刷新（两表格同步，验收项）。"""
        for pid in ("liked", "candidate"):
            page = self._pages.get(pid)
            if page is changed_page or not isinstance(page, ZonePage):
                continue
            try:
                rows = zone_page_rows(self._store, pid)
            except Exception:
                continue  # 对侧刷新失败不掩盖发起页的成功状态
            page.apply_rows(rows)

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

    # —— N1：目录→库再扫描（手动「刷新」与定时自动扫描共用路径）——
    def _on_auto_scan(self) -> None:
        """QTimer 周期回调：上轮未完成跳过本轮（防重入）；UI 线程只派发。"""
        if self._auto_pending:
            return
        for zone in _AUTO_SCAN_ZONES:
            self._start_sync_refresh(zone, auto=True)

    def _start_sync_refresh(self, zone: str, *, auto: bool = False) -> None:
        """发起单专区目录→库再扫描（池线程；UI 线程零扫描零 DB 写，§11.1）。"""
        if self._store is None:
            return
        self._sync_inflight += 1
        if auto:
            self._auto_pending += 1
        worker = ScanWorker(self._store, zone, self._data_root)
        worker.signals.done.connect(
            lambda z, a, r, m, c, err, pid=_zone_page_id(zone), auto=auto:
                self._on_sync_done(pid, z, a, r, m, c, err, auto=auto))
        QThreadPool.globalInstance().start(worker)

    def _on_sync_done(self, page_id: str, zone: str, added: int, removed: int,
                      moved: int, changed: int, error: str, *, auto: bool = False) -> None:
        """同步完成 → UI 线程渲染（zone 页重查行；推荐页走 rank 唯一装配重绘）。"""
        self._sync_inflight = max(0, self._sync_inflight - 1)
        if auto:
            self._auto_pending = max(0, self._auto_pending - 1)
        page = self._pages.get(page_id)
        summary = "刷新失败（可重试）：" + error if error else _sync_summary(added, removed, moved, changed)
        if isinstance(page, ZonePage):
            if error:
                page._status.setText(summary)
                return
            from reco_ui.pages import zone_page_rows

            try:
                page.apply_rows(zone_page_rows(self._store, zone))
            except Exception as exc:
                page.mark_failed(f"{type(exc).__name__}: {exc}")
                return
            page._status.setText(summary)
        elif isinstance(page, RecommendPage):
            page._status.setText(summary)
            if not error:
                self._start_load("recommend")  # rank 唯一装配层重绘（A6，零复制）

    # —— W12b 预热契约（Preloader 消费的三方法）——
    def store(self):
        return self._store

    def preload_jobs(self) -> dict:
        """page_id → 预热任务（True=成功）；覆盖 zone 数据页（liked/candidate）。

        recommend/profile 的真实加载在切片 2/4 worker；未在表中 = 无预热任务，
        Preloader 跳过（恒热页已加载则零动作）。
        """
        if self._store is None:
            return {}
        return {
            pid: self._make_zone_job(pid)
            for pid in ("liked", "candidate")
            if pid in self._pages
        }

    def _make_zone_job(self, page_id: str):
        def job() -> bool:
            from reco_ui.pages import zone_page_rows

            try:
                rows = zone_page_rows(self._store, page_id, lib_ms=_LOAD_LIB_MS)
            except Exception:
                return False
            page = self._pages.get(page_id)
            if isinstance(page, ZonePage):
                page.apply_rows(rows)
            return True
        return job

    def closeEvent(self, event) -> None:  # noqa: N802（Qt 命名）
        """退出前 flush 阅读进度 + 泄洪线程池，避免销毁后信号回访已删对象。"""
        if self._reader is not None:
            try:
                self._reader.close_book()  # flush 进度 + 清空视图（同实例下次换书复用）
            except Exception:
                pass
            if self._stack.currentWidget() is self._reader:
                self._stack.setCurrentIndex(0)  # 离开阅读页再销毁（Qt 生命周期兜底）
        QThreadPool.globalInstance().waitForDone(3000)
        super().closeEvent(event)



class ProfilePage(QWidget):
    """画像页：轴权重/样本量/置信度/Top-N/漂移/版本（C6 诚实展示）。"""

    proposals_requested = pyqtSignal()

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
        # #48：待确认提案入口（提案页常驻 stack，不进导航）
        self.btn_proposals = QPushButton("待确认提案")
        self.btn_proposals.clicked.connect(self.proposals_requested.emit)
        layout.addWidget(self.btn_proposals)
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
    refresh_requested = pyqtSignal()  # N1：「刷新」→ 主窗发起推荐专区目录→库再扫描

    def __init__(self, store=None, parent=None, *, data_root=None) -> None:
        super().__init__(parent)
        self._store = store
        self._data_root = data_root
        self.loaded = False
        self._model = RankTableModel()
        self._movable: set[int] = set()  # W19 N2：本轮 rank 行中可真移动的 book_id
        layout = QVBoxLayout(self)
        title = QLabel("推荐专区")
        title.setObjectName("page-title")
        layout.addWidget(title)
        # W16 F2：推荐专区页同样有「打开文件夹」（zone=推荐专区 映射）
        # N1：推荐专区页右上角「刷新」→ 目录→库再扫描（与 zone 页同一路径）。
        open_row = QHBoxLayout()
        open_row.addStretch(1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        open_row.addWidget(self.btn_refresh)
        self.btn_open_folder = QPushButton("打开文件夹")
        open_row.addWidget(self.btn_open_folder)
        layout.addLayout(open_row)
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
        # T4 空态提示：无 rank 行时显示明确文案（缺向量 vs 候选为空）
        self._empty = QLabel("")
        self._empty.setObjectName("dim")
        self._empty.setWordWrap(True)
        self._empty.setVisible(False)
        layout.addWidget(self._empty)
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

    def row_count(self) -> int:
        return self._model.rowCount()

    def empty_text(self) -> str:
        """空态文案（隐藏时为空串；测试/无障碍读取面）。"""
        return "" if self._empty.isHidden() else self._empty.text()

    def empty_visible(self) -> bool:
        return not self._empty.isHidden()

    def apply_rank(self, out) -> None:
        """rank 输出唯一消费面（W13 承接①）：RankRow 序列直接渲染，零排序（A6/§10）。

        T4 空态：无行时显示明确文案（n_vectors==0 → 缺向量并提示运行提取；
        否则 → 候选为空）；有行时不显示。
        """
        rows = list(out["rank_rows"])
        message = recommend_empty_text(out)
        self._empty.setText(message or "")
        self._empty.setVisible(message is not None)
        self._model.set_rows(rows, dict(out.get("names") or {}))
        self._movable = set(out.get("movable") or set())  # W19 N2：真移动候选集

    def load_now(self) -> None:
        """同步装填（快照/探针口径）：与生产 worker 同一装配层 rank_candidates，零复制。

        本方法不做任何重排——快照一致性测试与 GUI_PROBE 经此路径取行序；
        空态同样经 apply_rank 统一消费。
        """
        from reco_store.recommend import rank_candidates

        out = rank_candidates(self._store) if self._store is not None else {"rank_rows": (), "names": {}}
        self.apply_rank(out)
        self.mark_loaded()

    def mark_failed(self, message: str) -> None:
        self._status.setText(f"推荐加载失败（可重试）：{message}")

    def mark_loaded(self) -> None:
        self.loaded = True  # 「已加载」仅在按需/预热成功后置位（§11.2 坑 3）

    def _feedback(self, kind: str, axis) -> None:
        """发起反馈：写库投递线程池（UI 线程零阻塞，§11.1）；连点防护=反馈侧 in-flight 去重（坑 2）。"""
        if self._store is None:
            return
        raw = self._model.book_id_at(self.table.selectionModel().currentIndex().row())
        if raw is None:
            return
        if kind == "like":
            self._move_liked(raw)  # W19 N2：like 升级真移动
            return
        for b in (self.btn_dislike, self.btn_like, self.btn_pool):
            b.setEnabled(False)  # in-flight：按钮禁用防双写
        worker = FeedbackWorker(self._store, kind, int(raw), axis=axis)
        worker.signals.done.connect(self._on_feedback_done)
        QThreadPool.globalInstance().start(worker)

    def _move_liked(self, raw_book_id: str) -> None:
        """W19 N2：「标为喜欢」→ 真移动（推荐源 → liked/；UI 只发起，§11.1）。

        book_id 名空间不同（rank 输出 "b<N>"），先解析 db 内真实整数 book_id；
        file 行缺失（外部删除/仅剩 books 行）时回退书名→liked/ 同名产物，
        产物不存在则诚实降级不移动（不猜名字映射）。已迁移书的重复点击由
        move_book_to_liked 幂等护栏吸收（no-op 不落事件）。
        """
        from reco_ui.pages import ZONE_DIR_NAMES, LikeMoveWorker

        digits = str(raw_book_id).lstrip("b")
        try:
            book_id = int(digits)
        except ValueError:
            return
        conn = self._store.read_conn()
        try:
            row = conn.execute(
                "SELECT path, zone FROM files WHERE book_id = ? AND present = 1"
                " ORDER BY file_id LIMIT 1", (book_id,)).fetchone()
            if row is None:
                like_row = conn.execute(
                    "SELECT display_name FROM books WHERE book_id = ?", (book_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            if like_row is None:
                return
            # 回退探测只查路径，不建目录（无源文件绝不留下移动目录痕迹）
            src = Path(self._data_root) / ZONE_DIR_NAMES["liked"] / like_row[0]
            if not src.is_file():
                return
            row = (str(src), "liked")
        worker = LikeMoveWorker(self._store, str(row[1]), str(self._data_root),
                                src_path=str(row[0]))
        worker.signals.done.connect(self._on_like_move_done)
        QThreadPool.globalInstance().start(worker)

    def _on_like_move_done(self, ok: bool, rows: list, error: str) -> None:
        """真移动完成：成功重放 rank 输出（移出的书从候选集消失）；失败报错可重试。

        rows 为 worker 回传的重查结果（行重查在 worker 线程，§11.1）；推荐页
        只用 rank 重放渲染，故忽略 rows。
        """
        if not ok:
            self._status.setText(f"移动失败（可重试）：{error}")
            return
        try:
            from reco_store.recommend import rank_candidates

            out = rank_candidates(self._store)
        except Exception as exc:
            self._status.setText(f"已移到喜欢专区（重算推荐失败：{type(exc).__name__}: {exc}）")
            return
        self.apply_rank(out)
        self._status.setText("已记录")

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



def _default_db_path() -> Path:
    """默认运行库路径：自本文件向上找含 pyproject.toml 的仓库根，取其下 reco.db。

    不用固定 parents[N]：目录结构变动（如仓库上移一层）会让固定层数解析到错误目录，
    曾导致 GUI 打开空的 packages/reco.db、推荐页误报「库中缺少特征向量」。
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "packages").is_dir():
            return parent / "reco.db"
    # 兜底：旧行为（找不到仓库根时保持可运行）
    return here.parents[3] / "reco.db"


def run(argv=None) -> int:
    """事件循环入口；集中 QSS 在此挂载（美观不阻塞首帧骨架）。

    W18b：生产入口必须挂真实 Store（默认 db=项目根 reco.db）——
    store=None 时 _start_load 早退，专区页将永远停在「待加载」（用户实测 bug）。
    """
    app = QApplication.instance() or QApplication(argv or [])
    apply_dark_theme(app)
    _default_db = _default_db_path()
    from reco_store import open_store as _open_store

    _store = _open_store(_default_db)
    win = MainWindow(store=_store)
    win.resize(1080, 720)
    win.show()
    return app.exec()
