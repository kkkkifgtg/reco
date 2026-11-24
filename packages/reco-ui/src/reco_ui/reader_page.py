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

import sys

from datetime import datetime, timezone
from pathlib import Path

from reco_io.perf import perf_span
from reco_ui.qt import (
    QHBoxLayout,
    QLabel,
    QObject,
    QPlainTextEdit,
    QPushButton,
    QRunnable,
    QStackedWidget,
    QTextBrowser,
    QTextCursor,
    QThreadPool,
    QTimer,
    QVBoxLayout,
    QWidget,
    pyqtSignal,
)
from reco_ui.reader import ReaderError, chunk_text, load_book_text, parse_epub_spine

__all__ = ["ReaderLoadSignals", "ReaderLoadWorker", "ReaderPage",
           "ReaderLikeMoveSignals", "ReaderLikeMoveWorker"]

_SAVE_DELAY_MS = 1000  # 滚动停止 1s 后落进度（票面口径）
_APPEND_THRESHOLD_PX = 200  # 距底部小于该像素即预取下一块

# 运行中/排队中的 worker 持活注册表：QThreadPool 不持 Python 包装引用，
# 窗口被 GC 后排队 worker 的包装会悬垂（pages.py 同款生命周期纪律）。
_ACTIVE_READERS: set = set()
_ACTIVE_READER_MOVES: set = set()  # #80：阅读页「标为喜欢」真移动 worker 持活


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
        with perf_span("reader.epub.parse"):
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


class ReaderLikeMoveSignals(QObject):
    """阅读页「标为喜欢」完成信号：(ok, moved, error)。

    ok=False → 失败（error 非空）；ok=True 且 moved=True → 真移动落位；
    ok=True 且 moved=False → 幂等 no-op（书已在喜欢专区，无位移无事件）。
    """

    done = pyqtSignal(bool, bool, str)


