"""板块页与异步加载 worker（工单 12 切片 3）。

§11.1：UI 线程只发起+渲染——zone 查询在 QThreadPool 的 QRunnable 里跑；
§11.2 坑 1：分级超时（lib_ms 上限保护，慢库超时不冻结 UI）；
§11.2 坑 2：in-flight 去重在 MainWindow 统一裁决；
§11.2 坑 3：「已加载」仅在成功置位，失败可重试；
§11.2 坑 7：QMenu 每次按需构建、用后即弃（幂等）。

工单 19（N2）「我喜欢」真移动：
- 语义裁定：候选池→liked 是用户对既有的唯一一份实体表态归属，用真移动
  （os.replace 跨目录，同卷原子），不再 place_by_hardlink——落位语义
  （W3）源保留，会把文件同时留在 candidate/ 与 liked/，zone 语义被破坏；
  硬链接同盘成本≈0，移动即改目录项，无须复制兜底（与 W3 不同卷场景无关）。
- 事件形态：file_moved（path→new_path）+ payload 增补 zone=新目录 zone；
  store 投影以 COALESCE 同步 files.zone（W19 扩展；白名单已含 zone/new_path，
  无 zone 键的旧移动事件保持 zone 不变，向后兼容可重放）。
- 失败诚实：os.replace 失败上抛 → worker 捕获报错，不落事件、行保留可重试。
- 幂等护栏：源已在 liked/（重复点击/已迁移书）→ no-op 不落事件；
  推荐行无源文件（外部删除）→ 诚实降级不移动、不猜名字映射。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

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
    QThreadPool,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)

_ZONE_HEADERS = ("book_id", "书名", "大小", "备注")


# ---------------------------------------------------------------- 推荐空态（T4）
# 空态区分两个根因：库中尚无向量（去跑提取）vs 有向量但候选为空（candidate 区无书）。
RECOMMEND_EMPTY_NO_VECTORS = "暂无推荐：库中缺少特征向量，请运行提取（reco_cli extract）"
RECOMMEND_EMPTY_NO_CANDIDATES = "暂无推荐：候选为空"


def recommend_empty_text(out) -> str | None:
    """rank 输出 → 空态文案：有行返回 None；无行按 n_vectors 区分缺向量/候选为空。"""
    if list(out.get("rank_rows") or ()):
        return None
    if out.get("n_vectors") == 0:
        return RECOMMEND_EMPTY_NO_VECTORS
    return RECOMMEND_EMPTY_NO_CANDIDATES


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
        if not index.isValid():
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            # #66：悬停显示该书标签（Qt 原生 tooltip，悬停即弹出）
            if not (0 <= index.row() < len(self._rows)):
                return None
            row = self._rows[index.row()]
            if not row:
                return None
            try:
                book_id = int(row[0])
            except (TypeError, ValueError):
                return None
            provider = getattr(self, "tag_provider", None)
            labels = provider(book_id) if provider is not None else []
            if not labels:
                return "暂无标签"
            return "标签：" + "、".join(labels)
        if role != Qt.ItemDataRole.DisplayRole:
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
_ACTIVE_MOVES: set = set()     # W19：真移动 worker 持活注册表（同坑 2/生命周期）
_ACTIVE_IMPORTS: set = set()  # W16 F1：导入 worker 持活注册表


# W17：扫描口径为单一事实源；UI 导入白名单 = 扫描口径并集文档扩展
from reco_io.scanner import TEXT_EXTS as _SCAN_TEXT_EXTS

ALLOWED_IMPORT_EXTS = set(_SCAN_TEXT_EXTS) | {".docx", ".pdf"}


class ImportSignals(QObject):
    done = pyqtSignal(int, int, int, list, str)  # (imported, skipped, rejected, rows, error)


class ImportWorker(QRunnable):
    """W16 F1：place_by_hardlink 落位 + file_added 落库（池线程；UI 零阻塞）。

    payload 契约与 seed_books/scan 同构：path=专区目录产物实际路径、
    st_dev/st_ino 来自 PlacementResult 对应产物的 stat（队长契约补充：
    扫描器后续扫到同一文件按 (st_dev, st_ino) 配对，不重复建 book）。
    """

    def __init__(self, store, zone: str, data_root, paths) -> None:
        super().__init__()
        self._store = store
        self._zone = zone
        self._data_root = str(data_root)
        self._paths = list(paths)
        self.signals = ImportSignals()
        _ACTIVE_IMPORTS.add(self)

    def run(self) -> None:
        imported = skipped = rejected = 0
        try:
            from pathlib import Path

            from reco_io.placement import PlacementMode, place_by_hardlink
            from reco_store import dedup_key, normalize_book_name
            from reco_ui.pages import open_zone_folder

            zone_dir = Path(open_zone_folder(self._data_root, self._zone, opener=lambda p: None))
            for raw in self._paths:
                p = Path(raw)
                if not p.is_file() or p.suffix.lower() not in ALLOWED_IMPORT_EXTS:
                    rejected += 1
                    continue
                result = place_by_hardlink(p, zone_dir)
                if result.mode == PlacementMode.SKIPPED:
                    skipped += 1
                    continue
                st = result.dst.stat()
                display = result.dst.name
                from reco_io.int64 import to_int64

                self._store.append_event("file_added", payload={
                    "path": str(result.dst),
                    "zone": self._zone,
                    "ext": result.dst.suffix.lower(),
                    "size_bytes": int(st.st_size),
                    "mtime_ns": int(st.st_mtime_ns),
                    # N1：Windows 无符号 64 位身份 → SQLite int64（补码回绕契约），
                    # 与 resync/导入/种子同一编码口径（契约测试 test_int64_contract）。
                    "st_dev": to_int64(int(st.st_dev)),
                    "st_ino": to_int64(int(st.st_ino)),
                    "display_name": display,
                    "normalized_name": normalize_book_name(display),
                    "dedup_key": dedup_key(int(st.st_size), display),
                }, occurred_at=_utcnow_iso(), config_fingerprint="gui/import/1", actor="user")
                imported += 1
            error = ""
        except Exception as exc:  # UI 只收字符串
            error = f"{type(exc).__name__}: {exc}"
        rows = self._query_rows()
        self._emit(imported, skipped, rejected, rows, error)

    def _query_rows(self) -> list:
        """导入后重查本专区行（worker 线程，§11.1 UI 线程零 DB）。"""
        try:
            return list(zone_page_rows(self._store, self._zone))
        except Exception:
            return []

    def _emit(self, imported: int, skipped: int, rejected: int, rows: list, error: str) -> None:
        try:
            self.signals.done.emit(imported, skipped, rejected, list(rows), error)
        except RuntimeError:
            pass  # 接收方已销毁
        finally:
            _ACTIVE_IMPORTS.discard(self)


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class ScanSignals(QObject):
    """N1 再扫描信号：done(zone, added, removed, moved, changed, error)。"""

    done = pyqtSignal(str, int, int, int, int, str)


class ScanWorker(QRunnable):
    """N1 单专区目录→库再扫描 worker（池线程；UI 线程零扫描零 DB 写）。

    语义实现在 reco_ui.sync.sync_zone（纯层，独立测试钉死）；本类只做
    QRunnable 包装与异常→字符串收敛（§11.1 同款口径）。
    """

    def __init__(self, store, zone: str, data_root, *, stat_provider=None) -> None:
        super().__init__()
        self._store = store
        self._zone = zone
        self._data_root = data_root
        self._stat_provider = stat_provider
        self.signals = ScanSignals()
        _ACTIVE_IMPORTS.add(self)  # 复用扫描类 worker 持活注册表（生命周期同款）

    def run(self) -> None:
        from reco_ui.sync import sync_zone

        try:
            result = sync_zone(self._store, self._zone, self._data_root,
                               stat_provider=self._stat_provider)
            self._emit(result.added, result.removed, result.moved, result.changed, "")
        except Exception as exc:  # UI 只收字符串
            self._emit(0, 0, 0, 0, f"{type(exc).__name__}: {exc}")

    def _emit(self, added: int, removed: int, moved: int, changed: int, error: str) -> None:
        try:
            self.signals.done.emit(self._zone, added, removed, moved, changed, error)
        except RuntimeError:
            pass  # 接收方已销毁
        finally:
            _ACTIVE_IMPORTS.discard(self)
class LikeMoveSignals(QObject):
    done = pyqtSignal(bool, list, str)  # (ok, rows, error)  # (成功?, error)；error 非空 = 失败


class LikeMoveWorker(QRunnable):
    """「标为喜欢」真移动 worker（池线程：文件移动+落库都不在 UI 线程，§11.1）。

    成功路径：move_book_to_liked 一次完成「os.replace 真移动 + file_moved
    落库（zone 变更）」。失败路径：不落任何事件，error 字符串回 UI。
    """

    def __init__(self, store, src_zone: str, data_root: str, *, src_path: str | None = None,
                 row: int | None = None) -> None:
        super().__init__()
        self._store = store
        self._src_zone = src_zone
        self._data_root = str(data_root)
        self._src_path = src_path
        self._row = row
        self.signals = LikeMoveSignals()
        _ACTIVE_MOVES.add(self)

    def run(self) -> None:
        try:
            src_path = self._src_path if self._src_path is not None else self._lookup_source()
            if not src_path:
                self._emit(False, [], "源文件不在本专区")
                return
            move_book_to_liked(self._store, src_path,
                               self._data_root, from_zone=self._src_zone)
        except Exception as exc:  # UI 只收字符串
            self._emit(False, [], f"{type(exc).__name__}: {exc}")
            return
        self._emit(True, self._query_rows(), "")

    def _lookup_source(self) -> str:
        """按行号取本专区该书的现存路径（worker 线程，§11.1）。"""
        rows = zone_page_rows(self._store, self._src_zone)
        if self._row is None or not (0 <= int(self._row) < len(rows)):
            return ""
        book_id = int(rows[int(self._row)][0])
        conn = self._store.read_conn()
        try:
            row = conn.execute(
                "SELECT path FROM files WHERE book_id = ? AND zone = ? AND present = 1"
                " ORDER BY file_id LIMIT 1", (book_id, self._src_zone)).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row is not None else ""

    def _query_rows(self) -> list:
        """移动后重查本专区行（worker 线程，§11.1 UI 线程零 DB）。"""
        try:
            return list(zone_page_rows(self._store, self._src_zone))
        except Exception:
            return []

    def _emit(self, ok: bool, rows: list, error: str) -> None:
        try:
            self.signals.done.emit(ok, list(rows), error)
        except RuntimeError:
            pass  # 接收方已销毁
        finally:
            _ACTIVE_MOVES.discard(self)


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


class BookSourceSignals(QObject):
    """双击源查询信号：done(book_id, path, name, error)。"""

    done = pyqtSignal(int, str, str, str)


class BookSourceWorker(QRunnable):
    """双击打开阅读页的源查询 worker：行与源路径都在池线程读取（§11.1 UI 线程零 DB）。

    #58：原实现在 UI 线程调 zone_page_rows + read_conn；改为 worker 后
    UI 线程只接收结果并决定是否发 reader_requested。
    """

    def __init__(self, store, zone: str, row: int) -> None:
        super().__init__()
        self._store = store
        self._zone = zone
        self._row = int(row)
        self.signals = BookSourceSignals()
        _ACTIVE_LOADERS.add(self)

    def run(self) -> None:
        try:
            rows = zone_page_rows(self._store, self._zone)
        except Exception as exc:  # UI 只收字符串/类名
            self._emit(0, "", "", f"{type(exc).__name__}")
            return
        if not (0 <= self._row < len(rows)):
            self._emit(0, "", "", "")
            return
        book_id = int(rows[self._row][0])
        name = str(rows[self._row][1])
        path = self._lookup_source(book_id)
        self._emit(book_id, path, name, "")

    def _lookup_source(self, book_id: int) -> str:
        """本专区该书的现存路径；无 files(present=1) 行 → 空串（不猜名字映射）。"""
        conn = self._store.read_conn()
        try:
            row = conn.execute(
                "SELECT path FROM files WHERE book_id = ? AND zone = ? AND present = 1"
                " ORDER BY file_id LIMIT 1", (int(book_id), self._zone)).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row is not None else ""

    def _emit(self, book_id: int, path: str, name: str, error: str) -> None:
        try:
            self.signals.done.emit(int(book_id), path, name, error)
        except RuntimeError:
            pass  # UI 已销毁：迟到发射静默丢弃
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


def _makedirs_zone(data_root, zone: str) -> Path:
    """W19：移动目标的专区目录（用户点击即意图，目录缺失随移动创建）。"""
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / ZONE_DIR_NAMES[zone]
    target.mkdir(exist_ok=True)
    return target


def move_file_across_zones(src: Path, dst_dir: Path, dst_zone: str) -> Path:
    """真移动：os.replace 跨目录（同卷目录项操作，原子）；冲突确定性改名。

    改名对齐 W3 _conflict_free_name 语义（book(1).txt 递增），不覆盖既有文件。
    返回落位后的目标路径。
    """
    src, dst_dir = Path(src), Path(dst_dir)
    dst = dst_dir / src.name
    if dst.exists():
        stem, suffix = src.stem, src.suffix
        n = 1
        while (candidate := dst_dir / f"{stem}({n}){suffix}").exists():
            n += 1
        dst = candidate
    os.replace(src, dst)
    return dst


def move_book_to_liked(
    store: Store,
    src_path,
    data_root: str,
    *,
    from_zone: str = "candidate",
) -> Path:
    """「我喜欢」真移动唯一写入口（工单 19 N2）。

    先 db 找源（from_zone 内 path=src_path 且 present=1），零行=无事可做
    （重复点击/已移走：no-op 返回 src）；找到才动文件：os.replace 成功后
    append file_moved（payload 补 zone=新 zone）——文件系统与事件流同一
    事务语义（文件动了必然落库；落库失败则文件已动，读面以下次扫描/重试
    收敛，不在 UI 线程做反向补偿）。
    """
    from reco_ui.glue import zone_rows

    src = Path(src_path)
    rows = zone_rows(store, from_zone)
    book_id = next((int(r[0]) for r in rows
                    if Path(str(r[1])).name == src.name), None)
    if book_id is None:
        return src  # 源不在 from_zone（已移走/重复点击）：no-op 防护
    dst_dir = _makedirs_zone(data_root, "liked")
    if src.parent == dst_dir:
        return src  # 已在 liked：幂等 no-op（不重复移动、不落事件）
    st = src.stat()  # 源必须真实存在；缺失直接抛（诚实失败）
    dst = move_file_across_zones(src, dst_dir, "liked")
    if not os.environ.get("RECO_DISABLE_MOVE_EVENT"):  # 逆向验证开关（默认关）
        store.append_event("file_moved", payload={
            "path": str(src),
            "new_path": str(dst),
            "zone": "liked",
            "st_ino": int(st.st_ino),
        }, occurred_at=_utcnow_iso(), config_fingerprint="gui/move/1", actor="user")
    return dst


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
    W18 U2：加载中可见反馈 + 失败「重试」入口（mark_failed 不再是死页）。
    """

    recommend_requested = pyqtSignal()  # F3：candidate 页「开始推荐」点击
    reload_requested = pyqtSignal()     # U2：「重试」按钮 → 主窗重新发起加载
    refresh_requested = pyqtSignal()    # N1：「刷新」按钮 → 主窗发起目录→库再扫描
    reader_requested = pyqtSignal(str, object)  # RR1：双击作品 → 主窗打开阅读页 (path, book_id)

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
        # #66：悬停显示标签（provider 惰性查库，悬停时才触发）
        if store is not None:
            _store_ref = store

            def _tags_of(book_id, _s=_store_ref):
                return book_tags_display(_s, book_id)

            self._model.tag_provider = _tags_of
        self._table.setModel(self._model)
        self._table.doubleClicked.connect(self._on_double_clicked)  # RR1 T5：双击进阅读页
        layout.addWidget(self._table, 1)
        self._status = QLabel("待加载")
        self._status.setObjectName("dim")
        self.zone_changed = None  # W19 N2：跨页联动钩子（MainWindow 注入；页不认识兄弟页）
        layout.addWidget(self._status)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_retry = QPushButton("重试")  # W18 U2：失败后可见可点（默认隐藏）
        self.btn_retry.setObjectName("primary")
        self.btn_retry.clicked.connect(lambda: self.reload_requested.emit())
        self.btn_retry.hide()
        btn_row.addWidget(self.btn_retry)
        # N1：「刷新」在「打开文件夹」旁——目录→库再扫描落库后重查本页行。
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        btn_row.addWidget(self.btn_refresh)
        self.btn_open_folder = QPushButton("打开文件夹")
        self.btn_open_folder.clicked.connect(self._open_folder)
        btn_row.addWidget(self.btn_open_folder)
        if zone == "candidate":  # F3：仅候选页有「开始推荐」
            self.btn_recommend = QPushButton("开始推荐")
            self.btn_recommend.clicked.connect(self.recommend_requested.emit)
            btn_row.addWidget(self.btn_recommend)
        layout.addLayout(btn_row)

    def mark_loading(self) -> None:
        """W18 U2：加载中可见反馈——立即清「重试」并显示进行中状态。"""
        self._status.setText("加载中…")
        self.btn_retry.hide()

    def _open_folder(self) -> None:
        """「打开文件夹」按钮（W16 F2）：data_root + zone 映射，ensure_zones 创建。"""
        open_zone_folder(self._data_root, self.zone)

    # —— RR1 T5：双击作品 → 阅读页（查源路径；无源则状态行诚实提示）——
    def _present_path(self, book_id: int) -> str | None:
        """本专区该书的现存路径；无 files(present=1) 行 → None（不猜名字映射）。"""
        if self._store is None:
            return None
        conn = self._store.read_conn()
        try:
            row = conn.execute(
                "SELECT path FROM files WHERE book_id = ? AND zone = ? AND present = 1"
                " ORDER BY file_id LIMIT 1", (int(book_id), self.zone)).fetchone()
        finally:
            conn.close()
        return str(row[0]) if row is not None else None

    def _on_double_clicked(self, index) -> None:
        """双击行 → reader_requested(path, book_id)；行已失效/无源文件只报状态。

        双击是用户即时手势：行与源路径同步取（单行点查，耗时可忽略），
        打开后的正文加载才走 worker（ReaderLoadWorker）。#47 只覆盖导入回调
        与移动发起两处 UI 线程读库，此处不在其范围。
        """
        if self._store is None or index is None or not index.isValid():
            return
        row = index.row()
        if not (0 <= row < self._model.rowCount()):
            return
        self._status.setText("打开中…")
        worker = BookSourceWorker(self._store, self.zone, row)
        worker.signals.done.connect(self._on_source_ready)
        QThreadPool.globalInstance().start(worker)

    def _on_source_ready(self, book_id: int, path: str, name: str, error: str) -> None:
        """源查询结果 → 决定是否打开阅读页；缺失/失败只报状态（正文不上屏，§R3）。"""
        if error:
            self._status.setText(f"打开失败：{error}")
            return
        if not path:
            return
        if not os.path.isfile(path):  # 已移动/删除：不打开、只诚实提示
            self._status.setText(f"文件缺失，无法打开：{name}")
            return
        self.reader_requested.emit(path, int(book_id))

    # —— W16 F1：拖拽导入 ——
    def dragEnterEvent(self, event) -> None:  # noqa: N802（Qt 命名）
        """扩展名白名单预筛（目录/非法扩展忽略，不进入 drop）。"""
        from reco_ui.qt import QUrl

        for url in event.mimeData().urls():
            path = QUrl(url).toLocalFile()
            if os.path.isfile(path) and os.path.splitext(path)[1].lower() in ALLOWED_IMPORT_EXTS:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802（Qt 命名）
        """drop → 池线程导入 worker（UI 零阻塞）；完成回调刷新表格。"""
        from reco_ui.qt import QUrl

        paths = [QUrl(u).toLocalFile() for u in event.mimeData().urls()]
        event.acceptProposedAction()
        self._start_import(paths)

    def _start_import(self, paths) -> None:
        """发起导入：worker 池线程执行，完成信号回 UI 刷新（复用切片 3 回调模式）。"""
        if self._store is None:
            return
        self._status.setText(f"导入中（{len(paths)} 项）…")
        worker = ImportWorker(self._store, self.zone, self._data_root, paths)
        worker.signals.done.connect(self._on_import_done)
        QThreadPool.globalInstance().start(worker)

    # —— 工单 19 N2：右键「标为喜欢」真移动 ——
    def move_selected_book_to_liked(self, row: int) -> bool:
        """发起真移动：行与源路径查询在池线程（§11.1），移动+落库同在 worker。

        返回 False = 无选中/空表（零副作用）；True = 已发起（结果经完成信号回传）。
        """
        if self._store is None:
            return False
        if not (0 <= row < self._model.rowCount()):
            return False
        self._status.setText("移动中…")
        worker = LikeMoveWorker(self._store, self.zone, self._data_root, row=row)
        worker.signals.done.connect(self._on_move_done)
        QThreadPool.globalInstance().start(worker)
        return True

    def _on_move_done(self, ok: bool, rows: list, error: str) -> None:
        """移动完成 → 渲染 worker 回传的行（行重查在 worker 线程，§11.1）。"""
        if not ok:
            self._status.setText(f"移动失败（可重试）：{error}")
            return
        self.apply_rows(rows)
        if self.zone_changed is not None:
            self.zone_changed(self)  # 对侧专区页同步重查（两表格联动，MainWindow 注入）
        self._status.setText("已移到喜欢专区")

    def _on_import_done(self, imported: int, skipped: int, rejected: int, rows: list,
                        error: str) -> None:
        """导入完成 → 渲染 worker 回传的行（行重查在 worker 线程，§11.1）。"""
        if error:
            self._status.setText(f"导入失败（可重试）：{error}")
            return
        self.apply_rows(rows)
        parts = [f"导入 {imported}"]
        if skipped:
            parts.append(f"跳过 {skipped}")
        if rejected:
            parts.append(f"拒绝 {rejected}")
        self._status.setText("，".join(parts))

    def load_now(self) -> None:
        """同步装填（分级超时默认档；成功后才置「已加载」，坑 3）。"""
        try:
            with perf_span(f"ui.load.{self.zone}"):
                rows = zone_page_rows(self._store, self.zone)
        except TimeoutError as exc:
            self.mark_failed(f"加载超时：{exc}")
            return
        self._model.set_rows(rows)
        self.loaded = True
        self._status.setText("已加载")
        self.btn_retry.hide()

    def apply_rows(self, rows) -> None:
        """worker 回调装填（UI 线程：只渲染）。"""
        self._model.set_rows(rows)
        self.loaded = True
        self._status.setText("已加载")
        self.btn_retry.hide()

    def mark_failed(self, message: str) -> None:
        """失败不置「已加载」，亮出「重试」按钮（W18 U2：不再是死页）。"""
        self._status.setText(f"加载失败（可重试）：{message}")
        self.btn_retry.show()

    def row_count(self) -> int:
        return self._model.rowCount()

    def build_context_menu(self) -> QMenu:
        """每次新构建（不缓存实例 → 无悬挂 QMenu，坑 7 幂等）。"""
        menu = QMenu(self)
        if self.zone == "candidate":
            like_action = menu.addAction("标为喜欢")
            like_action.triggered.connect(
                lambda: self.move_selected_book_to_liked(
                    self._table.currentIndex().row()))
            menu.addAction("不合口味")
        else:
            menu.addAction("移到候选池")
        return menu


