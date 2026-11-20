"""工单 04——extract_features 降级矩阵、泄漏审计、perf 打点（红测试，S11/S12）。

全部注入阈值/时钟/块大小，不用真实 51MB 文件；tmp_path 内全合成数据（B5）。
读完即弃：正文不出结果、不落临时文件、目录快照前后一致（B2/审计断言）。
"""
from __future__ import annotations

import json
import os
import zipfile

import pytest

import reco_io.perf as perf
from reco_io.features import (
    EXTRACTOR_VERSION,
    extract_features,
    extract_statistics,
)

TEXT_S = "一二“你好呀朋友”三四。\n\n——好的。五\n"


def _write(root, name, data):
    p = root / name
    p.write_bytes(data)
    return p


def test_extractor_version_pinned():
    assert EXTRACTOR_VERSION == "reco-stats/1"


def test_ok_utf8_below_threshold(tmp_path):
    p = _write(tmp_path, "ok.txt", "你好，世界。".encode("utf-8"))
    r = extract_features(p)
    assert r.extractor_version == EXTRACTOR_VERSION
    assert r.quality_flag == "ok"
    assert r.stats_complete is True
    assert r.stats["char_count"] == 6


def test_streamed_equals_whole_read(tmp_path):
    """阈值注入后走流式路径：全部统计量与整读逐项相等（同一累加器两条驱动路径）。"""
    p = _write(tmp_path, "s.txt", TEXT_S.encode("utf-8"))
    r = extract_features(p, oversized_threshold_bytes=16, chunk_bytes=7)
    assert r.quality_flag == "oversized_streamed"
    assert r.stats_complete is True
    assert r.stats == extract_statistics(TEXT_S)
    assert r.stats["punct_density"] == pytest.approx(1 / 3, abs=1e-6)  # TEXT_S：6 标点/18 非空白字符，手算钉死流式数值


def test_empty_file_zero_truth(tmp_path):
    p = _write(tmp_path, "empty.txt", b"")
    r = extract_features(p)
    assert r.quality_flag == "empty"
    assert r.stats_complete is True
    assert r.stats["char_count"] == 0
    assert r.stats["paragraph_count"] == 0
    assert r.ngram_buckets == ()


def test_whitespace_only_is_empty(tmp_path):
    p = _write(tmp_path, "ws.txt", "\n\n  \t".encode("utf-8"))
    r = extract_features(p)
    assert r.quality_flag == "empty"
    assert r.stats["char_count"] == 0


def test_undecodable_no_fabrication(tmp_path):
    p = _write(tmp_path, "bad.txt", b"\x80\x80\x80")
    r = extract_features(p)
    assert r.quality_flag == "undecodable"
    assert r.stats_complete is False
    assert r.stats is None
    assert r.ngram_buckets is None
    assert r.char_freq == ()


def test_gb18030_decoded(tmp_path):
    p = _write(tmp_path, "gb.txt", "你好".encode("gb18030"))
    r = extract_features(p)
    assert r.quality_flag == "ok"
    assert r.stats["char_count"] == 2
    assert r.stats["cjk_char_count"] == 2


def test_utf8_bom_stripped(tmp_path):
    p = _write(tmp_path, "bom.txt", b"\xef\xbb\xbf" + "你好".encode("utf-8"))
    r = extract_features(p)
    assert r.quality_flag == "ok"
    assert r.stats["char_count"] == 2


def test_pending_timeout_keeps_partial_truth(tmp_path):
    p = _write(tmp_path, "slow.txt", ("一二三" * 10).encode("utf-8"))
    ticks = iter([0.0, 10.0, 10.0, 10.0])
    r = extract_features(
        p, chunk_bytes=7, budget_seconds=5.0, time_provider=lambda: next(ticks)
    )
    assert r.quality_flag == "pending_timeout"
    assert r.stats_complete is False
    assert r.ngram_buckets is None
    assert r.stats["char_count"] == 2  # 首块真值（「一二」；三 的首字节被增量解码器持有）


