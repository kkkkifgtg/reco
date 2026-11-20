"""目录扫描与三专区同步（工单 02；ARCHITECTURE §8 实现路径）。

- 单趟递归用 os.walk（scandir 驱动，PEP 471）；跳过隐藏/系统/点前缀项。
- 身份 (st_dev, st_ino) 只来自 os.stat(path, follow_symlinks=False)：
  Windows 上 DirEntry.stat() 的 st_dev/st_ino/st_nlink 恒为 0（F-5/R1），
  绝不用 DirEntry.stat 判身份。
- 内容变化 = (size_bytes, st_mtime_ns) 变化且身份不变；不读正文。
- 移动 = 同卷身份换路径（保留标注，book_id 随 delta 传递）。
- 专区缺失只上报（missing_zones），绝不自动创建（§8.1）。
"""
from __future__ import annotations

import os
import stat as stat_module
from collections.abc import Iterable
from dataclasses import dataclass, replace

from reco_io.perf import perf_span

DEFAULT_ZONES: tuple[str, ...] = ("liked", "candidate", "推荐专区")
TEXT_EXTS: frozenset[str] = frozenset({".txt", ".epub", ".md"})

_SKIP_DIRS = frozenset({".git", "$RECYCLE.BIN", "System Volume Information"})

# FILE_ATTRIBUTE_* 常量（Windows stat 专属，手工定值保持跨平台可导入）
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _os_stat(path, follow_symlinks: bool) -> os.stat_result:
    return os.stat(path, follow_symlinks=follow_symlinks)


def ensure_zones(root, zones: Iterable[str] = DEFAULT_ZONES) -> tuple[str, ...]:
    """确认专区集合，返回规范专区名元组（保持传入顺序）。

    缺失目录不自动创建（避免猜用户意图，§8.1）——缺失由 scan_zones
    以 missing_zones 上报。
    """
    resolved = (zones,) if isinstance(zones, str) else tuple(zones)
    if not resolved:
        raise ValueError("zones 不能为空")
    if not os.path.isdir(os.fspath(root)):
        raise NotADirectoryError(os.fspath(root))
    return resolved


@dataclass(frozen=True, order=True)
class FileFingerprint:
    """单文件指纹；排序键 (st_dev, st_ino, size, mtime, path) 确定性归并用。"""

    st_dev: int
    st_ino: int
    size_bytes: int
    mtime_ns: int
    path: str
    zone: str
    ext: str
    book_id: int | None = None  # 上层标注；scan 恒 None，classify_delta 传递


@dataclass(frozen=True)
class ScanResult:
    fingerprints: tuple[FileFingerprint, ...]
    missing_zones: tuple[str, ...]
    zones_scanned: tuple[str, ...]
    ignored: tuple[str, ...]  # 只文件名，不带路径


@dataclass(frozen=True)
class Moved:
    previous: FileFingerprint
    current: FileFingerprint  # 已继承 previous.book_id：移动即等于标注


@dataclass(frozen=True)
class Changed:
    previous: FileFingerprint
    current: FileFingerprint  # 已继承 previous.book_id


@dataclass(frozen=True)
class Delta:
    added: tuple[FileFingerprint, ...]
    removed: tuple[FileFingerprint, ...]
    moved: tuple[Moved, ...]
    changed: tuple[Changed, ...]


def scan_zones(
    root,
    zones: Iterable[str] = DEFAULT_ZONES,
    stat_provider=None,
) -> ScanResult:
    """递归扫描各专区，产出当前快照；stat_provider 可注入 FakeStat（§13）。"""
    resolved = ensure_zones(root, zones)
    root_path = os.path.abspath(os.fspath(root))
    provider = stat_provider if stat_provider is not None else _os_stat
    fingerprints: list[FileFingerprint] = []
    ignored: set[str] = set()
    zones_scanned: list[str] = []
    missing: list[str] = []
    with perf_span("scan.scan_zones"):
        for zone in resolved:
            zone_root = os.path.join(root_path, zone)
            if not os.path.isdir(zone_root):
                missing.append(zone)
                continue
            zones_scanned.append(zone)
            for dirpath, dirnames, filenames in os.walk(zone_root, topdown=True):
                dirnames[:] = sorted(
                    d
                    for d in dirnames
                    if not d.startswith(".") and d not in _SKIP_DIRS
                )
                for filename in sorted(filenames):
                    ext = os.path.splitext(filename)[1].lower()
                    if filename.startswith(".") or ext not in TEXT_EXTS:
                        ignored.add(filename)  # 上报只到文件名
                        continue
                    path = os.path.abspath(os.path.join(dirpath, filename))
                    st = provider(path, follow_symlinks=False)
                    attrs = getattr(st, "st_file_attributes", 0)
                    if attrs & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM):
                        ignored.add(filename)
                        continue
                    mode = getattr(st, "st_mode", 0)
                    if (mode and stat_module.S_ISLNK(mode)) or attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
                        continue  # 符号链接/junction 非常规文件，静默跳过
                    fingerprints.append(
                        FileFingerprint(
                            st_dev=st.st_dev,
                            st_ino=st.st_ino,
                            size_bytes=st.st_size,
                            mtime_ns=st.st_mtime_ns,
                            path=path,
                            zone=zone,
                            ext=ext,
                        )
                    )
    return ScanResult(
        fingerprints=tuple(sorted(fingerprints)),
        missing_zones=tuple(missing),
        zones_scanned=tuple(zones_scanned),
        ignored=tuple(sorted(ignored)),
    )


def classify_delta(previous, current) -> Delta:
    """对两次快照按身份 (st_dev, st_ino) 做双指针归并差分。

    - 同身份不同路径 → 移动（current 继承 previous.book_id）
    - 同身份同路径但 (size, mtime_ns) 变 → 内容变化（同样继承标注）
    - 仅 current → 新增；仅 previous → 删除（软删由上层落库）
    - 身份重复（硬链接形态）按路径字典序先到先配，其余计新增
    """
    prev_sorted = sorted(previous)
    curr_sorted = sorted(current)
    added: list[FileFingerprint] = []
    removed: list[FileFingerprint] = []
    moved: list[Moved] = []
    changed: list[Changed] = []
    i = j = 0
    while i < len(prev_sorted) and j < len(curr_sorted):
        prev, curr = prev_sorted[i], curr_sorted[j]
        prev_key = (prev.st_dev, prev.st_ino)
        curr_key = (curr.st_dev, curr.st_ino)
        if prev_key < curr_key:
            removed.append(prev)
            i += 1
        elif prev_key > curr_key:
            added.append(curr)
            j += 1
        else:
            if prev.path != curr.path:
                moved.append(Moved(prev, replace(curr, book_id=prev.book_id)))
            elif (prev.size_bytes, prev.mtime_ns) != (curr.size_bytes, curr.mtime_ns):
                changed.append(Changed(prev, replace(curr, book_id=prev.book_id)))
            i += 1
            j += 1
    removed.extend(prev_sorted[i:])
    added.extend(curr_sorted[j:])
    return Delta(tuple(added), tuple(removed), tuple(moved), tuple(changed))
