"""RR1 阅读页 ReaderPage（TXT 主路径；工单 #36 / spec #33）。

与 reader.py 纯函数分层：reader.py 保持零 Qt（T2 金丝雀 test_reader_module_is_qt_free
在子进程断言 import reco_ui.reader 后 sys.modules 无 PyQt6/reco_ui.qt），因此渲染层
独立成本模块——Qt 一律经 reco_ui.qt 薄适配层（§11.5），本模块不 import PyQt6。

线程模型（§11.1）：UI 线程只发起 + 渲染；读文件/解码/分块在 QThreadPool worker：
- reader.load            worker 内 load（load_book_text + chunk_text，或 EPUB spine 解析）
- reader.first_frame     open() 发起 → UI 首帧安装完成的端到端时延（含 worker 加载）
- reader.chunk.append    滚动接近底部时追加下一块的 UI 耗时

进度（T1 store API）：QTimer(1000ms) singleShot 节流保存 + flush_progress()；
char_offset = textCursor().position()，percent = offset / len(text)；零事件（§R4）。

正文边界（§R3/B1）：状态行失败提示只含文件名 + 枚举（ReaderError.__str__），
worker 的其余异常只报异常类名，绝不回显底层消息（可能含正文片段）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reco_io.perf import perf_span
from reco_ui.qt import (
    QLabel,
    QObject,
    QPlainTextEdit,
    QPushButton,
    QRunnable,
    QTextCursor,
    QThreadPool,
    QTimer,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from reco_ui.reader import ReaderError, chunk_text, load_book_text, parse_epub_spine

__all__ = ["ReaderLoadSignals", "ReaderLoadWorker", "ReaderPage"]

_SAVE_DELAY_MS = 1000  # 滚动停止 1s 后落进度（票面口径）
_APPEND_THRESHOLD_PX = 200  # 距底部小于该像素即预取下一块

# 运行中/排队中的 worker 持活注册表：QThreadPool 不持 Python 包装引用，
# 窗口被 GC 后排队 worker 的包装会悬垂（pages.py 同款生命周期纪律）。
_ACTIVE_READERS: set = set()


class ReaderLoadSignals(QObject):
    """加载 worker 信号：loaded(payload dict | None, error str)。"""

    loaded = pyqtSignal(object, str)


def _error_text(path: str, exc: BaseException) -> str:
    """异常 → 状态行文本：只允许文件名 + 枚举/类名（正文绝不入提示）。"""
    if isinstance(exc, ReaderError):
        return str(exc)  # "name: reason"（reader.ReaderError 已只暴露文件名+枚举）
    return f"{Path(path).name}: {type(exc).__name__}"


def _load_payload(path: str) -> dict:
    """池线程加载口径：text 格式全量解码+分块；EPUB 走 spine 解析（T4 接逐章导航）。"""
    source = Path(path)
    if source.suffix.lower() == ".epub":
        chapters = parse_epub_spine(source)
        first = chapters[0]
        text = str(first.get("text") or "")
        return {
            "format": "epub",
            "chapters": chapters,
            "text": text,
            "chunks": chunk_text(text),
            "title_hint": str(first.get("title") or ""),
        }
    text = load_book_text(source)
    return {"format": "text", "chapters": (), "text": text, "chunks": chunk_text(text),
            "title_hint": ""}


class ReaderLoadWorker(QRunnable):
    """读文件/解码/分块 worker（池线程；UI 线程零 IO 零解码，§11.1）。"""

    def __init__(self, path, store=None, book_id=None) -> None:
        super().__init__()
        self._path = str(path)
        self._store = store
        self._book_id = book_id
        self.signals = ReaderLoadSignals()
        _ACTIVE_READERS.add(self)

    def run(self) -> None:
        try:
            progress = None
            if self._store is not None and self._book_id is not None:
                progress = self._store.get_reading_progress(int(self._book_id))
            with perf_span("reader.load"):
                payload = _load_payload(self._path)
            payload["progress"] = progress
        except ReaderError as exc:
            self._emit(None, str(exc))
            return
        except Exception as exc:  # UI 只收字符串，不收异常对象
            self._emit(None, _error_text(self._path, exc))
            return
        self._emit(payload, "")

    def _emit(self, payload, error: str) -> None:
        try:
            self.signals.loaded.emit(payload, error)
        except RuntimeError:
            pass  # 接收方已销毁
        finally:
            _ACTIVE_READERS.discard(self)


class ReaderPage(QWidget):
    """阅读页：书名+percent 标题、只读正文区、状态行、返回按钮（同一实例换书）。"""

    back_requested = pyqtSignal()

    def __init__(self, store=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._book_id: int | None = None
        self._path: str | None = None
        self._format = "text"
        self._chapters: tuple = ()
        self._text = ""
        self._chunks: list[str] = []
        self._chunk_index = 0
        self._loaded_chars = 0
        self._total_chars = 0
        self._loaded = False
        self._token = 0  # open 代际：迟到的旧 worker 结果直接丢弃
        self._worker: ReaderLoadWorker | None = None
        self._first_frame: perf_span | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save_progress)
        self._title = QLabel()
        self._title.setObjectName("page-title")
        self._title.setWordWrap(True)
        self._status = QLabel()
        self._status.setObjectName("dim")
        self._viewer = QPlainTextEdit()
        self._viewer.setReadOnly(True)
        self._viewer.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._back = QPushButton("返回")
        self._back.clicked.connect(self._on_back)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._title)
        layout.addWidget(self._viewer, 1)
        layout.addWidget(self._status)
        layout.addWidget(self._back)

    # ---------------------------------------------------------- 对外读取面（测试/主窗）
    @property
    def viewer(self) -> QPlainTextEdit:
        return self._viewer

    @property
    def status_text(self) -> str:
        return self._status.text()

    @property
    def title_text(self) -> str:
        return self._title.text()

    @property
    def loaded(self) -> bool:
        return self._loaded

    # ---------------------------------------------------------- 打开/关闭
    def open_book(self, path, book_id) -> None:
        """打开一本书；若上一本已加载则先 flush 其进度。"""
        if self._loaded:
            self.flush_progress()
        self._close_first_frame()
        self._token += 1
        token = self._token
        self._book_id = int(book_id)
        self._path = str(path)
        self._format = "text"
        self._chapters = ()
        self._text = ""
        self._chunks = []
        self._chunk_index = 0
        self._loaded_chars = 0
        self._total_chars = 0
        self._loaded = False
        self._viewer.clear()
        self._save_timer.stop()
        self._title.setText(Path(self._path).name)
        self._status.setText("加载中…")
        # 首帧 span 跨线程：open 发起即进，UI 首帧安装完成才出（端到端时延）。
        self._first_frame = perf_span("reader.first_frame")
        self._first_frame.__enter__()
        worker = ReaderLoadWorker(self._path, self._store, self._book_id)
        worker.signals.loaded.connect(
            lambda payload, error, t=token: self._on_loaded(t, payload, error)
        )
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def close_book(self) -> None:
        """关闭当前书：flush 进度、丢弃在途加载结果、清空视图。"""
        self.flush_progress()
        self._save_timer.stop()
        self._token += 1
        self._close_first_frame()
        self._loaded = False
        self._path = None
        self._book_id = None
        self._chapters = ()
        self._text = ""
        self._chunks = []
        self._chunk_index = 0
        self._loaded_chars = 0
        self._total_chars = 0
        self._viewer.clear()
        self._title.clear()
        self._status.clear()

    def _on_back(self) -> None:
        self.flush_progress()
        self.back_requested.emit()

    # ---------------------------------------------------------- 加载回调 / 首帧
    def _on_loaded(self, token: int, payload, error: str) -> None:
        if token != self._token:
            return  # 已被后一次 open 取代：丢弃迟到结果
        self._worker = None
        if error:
            self._close_first_frame()
            name = Path(self._path).name if self._path else ""
            self._title.setText(name)
            self._status.setText(f"打开失败：{error}")
            return
        self._format = str(payload.get("format") or "text")
        self._chapters = tuple(payload.get("chapters") or ())
        self._text = str(payload.get("text") or "")
        self._total_chars = len(self._text)
        self._chunks = list(payload.get("chunks") or [])
        self._install_first_frame(payload.get("progress"))
        self._loaded = True
        self._status.setText("")

    def _install_first_frame(self, progress) -> None:
        """只装 chunk[0] 即完成首帧；续读时把覆盖目标偏移的块全部装入后再定位光标。"""
        target = int((progress or {}).get("char_offset") or 0)
        first = self._chunks[0] if self._chunks else ""
        self._viewer.setPlainText(first)
        self._chunk_index = 0
        self._loaded_chars = len(first)
        while target > self._loaded_chars and self._chunk_index + 1 < len(self._chunks):
            self._append_next_chunk(record_span=False)
        if target > 0:
            self._move_cursor_to(min(target, self._loaded_chars))
        self._close_first_frame()  # reader.first_frame 到此为止
        self._update_title()

    def _close_first_frame(self) -> None:
        span, self._first_frame = self._first_frame, None
        if span is not None:
            span.__exit__(None, None, None)

    def _move_cursor_to(self, offset: int) -> None:
        limit = max(0, self._viewer.document().characterCount() - 1)
        cursor = self._viewer.textCursor()
        cursor.setPosition(max(0, min(int(offset), limit)))
        self._viewer.setTextCursor(cursor)
        self._viewer.centerCursor()

    # ---------------------------------------------------------- 分块追加
    def _on_scroll(self, _value: int) -> None:
        if not self._loaded:
            return
        self._save_timer.start()  # 滚动重启 1s 节流
        self._append_while_near_bottom()

    def _near_bottom(self) -> bool:
        bar = self._viewer.verticalScrollBar()
        return bar.maximum() - bar.value() <= _APPEND_THRESHOLD_PX

    def _append_while_near_bottom(self) -> None:
        while self._near_bottom() and self._append_next_chunk():
            pass

    def _append_next_chunk(self, *, record_span: bool = True) -> bool:
        next_index = self._chunk_index + 1
        if next_index >= len(self._chunks):
            return False
        chunk = self._chunks[next_index]
        bar = self._viewer.verticalScrollBar()
        previous = bar.value()
        if record_span:
            with perf_span("reader.chunk.append"):
                self._insert_tail(chunk)
        else:
            self._insert_tail(chunk)
        self._chunk_index = next_index
        self._loaded_chars += len(chunk)
        bar.setValue(previous)  # 防视口跳动：追加后回设旧滚动值（RS1 注记 4）
        return True

    def _insert_tail(self, text: str) -> None:
        """只在尾部 append（RS1：绝不头部插入，会推走已读内容并触发全文档重排）。"""
        cursor = QTextCursor(self._viewer.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)

    # ---------------------------------------------------------- 进度
    def _percent_for(self, offset: int) -> float:
        if self._format == "epub" and self._chapters:
            return min(1.0, 1.0 / len(self._chapters))  # T4：改为按 spine 序累计
        if self._total_chars <= 0:
            return 0.0
        return max(0.0, min(1.0, offset / self._total_chars))

    def _save_progress(self) -> None:
        if not self._loaded or self._store is None or self._book_id is None:
            return
        offset = self._viewer.textCursor().position()
        percent = self._percent_for(offset)
        fields = {
            "percent": percent,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if self._format == "epub":
            fields["epub_spine_index"] = 0
            fields["epub_block_pos"] = int(self._viewer.verticalScrollBar().value())
        else:
            fields["char_offset"] = offset
        ext = Path(self._path).suffix.lower().lstrip(".") if self._path else ""
        try:
            self._store.upsert_reading_progress(self._book_id, ext=ext, **fields)
        except Exception as exc:  # UI 只收字符串
            self._status.setText(f"进度保存失败：{type(exc).__name__}")
            return
        self._update_title(percent)

    def flush_progress(self) -> None:
        """节流计时器未到期也要落盘（返回/关窗/换书路径）。"""
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save_progress()

    def _update_title(self, percent: float | None = None) -> None:
        if self._path is None:
            self._title.clear()
            return
        if percent is None:
            percent = self._percent_for(self._viewer.textCursor().position())
        name = Path(self._path).name
        suffix = ""
        if self._format == "epub" and self._chapters:
            suffix = f" · {self._chapters[0].get('title', '')}"
        self._title.setText(f"{name} · {int(percent * 100)}%{suffix}")
