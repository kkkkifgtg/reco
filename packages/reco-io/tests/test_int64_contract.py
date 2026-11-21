"""N1——st_dev/st_ino 的 int64 编码契约（Windows 无符号 64 位超 SQLite INTEGER）。

背景：Windows 的 st_dev/st_ino 是无符号 64 位；SQLite INTEGER 是有符号 64 位
（上界 2^63-1）。真实 NTFS 文件引用（MFT 序号段置高位）会超出有符号域，
直接绑定参数写库会 OverflowError。契约口径与 .scratch/import-data-root.py
临时实现一致：按 C 层双字节序补码回绕（two's complement wrap），幂等可逆。

契约四条：
1. 普通值（< 2^63）恒等通过；
2. 超界值（≥ 2^63）回绕进 [-2^63, 2^63)，不抛 OverflowError；
3. roundtrip 恒等：from_int64(to_int64(v)) == v 对全 64 位域成立；
4. 域外输入（负值/≥ 2^64）显式 ValueError，不静默截断。
"""

from __future__ import annotations

import random

import pytest

from reco_io.int64 import from_int64, to_int64

MASK64 = (1 << 64) - 1


def test_ordinary_values_pass_through():
    assert to_int64(0) == 0
    assert to_int64(1) == 1
    assert to_int64(2**63 - 1) == 2**63 - 1  # SQLite INTEGER 上界：恒等


def test_huge_values_wrap_without_overflow():
    huge = 0x81D6_527F_2C81_09AB  # NTFS file reference 高位段形态（MFT 序号段置高位，真实库实测可出现）
    assert huge > 2**63 - 1
    wrapped = to_int64(huge)
    assert -2**63 <= wrapped < 2**63, "回绕后必须落在 SQLite 有符号 64 位域"
    assert wrapped == huge - (1 << 64)
    assert wrapped % (1 << 64) == huge, "按无符号解释必须还原原值（补码语义）"


def test_roundtrip_identity_over_sampled_domain():
    rng = random.Random(20261121)
    samples = [0, 1, 2**63 - 1, 2**63, 2**63 + 1, 2**64 - 1]
    samples += [rng.randrange(0, 1 << 64) for _ in range(200)]
    for v in samples:
        assert 0 <= v <= MASK64
        assert from_int64(to_int64(v)) == v, f"roundtrip 恒等破坏于 {v}"


def test_boundary_pairing_2_63():
    assert to_int64(2**63 - 1) == 2**63 - 1
    assert to_int64(2**63) == -(2**63)  # 上界外第一个值回绕到有符号下界


def test_from_int64_is_exact_inverse():
    for v in (0, 5, 2**63 - 1, 2**63, 2**64 - 1):
        assert from_int64(to_int64(v)) == v
    assert from_int64(-1) == 2**64 - 1  # 已回绕的负值解码回无符号域


def test_out_of_domain_rejected_explicitly():
    with pytest.raises(ValueError):
        to_int64(-1)
    with pytest.raises(ValueError):
        to_int64(1 << 64)
