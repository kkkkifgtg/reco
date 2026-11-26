"""RR1/t3 红测试：ReaderPage TXT 主路径（工单 #36 / spec #33）。

契约：
- qt.py 导出 QPlainTextEdit/QTextBrowser/QTextCursor/QScrollBar（§11.5 后仍只有 qt.py 碰 PyQt6）；
- open txt → 首帧（chunk[0]）出现，其余块接近底部滚动追加；
- 10MB 合成 TXT 首帧 < 500ms，且首帧后文本长度 < 全文（分块生效）；
- 预存进度重开：先装入覆盖目标偏移的块再定位 cursor（不 sleep，直断言位置）；
- 打开坏文件/缺失文件 → 状态行仅文件名 + 枚举（金丝雀正文绝不上屏）；
- 进度 QTimer 节流 + flush 落 T1 reading_progress；保存零事件（§R4）；
- 四格式 open+close 后 tmp_path 零落盘（§R2）。

夹具全合成（R8/B5）：TXT 现写；DOCX 用 python-docx；PDF 手搓最小文本 PDF；
EPUB 用 zipfile 手搓最小容器（与 test_reader_pure 同法，本文件自带工厂）。
"""
from __future__ import annotations

import contextlib
import io
import os
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

CANARY = "金丝雀绝密正文"
NOW = "2026-11-22T02:00:00+00:00"


# ---------------------------------------------------------- 合成样例工厂


def _write_txt(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _make_text(total_chars: int) -> str:
    """≥ total_chars 的确定性多段文本（每段 600 字，段界 \\n\\n）。"""
    para = "文" * 598
    paragraphs: list[str] = []
    size = 0
    index = 0
    while size < total_chars:
        piece = f"{index:04d}" + para
        paragraphs.append(piece)
        size += len(piece) + 2
        index += 1
    return "\n\n".join(paragraphs)


def make_pdf_with_text(path: Path, text: str) -> Path:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n",
        b"4 0 obj<</Length " + str(len(content)).encode() + b">>stream\n" + content + b"\nendstream\nendobj\n",
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out += obj
    xref_pos = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    path.write_bytes(out)
    return path


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(path)
    return path


def make_epub(
    path: Path, chapters: list[tuple[str, str]], *, titles: list[str] | None = None
) -> Path:
    """最小合法 EPUB：container.xml + OPF + N 章 XHTML；titles 给各章 <title>/<h1>。"""
    names = [name for name, _ in chapters]
    title_by = dict(zip(names, titles)) if titles else {}
    ids = {name: f"id{i}" for i, name in enumerate(names)}
    manifest = "".join(
        f'<item id="{ids[name]}" href="{name}" media-type="application/xhtml+xml"/>'
        for name in names
    )
    itemrefs = "".join(f'<itemref idref="{ids[name]}"/>' for name in names)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
        f"<manifest>{manifest}</manifest><spine>{itemrefs}</spine></package>"
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for name, body in chapters:
            title = title_by.get(name, name)
            markup = (
                '<?xml version="1.0" encoding="utf-8"?>'
                f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>'
                f"<body><h1>{title}</h1><p>{body}</p></body></html>"
            )
            zf.writestr(f"OEBPS/{name}", markup.encode("utf-8"))
    return path


# ---------------------------------------------------------- 夹具 / 工具


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "reader.db")
    yield s
    s.close()


@pytest.fixture()
def page(store):
    from reco_ui.reader_page import ReaderPage

    p = ReaderPage(store)
    yield p
    p.close_book()
    p.deleteLater()


def _seed_book(store, *, name: str, ino: int, ext: str = ".txt", size: int = 100) -> int:
    from reco_store import dedup_key

    key = dedup_key(size, name)
    store.append_event(
        "file_added",
        payload={
            "path": f"Z:/synthetic/{name}",
            "zone": "liked",
            "ext": ext,
            "size_bytes": size,
            "mtime_ns": 1000 + ino,
            "st_dev": 1,
            "st_ino": ino,
            "display_name": name,
            "normalized_name": name,
            "dedup_key": key,
            "mount_hint": "Z:",
        },
        occurred_at=NOW,
        config_fingerprint="t3/seed/1",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute(
            "SELECT book_id FROM books WHERE dedup_key = ?", (key,)
        ).fetchone()[0])
    finally:
        conn.close()


