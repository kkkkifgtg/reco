"""RECO_PERF 性能打点（ARCHITECTURE §11.3）。

RECO_PERF=1 时，每个 perf_span 作用域结束向 stderr 输出一行
`phase=<name> ms=<float>`（毫秒、三位小数），便于脚本化 diff；
关闭时仅一次布尔判断，无 IO 开销。
"""
from __future__ import annotations

import os
import sys
import time

perf_enabled: bool = os.environ.get("RECO_PERF", "") not in ("", "0")


class perf_span:
    """作用域打点：`with perf_span("scan.scan_zones"): ...`。"""

    __slots__ = ("_name", "_start")

    def __init__(self, name: str) -> None:
        self._name = name

    def __enter__(self) -> "perf_span":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if perf_enabled:
            elapsed_ms = (time.perf_counter() - self._start) * 1000.0
            sys.stderr.write(f"phase={self._name} ms={elapsed_ms:.3f}\n")
        return False
