"""#35 红测试：reader.py 纯函数（零 Qt）。

契约（票面 #35 / spec #33）：
- parse_epub_spine：zipfile → META-INF/container.xml → OPF → manifest → spine 顺序；
  title 取 <title> 或首个 <h1>，缺失「第 N 章」；剥离 script/style/iframe；
- 错误口径：坏容器/缺 container/缺 OPF/空 spine → corrupt_container，
  坏编码 → undecodable，缺文件 → not_found，超限 → oversized；
- ReaderError 只暴露文件名 + 枚举，异常 str 不含正文片段（金丝雀）；
- load_book_text：txt/md utf-8 → gb18030 strict；docx/pdf 复用 reco_io.features._READERS；
- chunk_text：空行段界切块、超长单段硬切、同输入同输出、拼接等于原文；
- 本模块零 Qt：子进程导入后 sys.modules 无 PyQt6。

夹具全合成：EPUB 用 zipfile 手搓（spine 顺序故意 ≠ 成员名序）；DOCX 用 python-docx；
PDF 用手工最小 PDF（与 test_formats_w17 同法）；TXT 直接写 tmp_path。
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

CANARY = "金丝雀绝密正文"


# ---------------------------------------------------------- 合成样例工厂


def _xhtml(title: str | None, body: str, *, head_extra: str = "") -> bytes:
    head = f"<title>{title}</title>" if title is not None else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"{head}{head_extra}</head><body>{body}</body></html>"
    ).encode("utf-8")


def _container_xml(opf_member: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        f'<rootfiles><rootfile full-path="{opf_member}" '
        'media-type="application/oebps-package+xml"/></rootfiles>'
        "</container>"
    )


def make_epub(
    path: Path,
    chapters: list[tuple[str, bytes]],
    *,
    spine: list[str] | None = None,
    opf_member: str = "OEBPS/content.opf",
) -> Path:
    """zipfile 手搓最小合法 EPUB；chapters=[(成员名, 字节)]，spine 给 idref 顺序。"""
    names = [name for name, _ in chapters]
    order = list(spine) if spine is not None else list(names)
    ids = {name: f"id{i}" for i, name in enumerate(names)}
    manifest = "".join(
        f'<item id="{ids[name]}" href="{name}" media-type="application/xhtml+xml"/>'
        for name in names
    )
    itemrefs = "".join(f'<itemref idref="{ids[name]}"/>' for name in order)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
        f"<manifest>{manifest}</manifest><spine>{itemrefs}</spine></package>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", _container_xml(opf_member))
        zf.writestr(opf_member, opf)
        for name, data in chapters:
            zf.writestr(f"OEBPS/{name}", data)
    return path


def write_zip(path: Path, entries: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def make_pdf_with_text(path: Path, text: str) -> Path:
    """手工构造最小 PDF（含 Tj 文本流；ASCII 文本，pypdf 可提取）。"""
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


@pytest.fixture
def reader():
    from reco_ui import reader as module

    return module


# ---------------------------------------------------------- 公开面


def test_public_surface(reader):
    assert set(reader.READER_REASONS) == {
        "corrupt_container",
        "undecodable",
        "not_found",
        "oversized",
    }
    for name in ("parse_epub_spine", "load_book_text", "chunk_text", "ReaderError"):
        assert hasattr(reader, name), name


def test_reader_error_rejects_unknown_reason(reader, tmp_path):
    with pytest.raises(ValueError):
        reader.ReaderError(tmp_path / "a.txt", "boom")


def test_reader_error_only_exposes_filename(reader, tmp_path):
    err = reader.ReaderError(tmp_path / "深目录" / "书.txt", "not_found")
    assert err.filename == "书.txt"
    assert str(tmp_path) not in str(err)
    assert err.reason == "not_found"


# ---------------------------------------------------------- EPUB：spine 顺序


def test_spine_order_follows_spine_not_member_name(reader, tmp_path):
    chapters = [
        ("ch_a.xhtml", _xhtml("阿章", "<p>正文 A</p>")),
        ("ch_m.xhtml", _xhtml("么章", "<p>正文 M</p>")),
        ("ch_z.xhtml", _xhtml("兹章", "<p>正文 Z</p>")),
    ]
    # 成员名序 a/m/z ≠ spine 顺序 z/a/m（钉死顺序来源）
    path = make_epub(
        tmp_path / "book.epub", chapters, spine=["ch_z.xhtml", "ch_a.xhtml", "ch_m.xhtml"]
    )
    got = reader.parse_epub_spine(path)
    assert [c["index"] for c in got] == [0, 1, 2]
    assert [c["title"] for c in got] == ["兹章", "阿章", "么章"]
    assert [c["text"] for c in got] == ["正文 Z", "正文 A", "正文 M"]
    assert set(got[0]) == {"index", "title", "html", "text"}


def test_title_prefers_title_tag_over_h1(reader, tmp_path):
    path = make_epub(
        tmp_path / "t.epub", [("c.xhtml", _xhtml("书名章节", "<h1>页面大标题</h1><p>正文</p>"))]
    )
    assert reader.parse_epub_spine(path)[0]["title"] == "书名章节"


def test_title_falls_back_to_h1(reader, tmp_path):
    path = make_epub(
        tmp_path / "t.epub", [("c.xhtml", _xhtml(None, "<h1>手写标题</h1><p>正文</p>"))]
    )
    assert reader.parse_epub_spine(path)[0]["title"] == "手写标题"


def test_title_falls_back_to_chapter_number(reader, tmp_path):
    chapters = [
        ("a.xhtml", _xhtml(None, "<p>一</p>")),
        ("b.xhtml", _xhtml(None, "<p>二</p>")),
    ]
    path = make_epub(tmp_path / "t.epub", chapters)
    assert [c["title"] for c in reader.parse_epub_spine(path)] == ["第 1 章", "第 2 章"]


def test_epub_text_excludes_head_title(reader, tmp_path):
    path = make_epub(
        tmp_path / "t.epub", [("c.xhtml", _xhtml("只有标题不是正文", "<p>正文段落</p>"))]
    )
    chapter = reader.parse_epub_spine(path)[0]
    assert chapter["title"] == "只有标题不是正文"
    assert "只有标题不是正文" not in chapter["text"]
    assert chapter["text"] == "正文段落"


def test_script_style_iframe_stripped(reader, tmp_path):
    body = (
        f"<p>可见段落</p><script>alert('{CANARY}')</script>"
        f"<style>.x{{content:'{CANARY}'}}</style><iframe src='x'>{CANARY}</iframe>"
        "<p>结尾段落</p>"
    )
    path = make_epub(tmp_path / "t.epub", [("c.xhtml", _xhtml("章", body))])
    chapter = reader.parse_epub_spine(path)[0]
    assert CANARY not in chapter["html"]
    assert CANARY not in chapter["text"]
    lowered = chapter["html"].lower()
    for token in ("<script", "<style", "<iframe"):
        assert token not in lowered, token
    assert "可见段落" in chapter["text"]
    assert "结尾段落" in chapter["text"]


# ---------------------------------------------------------- EPUB：错误口径


def test_non_zip_is_corrupt_container(reader, tmp_path):
    path = tmp_path / "bad.epub"
    path.write_bytes(b"this is not a zip container")
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.parse_epub_spine(path)
    assert excinfo.value.reason == "corrupt_container"
    assert excinfo.value.filename == "bad.epub"


def test_missing_container_is_corrupt_container(reader, tmp_path):
    path = write_zip(tmp_path / "nocontainer.epub", {"OEBPS/content.opf": b"<package/>"})
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.parse_epub_spine(path)
    assert excinfo.value.reason == "corrupt_container"


def test_missing_opf_is_corrupt_container(reader, tmp_path):
    path = write_zip(
        tmp_path / "noopf.epub",
        {"META-INF/container.xml": _container_xml("OEBPS/absent.opf")},
    )
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.parse_epub_spine(path)
    assert excinfo.value.reason == "corrupt_container"


def test_empty_spine_is_corrupt_container(reader, tmp_path):
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<manifest><item id="i0" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>'
        "<spine></spine></package>"
    )
    path = write_zip(
        tmp_path / "emptyspine.epub",
        {
            "META-INF/container.xml": _container_xml("OEBPS/content.opf"),
            "OEBPS/content.opf": opf,
            "OEBPS/c.xhtml": _xhtml(None, "<p>正文</p>"),
        },
    )
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.parse_epub_spine(path)
    assert excinfo.value.reason == "corrupt_container"


def test_missing_epub_path_is_not_found(reader, tmp_path):
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.parse_epub_spine(tmp_path / "nope.epub")
    assert excinfo.value.reason == "not_found"
    assert excinfo.value.filename == "nope.epub"


def test_bad_chapter_encoding_is_undecodable_without_body_leak(reader, tmp_path):
    bad = CANARY.encode("utf-8") + b"\xff\xff\xff\xff"
    path = make_epub(tmp_path / "badenc.epub", [("c.xhtml", bad)])
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.parse_epub_spine(path)
    exc = excinfo.value
    assert exc.reason == "undecodable"
    assert exc.filename == "badenc.epub"
    assert CANARY not in str(exc)
    assert CANARY not in repr(exc)
    assert str(tmp_path) not in str(exc)


# ---------------------------------------------------------- load_book_text


def test_load_txt_utf8(reader, tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("你好世界\n\n第二段", encoding="utf-8")
    assert reader.load_book_text(path) == "你好世界\n\n第二段"


def test_load_md_utf8(reader, tmp_path):
    path = tmp_path / "a.md"
    path.write_text("# 标题\n\n正文", encoding="utf-8")
    assert reader.load_book_text(path) == "# 标题\n\n正文"


def test_load_txt_normalizes_crlf(reader, tmp_path):
    path = tmp_path / "crlf.txt"
    path.write_bytes("第一行\r\n第二行\r\n".encode("utf-8"))
    assert reader.load_book_text(path) == "第一行\n第二行\n"


def test_load_txt_gb18030_fallback(reader, tmp_path):
    path = tmp_path / "gbk.txt"
    path.write_bytes("你好，世界".encode("gb18030"))
    assert reader.load_book_text(path) == "你好，世界"


def test_load_undecodable_keeps_body_out_of_error(reader, tmp_path):
    path = tmp_path / "bad.txt"
    path.write_bytes(CANARY.encode("utf-8") + b"\xff\xff\xff")
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.load_book_text(path)
    assert excinfo.value.reason == "undecodable"
    assert excinfo.value.filename == "bad.txt"
    assert CANARY not in str(excinfo.value)


def test_load_missing_is_not_found(reader, tmp_path):
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.load_book_text(tmp_path / "missing.txt")
    assert excinfo.value.reason == "not_found"


def test_load_oversized(reader, tmp_path):
    path = tmp_path / "big.txt"
    path.write_text("x" * 10, encoding="utf-8")
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.load_book_text(path, max_bytes=5)
    assert excinfo.value.reason == "oversized"


def test_load_docx_reuses_features_reader(reader, tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("第一段文档正文")
    document.add_paragraph("第二段文档正文")
    path = tmp_path / "d.docx"
    document.save(path)
    text = reader.load_book_text(path)
    assert "第一段文档正文" in text
    assert "第二段文档正文" in text


def test_load_corrupt_docx_is_corrupt_container(reader, tmp_path):
    path = tmp_path / "bad.docx"
    path.write_bytes(b"not a docx zip")
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.load_book_text(path)
    assert excinfo.value.reason == "corrupt_container"


def test_load_pdf_reuses_features_reader(reader, tmp_path):
    pytest.importorskip("pypdf")
    path = make_pdf_with_text(tmp_path / "d.pdf", "PDF reader text")
    assert "PDF reader text" in reader.load_book_text(path)


def test_load_unsupported_extension_rejected(reader, tmp_path):
    path = tmp_path / "a.mobi"
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        reader.load_book_text(path)


# ---------------------------------------------------------- chunk_text


def test_chunk_never_splits_a_paragraph(reader):
    paragraphs = [f"段落{i:02d}" + "汉" * 30 for i in range(6)]
    text = "\n\n".join(paragraphs)
    chunks = reader.chunk_text(text, chunk_chars=40)
    assert "".join(chunks) == text
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert len(chunks) > 1
    for para in paragraphs:
        assert any(para in chunk for chunk in chunks), para


def test_chunk_hard_splits_overlong_paragraph(reader):
    para = "超" * 95
    chunks = reader.chunk_text(para, chunk_chars=20)
    assert [len(chunk) for chunk in chunks] == [20, 20, 20, 20, 15]
    assert "".join(chunks) == para


def test_chunk_is_deterministic(reader):
    text = "\n\n".join(f"P{i}" + "x" * 50 for i in range(40))
    assert reader.chunk_text(text, chunk_chars=100) == reader.chunk_text(text, chunk_chars=100)


def test_chunk_empty_text(reader):
    assert reader.chunk_text("") == []


def test_chunk_rejects_bad_size(reader):
    with pytest.raises(ValueError):
        reader.chunk_text("abc", chunk_chars=0)


def test_chunk_default_size_bounds(reader):
    text = "\n\n".join("x" * 1000 for _ in range(300))
    chunks = reader.chunk_text(text)
    assert len(chunks) >= 3
    assert max(len(chunk) for chunk in chunks) <= reader.CHUNK_CHARS
    assert "".join(chunks) == text


# ---------------------------------------------------------- 零 Qt


def test_reader_module_is_qt_free():
    code = (
        "import sys\n"
        "import reco_ui.reader\n"
        "bad = [m for m in ('PyQt6', 'reco_ui.qt') if m in sys.modules]\n"
        "print('QT' if bad else 'CLEAN', ','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "CLEAN", out.stdout
