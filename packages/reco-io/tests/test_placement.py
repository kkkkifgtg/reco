"""工单 03 红测试：硬链接/复制落位（契约：docs/ARCHITECTURE.md §9）。

红阶段策略：reco_io.placement 由本票绿阶段实现（绿阶段等 t2/W2 completed）。
绿阶段前通过 fixture 惰性导入——若用模块级 import，collection error 会以
「Interrupted」中止整个 pytest 会话，殃及他人全量跑测；fixture 级
ImportError 只让本文件逐测试转红（00-index：在途红不计入他票验收）。

验收映射（.scratch/reco-impl/issues/03-placement.md）：
- 同卷硬链接：st_ino 相等 + st_nlink>=2 + 删源可读；实现若用复制冒充
  硬链接，st_ino 断言必然转红（假硬链接逆向检测点）。
- EXDEV / 非 NTFS：自动退回复制，不抛用户。
- 跨卷模拟：指纹提供者注入 st_dev，不依赖真第二块盘。
- 推荐专区只存索引与分数：本票不提供面向推荐专区的实体复制 API
  （索引写盘归票 12），落位原语仅供 liked/candidate 实体专区使用。
"""

from __future__ import annotations

import dataclasses
import errno
import os
import shutil
from pathlib import Path

import pytest

CONTENT_A = "合成正文-A"
CONTENT_B = "合成正文-B"
CONTENT_C = "合成正文-C"
CONTENT_D = "合成正文-D"
CONTENT_OLD = "旧内容-X"


@pytest.fixture
def placement():
    """绿阶段前 ImportError：本文件逐测试在途红，不中止他人收集。"""
    from reco_io import placement

    return placement


@pytest.fixture
def make_stat_provider(placement):
    """指纹提供者工厂：包装真实 os.stat，仅覆盖 st_dev（模拟跨卷）。"""

    def factory(dev_overrides: dict[Path, int] | None = None):
        overrides = {Path(k): v for k, v in (dev_overrides or {}).items()}

        def provider(path):
            st = os.stat(path)
            return placement.StatInfo(
                overrides.get(Path(path), st.st_dev),
                st.st_size,
                st.st_mtime_ns,
                st.st_ino,
                st.st_nlink,
            )

        return provider

    return factory


@pytest.fixture
def link_stub():
    """可注入失败的 os.link 桩，记录全部调用。"""

    class Recorder:
        def __init__(self, exc: OSError | None = None):
            self.calls: list[tuple[Path, Path]] = []
            self.exc = exc

        def __call__(self, src, dst):
            self.calls.append((Path(src), Path(dst)))
            if self.exc is not None:
                raise self.exc

    return Recorder


def winerror_oserror(winerror: int) -> OSError:
    """按 Windows os.link 在非 NTFS 卷上的实测形态构造 OSError（F-7）。"""
    exc = OSError(errno.EINVAL, f"WinError {winerror}（模拟非 NTFS）")
    try:
        exc.winerror = winerror  # 仅 Windows 构建有 winerror 描述符
    except AttributeError:
        pass
    return exc


def write_src(tmp_path: Path, content: str) -> Path:
    src = tmp_path / "book.txt"
    src.write_text(content, encoding="utf-8")
    return src


def make_dst_dir(tmp_path: Path) -> Path:
    dst_dir = tmp_path / "liked"
    dst_dir.mkdir()
    return dst_dir


def test_same_volume_hardlink_end_to_end(placement, tmp_path):
    src = write_src(tmp_path, CONTENT_A)
    dst_dir = make_dst_dir(tmp_path)

    result = placement.place_by_hardlink(src, dst_dir)

    dst = dst_dir / "book.txt"
    assert result.mode is placement.PlacementMode.HARDLINK
    assert result.dst == dst
    dst_st = os.stat(dst)
    assert dst_st.st_ino == os.stat(src).st_ino  # 复制冒充硬链接在此转红
    assert dst_st.st_nlink >= 2
    assert (result.st_ino, result.st_nlink) == (dst_st.st_ino, dst_st.st_nlink)
    assert src.exists()  # 源文件保留（落位语义）
    assert dst.read_text(encoding="utf-8") == CONTENT_A
    os.unlink(src)
    assert dst.read_text(encoding="utf-8") == CONTENT_A  # 删源仍可读


def test_place_twice_second_is_skipped(placement, tmp_path):
    src = write_src(tmp_path, CONTENT_A)
    dst_dir = make_dst_dir(tmp_path)

    first = placement.place_by_hardlink(src, dst_dir)
    second = placement.place_by_hardlink(src, dst_dir)

    assert first.mode is placement.PlacementMode.HARDLINK
    assert second.mode is placement.PlacementMode.SKIPPED


def test_existing_identical_fingerprint_skips(placement, tmp_path):
    src = write_src(tmp_path, CONTENT_B)
    dst_dir = make_dst_dir(tmp_path)
    dst = dst_dir / "book.txt"
    dst.write_text(CONTENT_B, encoding="utf-8")
    src_st = os.stat(src)
    os.utime(dst, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))  # 指纹 (size, mtime_ns) 判等

    result = placement.place_by_hardlink(src, dst_dir)

    assert result.mode is placement.PlacementMode.SKIPPED
    assert os.stat(dst).st_mtime_ns == src_st.st_mtime_ns  # 既有目标未被改动


