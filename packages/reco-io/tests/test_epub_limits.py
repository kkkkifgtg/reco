"""CWE-409 回归：EPUB 解压上限与高压缩比预筛，超限诚实降级不整段入内存。"""
from __future__ import annotations

import zipfile

import reco_io.features as features
from reco_io.features import extract_features


def _write_epub(path, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("chapter1.xhtml", payload, compress_type=zipfile.ZIP_DEFLATED)


def test_normal_epub_still_extracts(tmp_path):
    epub = tmp_path / "ok.epub"
    _write_epub(epub, ("<html><body>" + "正常文本。" * 200 + "</body></html>").encode())
    res = extract_features(epub)
    assert res.stats is not None
    assert res.quality_flag == "ok"


def test_high_ratio_member_degrades(tmp_path):
    epub = tmp_path / "bomb.epub"
    _write_epub(epub, ("<html><body>" + "a" * 1_000_000 + "</body></html>").encode())
    res = extract_features(epub)
    assert res.quality_flag == "undecodable"  # 读取器 ValueError → undecodable 口径
    assert res.stats is None


def test_declared_member_cap_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "_MAX_DECOMPRESSED_MEMBER_BYTES", 64)
    epub = tmp_path / "tiny-cap.epub"
    _write_epub(epub, ("<html><body>" + "x" * 200 + "</body></html>").encode())
    res = extract_features(epub)
    assert res.quality_flag == "undecodable"