def test_epub_two_members_and_css_ignored(tmp_path):
    p = tmp_path / "book.epub"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("OEBPS/1.xhtml", "<html><body><p>第一幕</p></body></html>")
        zf.writestr("OEBPS/style.css", "body { color: red }")
        zf.writestr("OEBPS/2.xhtml", "<html><body><p>第二幕</p></body></html>")
    r = extract_features(p)
    assert r.quality_flag == "ok"
    assert r.stats["char_count"] == 6
    assert r.stats["paragraph_count"] == 2
    assert r.stats["avg_paragraph_chars"] == pytest.approx(3.0)


def test_epub_corrupt_container(tmp_path):
    p = _write(tmp_path, "bad.epub", b"PK\x03\x04trunc")
    r = extract_features(p)
    assert r.quality_flag == "corrupt_container"
    assert r.stats_complete is False
    assert r.stats is None


def test_no_body_leak_in_result(tmp_path):
    canary = "金丝雀绝密正文"
    p = _write(tmp_path, "leak.txt", (canary + "\n").encode("utf-8"))
    r = extract_features(p)
    blob = json.dumps(
        {
            "extractor_version": r.extractor_version,
            "quality_flag": r.quality_flag,
            "stats_complete": r.stats_complete,
            "stats": r.stats,
            "char_freq": r.char_freq,
            "ngram_buckets": r.ngram_buckets,
        },
        ensure_ascii=False,
        default=str,
    )
    assert "金丝雀" not in blob  # 正文片段（≥2 字连续串）绝不出结果（B2）
    assert "绝密" not in blob
    assert all(isinstance(s, int) and isinstance(c, int) for s, c in r.ngram_buckets)


def _boom(name):
    def _f(*args, **kwargs):
        raise AssertionError(f"特征提取不得创建临时文件：tempfile.{name}")

    return _f


def _snapshot(root):
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            fp = os.path.join(dirpath, name)
            out.append((os.path.relpath(fp, root), os.path.getsize(fp)))
    return sorted(out)


def test_no_temp_files_no_disk_writes(tmp_path, monkeypatch):
    import tempfile

    for name in ("NamedTemporaryFile", "mkstemp", "mkdtemp"):
        monkeypatch.setattr(tempfile, name, _boom(name))
    _write(tmp_path, "in.txt", TEXT_S.encode("utf-8"))
    before = _snapshot(tmp_path)
    extract_features(tmp_path / "in.txt")
    extract_features(tmp_path / "in.txt", oversized_threshold_bytes=16, chunk_bytes=7)
    assert _snapshot(tmp_path) == before  # 读完即弃：目录快照前后一致


def test_perf_span_emitted(tmp_path, capsys):
    _write(tmp_path, "in.txt", "你好".encode("utf-8"))
    perf.perf_enabled = True
    try:
        extract_features(tmp_path / "in.txt")
    finally:
        perf.perf_enabled = False
    err = capsys.readouterr().err
    assert "phase=features.extract" in err


def test_alias_counts_passthrough_and_degraded(tmp_path):
    p = _write(tmp_path, "alias.txt", "师尊来了。".encode("utf-8"))
    r = extract_features(p, alias_probe=("师尊", "徒儿"))
    assert r.alias_counts == {"师尊": 1, "徒儿": 0}  # probe 全量出现：真零记 0
    p2 = _write(tmp_path, "bad2.txt", b"\x80\x80\x80")
    r2 = extract_features(p2, alias_probe=("师尊",))
    assert r2.quality_flag == "undecodable"
    assert r2.alias_counts == {}


def test_alias_counts_stream_boundary_safe(tmp_path):
    text = "一二三四五师尊六七"
    p = _write(tmp_path, "span.txt", text.encode("utf-8"))
    r = extract_features(p, oversized_threshold_bytes=8, chunk_bytes=4, alias_probe=("师尊",))
    assert r.quality_flag == "oversized_streamed"
    assert r.alias_counts == {"师尊": 1}  # 别名跨块边界不漏计


def test_alias_counts_zero_truth_not_fabricated(tmp_path):
    p = _write(tmp_path, "noalias.txt", "今天天气不错".encode("utf-8"))
    r = extract_features(p, alias_probe=("师尊",))
    assert r.alias_counts == {"师尊": 0}  # 真零：出现 0 次如实记 0

