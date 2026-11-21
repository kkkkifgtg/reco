"""板块页与异步加载 worker（工单 12 切片 3）。

§11.1：UI 线程只发起+渲染——zone 查询在 QThreadPool 的 QRunnable 里跑；
§11.2 坑 1：分级超时（lib_ms 上限保护，慢库超时不冻结 UI）；
§11.2 坑 2：in-flight 去重在 MainWindow 统一裁决；
§11.2 坑 3：「已加载」仅在成功置位，失败可重试；
§11.2 坑 7：QMenu 每次按需构建、用后即弃（幂等）。
"""
from __future__ import annotations

import os
import time

from reco_io.perf import perf_span
from reco_ui.qt import (
    QAbstractTableModel,
    QHBoxLayout,
    QLabel,
    QMenu,
    QModelIndex,
    QObject,
    QPushButton,
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
    """画像行（W13 承接②：接 W9 真实画像 rehydrate，替代硬编码占位快照）。

    空画像（零反馈事件且无轴）诚实降级；有画像时渲染真实轴权重/样本量/置信度
    （C6 display 直出）/Top-N/版本。数值与标签 id，无正文（B4）。
    """
    from reco_store.recommend import load_profile

    profile = load_profile(store)
    counts = dict(profile.sample_counts)
    n_pos = int(counts.get("tag_like", 0))
    n_neg = int(counts.get("tag_dislike", 0))
    if n_pos == 0 and n_neg == 0 and not profile.axis_weights:
        return {"has_profile": False}
    conf = profile.confidence
    return {
        "has_profile": True,
        "axis_weights": {str(k): float(v) for k, v in profile.axis_weights.items()},
        "sample_counts": {"n_pos": n_pos, "n_neg": n_neg},
        "confidence": float(conf.value),
        "confidence_display": str(conf.display),
        "top_tags": tuple(profile.top_tags),
        "drift": "flat",
        "version": int(profile.version),
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


# 运行中/排队中的 loader 注册表：QThreadPool 不持 Python 包装引用，
# 窗口被 GC 后排队 worker 的包装会悬垂 → 退出期 0xC0000409。
# 注册表在 run() 结束（finally）时释放，保证任何时点 worker 都有活引用。
_ACTIVE_LOADERS: set = set()
_ACTIVE_FEEDBACK: set = set()  # 反馈 worker 同款持活注册表（坑 2/生命周期）


class FeedbackSignals(QObject):
    done = pyqtSignal(int, str)  # (event_id, error)；error 非空 = 失败


class FeedbackWorker(QRunnable):
    """反馈落库 worker：record_feedback（含静音窗口）在池线程执行，§11.1。"""

    def __init__(self, store, kind: str, book_id: int, axis=None) -> None:
        super().__init__()
        self._store = store
        self._kind = kind
        self._book_id = book_id
        self._axis = axis
        self.signals = FeedbackSignals()
        _ACTIVE_FEEDBACK.add(self)

    def run(self) -> None:
        from reco_ui.glue import record_feedback

        try:
            eid = record_feedback(self._store, self._kind, self._book_id, axis=self._axis)
            self._emit(int(eid), "")
        except Exception as exc:
            self._emit(0, f"{type(exc).__name__}: {exc}")

    def _emit(self, event_id: int, error: str) -> None:
        try:
            self.signals.done.emit(event_id, error)
        except RuntimeError:
            pass  # 接收方已销毁：结果无人接收
        finally:
            _ACTIVE_FEEDBACK.discard(self)


class RankLoaderSignals(QObject):
    finished = pyqtSignal(object, str)  # (rank 输出 dict, error)；error 非空 = 失败


class RankLoader(QRunnable):
    """QRunnable 推荐装配：rank_candidates（唯一装配层）在池线程执行（§11.1）。

    W13 承接①：RankRow 经生产 worker 路径送达 RecommendPage——CLI/GUI 逐位一致的
    生产对齐面；装配层零排序复制（A6）。
    """

    def __init__(self, store) -> None:
        super().__init__()
        self._store = store
        self.signals = RankLoaderSignals()
        _ACTIVE_LOADERS.add(self)  # 入队即持活（QThreadPool 不持 Python 包装）

    def run(self) -> None:
        try:
            from reco_store.recommend import rank_candidates

            with perf_span("ui.rank.load"):
                out = rank_candidates(self._store)
        except Exception as exc:  # UI 只收字符串消息，不收异常对象
            self._emit(None, f"{type(exc).__name__}: {exc}")
            return
        self._emit(out, "")

    def _emit(self, out, error: str) -> None:
        try:
            self.signals.finished.emit(out, error)
        except RuntimeError:
            pass  # UI 已销毁：迟到发射静默丢弃
        finally:
            _ACTIVE_LOADERS.discard(self)


class ProfileLoaderSignals(QObject):
    finished = pyqtSignal(object, str)  # (profile_rows dict, error)


class ProfileLoader(QRunnable):
    """QRunnable 画像装配：profile_rows（W9 真实画像重放）在池线程执行（§11.1）。"""

    def __init__(self, store) -> None:
        super().__init__()
        self._store = store
        self.signals = ProfileLoaderSignals()
        _ACTIVE_LOADERS.add(self)

    def run(self) -> None:
        try:
            with perf_span("ui.profile.load"):
                data = profile_rows(self._store)
        except Exception as exc:
            self._emit(None, f"{type(exc).__name__}: {exc}")
            return
        self._emit(data, "")

    def _emit(self, data, error: str) -> None:
        try:
            self.signals.finished.emit(data, error)
        except RuntimeError:
            pass
        finally:
            _ACTIVE_LOADERS.discard(self)


class ZoneLoader(QRunnable):
    """QRunnable zone 查询：结果/异常经信号回 UI 线程（UI 线程零 DB，§11.1）。"""

    def __init__(self, store, zone: str, *, lib_ms: float = 800.0) -> None:
        super().__init__()
        self._store = store
        self._zone = zone
        self._lib_ms = lib_ms
        self.signals = LoaderSignals()
        _ACTIVE_LOADERS.add(self)  # 入队即持活（见上方注册表说明）

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
        finally:
            _ACTIVE_LOADERS.discard(self)  # run() 完成，注册表释放

    def _emit_failed(self, message: str) -> None:
        try:
            self.signals.failed.emit(message)
        except RuntimeError:
            pass  # 同上：接收方已不存在，迟到失败上报无意义
        finally:
            _ACTIVE_LOADERS.discard(self)  # run() 完成，注册表释放


# W16 F2：专区目录映射（data_root + zone 名）；推荐专区中文目录名与文件表 zone 值一致。
ZONE_DIR_NAMES = {"liked": "liked", "candidate": "candidate", "推荐专区": "推荐专区"}


def open_zone_folder(data_root, zone: str, *, opener=None) -> str:
    """打开专区数据目录：缺失则创建后 os.startfile。

    注意：W2 的 ensure_zones 刻意不建目录（§8.1 避免猜用户意图）；
    F2 的用户点击本身就是意图，故目录创建在本函数完成（root/专区层），
    再调 ensure_zones 走 W2 校验契约。opener 可注入（测试断言参数）。
    """
    from pathlib import Path

    from reco_io.scanner import ensure_zones

    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / ZONE_DIR_NAMES[zone]
    target.mkdir(exist_ok=True)
    ensure_zones(root, zones=(zone,))
    (opener or os.startfile)(str(target))
    return str(target)


class ZonePage(QWidget):
    """喜欢/候选板块：表格 + 状态行；菜单按需构建（坑 7 幂等）。

    W16：右上角「打开文件夹」（F2）+ 候选页专属「开始推荐」（F3，
    完成后由主窗切到推荐专区页）；setAcceptDrops 拖拽导入（F1，切片 2）。
    """

    recommend_requested = pyqtSignal()  # F3：candidate 页「开始推荐」点击

    def __init__(self, zone: str, store=None, parent=None, *, data_root=None) -> None:
        super().__init__(parent)
        self.zone = zone
        self.page_id = zone  # 与 PAGE_ORDER 对齐（liked/candidate）
        self.loaded = False
        self._store = store
        self._data_root = data_root
        self.setAcceptDrops(True)  # W16 F1：文件拖拽导入（实现在切片 2）
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
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_open_folder = QPushButton("打开文件夹")
        self.btn_open_folder.clicked.connect(self._open_folder)
        btn_row.addWidget(self.btn_open_folder)
        if zone == "candidate":  # F3：仅候选页有「开始推荐」
            self.btn_recommend = QPushButton("开始推荐")
            self.btn_recommend.clicked.connect(self.recommend_requested.emit)
            btn_row.addWidget(self.btn_recommend)
        layout.addLayout(btn_row)

    def _open_folder(self) -> None:
        """「打开文件夹」按钮（W16 F2）：data_root + zone 映射，ensure_zones 创建。"""
        open_zone_folder(self._data_root, self.zone)

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
