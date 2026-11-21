"""N1——每专区三态再扫描同步（sync_zone 纯层测试）。

FakeStat 注入（§13）：跨卷/移动/超界身份不需要真实第二块盘。
身份域全程无符号；落库编码（int64 补码回绕）由 test_int64_contract.py 契约钉死；
store 投影语义由 test_file_resync_projection.py 钉死；本文件钉同步装配语义。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from reco_io.int64 import to_int64
from reco_store import open_store


@pytest.fixture()
def store(tmp_path):
    s = open_store(tmp_path / "sync.db")
    yield s
    s.close()


def _fs_zone(tmp_path: Path, zone: str) -> Path:
    d = tmp_path / zone
    d.mkdir(parents=True, exist_ok=True)
    return d


class FakeStat:
    """os.stat 形态桩：st_file_attributes=0 保证不被隐藏/系统位过滤。"""

    def __init__(self, st_dev: int, st_ino: int, st_size: int, st_mtime_ns: int) -> None:
        self.st_dev = st_dev
        self.st_ino = st_ino
        self.st_size = st_size
        self.st_mtime_ns = st_mtime_ns
        self.st_file_attributes = 0
        self.st_mode = 0


def _provider_for(mapping: dict):
    def provider(path, follow_symlinks=False):
        st = mapping.get(os.path.abspath(str(path)))
        return st if st is not None else os.stat(path, follow_symlinks=follow_symlinks)

    return provider


def _put(zone_dir: Path, name: str, content: bytes, mapping: dict, st: FakeStat) -> Path:
    p = zone_dir / name
    p.write_bytes(content)
    mapping[os.path.abspath(str(p))] = st
    return p


def _files_rows(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT path, zone, st_dev, st_ino, present, book_id, moved_from_path FROM files"
        ).fetchall()
    finally:
        conn.close()


def test_added_file_lands_event(tmp_path, store):
    from reco_ui.sync import sync_zone

    zone_dir = _fs_zone(tmp_path, "liked")
    mapping: dict = {}
    _put(zone_dir, "a.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    result = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert (result.added, result.removed, result.moved) == (1, 0, 0)
    rows = _files_rows(store)
    assert len(rows) == 1 and rows[0][1] == "liked" and rows[0][4] == 1
    assert len(store.read_events()) == 1


def test_removed_file_marks_tombstone(tmp_path, store):
    from reco_ui.sync import sync_zone

    zone_dir = _fs_zone(tmp_path, "liked")
    mapping: dict = {}
    p = _put(zone_dir, "a.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    mapping.pop(os.path.abspath(str(p)))
    p.unlink()
    result = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert (result.added, result.removed, result.moved) == (0, 1, 0)
    rows = _files_rows(store)
    assert len(rows) == 1 and rows[0][4] == 0, "删除必须落墓碑（present=0），不物理删行"


def test_moved_same_zone_keeps_book_id(tmp_path, store):
    from reco_ui.sync import sync_zone

    zone_dir = _fs_zone(tmp_path, "liked")
    mapping: dict = {}
    old = _put(zone_dir, "a.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    book_before = _files_rows(store)[0][5]
    st = mapping.pop(os.path.abspath(str(old)))
    new = zone_dir / "b.txt"
    old.rename(new)
    mapping[os.path.abspath(str(new))] = st
    result = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert (result.added, result.removed, result.moved) == (0, 0, 1), "同专区移动必须按身份配对为移动"
    rows = _files_rows(store)
    assert len(rows) == 1
    assert rows[0][0] == os.path.abspath(str(new)) and rows[0][4] == 1
    assert rows[0][6] == os.path.abspath(str(old)), "moved_from_path 必须记录原路径"
    assert rows[0][5] == book_before, "移动保留标注：book_id 不变"


def test_moved_cross_zone_relands_added(tmp_path, store):
    from reco_ui.sync import sync_zone

    liked = _fs_zone(tmp_path, "liked")
    cand = _fs_zone(tmp_path, "candidate")
    mapping: dict = {}
    p = _put(liked, "a.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    r1 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert r1.added == 1
    r2 = sync_zone(store, "candidate", tmp_path, stat_provider=_provider_for(mapping))
    assert (r2.added, r2.removed, r2.moved) == (0, 0, 0), "候选专区无前史：零动作"
    st = mapping.pop(os.path.abspath(str(p)))
    newp = cand / "a.txt"
    p.rename(newp)
    mapping[os.path.abspath(str(newp))] = st
    r3 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    r4 = sync_zone(store, "candidate", tmp_path, stat_provider=_provider_for(mapping))
    assert r3.removed == 1, "源专区按删除落墓碑"
    assert r4.added == 1, "目标专区无前史：按新增落库（本票口径）"
    rows = _files_rows(store)
    liked_rows = [r for r in rows if r[1] == "liked"]
    cand_rows = [r for r in rows if r[1] == "candidate"]
    assert liked_rows[0][4] == 0 and cand_rows[0][4] == 1
    assert liked_rows[0][5] == cand_rows[0][5], "dedup 归并：同一本书"


def test_moved_in_from_unscanned_zone_lands_added(tmp_path, store):
    from reco_ui.sync import sync_zone

    liked = _fs_zone(tmp_path, "liked")
    cand = _fs_zone(tmp_path, "candidate")
    mapping: dict = {}
    p = _put(cand, "x.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    r1 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert (r1.added, r1.removed, r1.moved) == (0, 0, 0)
    st = mapping.pop(os.path.abspath(str(p)))
    newp = liked / "x.txt"
    p.rename(newp)
    mapping[os.path.abspath(str(newp))] = st
    r2 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert (r2.added, r2.removed, r2.moved) == (1, 0, 0), "跨专区移入（目标专区无前史）按新增落库"
    rows = [r for r in _files_rows(store) if r[1] == "liked"]
    assert rows[0][4] == 1


def test_resync_without_changes_appends_nothing(tmp_path, store):
    from reco_ui.sync import SyncResult, sync_zone

    zone_dir = _fs_zone(tmp_path, "liked")
    mapping: dict = {}
    _put(zone_dir, "a.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    _put(zone_dir, "b.txt", b"y", mapping, FakeStat(1, 11, 200, 222))
    r1 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert r1.added == 2
    n_events = len(store.read_events())
    r2 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert r2 == SyncResult(added=0, removed=0, moved=0, changed=0), "无变化必须零事件（幂等）"
    assert len(store.read_events()) == n_events


def test_content_change_refreshes_stats_via_file_added_upsert(tmp_path, store):
    from reco_ui.sync import sync_zone

    zone_dir = _fs_zone(tmp_path, "liked")
    mapping: dict = {}
    p = _put(zone_dir, "a.txt", b"x", mapping, FakeStat(1, 10, 100, 111))
    r1 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert r1.added == 1
    n_events = len(store.read_events())
    mapping[os.path.abspath(str(p))] = FakeStat(1, 10, 200, 999)  # 同身份同路径：内容变化
    r2 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert (r2.added, r2.removed, r2.moved, r2.changed) == (0, 0, 0, 1)
    assert len(store.read_events()) == n_events + 1, "内容变化经 file_added upsert 刷新统计（收敛，不逐轮重报）"
    conn = store.read_conn()
    try:
        size, mtime = conn.execute("SELECT size_bytes, mtime_ns FROM files").fetchone()
    finally:
        conn.close()
    assert (size, mtime) == (200, 999)


def test_huge_identity_encoded_as_int64_wrapped(tmp_path, store):
    from reco_ui.sync import sync_zone

    zone_dir = _fs_zone(tmp_path, "liked")
    mapping: dict = {}
    huge_dev, huge_ino = (1 << 63) + 5, (1 << 64) - 7
    _put(zone_dir, "big.txt", b"x", mapping, FakeStat(huge_dev, huge_ino, 100, 111))
    result = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert result.added == 1
    rows = _files_rows(store)
    assert rows[0][2] == to_int64(huge_dev), "st_dev 落库必须是 int64 回绕编码"
    assert rows[0][3] == to_int64(huge_ino), "st_ino 落库必须是 int64 回绕编码"
    assert rows[0][2] % (1 << 64) == huge_dev
    assert rows[0][3] % (1 << 64) == huge_ino


def test_second_sync_same_zone_roundtrips_wrapped_identity(tmp_path, store):
    """回绕身份二次扫描：db 侧 int64 → 无符号还原 → 身份配对成功（不误判增删）。"""
    from reco_ui.sync import sync_zone

    zone_dir = _fs_zone(tmp_path, "liked")
    mapping: dict = {}
    huge_dev, huge_ino = (1 << 63) + 5, (1 << 64) - 7
    _put(zone_dir, "big.txt", b"x", mapping, FakeStat(huge_dev, huge_ino, 100, 111))
    r1 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert r1.added == 1
    r2 = sync_zone(store, "liked", tmp_path, stat_provider=_provider_for(mapping))
    assert (r2.added, r2.removed, r2.moved, r2.changed) == (0, 0, 0, 0), \
        "回绕身份必须从 db 还原配对，不产生虚假增删"
