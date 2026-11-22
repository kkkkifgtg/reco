"""RR1 阅读模块纯函数（零 Qt；工单 #35 / spec #33）。

公开 API：
- parse_epub_spine(path)              EPUB 按 spine 顺序逐章 → [{index,title,html,text}]
- load_book_text(path, *, max_bytes)  txt/md 全量解码；docx/pdf 复用 reco_io.features._READERS
- chunk_text(text, *, chunk_chars)    空行段界切块、超长单段硬切、确定性
- ReaderError                         只暴露文件名 + 枚举

纪律（架构 §5.6；审计 R2/R3/B5）：
- 正文与片段绝不进入异常信息、日志或磁盘：ReaderError 的 str/repr 只含文件名与枚举；
- 异常一律 from None，压掉链式上下文（底层 UnicodeDecodeError 的消息含正文片段）；
- 不 import PyQt6（渲染归 ReaderPage；本模块可脱离 GUI 测试）。
"""

from __future__ import annotations

import html
import os
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

__all__ = [
    "CHUNK_CHARS",
    "READER_REASONS",
    "ReaderError",
    "chunk_text",
    "load_book_text",
    "parse_epub_spine",
]

# 默认分块大小（≈120k 字符）：首帧先渲染一块，其余按块追加（T3/T4 消费）。
CHUNK_CHARS = 120_000

# 错误枚举：ReaderError.reason 只允许这几种；新增枚举即破坏「只报枚举」口径。
READER_REASONS = ("corrupt_container", "undecodable", "not_found", "oversized")

_CONTAINER_MEMBER = "META-INF/container.xml"
_OPF_MEDIA_TYPE = "application/oebps-package+xml"
# spine 里只把「可当文本渲染」的条目当章节；图片/字体等非 HTML 项跳过。
_TEXT_MEDIA_TYPES = frozenset(
    {"application/xhtml+xml", "text/html", "application/xml", "text/xml", ""}
)
# load_book_text 允许的格式：txt/md 走解码链；docx/pdf 复用提取器接缝（epub 走 parse_epub_spine）。
_PLAIN_SUFFIXES = frozenset({".txt", ".md"})
_READER_SUFFIXES = frozenset({".docx", ".pdf"})


class ReaderError(Exception):
    """阅读失败：只暴露文件名 + 枚举（正文绝不出现在异常里）。"""

    def __init__(self, path, reason: str) -> None:
        if reason not in READER_REASONS:
            raise ValueError(f"unknown reader reason: {reason!r}")
        self.filename = _filename(path)
        self.reason = reason
        super().__init__(self.filename, reason)

    def __str__(self) -> str:
        return f"{self.filename}: {self.reason}"

    def __repr__(self) -> str:
        return f"ReaderError(filename={self.filename!r}, reason={self.reason!r})"


def _filename(path) -> str:
    try:
        name = os.path.basename(os.fspath(path))
    except TypeError:
        return ""
    return str(name)


# ---------------------------------------------------------- EPUB（spine）


def parse_epub_spine(path) -> list[dict]:
    """EPUB 逐章：zipfile → META-INF/container.xml → OPF → manifest → spine 顺序。

    产出 [{index, title, html, text}]；title 取 <title> 或首个 <h1>，缺失用「第 N 章」；
    剥离 script/style/iframe（连内容）。spine 顺序不等于 zip 成员名序，顺序只认 spine。
    """
    source = Path(path)
    try:
        archive = zipfile.ZipFile(source)
    except FileNotFoundError:
        raise ReaderError(source, "not_found") from None
    except (zipfile.BadZipFile, OSError):
        raise ReaderError(source, "corrupt_container") from None

    with archive:
        opf_member = _locate_opf_member(archive, source)
        spine = _read_spine_members(archive, opf_member, source)
        chapters: list[dict] = []
        for member in spine:
            markup = _read_chapter_markup(archive, member, source)
            if markup is None:
                continue  # 引用存在但成员缺失：跳过该章，其余章仍可读
            sanitized = _strip_embedded(markup)
            chapters.append(
                {
                    "index": len(chapters),
                    "title": _chapter_title(sanitized, len(chapters)),
                    "html": sanitized,
                    "text": _chapter_text(sanitized),
                }
            )

    if not chapters:
        raise ReaderError(source, "corrupt_container")
    return chapters


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _locate_opf_member(archive: zipfile.ZipFile, source: Path) -> str:
    try:
        raw = archive.read(_CONTAINER_MEMBER)
    except KeyError:
        raise ReaderError(source, "corrupt_container") from None
    except (zipfile.BadZipFile, OSError):
        raise ReaderError(source, "corrupt_container") from None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ReaderError(source, "corrupt_container") from None

    rootfiles = [element for element in root.iter() if _localname(element.tag) == "rootfile"]
    preferred = [
        element
        for element in rootfiles
        if (element.get("media-type") or "").strip() == _OPF_MEDIA_TYPE
    ]
    for element in preferred or rootfiles:
        full_path = (element.get("full-path") or "").strip()
        if full_path:
            return _normalize_member(full_path)
    raise ReaderError(source, "corrupt_container")


