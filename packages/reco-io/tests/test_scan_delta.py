"""工单 02——classify_delta：新增/移动/删除/内容变化；标注(book_id)随移动保留。"""
from __future__ import annotations

import dataclasses
import os
from types import SimpleNamespace

from reco_io import FileFingerprint, classify_delta, scan_zones


def _write(root, zone: str, rel: str, content: bytes):
    path = root / zone / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _fake(mapping):
    def provider(path, follow_symlinks=False):
        st = mapping.get(os.fspath(path))
        return st if st is not None else os.stat(path, follow_symlinks=follow_symlinks)

    return provider


def test_first_scan_all_added(tmp_path):
    _write(tmp_path, "liked", "a.txt", b"x")
    _write(tmp_path, "candidate", "b.md", b"y")
    result = scan_zones(tmp_path)
    delta = classify_delta((), result.fingerprints)
    assert len(delta.added) == 2
    assert delta.removed == () and delta.moved == () and delta.changed == ()


def test_rename_is_move_preserving_book_id(tmp_path):
    old = _write(tmp_path, "liked", "a/one.txt", b"body")
    first = scan_zones(tmp_path)
    previous = [dataclasses.replace(f, book_id=i) for i, f in enumerate(first.fingerprints)]
    new = old.with_name("renamed.txt")
    os.rename(old, new)
    second = scan_zones(tmp_path)
    delta = classify_delta(previous, second.fingerprints)
    assert len(delta.moved) == 1
    mv = delta.moved[0]
    assert mv.previous.path == os.path.abspath(old)
    assert mv.current.path == os.path.abspath(new)
    assert mv.current.book_id == mv.previous.book_id == 0  # 标注随移动保留
    assert delta.added == () and delta.removed == ()


def test_cross_zone_move_keeps_identity(tmp_path):
    old = _write(tmp_path, "liked", "one.txt", b"body")
    (tmp_path / "candidate").mkdir()  # 目标专区已存在；跨专区移动不创建目录
    first = scan_zones(tmp_path)
    previous = [dataclasses.replace(f, book_id=7) for f in first.fingerprints]
    new = tmp_path / "candidate" / "one.txt"
    os.rename(old, new)
    second = scan_zones(tmp_path)
    delta = classify_delta(previous, second.fingerprints)
    assert len(delta.moved) == 1
    assert delta.moved[0].current.zone == "candidate"
    assert delta.moved[0].current.book_id == 7


def test_fakestat_move_and_cross_volume_split(tmp_path):
    a = _write(tmp_path, "liked", "a.txt", b"1")
    b = _write(tmp_path, "liked", "b.txt", b"2")
    dev = 12345

    def entry(ino):
        return SimpleNamespace(st_dev=dev, st_ino=ino, st_size=1, st_mtime_ns=1)

    first_map = {os.fspath(a): entry(100), os.fspath(b): entry(200)}
    previous = scan_zones(tmp_path, stat_provider=_fake(first_map)).fingerprints
    assert sorted(f.st_ino for f in previous) == [100, 200]

    os.remove(a)
    c = _write(tmp_path, "liked", "c.txt", b"3")
    second_map = {os.fspath(b): entry(200), os.fspath(c): entry(100)}
    second = scan_zones(tmp_path, stat_provider=_fake(second_map))
    delta = classify_delta(previous, second.fingerprints)
    assert len(delta.moved) == 1
    assert delta.moved[0].previous.path == os.path.abspath(a)
    assert delta.moved[0].current.path == os.path.abspath(c)
    assert delta.added == () and delta.removed == ()

    # 跨卷（dev 不同）：同 ino 换卷绝不配对成移动——判删+增。
    # z 携带与 a 相同的 ino 且卷号更低（快照排序中 z 先于 b）：
    # 若身份判定漏掉 st_dev，会把 a 与 z 误配成移动。
    os.remove(c)
    z = _write(tmp_path, "candidate", "z.txt", b"4")
    cross_map = {
        os.fspath(b): entry(200),  # 锚定 b 的合成身份，避免真实 stat 泄漏进快照
        os.fspath(z): SimpleNamespace(st_dev=dev - 1, st_ino=100, st_size=1, st_mtime_ns=1),
    }
    third = scan_zones(tmp_path, stat_provider=_fake(cross_map))
    delta2 = classify_delta(previous, third.fingerprints)
    assert delta2.moved == ()
    assert [f.path for f in delta2.removed] == [os.path.abspath(a)]
    assert [f.path for f in delta2.added] == [os.path.abspath(z)]


