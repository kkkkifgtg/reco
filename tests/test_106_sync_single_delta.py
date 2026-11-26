"""#106：同步只有一套差分（reco_io.scanner.classify_delta）。

修复前 sync_zone 手写「path 键前史 + 身份四元组配对」，与 classify_delta 的
「身份双指针归并」并存：纯函数被抽出并测透，真 bug 面留在调用方的手写差分里。
两套语义的可见分歧：改名同时改内容时，手写版判「删+增」，身份版判「移动」。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from reco_io import scanner
from reco_store import open_store


@pytest.fixture()
def store(tmp_path):
    s = open_store(tmp_path / "delta.db")
    yield s
    s.close()


class FakeStat:
    def __init__(self, st_dev, st_ino, st_size, st_mtime_ns):
        self.st_dev = st_dev
        self.st_ino = st_ino
        self.st_size = st_size
        self.st_mtime_ns = st_mtime_ns
        self.st_file_attributes = 0
        self.st_mode = 0


def _provider(mapping: dict):
    def provider(path, follow_symlinks=False):
        st = mapping.get(os.path.abspath(str(path)))
        return st if st is not None else os.stat(path, follow_symlinks=follow_symlinks)

    return provider


def _put(zone_dir: Path, name: str, data: bytes, mapping: dict, st: FakeStat) -> Path:
    p = zone_dir / name
    p.write_bytes(data)
    mapping[os.path.abspath(str(p))] = st
    return p


def _row(store, path: str):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT path, zone, size_bytes, mtime_ns, present, moved_from_path"
            " FROM files WHERE path = ?", (path,)
        ).fetchone()
    finally:
        conn.close()


def test_sync_uses_the_one_classify_delta():
    from reco_ui import sync as sync_mod

    assert sync_mod.classify_delta is scanner.classify_delta, "差分必须只有一份实现"


def test_rename_with_content_change_is_a_move_not_add_plus_remove(tmp_path, store):
    from reco_ui.sync import sync_zone

    liked = tmp_path / "liked"
    liked.mkdir()
    mapping: dict = {}
    old = _put(liked, "旧名.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    assert sync_zone(store, "liked", tmp_path, stat_provider=_provider(mapping)).added == 1

    # 同一身份（st_dev, st_ino）改名，同时内容变长、mtime 前进
    st = mapping.pop(os.path.abspath(str(old)))
    new = liked / "新名.txt"
    old.rename(new)
    mapping[os.path.abspath(str(new))] = FakeStat(1, 10, 250, 999)

    result = sync_zone(store, "liked", tmp_path, stat_provider=_provider(mapping))
    assert (result.added, result.removed, result.moved) == (0, 0, 1), \
        "身份不变就是移动；此时判删+增会让标注和特征白丢"
    row = _row(store, os.path.abspath(str(new)))
    assert row is not None and row[4] == 1
    assert row[2:] == (250, 999, 1, os.path.abspath(str(old))), \
        "移动后的尺寸/mtime 必须被刷新（file_moved 只搬路径）"
    assert _row(store, os.path.abspath(str(old))) is None, "旧路径不留行"


def test_pure_rename_moved_counts_once(tmp_path, store):
    from reco_ui.sync import sync_zone

    liked = tmp_path / "liked"
    liked.mkdir()
    mapping: dict = {}
    old = _put(liked, "a.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    sync_zone(store, "liked", tmp_path, stat_provider=_provider(mapping))
    before = len(store.read_events())

    st = mapping.pop(os.path.abspath(str(old)))
    new = liked / "b.txt"
    old.rename(new)
    mapping[os.path.abspath(str(new))] = st
    result = sync_zone(store, "liked", tmp_path, stat_provider=_provider(mapping))

    assert (result.added, result.removed, result.moved, result.changed) == (0, 0, 1, 0)
    assert len(store.read_events()) == before + 1, "纯改名只落一条 file_moved"