def _pump_until(predicate, timeout: float = 15.0) -> bool:
    """池线程泄洪 + 泵事件直到谓词为真（不 sleep；E8 直断言口径）。"""
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(15000)
    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return predicate()


def _open_and_wait(page, path, book_id) -> None:
    page.open_book(path, book_id)
    assert _pump_until(lambda: page.loaded or page.status_text != "加载中…"), "open 超时"
    assert page.loaded, page.status_text
    # #73：打开即写一条 reopen 信号（池线程）。predicate 只在事件泵之后判，
    # worker 可能刚好还在跑；采样事件数前先泄洪，否则「进度零事件」会间歇性
    # 数到这条 reopen（与进度无关）。
    from reco_ui.qt import QThreadPool

    QThreadPool.globalInstance().waitForDone(15000)


# ---------------------------------------------------------- 导出面


def test_qt_exports_reader_controls(qapp):
    from reco_ui.qt import QPlainTextEdit, QScrollBar, QTextBrowser, QTextCursor

    assert all((QPlainTextEdit, QScrollBar, QTextBrowser, QTextCursor))


# ---------------------------------------------------------- 首帧 / 分块


def test_open_txt_first_frame_contains_first_chunk(page, store, tmp_path):
    text = "第一章 开始\n\n这是第一段正文。"
    path = _write_txt(tmp_path / "novel.txt", text)
    bid = _seed_book(store, name="novel.txt", ino=1)
    _open_and_wait(page, path, bid)
    assert page.viewer.toPlainText() == text
    assert page.viewer.isReadOnly()
    assert "novel.txt" in page.title_text
    assert page.status_text == ""


def test_10mb_txt_first_frame_under_500ms_and_chunked(page, store, tmp_path):
    text = _make_text(total_chars=3_600_000)  # ≥10MiB UTF-8（中文 3 字节/字）
    path = _write_txt(tmp_path / "big.txt", text)
    assert path.stat().st_size >= 10 * 1024 * 1024
    bid = _seed_book(store, name="big.txt", ino=2)

    started = time.monotonic()
    page.open_book(path, bid)
    assert _pump_until(lambda: page.loaded)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert elapsed_ms < 500.0, f"首帧 {elapsed_ms:.1f}ms 超出 <500ms 口径"
    total = len(text)
    assert len(page.viewer.toPlainText()) < total  # 分块生效：首帧只装 chunk[0]
    assert page.viewer.toPlainText() == page._chunks[0]


def test_chunk_appends_when_scrolled_near_bottom(page, store, tmp_path):
    text = _make_text(total_chars=300_000)
    path = _write_txt(tmp_path / "multi.txt", text)
    bid = _seed_book(store, name="multi.txt", ino=3)
    page.resize(800, 600)
    page.show()
    _open_and_wait(page, path, bid)
    assert len(page._chunks) >= 2
    first_len = len(page.viewer.toPlainText())
    bar = page.viewer.verticalScrollBar()
    assert bar.maximum() > 0

    from reco_ui.qt import QApplication

    bar.setValue(bar.maximum())
    QApplication.processEvents()

    assert len(page.viewer.toPlainText()) > first_len
    assert page.viewer.toPlainText() == page._chunks[0] + page._chunks[1]


# ---------------------------------------------------------- 进度 / 续读


def test_restore_loads_chunks_covering_offset_then_moves_cursor(page, store, tmp_path):
    text = _make_text(total_chars=300_000)
    path = _write_txt(tmp_path / "resume.txt", text)
    bid = _seed_book(store, name="resume.txt", ino=4)
    offset = 200_000
    store.upsert_reading_progress(
        bid, ext="txt", char_offset=offset,
        percent=offset / len(text), updated_at=NOW,
    )

    _open_and_wait(page, path, bid)

    assert page._loaded_chars >= offset
    assert page._chunk_index >= 1
    assert len(page.viewer.toPlainText()) < len(text)  # 只装覆盖目标偏移的块
    assert abs(page.viewer.textCursor().position() - offset) <= 1


def test_flush_progress_writes_char_offset_percent_and_no_events(page, store, tmp_path):
    text = _make_text(total_chars=4_000)
    path = _write_txt(tmp_path / "progress.txt", text)
    bid = _seed_book(store, name="progress.txt", ino=5)
    _open_and_wait(page, path, bid)
    before_events = len(store.read_events())

    target = 1500
    from reco_ui.qt import QTextCursor

    cursor = page.viewer.textCursor()
    cursor.setPosition(target)
    page.viewer.setTextCursor(cursor)

    page.flush_progress()

    saved = store.get_reading_progress(bid)
    assert saved is not None
    assert saved["char_offset"] == target
    assert abs(saved["percent"] - target / len(text)) < 1e-9
    assert saved["updated_at"]
    assert len(store.read_events()) == before_events  # §R4：进度零事件
    assert f"{int(target / len(text) * 100)}%" in page.title_text