# ---------------------------------------------------------------- 标签悬停（#66）
def book_tags_display(store, book_id: int) -> list[str]:
    """某本书的标签**显示名**列表（悬停 tooltip 用）。

    join book_tags → tags 取 label（无 label 时回退 tag_id）；按 tag_id 排序保证稳定。
    无标签/无库 → 空列表（诚实降级，不伪造）。
    """
    if store is None:
        return []
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT t.tag_id, t.label FROM book_tags bt"
            " JOIN tags t ON t.tag_id = bt.tag_id"
            " WHERE bt.book_id = ? ORDER BY t.tag_id", (int(book_id),)).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    out: list[str] = []
    for tag_id, label in rows:
        name = str(label) if label else str(tag_id)
        if name and name not in out:
            out.append(name)
    return out


# ---------------------------------------------------------------- proposals 待确认（#48）
def proposal_rows(store) -> list[dict]:
    """proposals 待确认列表（UI 行）：只读 pending，candidate/count 从 payload 取。

    无 Store 或无提案 → 空列表（诚实降级，不伪造行）。
    """
    if store is None:
        return []
    from reco_labels.auto_tag import StoreAdapter

    adapter = StoreAdapter(store)
    rows: list[dict] = []
    for p in adapter.iter_proposals():
        if p.status != "pending":
            continue
        payload = dict(p.payload or {})
        rows.append({
            "proposal_id": p.proposal_id,
            "candidate": str(payload.get("candidate") or p.candidate or ""),
            "count": int(payload.get("count") or 0),
            "threshold": int(payload.get("threshold") or 0),
            "kind": p.kind,
            "status": p.status,
            "created_at": p.created_at,
        })
    return rows