def test_existing_different_fingerprint_renames(placement, tmp_path):
    src = write_src(tmp_path, CONTENT_C)
    dst_dir = make_dst_dir(tmp_path)
    dst = dst_dir / "book.txt"
    dst.write_text(CONTENT_OLD, encoding="utf-8")

    result = placement.place_by_hardlink(src, dst_dir)

    assert result.dst == dst_dir / "book(1).txt"
    assert result.mode is placement.PlacementMode.HARDLINK
    assert (dst_dir / "book(1).txt").read_text(encoding="utf-8") == CONTENT_C
    assert dst.read_text(encoding="utf-8") == CONTENT_OLD  # 原目标不动


def test_rename_conflict_is_deterministic(placement, tmp_path):
    src = write_src(tmp_path, CONTENT_C)
    dst_dir = make_dst_dir(tmp_path)
    (dst_dir / "book.txt").write_text(CONTENT_OLD, encoding="utf-8")
    (dst_dir / "book(1).txt").write_text(CONTENT_OLD + "!", encoding="utf-8")

    result = placement.place_by_hardlink(src, dst_dir)

    assert result.dst == dst_dir / "book(2).txt"


def test_predicted_cross_volume_copies_without_link_attempt(placement, make_stat_provider, link_stub, tmp_path):
    src = write_src(tmp_path, CONTENT_D)
    dst_dir = make_dst_dir(tmp_path)
    link = link_stub()
    policy = placement.PlacementPolicy(
        stat_provider=make_stat_provider({src: 12345, dst_dir: 67890}),
        link_impl=link,
    )

    result = placement.place_by_hardlink(src, dst_dir, policy)

    assert result.mode is placement.PlacementMode.COPY
    assert link.calls == []  # 契约步骤 1：不同卷直接复制，不制造一次失败
    assert (dst_dir / "book.txt").read_text(encoding="utf-8") == CONTENT_D


def test_exdev_fallback_to_copy(placement, link_stub, tmp_path):
    src = write_src(tmp_path, CONTENT_D)
    dst_dir = make_dst_dir(tmp_path)
    link = link_stub(OSError(errno.EXDEV, "Invalid cross-device link"))
    policy = placement.PlacementPolicy(link_impl=link)

    result = placement.place_by_hardlink(src, dst_dir, policy)

    assert result.mode is placement.PlacementMode.COPY
    assert len(link.calls) == 1  # 先尝试、EXDEV 后退回复制
    assert (dst_dir / "book.txt").read_text(encoding="utf-8") == CONTENT_D


@pytest.mark.parametrize(
    "exc",
    [winerror_oserror(1), OSError(errno.ENOSYS, "Function not implemented")],
    ids=["winerror-1-fat32", "enosys"],
)
def test_non_ntfs_fallback_to_copy(placement, link_stub, tmp_path, exc):
    src = write_src(tmp_path, CONTENT_D)
    dst_dir = make_dst_dir(tmp_path)
    policy = placement.PlacementPolicy(link_impl=link_stub(exc))

    result = placement.place_by_hardlink(src, dst_dir, policy)

    assert result.mode is placement.PlacementMode.COPY  # 自动复制不抛用户
    assert (dst_dir / "book.txt").read_text(encoding="utf-8") == CONTENT_D


@pytest.mark.parametrize(
    "exc",
    [OSError(errno.EPERM, "Operation not permitted"), OSError(errno.EACCES, "Access denied")],
    ids=["eperm", "eacces"],
)
def test_permission_raises_without_copy_fallback(placement, link_stub, tmp_path, exc):
    src = write_src(tmp_path, CONTENT_D)
    dst_dir = make_dst_dir(tmp_path)
    policy = placement.PlacementPolicy(link_impl=link_stub(exc))

    with pytest.raises(placement.PlacementPermissionError):
        placement.place_by_hardlink(src, dst_dir, policy)

    assert not (dst_dir / "book.txt").exists()  # 不静默降级成复制


def test_copy_size_mismatch_raises(placement, make_stat_provider, tmp_path):
    src = write_src(tmp_path, CONTENT_D)
    dst_dir = make_dst_dir(tmp_path)

    def wrong_size_copy(s, d):
        Path(d).write_text("短", encoding="utf-8")

    policy = placement.PlacementPolicy(
        stat_provider=make_stat_provider({src: 1, dst_dir: 2}),
        copy_impl=wrong_size_copy,
    )

    with pytest.raises(placement.PlacementVerificationError):
        placement.place_by_hardlink(src, dst_dir, policy)

    assert not (dst_dir / "book.txt").exists()  # 半成品清理，不污染后续指纹判等


def test_copy_mtime_mismatch_raises(placement, make_stat_provider, tmp_path):
    src = write_src(tmp_path, CONTENT_D)
    dst_dir = make_dst_dir(tmp_path)
    src_st = os.stat(src)
    dst = dst_dir / "book.txt"

    def shifted_mtime_copy(s, d):
        shutil.copy2(s, d)
        os.utime(d, ns=(src_st.st_atime_ns, src_st.st_mtime_ns - 1_000_000))  # 同 size 不同 mtime_ns

    policy = placement.PlacementPolicy(
        stat_provider=make_stat_provider({src: 1, dst_dir: 2}),
        copy_impl=shifted_mtime_copy,
    )

    with pytest.raises(placement.PlacementVerificationError):
        placement.place_by_hardlink(src, dst_dir, policy)

    assert not dst.exists()


def test_result_fields_carry_no_content(placement, tmp_path):
    src = write_src(tmp_path, CONTENT_A)
    dst_dir = make_dst_dir(tmp_path)

    result = placement.place_by_hardlink(src, dst_dir)

    payload = dataclasses.asdict(result)
    assert set(payload) == {"mode", "src", "dst", "st_ino", "st_nlink", "size_bytes"}
    assert all(not isinstance(v, bytes) for v in payload.values())
    assert CONTENT_A not in str(payload)  # 结果只含模式/路径/数值证据（B 类：无正文）
