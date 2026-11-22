"""CWE-61 回归：目标位置的 symlink（含悬空）不得被跟随写入。"""
from __future__ import annotations

import os

import pytest

from reco_io.placement import place_by_hardlink


@pytest.mark.skipif(os.name == "nt", reason="Windows 建 symlink 需特权；POSIX 覆盖")
def test_dangling_symlink_is_conflict_not_target(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("正文", encoding="utf-8")
    dst_dir = tmp_path / "zone"
    dst_dir.mkdir()
    outside = tmp_path / "outside.txt"
    (dst_dir / "book.txt").symlink_to(outside)
    res = place_by_hardlink(src, dst_dir)
    assert res.dst.name == "book(1).txt"
    assert (dst_dir / "book(1).txt").exists()
    assert not outside.exists()
