"""事件时间戳的唯一来源（#100）。

调用方给了时刻就用它（测试与迁移需要确定性），没给就用真实时钟。事件里不再
出现 2026-11-20 / 2026-11-21 这类写死的占位时间——重放与审计时刻必须来自真实
发生时间，否则标签事件流与核心事件流对不上账。
"""
from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["now_iso", "resolve"]


def now_iso() -> str:
    """当前时刻（UTC，秒级；与 reco_store.store._now_iso 同一口径）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve(at: str = "") -> str:
    """调用方给的时刻优先；空串/None 用真实时钟（绝不回落到字面日期）。"""
    return str(at) if at else now_iso()