"""工单 02——scan_zones：身份判定、扩展名过滤、忽略上报、专区检查。"""
from __future__ import annotations

import builtins
import os
from types import SimpleNamespace

import pytest

from reco_io import ensure_zones, scan_zones

DEFAULT_ZONES = ("liked", "candidate", "推荐专区")


def _write(root, zone: str, rel: str, content: bytes):
    path = root / zone / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_missing_zones_reported_without_creating(tmp_path):
    result = scan_zones(tmp_path)
    assert result.fingerprints == ()
    assert result.missing_zones == DEFAULT_ZONES
    assert result.zones_scanned == ()
    assert result.ignored == ()
    assert ensure_zones(tmp_path) == DEFAULT_ZONES
    assert list(tmp_path.iterdir()) == []  # 缺失专区只上报，绝不自动创建


def test_recursive_txt_epub_md_collected(tmp_path):
    _write(tmp_path, "liked", "a/one.txt", b"1")
    _write(tmp_path, "liked", "b/c/two.epub", b"22")
    _write(tmp_path, "candidate", "x/three.md", b"333")
    result = scan_zones(tmp_path)
    assert sorted((f.zone, f.ext) for f in result.fingerprints) == [
        ("candidate", ".md"),
        ("liked", ".epub"),
        ("liked", ".txt"),
    ]
    by_ext = {f.ext: f for f in result.fingerprints}
    assert by_ext[".txt"].path == os.path.abspath(tmp_path / "liked" / "a" / "one.txt")
    assert by_ext[".txt"].size_bytes == 1


def test_identity_from_os_stat_follow_symlinks_false(tmp_path):
    _write(tmp_path, "liked", "a/one.txt", b"hello")
    calls: list[tuple[str, bool]] = []

    def recording_provider(path, follow_symlinks=True):
        calls.append((os.fspath(path), follow_symlinks))
        return os.stat(path, follow_symlinks=follow_symlinks)

    result = scan_zones(tmp_path, stat_provider=recording_provider)
    fp = result.fingerprints[0]
    real = os.stat(fp.path, follow_symlinks=False)
    assert (fp.st_dev, fp.st_ino) == (real.st_dev, real.st_ino)
    assert fp.st_ino != 0  # Windows 上 DirEntry.stat() 三字段恒 0；身份必须来自 os.stat
    assert (fp.size_bytes, fp.mtime_ns) == (real.st_size, real.st_mtime_ns)
    assert calls and all(follow is False for _, follow in calls)


def test_non_text_files_ignored_names_only(tmp_path):
    _write(tmp_path, "liked", "a/one.txt", b"1")
    _write(tmp_path, "liked", "a/song.mp3", b"m")
    _write(tmp_path, "candidate", "notes.PDF", b"p")
    result = scan_zones(tmp_path)
    assert result.ignored == ("notes.PDF", "song.mp3")
    assert len(result.fingerprints) == 1
    for name in result.ignored:
        assert "/" not in name and "\\" not in name  # 只文件名，不带路径


def test_dot_and_hidden_attribute_entries_skipped(tmp_path):
    _write(tmp_path, "liked", ".git/inner/one.txt", b"1")
    _write(tmp_path, "liked", "visible.txt", b"2")
    _write(tmp_path, "liked", "secret.txt", b"3")
    mapping = {
        os.fspath(tmp_path / "liked" / "secret.txt"): SimpleNamespace(
            st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4,
            st_file_attributes=0x2),  # FILE_ATTRIBUTE_HIDDEN
    }

    def provider(path, follow_symlinks=False):
        st = mapping.get(os.fspath(path))
        return st if st is not None else os.stat(path, follow_symlinks=follow_symlinks)

    result = scan_zones(tmp_path, stat_provider=provider)
    assert [f.path for f in result.fingerprints] == [
        os.path.abspath(tmp_path / "liked" / "visible.txt"),
    ]


def test_corrupt_and_oversized_scanned_without_reading(tmp_path, monkeypatch):
    _write(tmp_path, "candidate", "broken.epub", b"PK\x03\x04trunc")
    _write(tmp_path, "candidate", "bad.txt", b"\xff\xfe\xfa\xfa")
    small_real = _write(tmp_path, "candidate", "huge.txt", b"tiny")
    mapping = {
        os.fspath(small_real): SimpleNamespace(
            st_dev=1, st_ino=9, st_size=53_000_000, st_mtime_ns=42,
            st_file_attributes=0),
    }

    def provider(path, follow_symlinks=False):
        st = mapping.get(os.fspath(path))
        return st if st is not None else os.stat(path, follow_symlinks=follow_symlinks)

    def no_open(*args, **kwargs):
        raise AssertionError("scan_zones 不得打开/读取正文")

    monkeypatch.setattr(builtins, "open", no_open)
    result = scan_zones(tmp_path, stat_provider=provider)
    assert len(result.fingerprints) == 3
    huge = next(f for f in result.fingerprints if f.path.endswith("huge.txt"))
    assert huge.size_bytes == 53_000_000
    assert {f.ext for f in result.fingerprints} == {".txt", ".epub"}


def test_symlink_to_text_file_skipped(tmp_path):
    target = _write(tmp_path, "liked", "real.txt", b"1")
    link = tmp_path / "liked" / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("当前环境无法创建符号链接")
    result = scan_zones(tmp_path)
    assert [os.path.basename(f.path) for f in result.fingerprints] == ["real.txt"]


def test_existing_empty_zones_scan_success(tmp_path):
    """合成矩阵·空目录：三专区都存在但为空——扫描成功、0 文件。"""
    for zone in DEFAULT_ZONES:
        (tmp_path / zone).mkdir()
    result = scan_zones(tmp_path)
    assert result.fingerprints == ()
    assert result.missing_zones == ()
    assert result.zones_scanned == DEFAULT_ZONES
    assert result.ignored == ()


def test_direntry_stat_zeros_regression(tmp_path):
    """F-5/R1 回归防护：Windows 上 DirEntry.stat() 三字段恒 0，身份必须走 os.stat。"""
    _write(tmp_path, "liked", "a/one.txt", b"hello")
    entry_stat = None
    with os.scandir(tmp_path / "liked" / "a") as it:
        for entry in it:
            if entry.name == "one.txt":
                entry_stat = entry.stat()
    assert entry_stat is not None
    if os.name != "nt":
        pytest.skip("DirEntry.stat 零字段是 Windows 特有行为（F-5）")
    assert (entry_stat.st_dev, entry_stat.st_ino, entry_stat.st_nlink) == (0, 0, 0)
    result = scan_zones(tmp_path)
    fp = result.fingerprints[0]
    real = os.stat(fp.path, follow_symlinks=False)
    assert (fp.st_dev, fp.st_ino) == (real.st_dev, real.st_ino) != (0, 0)