class ReaderLikeMoveWorker(QRunnable):
    """#80：阅读页「标为喜欢」真移动 worker（池线程：查源+移动+落库，§11.1）。

    源查询优先取 zone='candidate' 的 files 行（PR #78 口径）：同一本书在
    liked/ 与 candidate/ 各有一份时，移动的是 candidate 那份，否则会被
    move_book_to_liked 判为幂等 no-op，文件永远不进 liked/。无 candidate 行
    再回退任意现存专区行；都没有则诚实降级（不移动、不落事件）。
    """

    def __init__(self, store, book_id: int, data_root: str) -> None:
        super().__init__()
        self._store = store
        self._book_id = int(book_id)
        self._data_root = str(data_root)
        self.signals = ReaderLikeMoveSignals()
        _ACTIVE_READER_MOVES.add(self)

    def run(self) -> None:
        from pathlib import Path

        from reco_ui.pages import move_book_to_liked

        # F3：data_root 未注入时绝不移动——move_book_to_liked 会以 CWD 为根
        # 凭空建 liked/ 目录（用户可见的副作用），此处提前诚实降级。
        if not self._data_root:
            self._emit(False, False, "未配置数据目录，无法移动到喜欢专区")
            return
        try:
            row = self._lookup_source()
            if row is None:
                self._emit(False, False, "本机已找不到这本书的文件")
                return
            src_path, zone = row
            dst = move_book_to_liked(self._store, src_path, self._data_root,
                                     from_zone=zone, book_id=self._book_id)
        except Exception as exc:  # UI 只收字符串
            self._emit(False, False, f"{type(exc).__name__}: {exc}")
            return
        # F2：move_book_to_liked 返回原路径 = 幂等 no-op（已在喜欢专区/源已移走），
        # 此时不能谎称「已移到喜欢专区」。
        moved = Path(str(dst)) != Path(src_path)
        if moved:
            # #85：标为喜欢后打通 提取→挖掘→打标→画像折叠（失败不影响移动结果）
            try:
                from reco_ui.tagging import process_book_for_profile

                process_book_for_profile(
                    self._store, str(dst), "liked",
                    now=datetime.now(timezone.utc).isoformat())
            except Exception as exc:
                print(f"warn: 标为喜欢打标失败 book={self._book_id}: {exc}",
                      file=sys.stderr)
        self._emit(True, moved, "")

    def _lookup_source(self):
        """(path, zone) 候选源；优先 candidate 行，其次任意现存专区行。"""
        conn = self._store.read_conn()
        try:
            row = conn.execute(
                "SELECT path, zone FROM files WHERE book_id = ? AND present = 1"
                " AND zone = 'candidate' LIMIT 1", (self._book_id,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT path, zone FROM files WHERE book_id = ? AND present = 1"
                    " ORDER BY file_id LIMIT 1", (self._book_id,)).fetchone()
        finally:
            conn.close()
        return (str(row[0]), str(row[1])) if row is not None else None

    def _emit(self, ok: bool, moved: bool, error: str) -> None:
        try:
            self.signals.done.emit(ok, moved, error)
        except RuntimeError:
            pass  # 接收方已销毁
        finally:
            _ACTIVE_READER_MOVES.discard(self)


class ReaderPage(QWidget):
    """阅读页：书名+percent 标题、只读正文区、状态行、返回按钮（同一实例换书）。

    #80：「标为喜欢」「不合口味」两个反馈入口只在本页出现；从「我喜欢」专区
    （liked）进入阅读时不显示（来源专区由 open_book 的 from_zone 传入）。
    """

    back_requested = pyqtSignal()
    zone_moved = pyqtSignal()  # #80：「标为喜欢」真移动完成 → 主窗刷新专区页

    def __init__(self, store=None, parent: QWidget | None = None, *,
                 data_root=None) -> None:
        super().__init__(parent)
        self._store = store
        self._data_root = data_root  # 「标为喜欢」真移动的目标根目录（liked/）
        # #80：反馈按钮默认隐藏；open_book 按来源专区重算（从「我喜欢」进阅读不显示）
        self._feedback_visible = False
        self._feedback_inflight = False
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
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._viewer)
        self._stack.addWidget(self._browser)
        self._btn_prev = QPushButton("上一章")
        self._btn_prev.clicked.connect(self.prev_chapter)
        self._btn_next = QPushButton("下一章")
        self._btn_next.clicked.connect(self.next_chapter)
        self._back = QPushButton("返回")
        self._back.clicked.connect(self._on_back)
        # #80：「标为喜欢」「不合口味」只出现在阅读界面（列表层入口已移除）
        self._btn_like = QPushButton("标为喜欢")
        self._btn_like.setObjectName("primary")
        self._btn_like.clicked.connect(self._on_like_clicked)
        self._btn_dislike = QPushButton("不合口味")
        self._btn_dislike.setObjectName("danger")
        self._btn_dislike.clicked.connect(self._on_dislike_clicked)
        self._btn_like.setVisible(False)
        self._btn_dislike.setVisible(False)
        nav = QHBoxLayout()
        nav.addWidget(self._back)
        nav.addStretch(1)
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._btn_next)
        nav.addWidget(self._btn_like)
        nav.addWidget(self._btn_dislike)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._title)
        layout.addWidget(self._stack, 1)
        layout.addWidget(self._status)
        layout.addLayout(nav)
        self._update_chapter_buttons()

    # ---------------------------------------------------------- 对外读取面（测试/主窗）
    @property
    def viewer(self) -> QPlainTextEdit:
        return self._viewer

    @property
    def browser(self) -> QTextBrowser:
        return self._browser

    @property
    def chapter_index(self) -> int:
        return self._chapter_index

    @property
    def status_text(self) -> str:
        return self._status.text()

    @property
    def title_text(self) -> str:
        return self._title.text()

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def like_button(self) -> QPushButton:
        """「标为喜欢」按钮（测试/验收读取面；#80）。"""
        return self._btn_like

    @property
    def dislike_button(self) -> QPushButton:
        """「不合口味」按钮（测试/验收读取面；#80）。"""
        return self._btn_dislike

    # ---------------------------------------------------------- 打开/关闭
    def open_book(self, path, book_id, *, from_zone=None) -> None:
        """打开一本书；若上一本已加载则先 flush 其进度。

        from_zone = 来源专区（"liked"/"candidate"/"recommend"）。#80：每次换书
        都要重算反馈按钮显隐——从「我喜欢」专区（liked）进入阅读不显示这两个按钮。
        """
        self.set_feedback_visible(from_zone != "liked")
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
        self._chapter_index = 0
        self._pending_block_pos = None
        self._viewer.clear()
        self._browser.clear()
        self._stack.setCurrentWidget(self._viewer)
        self._update_chapter_buttons()
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
        self._chapter_index = 0
        self._pending_block_pos = None
        self._viewer.clear()
        self._browser.clear()
        self._stack.setCurrentWidget(self._viewer)
        self._update_chapter_buttons()
        self._title.clear()
        self._status.clear()
        self.set_feedback_visible(False)  # 离开阅读即收起反馈入口

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
        if self._format == "epub":
            self._install_epub(payload.get("progress"))
        else:
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

    def showEvent(self, event) -> None:
        """重新显示时校准恢复的章内滚动值（首次 open 可能早于页面 show）。"""
        super().showEvent(event)
        if self._format == "epub" and self._pending_block_pos is not None:
            bar = self._browser.verticalScrollBar()
            bar.setValue(max(0, min(self._pending_block_pos, bar.maximum())))

    # ---------------------------------------------------------- 分块追加
    def _on_scroll(self, _value: int) -> None:
        if not self._loaded:
            return
        self._save_timer.start()  # 滚动重启 1s 节流
        if self._format == "epub":
            self._pending_block_pos = None  # 用户已自行滚动，别在 show 时回设旧位置
            return
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

    # ---------------------------------------------------------- EPUB 章节
    def _install_epub(self, progress) -> None:
        """EPUB 首帧：按 spine 序装目标章 HTML；进度命中则恢复章内滚动值。"""
        self._stack.setCurrentWidget(self._browser)
        index = int((progress or {}).get("epub_spine_index") or 0)
        self._chapter_index = max(0, min(index, len(self._chapters) - 1))
        block_pos = int((progress or {}).get("epub_block_pos") or 0)
        self._render_chapter(self._chapter_index, block_pos)
        self._pending_block_pos = block_pos
        self._update_chapter_buttons()
        self._close_first_frame()  # reader.first_frame 到此为止
        self._update_title()

    def _render_chapter(self, index: int, block_pos: int = 0) -> None:
        """单章 HTML 灌入 QTextBrowser（绝不整本灌入）；强制排版后恢复章内滚动值。"""
        chapter = self._chapters[index]
        self._browser.setHtml(str(chapter.get("html") or ""))
        document = self._browser.document()
        width = self._browser.viewport().width()
        document.setTextWidth(width if width > 0 else 640)
        document.documentLayout().documentSize()  # 强制排版：未 show 也能定位滚动范围
        bar = self._browser.verticalScrollBar()
        bar.setValue(max(0, min(int(block_pos or 0), bar.maximum())))

    def prev_chapter(self) -> None:
        self._switch_chapter(self._chapter_index - 1)

    def next_chapter(self) -> None:
        self._switch_chapter(self._chapter_index + 1)

    def _switch_chapter(self, index: int) -> None:
        if self._format != "epub" or not self._chapters:
            return
        if not 0 <= index < len(self._chapters):
            return
        self._chapter_index = index
        self._pending_block_pos = 0
        self._render_chapter(index, 0)
        self._update_chapter_buttons()
        self._update_title()
        if self._loaded:
            self._save_progress()  # 翻章即落盘（票面：翻页/关窗时保存）

    def _update_chapter_buttons(self) -> None:
        is_epub = self._format == "epub" and bool(self._chapters)
        self._btn_prev.setVisible(is_epub)
        self._btn_next.setVisible(is_epub)
        if is_epub:
            self._btn_prev.setEnabled(self._chapter_index > 0)
            self._btn_next.setEnabled(self._chapter_index < len(self._chapters) - 1)

    # ---------------------------------------------------------- 进度
    def _percent_for(self, offset: int) -> float:
        if self._format == "epub" and self._chapters:
            return min(1.0, (self._chapter_index + 1) / len(self._chapters))
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
            fields["epub_spine_index"] = self._chapter_index
            fields["epub_block_pos"] = int(self._browser.verticalScrollBar().value())
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

    # ---------------------------------------------------------- 反馈入口（#80）
    def set_feedback_visible(self, visible: bool) -> None:
        """显示/隐藏两个反馈按钮（来源专区判定结果；换书时重算）。"""
        self._feedback_visible = bool(visible)
        self._btn_like.setVisible(self._feedback_visible)
        self._btn_dislike.setVisible(self._feedback_visible)

    def feedback_visible(self) -> bool:
        """反馈按钮当前是否可见（测试/验收读取面）。"""
        return self._feedback_visible

    def _on_like_clicked(self) -> None:
        """「标为喜欢」：真移动进 liked/（池线程；UI 线程零 DB 零阻塞，§11.1）。"""
        if self._store is None or self._book_id is None or self._feedback_inflight:
            return
        # F3：data_root 未注入 → 诚实降级（不移动、不在 CWD 建目录）
        if not self._data_root:
            self._status.setText("未配置数据目录，无法移动到喜欢专区")
            return
        self._set_feedback_buttons_enabled(False)
        worker = ReaderLikeMoveWorker(self._store, self._book_id,
                                      str(self._data_root))
        worker.signals.done.connect(self._on_like_move_done)
        QThreadPool.globalInstance().start(worker)

    def _on_like_move_done(self, ok: bool, moved: bool, error: str) -> None:
        self._set_feedback_buttons_enabled(True)
        if not ok:
            self._status.setText(f"移动失败（可重试）：{error}")
            return
        if not moved:
            # F2：幂等 no-op（书已在喜欢专区）——不谎称已移动
            self._status.setText("这本已在喜欢专区，无需重复移动")
            return
        self._status.setText("已移到喜欢专区")
        try:
            self.zone_moved.emit()
        except RuntimeError:
            pass  # 接收方已销毁

    def _on_dislike_clicked(self) -> None:
        """「不合口味」：写库（rec_feedback + 静音窗口）在池线程（§11.1）。"""
        if self._store is None or self._book_id is None or self._feedback_inflight:
            return
        self._set_feedback_buttons_enabled(False)
        from reco_ui.pages import FeedbackWorker

        worker = FeedbackWorker(self._store, "dislike", self._book_id, axis="tag")
        worker.signals.done.connect(self._on_dislike_done)
        QThreadPool.globalInstance().start(worker)

    def _on_dislike_done(self, event_id: int, error: str) -> None:
        self._set_feedback_buttons_enabled(True)
        self._status.setText("已记录" if not error else f"反馈失败（可重试）：{error}")

    def _set_feedback_buttons_enabled(self, enabled: bool) -> None:
        """in-flight 期间禁用两按钮，防双写（连点防护）。"""
        self._feedback_inflight = not enabled
        self._btn_like.setEnabled(enabled)
        self._btn_dislike.setEnabled(enabled)

    def _update_title(self, percent: float | None = None) -> None:
        if self._path is None:
            self._title.clear()
            return
        if percent is None:
            percent = self._percent_for(self._viewer.textCursor().position())
        name = Path(self._path).name
        suffix = ""
        if self._format == "epub" and self._chapters:
            chapter = self._chapters[self._chapter_index]
            suffix = (f" · {chapter.get('title', '')}"
                      f" · 第 {self._chapter_index + 1}/{len(self._chapters)} 章")
        self._title.setText(f"{name} · {int(percent * 100)}%{suffix}")