def test_pending_save_timer_keeps_progress_off_until_flush(page, store, tmp_path):
    text = _make_text(total_chars=4_000)
    path = _write_txt(tmp_path / "timer.txt", text)
    bid = _seed_book(store, name="timer.txt", ino=6)
    _open_and_wait(page, path, bid)

    from reco_ui.qt import QTextCursor

    cursor = page.viewer.textCursor()
    cursor.setPosition(900)
    page.viewer.setTextCursor(cursor)
    page._on_scroll(0)  # 滚动重启节流计时（1s 后才写）
    assert store.get_reading_progress(bid) is None
    assert page._save_timer.isActive()

    page.flush_progress()
    assert store.get_reading_progress(bid)["char_offset"] == 900


# ---------------------------------------------------------- 诚实错误面


def test_open_missing_file_reports_filename_enum_only(page, store, tmp_path):
    bid = _seed_book(store, name="ghost.txt", ino=7)
    missing = tmp_path / "ghost.txt"
    page.open_book(missing, bid)
    assert _pump_until(lambda: page.status_text != "加载中…")
    assert page.status_text == "打开失败：ghost.txt: not_found"
    assert str(tmp_path) not in page.status_text
    assert page.viewer.toPlainText() == ""
    assert not page.loaded


def test_open_undecodable_file_keeps_body_out_of_ui(page, store, tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_bytes(CANARY.encode("utf-8") + b"\xff\xff\xff")
    bid = _seed_book(store, name="bad.txt", ino=8)
    page.open_book(bad, bid)
    assert _pump_until(lambda: page.status_text != "加载中…")
    assert page.status_text == "打开失败：bad.txt: undecodable"
    assert CANARY not in page.status_text
    assert CANARY not in page.title_text
    assert CANARY not in page.viewer.toPlainText()


# ---------------------------------------------------------- 四格式零落盘


def test_four_formats_open_close_write_nothing(page, store, tmp_path):
    txt = _write_txt(tmp_path / "f.txt", "TXT 正文段落。\n\n第二段。")
    docx = make_docx(tmp_path / "f.docx", ["DOCX 第一段", "DOCX 第二段"])
    pdf = make_pdf_with_text(tmp_path / "f.pdf", "PDF reader text")
    epub = make_epub(tmp_path / "f.epub", [("c1.xhtml", "第一章正文"), ("c2.xhtml", "第二章正文")])
    books = {
        txt: _seed_book(store, name="f.txt", ino=9, ext=".txt", size=11),
        docx: _seed_book(store, name="f.docx", ino=10, ext=".docx", size=12),
        pdf: _seed_book(store, name="f.pdf", ino=11, ext=".pdf", size=13),
        epub: _seed_book(store, name="f.epub", ino=12, ext=".epub", size=14),
    }

    before = sorted(p.name for p in tmp_path.iterdir())
    for path, bid in books.items():
        _open_and_wait(page, path, bid)
        assert page.status_text == ""
        if path.suffix.lower() == ".epub":
            assert page.browser.toPlainText(), f"{path.name} 未渲染任何文本"
        else:
            assert page.viewer.toPlainText(), f"{path.name} 未渲染任何文本"
        page.close_book()
    after = sorted(p.name for p in tmp_path.iterdir())
    assert after == before  # R2：零落盘


# ---------------------------------------------------------- EPUB 章节


def _make_epub_book(store, tmp_path, *, size: int, ino: int, long_second: bool = False):
    body = "第二章正文"
    if long_second:
        body += ("\n\n" + "文" * 200) * 40
    path = make_epub(
        tmp_path / f"epub-{ino}.epub",
        [("c1.xhtml", "第一章正文"), ("c2.xhtml", body), ("c3.xhtml", "第三章正文")],
        titles=["第一章 开始", "第二章 遇见", "第三章 尾声"],
    )
    book_id = _seed_book(store, name=f"epub-{ino}.epub", ino=ino, ext=".epub", size=size)
    return path, book_id


def test_epub_first_chapter_renders_title_and_body(page, store, tmp_path):
    path, bid = _make_epub_book(store, tmp_path, size=101, ino=21)
    _open_and_wait(page, path, bid)
    text = page.browser.toPlainText()
    assert "第一章 开始" in text
    assert "第一章正文" in text
    assert "第二章正文" not in text  # 单章渲染，绝不整本灌入
    assert page._stack.currentWidget() is page.browser
    assert "epub-21.epub" in page.title_text
    assert "第一章 开始" in page.title_text
    assert page._btn_next.isHidden() is False
    assert page._btn_prev.isEnabled() is False
    assert page._btn_next.isEnabled() is True


def test_epub_next_and_prev_chapter_switch(page, store, tmp_path):
    path, bid = _make_epub_book(store, tmp_path, size=102, ino=22)
    _open_and_wait(page, path, bid)

    page.next_chapter()
    assert page.chapter_index == 1
    assert "第二章正文" in page.browser.toPlainText()
    assert "第一章正文" not in page.browser.toPlainText()
    assert "第二章 遇见" in page.title_text
    saved = store.get_reading_progress(bid)
    assert saved is not None
    assert saved["epub_spine_index"] == 1  # 翻章即落盘（票面：翻页时保存）
    assert saved["char_offset"] is None

    page.next_chapter()
    assert page.chapter_index == 2
    assert page._btn_next.isEnabled() is False

    page.prev_chapter()
    assert page.chapter_index == 1
    assert "第二章正文" in page.browser.toPlainText()


def test_epub_reopen_restores_spine_index_and_scroll(page, store, tmp_path):
    path, bid = _make_epub_book(store, tmp_path, size=103, ino=23, long_second=True)
    store.upsert_reading_progress(
        bid, ext="epub", epub_spine_index=1, epub_block_pos=120, percent=0.5, updated_at=NOW,
    )
    _open_and_wait(page, path, bid)

    assert page.chapter_index == 1
    text = page.browser.toPlainText()
    assert "第二章 遇见" in text
    assert "第二章正文" in text
    bar = page.browser.verticalScrollBar()
    assert bar.maximum() > 120
    assert bar.value() == 120  # 章内滚动值恢复（spine_index + 章内位置）


def test_text_format_hides_chapter_buttons(page, store, tmp_path):
    path = _write_txt(tmp_path / "plain.txt", "正文")
    bid = _seed_book(store, name="plain.txt", ino=24, size=104)
    _open_and_wait(page, path, bid)
    assert page._btn_prev.isHidden() is True
    assert page._btn_next.isHidden() is True
    assert page._stack.currentWidget() is page.viewer


def test_epub_parse_perf_span(store, tmp_path, monkeypatch):
    from reco_io import perf as perf_mod

    monkeypatch.setattr(perf_mod, "perf_enabled", True)
    path, bid = _make_epub_book(store, tmp_path, size=105, ino=25)

    from reco_ui.qt import QThreadPool
    from reco_ui.reader_page import ReaderPage

    pg = ReaderPage(store)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            pg.open_book(path, bid)
            assert _pump_until(lambda: pg.loaded)
    finally:
        pg.close_book()
        pg.deleteLater()
        QThreadPool.globalInstance().waitForDone(5000)
    assert "phase=reader.epub.parse ms=" in err.getvalue()


# ---------------------------------------------------------- RECO_PERF 打点


def test_perf_spans_for_load_first_frame_and_chunk_append(store, tmp_path, monkeypatch):
    from reco_io import perf as perf_mod

    monkeypatch.setattr(perf_mod, "perf_enabled", True)
    text = _make_text(total_chars=300_000)
    path = _write_txt(tmp_path / "span.txt", text)
    bid = _seed_book(store, name="span.txt", ino=13)

    from reco_ui.qt import QApplication, QThreadPool
    from reco_ui.reader_page import ReaderPage

    pg = ReaderPage(store)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            pg.open_book(path, bid)
            assert _pump_until(lambda: pg.loaded)
            bar = pg.viewer.verticalScrollBar()
            bar.setValue(bar.maximum())
            QApplication.processEvents()
    finally:
        pg.close_book()
        pg.deleteLater()
        QThreadPool.globalInstance().waitForDone(5000)

    marks = err.getvalue()
    for name in ("reader.load", "reader.first_frame", "reader.chunk.append"):
        assert f"phase={name} ms=" in marks, f"缺少 {name} 打点"
