"""文本读取的唯一实现：解码链、HTML 剥离、EPUB spine 顺序（#112）。

此前同一件事有四份实现：阅读器自带回溯正则剥 script/style、四份解码链回退顺序
各不相同、提取层的 epub 按压缩包成员名排序（不认 OPF spine）。病态输入（长未闭合
标签）在正则版本上会平方级耗时，在别的版本上不会——「同源」只写在 docstring 里。

本模块把它们收成一份：reco-ui 阅读器、reco-io 提取与挖掘都从这里取。纯函数、
零第三方依赖，正文绝不进异常或日志。
"""
from __future__ import annotations

import codecs
import html
import posixpath
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

__all__ = [
    "TEXT_SCAN_FALLBACK",
    "decode_bytes",
    "epub_opf_members",
    "html_to_text",
    "normalize_newlines",
    "strip_embedded",
    "strip_tags_linear",
]

# 阅读器与提取层共用的解码顺序：BOM 优先（utf-16 / utf-8-sig），否则 utf-8 严格，
# 最后 gb18030 严格。不做编码猜测、不 replace——失败就是 undecodable。
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def normalize_newlines(text: str) -> str:
    """统一 LF：渲染、光标偏移、分块与统计只认一种换行口径。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def decode_bytes(raw: bytes) -> str:
    """字节 → 文本（BOM → utf-8 → gb18030 严格），并统一换行。

    全部失败抛 UnicodeDecodeError，由调用方按各自口径降级（阅读器报
    undecodable，提取层记 undecodable，挖掘返回空串）。
    """
    for bom, codec in _BOMS:
        if raw.startswith(bom):
            return normalize_newlines(raw.decode(codec))
    try:
        return normalize_newlines(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return normalize_newlines(raw.decode("gb18030"))


# ---------------------------------------------------------------- HTML 剥离

_BLOCK_TAGS = ("script", "style", "iframe")


def strip_embedded(markup: str) -> str:
    r"""线性剥除 script/style/iframe 块（连内容）。

    等价 `(?is)<(script|style|iframe)\b[^>]*>.*?</\1\s*>` 的语义，但不用回溯正则：
    病态输入（长未闭合标签）在线性实现上是 O(n)，正则版本是平方级（CWE-1333）。
    未闭合的块不剥（与正则「无匹配」一致），残留标签由调用方继续处理。
    """
    lowered = markup.lower()
    out: list[str] = []
    i = 0
    n = len(markup)
    while i < n:
        found = _find_block_open(lowered, i)
        if found is None:
            out.append(markup[i:])
            break
        pos, tag = found
        out.append(markup[i:pos])
        close = _find_block_close(lowered, pos, tag)
        if close is None:
            out.append(markup[pos:])
            break
        out.append(" ")
        i = close
    return "".join(out)


def _tag_boundary_ok(text: str, idx: int) -> bool:
    """tag 名后一个字符须为边界（正则 \b 口径）；到结尾也算边界。"""
    return idx >= len(text) or not (text[idx].isalnum() or text[idx] == "_")


def _find_block_open(lowered: str, start: int):
    best = None
    for tag in _BLOCK_TAGS:
        pos = lowered.find("<" + tag, start)
        while pos >= 0:
            after = pos + 1 + len(tag)
            if _tag_boundary_ok(lowered, after) and lowered.find(">", after) >= 0:
                if best is None or pos < best[0]:
                    best = (pos, tag)
                break
            pos = lowered.find("<" + tag, pos + 1)
    return best


def _find_block_close(lowered: str, pos: int, tag: str):
    close = lowered.find("</" + tag, pos + 1)
    while close >= 0:
        after = close + 2 + len(tag)
        gt = lowered.find(">", after)
        if _tag_boundary_ok(lowered, after) and gt >= 0:
            return gt + 1
        close = lowered.find("</" + tag, close + 1)
    return None


def strip_tags_linear(text: str) -> str:
    """等价 `<[^>]+>` → 空格 的线性实现；未闭合的 `<` 剩余原样保留（正则亦不匹配）。"""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        lt = text.find("<", i)
        if lt < 0:
            out.append(text[i:])
            break
        gt = text.find(">", lt + 1)
        if gt < 0:
            out.append(text[i:])
            break
        if gt == lt + 1:  # "<>"：正则不匹配，原样保留
            out.append(text[i:gt + 1])
            i = gt + 1
            continue
        out.append(text[i:lt])
        out.append(" ")
        i = gt + 1
    return "".join(out)


def html_to_text(markup: str) -> str:
    """HTML → 纯文本（线性扫描，不回溯）。"""
    return html.unescape(strip_tags_linear(strip_embedded(markup)))


# ---------------------------------------------------------------- EPUB 顺序

CONTAINER_MEMBER = "META-INF/container.xml"
OPF_MEDIA_TYPE = "application/oebps-package+xml"
# spine 里只把「可当文本渲染」的条目算章节；图片/字体等非 HTML 项跳过。
TEXT_MEDIA_TYPES = frozenset(
    {"application/xhtml+xml", "text/html", "application/xml", "text/xml", ""}
)
# 没有 OPF 的容器只能按成员名序（提取层退化路径；阅读器视为损坏容器）。
TEXT_SCAN_FALLBACK = True


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_member(name: str) -> str:
    cleaned = unquote(name.replace("\\", "/").split("#", 1)[0].lstrip("/"))
    normalized = posixpath.normpath(cleaned)
    return "" if normalized == "." else normalized


def _resolve_member(base: str, href: str) -> str:
    return _normalize_member(posixpath.join(base, href) if base else href)


def _locate_opf_member(archive: zipfile.ZipFile) -> str:
    """container.xml → OPF 成员名；结构不成立抛 ValueError。"""
    try:
        raw = archive.read(CONTAINER_MEMBER)
    except KeyError:
        raise ValueError("epub 缺 META-INF/container.xml") from None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError("epub container.xml 不是合法 XML") from None
    rootfiles = [element for element in root.iter() if _localname(element.tag) == "rootfile"]
    preferred = [
        element
        for element in rootfiles
        if (element.get("media-type") or "").strip() == OPF_MEDIA_TYPE
    ]
    for element in preferred or rootfiles:
        full_path = (element.get("full-path") or "").strip()
        if full_path:
            return _normalize_member(full_path)
    raise ValueError("epub container.xml 没有 rootfile")


def epub_opf_members(archive: zipfile.ZipFile) -> list[str]:
    """按 OPF spine 顺序返回正文章节成员名（顺序只认 spine，不认文件名）。

    结构不成立（缺 container、缺 OPF、spine 为空）抛 ValueError，由调用方决定
    是报损坏（阅读器）还是退化为成员名序（提取层）。
    """
    opf_member = _locate_opf_member(archive)
    try:
        raw = archive.read(opf_member)
    except KeyError:
        raise ValueError("epub 缺 OPF 文件") from None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ValueError("epub OPF 不是合法 XML") from None

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
        if media_type not in TEXT_MEDIA_TYPES:
            continue
        spine.append(_resolve_member(base, href))
    if not spine:
        raise ValueError("epub spine 为空")
    return spine