def _normalize_member(name: str) -> str:
    cleaned = unquote(name.replace("\\", "/")).split("#", 1)[0].lstrip("/")
    normalized = posixpath.normpath(cleaned)
    return "" if normalized == "." else normalized


def _resolve_member(base: str, href: str) -> str:
    return _normalize_member(posixpath.join(base, href) if base else href)


def _read_spine_members(archive: zipfile.ZipFile, opf_member: str, source: Path) -> list[str]:
    try:
        raw = archive.read(opf_member)
    except KeyError:
        raise ReaderError(source, "corrupt_container") from None
    except (zipfile.BadZipFile, OSError):
        raise ReaderError(source, "corrupt_container") from None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ReaderError(source, "corrupt_container") from None

    manifest: dict[str, tuple[str, str]] = {}
    for element in root.iter():
        if _localname(element.tag) != "item":
            continue
        item_id = (element.get("id") or "").strip()
        href = (element.get("href") or "").strip()
        if item_id and href:
            manifest[item_id] = (href, (element.get("media-type") or "").strip().lower())

    base = posixpath.dirname(opf_member)
    spine: list[str] = []
    for element in root.iter():
        if _localname(element.tag) != "itemref":
            continue
        entry = manifest.get((element.get("idref") or "").strip())
        if entry is None:
            continue
        href, media_type = entry
        if media_type not in _TEXT_MEDIA_TYPES:
            continue
        spine.append(_resolve_member(base, href))
    if not spine:
        raise ReaderError(source, "corrupt_container")
    return spine


def _read_chapter_markup(archive: zipfile.ZipFile, member: str, source: Path) -> str | None:
    try:
        raw = archive.read(member)
    except KeyError:
        return None
    except (zipfile.BadZipFile, OSError):
        raise ReaderError(source, "corrupt_container") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030")  # strict：失败即 undecodable，不猜编码
    except UnicodeDecodeError:
        raise ReaderError(source, "undecodable") from None


_EMBEDDED_RE = re.compile(r"(?is)<(script|style|iframe)\b[^>]*>.*?</\1\s*>")
_EMBEDDED_STRAY_RE = re.compile(r"(?is)</?(?:script|style|iframe)\b[^>]*/?>")


def _strip_embedded(markup: str) -> str:
    """剥离 script/style/iframe（连内容），再清掉残留的自闭合/孤立标签。"""
    return _EMBEDDED_STRAY_RE.sub("", _EMBEDDED_RE.sub("", markup))


_TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title\s*>")
_H1_RE = re.compile(r"(?is)<h1\b[^>]*>(.*?)</h1\s*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _inline_text(fragment: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _chapter_title(markup: str, position: int) -> str:
    for pattern in (_TITLE_RE, _H1_RE):
        match = pattern.search(markup)
        if match is not None:
            title = _inline_text(match.group(1))
            if title:
                return title
    return f"第 {position + 1} 章"


_BODY_RE = re.compile(r"(?is)<body\b[^>]*>(.*?)</body\s*>")
_HEAD_RE = re.compile(r"(?is)<head\b[^>]*>.*?</head\s*>")
_BR_RE = re.compile(r"(?i)<br\s*/?>")
_BLOCK_END_RE = re.compile(
    r"(?i)</(?:p|div|h[1-6]|li|blockquote|section|article|header|footer|tr|pre|table|ul|ol|figure)\s*>"
)


def _chapter_text(markup: str) -> str:
    """XHTML → 纯文本：只取 body（无 body 时剥 head），换行落行、块尾落段界。"""
    body = _BODY_RE.search(markup)
    text = body.group(1) if body is not None else _HEAD_RE.sub("", markup)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------- 全量文本（txt/md/docx/pdf）


def load_book_text(path, *, max_bytes: int | None = None) -> str:
    """txt/md 全量解码（utf-8 → gb18030 strict）；docx/pdf 复用提取器接缝。

    epub 不在此列：逐章解析走 parse_epub_spine。max_bytes 给定时超限报 oversized。
    """
    source = Path(path)
    suffix = source.suffix.lower()
    try:
        size = source.stat().st_size
    except OSError:
        raise ReaderError(source, "not_found") from None
    if max_bytes is not None and size > max_bytes:
        raise ReaderError(source, "oversized")

    if suffix in _PLAIN_SUFFIXES:
        try:
            raw = source.read_bytes()
        except OSError:
            raise ReaderError(source, "not_found") from None
        return _decode_full_text(raw, source)

    reader = _format_reader(suffix) if suffix in _READER_SUFFIXES else None
    if reader is None:
        raise ValueError(f"unsupported reader extension: {suffix or '<none>'}")

    try:
        return reader(source)
    except FileNotFoundError:
        raise ReaderError(source, "not_found") from None
    except zipfile.BadZipFile:
        raise ReaderError(source, "corrupt_container") from None
    except KeyError:
        # docx 缺 word/document.xml 等成员：容器结构不成立
        raise ReaderError(source, "corrupt_container") from None
    except UnicodeDecodeError:
        raise ReaderError(source, "undecodable") from None
    except ValueError:
        # 提取器族口径（_pdf_text 把 pypdf 异常族归一为 ValueError）
        raise ReaderError(source, "undecodable") from None
    except OSError:
        raise ReaderError(source, "not_found") from None
    except RuntimeError:
        # 缺第三方依赖（pypdf 未安装）：按不可读口径，不猜内容
        raise ReaderError(source, "undecodable") from None


def _format_reader(suffix: str):
    from reco_io import features  # 延迟导入：EPUB/分块路径不拉提取模块

    return features._READERS.get(suffix)


def _decode_full_text(raw: bytes, source: Path) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")  # strict：失败即 undecodable，不猜编码
        except UnicodeDecodeError:
            raise ReaderError(source, "undecodable") from None
    # 统一 LF：渲染、光标偏移与分块只认一种换行口径（Windows 源文件常带 CRLF）。
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------- 分块


_PARAGRAPH_SEP_RE = re.compile(r"(\r?\n[ \t]*\r?\n)")


def _paragraph_units(text: str):
    """把原文切成「段落正文 + 其后段界」单元；单元拼接严格等于原文。"""
    parts = _PARAGRAPH_SEP_RE.split(text)
    for index in range(0, len(parts), 2):
        body = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ""
        unit = body + separator
        if unit:
            yield unit


def chunk_text(text: str, *, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """按空行段界切块；超长单段硬切；同输入同输出（"".join(chunks) == text）。"""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if chunk_chars < 1:
        raise ValueError("chunk_chars must be >= 1")
    if not text:
        return []

    chunks: list[str] = []
    buffer: list[str] = []
    size = 0
    for unit in _paragraph_units(text):
        unit_len = len(unit)
        if size and size + unit_len > chunk_chars:
            chunks.append("".join(buffer))
            buffer, size = [], 0
        if unit_len <= chunk_chars:
            buffer.append(unit)
            size += unit_len
            if size == chunk_chars:
                chunks.append("".join(buffer))
                buffer, size = [], 0
            continue
        for start in range(0, unit_len, chunk_chars):
            piece = unit[start : start + chunk_chars]
            if len(piece) == chunk_chars:
                chunks.append(piece)
            else:
                buffer.append(piece)
                size = len(piece)
    if buffer:
        chunks.append("".join(buffer))
    return chunks
