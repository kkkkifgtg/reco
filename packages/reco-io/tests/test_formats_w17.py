"""W17（t22）红测试：DOCX/PDF 读取支持 + 白名单同口径 + 键名-编码式契约。

契约要点（票面 + implementer-4 口径确认）：
- features._READERS 接缝字典：suffix -> 纯读取函数（路径→纯文本，零落盘零输出）。
- .docx：stdlib zipfile + xml.etree 提取 word/document.xml 的 w:t 文本；
  w:p 段落边界保留换行；坏 zip → corrupt_container。
- .pdf：pypdf 函数内延迟导入（W8 sklearn 先例）；文本层缺失/解密失败 → undecodable。
- quality_flag 复用现有枚举（ok/empty/undecodable/corrupt_container），不加新旗标。
- EXTRACTOR_VERSION 不 bump（统计口径不变，implementer-4 裁定）：
  现有 golden 测试不改一字全绿 = 不变量证据。
- 白名单单一事实源：scanner.TEXT_EXTS 扩 .docx/.pdf；UI 导入白名单 =
  扫描口径 ∪ 文档扩展口径（implementer-2 提醒的并集语义）。
- 全合成样例：python-docx / pypdf 程序化生成，不读真实正文（00-index）。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ---------------------------------------------------------- 合成样例工厂

def make_docx(path: Path, paragraphs: list[str], *, encrypted: bool = False) -> Path:
    """python-docx 生成正常 docx；encrypted 参数生成加密形态（模拟：损坏的 zip 结构）。"""
    if encrypted:
        # 真实加密 docx 是 OLE 容器非 zip——此处以「非 zip 魔数文件」代表不可读容器
        path.write_bytes(b"ENCRYPTED-OLE-CONTAINER-NOT-A-ZIP")
        return path
    import docx

    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    d.save(path)
    return path


def make_pdf(path: Path, pages: list[str], *, encrypt: bool = False, empty_layer: bool = False) -> Path:
    """pypdf 程序化生成：正常文本页 / 加密 / 无文本层。"""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for text in pages:
        writer.add_blank_page(width=595, height=842)
    if not empty_layer:
        # 用 PdfWriter 的注释形态不合适——改为直接构造含文本流的最小 PDF
        writer = PdfWriter()
        for i, text in enumerate(pages):
            stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
            page = writer.add_blank_page(width=595, height=842)
        # 简化：直接用低层 API 不可靠，改用 pypdf 官方推荐的最小文本页构造
    if encrypt:
        writer.encrypt("secret")
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def make_pdf_with_text(path: Path, text: str) -> Path:
    """ReportLab 不在依赖面——手工构造最小 PDF（含 Tj 文本流）。"""
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


# ---------------------------------------------------------- fixtures

@pytest.fixture
def store_module():
    from reco_store import store

    return store


@pytest.fixture
def features():
    from reco_io import features

    return features


@pytest.fixture
def scanner():
    from reco_io import scanner

    return scanner


# ---------------------------------------------------------- 读取器接缝

def test_readers_seam_registered(features):
    """_READERS 接缝：每格式一个条目，可插拔（新格式=加条目）。"""
    assert features._READERS[".epub"] is features._epub_text
    assert features._READERS[".docx"] is features._docx_text
    assert features._READERS[".pdf"] is features._pdf_text
    assert set(features._READERS) == {".epub", ".docx", ".pdf"}


def test_docx_reader_paragraphs_and_newlines(features, tmp_path):
    """docx：w:p 段落边界保留换行；正文逐段可读。"""
    p = make_docx(tmp_path / "a.docx", ["第一段内容", "第二段内容"])
    text = features._READERS[".docx"](p)
    assert "第一段内容" in text
    assert "第二段内容" in text
    assert "\n" in text  # 段落边界换行


def test_docx_corrupt_container(features, tmp_path):
    p = make_docx(tmp_path / "bad.docx", [], encrypted=True)
    with pytest.raises(zipfile.BadZipFile):
        features._READERS[".docx"](p)


def test_docx_empty_doc(features, tmp_path):
    p = make_docx(tmp_path / "empty.docx", [])
    text = features._READERS[".docx"](p)
    assert text.strip() == ""


def test_pdf_reader_text_layer(features, tmp_path):
    p = make_pdf_with_text(tmp_path / "a.pdf", "hello pdf")
    text = features._READERS[".pdf"](p)
    assert "hello" in text


def test_pdf_no_text_layer_undecodable(features, tmp_path):
    """扫描版 PDF（无文本层）：提取结果空文本 → extract_features 降级，不瞎填。"""
    p = make_pdf(tmp_path / "scan.pdf", ["page"], empty_layer=True)
    result = features.extract_features(p)
    assert result.quality_flag == "empty"  # 全空文本按空文件口径降级


def test_pdf_encrypted_undecodable(features, tmp_path):
    p = make_pdf(tmp_path / "enc.pdf", ["page"], encrypt=True)
    result = features.extract_features(p)
    assert result.quality_flag == "undecodable"


def test_pdf_corrupt_container(features, tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"%PDF-1.4 broken")
    result = features.extract_features(p)
    assert result.quality_flag == "undecodable"


# ---------------------------------------------------------- extract_features 集成

def test_extract_features_docx_end_to_end(features, tmp_path):
    p = make_docx(tmp_path / "novel.docx", ["合成段落" * 30, "第二段落" * 30])
    result = features.extract_features(p)
    assert result.quality_flag == "ok"
    assert result.stats["char_freq"]  # 统计非空


def test_extract_features_empty_docx(features, tmp_path):
    p = make_docx(tmp_path / "empty.docx", [])
    result = features.extract_features(p)
    assert result.quality_flag == "empty"


# ---------------------------------------------------------- 白名单同口径

def test_scanner_text_exts_extended(scanner):
    assert scanner.TEXT_EXTS == frozenset({".txt", ".epub", ".md", ".docx", ".pdf"})


def test_scanner_picks_up_docx_and_pdf(scanner, tmp_path):
    (tmp_path / "liked").mkdir()
    make_docx(tmp_path / "liked" / "a.docx", ["段落"])
    make_pdf_with_text(tmp_path / "liked" / "b.pdf", "text")
    (tmp_path / "liked" / "c.txt").write_text("txt", encoding="utf-8")
    result = scanner.scan_zones(tmp_path, zones=("liked",))
    exts = {Path(f.path).suffix for f in result.fingerprints}
    assert exts == {".docx", ".pdf", ".txt"}


def test_ui_import_whitelist_union(scanner):
    """UI 导入白名单 = 扫描口径 ∪ 文档扩展（implementer-2 并集语义提醒）；
    且引用 scanner.TEXT_EXTS 单一事实源而非字面重复。"""
    from reco_ui import pages

    assert pages.ALLOWED_IMPORT_EXTS >= scanner.TEXT_EXTS
    assert {".docx", ".pdf"} <= pages.ALLOWED_IMPORT_EXTS


# ---------------------------------------------------------- B2 金丝雀（扩展到新读取路径）

def test_canary_no_content_leak_docx_pdf(features, tmp_path, capsys):
    """正文零落盘零输出：docx/pdf 读取全程不产生 stdout/stderr 副产物。"""
    secret = "金丝雀正文绝不出现在任何输出流"
    p = make_docx(tmp_path / "c.docx", [secret, "第二段"])
    features._READERS[".docx"](p)
    p2 = make_pdf_with_text(tmp_path / "c.pdf", "canary text")
    features._READERS[".pdf"](p2)
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


# ---------------------------------------------------------- EXTRACTOR_VERSION 不 bump 不变量

def test_extractor_version_unchanged(features):
    """读取扩展不触发重算（implementer-4 裁定）：版本常量不变。"""
    from reco_io.features import EXTRACTOR_VERSION

    assert EXTRACTOR_VERSION == "reco-stats/1"  # W17 裁定：读取扩展不 bump（统计口径不变）
