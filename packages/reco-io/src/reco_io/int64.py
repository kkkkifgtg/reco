"""无符号 64 位身份 → SQLite 有符号 64 位 INTEGER 编码（工单 N1）。

Windows 的 os.stat().st_dev/st_ino 是无符号 64 位；SQLite INTEGER 是有符号
64 位。NTFS file reference（MFT 序号段置高位）会超出有符号域，直接绑定
sqlite3 参数抛 OverflowError。编码口径 = C 层双字节序补码回绕（two's
complement wrap）：与 .scratch/import-data-root.py 临时实现一致，幂等可逆。

- to_int64：无符号域 [0, 2^64) → [-2^63, 2^63)（落库形态）；
- from_int64：[-2^63, 2^63) → 无符号域（db 读取还原，配对在上层完成）；
- 域外输入显式 ValueError，不静默截断（TDD 红测已钉）。
"""

from __future__ import annotations

__all__ = ["to_int64", "from_int64"]

_MASK64 = (1 << 64) - 1
_SIGN_BIT = 1 << 63


def to_int64(value: int) -> int:
    """无符号 64 位身份 → SQLite 有符号 64 位形态（补码回绕，幂等）。

    值域 [0, 2^64)；域外 ValueError。≥ 2^63 的值回绕为 value - 2^64。
    """
    v = int(value)
    if v < 0 or v > _MASK64:
        raise ValueError(f"无符号 64 位身份越界: {v}（允许 [0, 2^64)）")
    return v - (1 << 64) if v >= _SIGN_BIT else v


def from_int64(value: int) -> int:
    """SQLite 有符号 64 位形态 → 无符号 64 位身份（to_int64 的精确逆）。"""
    v = int(value)
    if v < -(1 << 63) or v > _MASK64:
        raise ValueError(f"有符号 64 位形态越界: {v}（允许 [-2^63, 2^64)）")
    return v + (1 << 64) if v < 0 else v
