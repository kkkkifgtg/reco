"""#112：阅读与提取共用一套解码/剥离/spine 口径。

修复前：阅读器自带回溯正则剥 script/style（病态输入平方级）、四份解码链回退顺序不一，
提取层的 epub 按 zip 成员名排序（不认 OPF spine），1M 字窗口截断没有任何断言。
本文件锁定四条：病态输入限时、spine 顺序、截断窗口、解码链唯一。
"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

import reco_io.features as features
import reco_io.mining as mining
import reco_io.textkit as textkit
import reco_ui.reader as reader


# ---------------------------------------------------------- 合成 EPUB


def _xhtml(title: str, body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"<title>{title}</title></head><body>{body}</body></html>"
    ).encode("utf-8")


def _container(opf: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        f'<rootfiles><rootfile full-path="{opf}" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )


def make_epub(path: Path, chapters: list[tuple[str, bytes]], spine: list[str] | None = None) -> Path:
    """zipfile 手搓最小 EPUB；spine 故意可以 ≠ 成员名序。"""
    opf_member = "OEBPS/content.opf"
    names = [name for name, _ in chapters]
    order = list(spine) if spine is not None else list(names)
    ids = {name: f"id{i}" for i, name in enumerate(names)}
    manifest = "".join(
        f'<item id="{ids[n]}" href="{n}" media-type="application/xhtml+xml"/>' for n in names
    )
    itemrefs = "".join(f'<itemref idref="{ids[n]}"/>' for n in order)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        f"<manifest>{manifest}</manifest><spine>{itemrefs}</spine></package>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", _container(opf_member))
        zf.writestr(opf_member, opf)
        for name, data in chapters:
            zf.writestr(f"OEBPS/{name}", data)
    return path


# ---------------------------------------------------------- 1. 病态输入限时


def test_pathological_epub_renders_within_time_limit(tmp_path):
    """200 万个 `<script` 起始、只有一个 `>`：回溯正则会在这里平方级爆炸。"""
    pathological = "<script " * 250_000 + ">"
    epub = make_epub(
        tmp_path / "bad.epub",
        [("c1.xhtml", _xhtml("甲章", f"<p>正文甲</p>{pathological}<p>正文乙</p>"))],
    )

    started = time.perf_counter()
    chapters = reader.parse_epub_spine(epub)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"阅读器渲染耗时 {elapsed:.2f}s，线性实现不该这么慢"
    assert "正文甲" in chapters[0]["text"] and "正文乙" in chapters[0]["text"]

    started = time.perf_counter()
    extracted = features._epub_text(epub)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"提取层耗时 {elapsed:.2f}s"
    assert "正文甲" in extracted and "正文乙" in extracted


# ---------------------------------------------------------- 2. spine 顺序


def test_extraction_follows_opf_spine_not_member_names(tmp_path):
    epub = make_epub(
        tmp_path / "spine.epub",
        [("a-first.xhtml", _xhtml("甲", "<p>正文甲</p>")),
         ("z-second.xhtml", _xhtml("乙", "<p>正文乙</p>"))],
        spine=["z-second.xhtml", "a-first.xhtml"],  # spine 序 ≠ 成员名序
    )
    chapters = reader.parse_epub_spine(epub)
    assert [c["text"] for c in chapters] == ["正文乙", "正文甲"]
    extracted = features._epub_text(epub)
    assert extracted.index("正文乙") < extracted.index("正文甲"), "提取层必须跟着 spine 走"


def test_extraction_falls_back_to_member_order_without_opf(tmp_path):
    """没有 OPF 的合成容器：提取层退化为成员名序（不因此拒读整本书）。"""
    epub = tmp_path / "noopf.epub"
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("b.xhtml", _xhtml("乙", "<p>正文乙</p>"))
        zf.writestr("a.xhtml", _xhtml("甲", "<p>正文甲</p>"))
    extracted = features._epub_text(epub)
    assert extracted.index("正文甲") < extracted.index("正文乙")
    with pytest.raises(reader.ReaderError) as excinfo:
        reader.parse_epub_spine(epub)
    assert excinfo.value.reason == "corrupt_container"


# ---------------------------------------------------------- 3. 截断窗口


def test_ngram_window_is_first_million_chars():
    window = features.DEFAULT_REPEAT_WINDOW_CHARS
    assert window == 1_000_000, "窗口口径变了就要同步文档与测试"
    total = 1_200_000
    text = "".join(chr(0x4E00 + (i % 500)) for i in range(total))

    acc = features._Accumulator(
        topk=features.DEFAULT_TOPK,
        ngram_range=features.DEFAULT_NGRAM_RANGE,
        high_freq_min=features.DEFAULT_HIGH_FREQ_MIN,
        repeat_window_chars=window,
        alias_probe=(),
        num_buckets=features.DEFAULT_NUM_BUCKETS,
        hash_fn=None,
    )
    acc.feed(text)
    stats, buckets = acc.finish()

    assert stats["char_count"] == total, "统计量吃全文，不截断"
    expected = features.char_ngram_buckets(
        text[:window],
        ngram_range=features.DEFAULT_NGRAM_RANGE,
        num_buckets=features.DEFAULT_NUM_BUCKETS,
        hash_fn=None,
    )
    assert buckets == expected, "n-gram 桶只吃前 1M 字窗口（确定性前缀，不是随机采样）"


# ---------------------------------------------------------- 4. 解码链唯一


def test_one_decode_chain_across_reader_mining_and_features(tmp_path):
    assert reader.decode_bytes is textkit.decode_bytes
    assert features._strip_blocks_linear is textkit.strip_embedded

    gb = tmp_path / "gb.txt"
    gb.write_bytes("正文甲\r\n第二行".encode("gb18030"))
    assert reader.load_book_text(gb) == "正文甲\n第二行"
    assert mining._decode_text(gb) == "正文甲\n第二行"

    utf16 = tmp_path / "u16.txt"
    utf16.write_bytes(b"\xff\xfe" + "正文乙\r\n".encode("utf-16-le"))
    assert reader.load_book_text(utf16) == "正文乙\n"
    assert mining._decode_text(utf16) == "正文乙\n"
    assert textkit.decode_bytes(utf16.read_bytes()) == "正文乙\n"