def test_content_change_same_identity(tmp_path):
    f = _write(tmp_path, "candidate", "story.txt", b"short")
    first = scan_zones(tmp_path)
    previous = [dataclasses.replace(x, book_id=3) for x in first.fingerprints]
    f.write_bytes(b"much longer body than before")
    second = scan_zones(tmp_path)
    delta = classify_delta(previous, second.fingerprints)
    assert len(delta.changed) == 1
    ch = delta.changed[0]
    assert ch.previous.path == ch.current.path
    assert ch.current.size_bytes > ch.previous.size_bytes
    assert ch.current.book_id == 3  # 内容变化保留标注
    assert delta.moved == () and delta.added == () and delta.removed == ()


def test_removal_reported(tmp_path):
    f = _write(tmp_path, "liked", "gone.txt", b"x")
    first = scan_zones(tmp_path)
    previous = [dataclasses.replace(x, book_id=5) for x in first.fingerprints]
    os.remove(f)
    second = scan_zones(tmp_path)
    delta = classify_delta(previous, second.fingerprints)
    assert len(delta.removed) == 1
    assert delta.removed[0].book_id == 5
    assert delta.added == () and delta.moved == () and delta.changed == ()


def test_same_name_different_content_not_moved(tmp_path):
    """合成矩阵·同名不同内容：两专区同名文件、内容不同——两个身份、互不误判为移动。"""
    _write(tmp_path, "liked", "story.txt", b"liked version body")
    _write(tmp_path, "candidate", "story.txt", b"candidate version, longer body")
    first = scan_zones(tmp_path)
    assert len(first.fingerprints) == 2
    inos = sorted(f.st_ino for f in first.fingerprints)
    sizes = sorted(f.size_bytes for f in first.fingerprints)
    assert inos[0] != inos[1]  # 不同文件 → 不同身份
    assert sizes[0] != sizes[1]
    delta = classify_delta((), first.fingerprints)
    assert len(delta.added) == 2
    assert delta.moved == () and delta.changed == ()

    second = scan_zones(tmp_path)
    delta2 = classify_delta(first.fingerprints, second.fingerprints)
    assert delta2.added == () and delta2.removed == ()
    assert delta2.moved == () and delta2.changed == ()  # 同名不同内容绝不互相配对


def test_unchanged_tree_empty_delta(tmp_path):
    _write(tmp_path, "liked", "a.txt", b"x")
    _write(tmp_path, "candidate", "b/c.md", b"y")
    first = scan_zones(tmp_path)
    second = scan_zones(tmp_path)
    delta = classify_delta(first.fingerprints, second.fingerprints)
    assert delta.added == () and delta.removed == ()
    assert delta.moved == () and delta.changed == ()


def test_hardlink_duplicate_identity_deterministic(tmp_path):
    """原路径消失后同一身份出现多个新路径（硬链接形态）：
    按路径字典序先到先配移动（继承标注），其余计新增。"""
    a = _write(tmp_path, "liked", "a.txt", b"1")

    def st():
        return SimpleNamespace(st_dev=9, st_ino=777, st_size=1, st_mtime_ns=5)

    previous = (
        FileFingerprint(
            st_dev=9, st_ino=777, size_bytes=1, mtime_ns=5,
            path=os.path.abspath(a), zone="liked", ext=".txt", book_id=4,
        ),
    )
    current = tuple(sorted(
        FileFingerprint(
            st_dev=9, st_ino=777, size_bytes=1, mtime_ns=5,
            path=os.path.abspath(tmp_path / "liked" / name),
            zone="liked", ext=".txt",
        )
        for name in ("b.txt", "c.txt")
    ))
    delta = classify_delta(previous, current)
    assert len(delta.moved) == 1
    assert delta.moved[0].previous.path == os.path.abspath(a)
    assert delta.moved[0].current.path == os.path.abspath(tmp_path / "liked" / "b.txt")
    assert delta.moved[0].current.book_id == 4
    assert [f.path for f in delta.added] == [
        os.path.abspath(tmp_path / "liked" / "c.txt"),
    ]
