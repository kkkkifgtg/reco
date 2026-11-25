"""#73 阅读行为信号采集：记账层（零 Qt、零 DB）。

职责切分：
- 本模块只做「读了多久 / 读了多深」的记账，产出待落库的记录（纯数据）；
- 落库由阅读页的池线程 worker 走 store.append_event（§11.1：UI 线程零 DB）；
- 频率控制是本模块的核心纪律：dwell / read_time 每会话只在结算（关闭）时
  各出一条，读取过程中绝不逐秒/逐滚动写信号（否则信号表被噪声淹没）。

kind 取值与 ARCHITECTURE.md §signals 一致：dwell / read_time / completion / reopen。
session_id 匿名（架构要求不存设备信息）：随机十六进制串，不含路径、主机名等。
"""
from __future__ import annotations

import secrets
import time
from typing import Callable

__all__ = [
    "ACTIVE_GAP_S",
    "COMPLETION_PERCENT",
    "SIGNAL_SOURCE",
    "ReadingSession",
]

SIGNAL_SOURCE = "gui"
ACTIVE_GAP_S = 60.0  # 两次交互间隔 <= 60s 视为「在读」，更长视为离开（不计活跃时长）
COMPLETION_PERCENT = 0.95  # 读到 95% 以上算读完（末尾目录/版权页不算没读完）
_SESSION_ID_BYTES = 8  # 16 位十六进制：足够区分会话，又短到不构成设备指纹
_PRECISION = 3


class ReadingSession:
    """一次「打开→关闭」的阅读会话记账器。

    clock 可注入（测试用假时钟）；默认 time.monotonic（墙钟跳变不影响时长）。
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        session_id: str | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._session_id = session_id or secrets.token_hex(_SESSION_ID_BYTES)
        self._started = float(self._clock())
        self._last_activity = self._started
        self._active_s = 0.0
        self._completion_sent = False
        self._settled = False

    # ------------------------------------------------------------ 只读面
    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def completion_sent(self) -> bool:
        return self._completion_sent

    @property
    def settled(self) -> bool:
        return self._settled

    # ------------------------------------------------------------ 采集
    def note_activity(self) -> None:
        """记录一次交互（滚动/翻页）：间隔不超过 ACTIVE_GAP_S 的区间计入活跃时长。"""
        now = float(self._clock())
        gap = now - self._last_activity
        if 0.0 <= gap <= ACTIVE_GAP_S:
            self._active_s += gap
        self._last_activity = now

    def note_percent(self, percent: float | None) -> bool:
        """读到末尾判定：首跨阈值返回 True（调用方据此写一条 completion）。

        只判一次——返回 True 之后本会话永不再报，防同一次阅读重复写。
        """
        if self._completion_sent or percent is None:
            return False
        if float(percent) < COMPLETION_PERCENT:
            return False
        self._completion_sent = True
        return True

    # ------------------------------------------------------------ 结算
    def settle(self) -> list[dict]:
        """会话结束时的结算记录（dwell + read_time）；重复调用返回空（防双写）。"""
        if self._settled:
            return []
        self._settled = True
        now = float(self._clock())
        tail = now - self._last_activity
        active = self._active_s + (tail if 0.0 <= tail <= ACTIVE_GAP_S else 0.0)
        dwell = max(0.0, now - self._started)
        active = min(max(0.0, active), dwell)  # 活跃时长不可能超过整段停留
        return [
            {"kind": "dwell", "value": round(dwell, _PRECISION)},
            {"kind": "read_time", "value": round(active, _PRECISION)},
        ]
