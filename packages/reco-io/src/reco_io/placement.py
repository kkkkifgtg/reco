"""硬链接/复制落位（契约：docs/ARCHITECTURE.md §9；工单 03）。

契约要点：
- 预判同卷（st_dev）：不同卷直接走复制，不制造一次失败的 os.link。
- 同卷 os.link；OSError 分类：EXDEV/非 NTFS（WinError 1 / ENOSYS）→ 退回复制
  并记 placement_mode=copy；EPERM/EACCES → 报告权限问题，不静默降级。
- 目标已存在：指纹 (size_bytes, mtime_ns) 判等则跳过，不等则确定性改名去冲突。
- 复制后校验 (size, mtime_ns) 双判据，失败清理半成品并抛错。
- 注入点（stat_provider / link_impl / copy_impl）：跨卷与非 NTFS 全部模拟，
  不依赖真第二块盘（工单 03 验收项 4）。
- st_nlink 只是「是否已落位」旁证，不作为唯一判据（ARCHITECTURE F-5）。
- 本模块只服务 liked/candidate 实体专区落位；推荐专区只存索引与分数，
  不复制实体（索引写盘归票 12）——CONTEXT.md 落位。
"""

from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

#: 非 NTFS 卷的 os.link 实测失败形态（F-7）：Windows WinError 1（_errno 映射
#: EINVAL）/ WinError 17（EXDEV 的 Windows 形态）/ POSIX ENOSYS。
_CROSS_DEVICE_ERRNOS = frozenset({errno.EXDEV, errno.ENOSYS})
_CROSS_DEVICE_WINERRORS = frozenset({1, 17})
_PERMISSION_ERRNOS = frozenset({errno.EPERM, errno.EACCES})


class PlacementError(Exception):
    """落位失败基类。"""


class PlacementPermissionError(PlacementError):
    """os.link 报权限类失败：按契约必须上抛，禁止静默降级为复制。"""


class PlacementVerificationError(PlacementError):
    """复制后 (size, mtime_ns) 双判据校验失败。"""


class PlacementMode(Enum):
    HARDLINK = "hardlink"
    COPY = "copy"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class StatInfo:
    """指纹提供者的最小 stat 视图（字段顺序即位置参数顺序）。"""

    st_dev: int
    st_size: int
    st_mtime_ns: int
    st_ino: int
    st_nlink: int


@dataclass
class PlacementResult:
    """落位结果：只含模式、路径与数值证据，不含任何正文（审计 B 类）。"""

    mode: PlacementMode
    src: Path
    dst: Path
    st_ino: int
    st_nlink: int
    size_bytes: int


@dataclass
class PlacementPolicy:
    """落位策略注入点：缺省即真实 os.stat / os.link / shutil.copy2。"""

    stat_provider: Callable[[Path | str], StatInfo] | None = None
    link_impl: Callable[[Path | str, Path | str], None] | None = None
    copy_impl: Callable[[Path | str, Path | str], None] | None = None


def _default_stat(path: Path | str) -> StatInfo:
    st = os.stat(path)
    return StatInfo(st.st_dev, st.st_size, st.st_mtime_ns, st.st_ino, st.st_nlink)


def _default_link(src: Path | str, dst: Path | str) -> None:
    os.link(src, dst)


def _default_copy(src: Path | str, dst: Path | str) -> None:
    shutil.copy2(src, dst)  # copy2 保留 mtime_ns，保住指纹双判据可校验


def _is_cross_device(exc: OSError) -> bool:
    # 先看 errno（构造异常与真实 API 异常都可靠），winerror 只作补充。
    if exc.errno in _CROSS_DEVICE_ERRNOS:
        return True
    if exc.errno in _PERMISSION_ERRNOS:
        return False
    return getattr(exc, "winerror", None) in _CROSS_DEVICE_WINERRORS


def _copy_verified(
    src: Path,
    dst: Path,
    src_st: StatInfo,
    stat: Callable[[Path | str], StatInfo],
    copy: Callable[[Path | str, Path | str], None],
) -> StatInfo:
    copy(src, dst)
    dst_st = stat(dst)
    if (dst_st.st_size, dst_st.st_mtime_ns) != (src_st.st_size, src_st.st_mtime_ns):
        dst.unlink(missing_ok=True)  # 半成品清理，不污染后续指纹判等
        raise PlacementVerificationError(
            f"复制校验失败 (size, mtime_ns) 不一致：{dst.name}"
        )
    return dst_st


def _conflict_free_name(dst_dir: Path, name: str) -> Path:
    """确定性改名去冲突：book.txt -> book(1).txt -> book(2).txt ..."""
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while (candidate := dst_dir / f"{stem}({n}){suffix}").exists() or candidate.is_symlink():
        n += 1
    return candidate


def place_by_hardlink(
    src: Path | str,
    dst_dir: Path | str,
    policy: PlacementPolicy | None = None,
) -> PlacementResult:
    """把 src 落位进 dst_dir（契约步骤见模块 docstring）。

    同卷硬链接优先；EXDEV/非 NTFS 退回复制并记 COPY；权限类失败上抛。
    目标已存在时指纹 (size, mtime_ns) 判等则 SKIPPED，不等则改名去冲突。
    源文件始终保留（落位语义：硬链接/复制，绝不移动）。
    """
    src, dst_dir = Path(src), Path(dst_dir)
    policy = policy or PlacementPolicy()
    stat = policy.stat_provider or _default_stat
    link = policy.link_impl or _default_link
    copy = policy.copy_impl or _default_copy

    src_st = stat(src)
    dst = dst_dir / src.name

    # 契约步骤 2：目标已存在 → 指纹判等：等则跳过，不等则改名去冲突。
    # symlink（含悬空）一律视为冲突后改名：绝不跟随链接写到目标外（CWE-61）。
    if dst.is_symlink():
        dst = _conflict_free_name(dst_dir, src.name)
    elif dst.exists():
        dst_st = stat(dst)
        if (dst_st.st_size, dst_st.st_mtime_ns) == (src_st.st_size, src_st.st_mtime_ns):
            return PlacementResult(
                PlacementMode.SKIPPED, src, dst, dst_st.st_ino, dst_st.st_nlink, dst_st.st_size
            )
        dst = _conflict_free_name(dst_dir, src.name)

    # 契约步骤 1：预判同卷（st_dev），不同卷直接复制，不制造一次失败。
    same_volume = src_st.st_dev == stat(dst_dir).st_dev

    if same_volume:
        try:
            link(src, dst)
        except OSError as exc:
            if _is_cross_device(exc):
                dst_st = _copy_verified(src, dst, src_st, stat, copy)
                return PlacementResult(
                    PlacementMode.COPY, src, dst, dst_st.st_ino, dst_st.st_nlink, dst_st.st_size
                )
            # EPERM/EACCES 等其余 OSError：报告权限问题，不静默降级。
            raise PlacementPermissionError(
                f"硬链接失败（errno={exc.errno} winerror={getattr(exc, 'winerror', None)}）：{src.name}"
            ) from exc
        dst_st = stat(dst)
        return PlacementResult(
            PlacementMode.HARDLINK, src, dst, dst_st.st_ino, dst_st.st_nlink, dst_st.st_size
        )

    dst_st = _copy_verified(src, dst, src_st, stat, copy)
    return PlacementResult(
        PlacementMode.COPY, src, dst, dst_st.st_ino, dst_st.st_nlink, dst_st.st_size
    )
