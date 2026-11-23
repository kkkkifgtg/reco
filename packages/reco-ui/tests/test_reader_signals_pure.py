"""#73 阅读行为信号：记账层纯函数单测（零 Qt、零 DB）。

契约（issue #73）：
- open → reopen；close → dwell（本次停留秒数）；读到末尾（percent>=0.95）→ completion；
- read_time = 活跃阅读秒数（两次交互间隔 <= 60s 的区间计入，长空闲不计）；
- 频率控制：dwell/read_time 每个会话只在结算时各出一条，绝不每秒一条；
- session_id 匿名（architecture：不存设备信息）。
"""
from __future__ import annotations


class FakeClock:
    """可推进的单调时钟（替代 time.monotonic，保证测试确定性）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


def _session(clock):
    from reco_ui.reader_signals import ReadingSession

    return ReadingSession(clock=clock)


def test_settle_reports_dwell_and_active_read_time():
    """dwell = 整段停留；read_time = 活跃区间之和（长空闲不计入）。"""
    clock = FakeClock()
    s = _session(clock)
    clock.advance(30)
    s.note_activity()          # 0→30 活跃 30s
    clock.advance(300)         # 空闲 300s（不记活跃）
    s.note_activity()          # 330→630 间隔 300>60 → 不计
    clock.advance(10)          # 收尾 10s（<=60 → 计入）
    recs = s.settle()

    assert [r["kind"] for r in recs] == ["dwell", "read_time"]
    assert recs[0]["value"] == 340.0, "dwell 应覆盖整段停留（30+300+10）"
    assert recs[1]["value"] == 40.0, "read_time 只累计活跃区间（30+10）"


def test_settle_is_idempotent():
    """结算只发生一次：重复 settle 不再产出记录（防双写）。"""
    clock = FakeClock()
    s = _session(clock)
    clock.advance(5)
    assert len(s.settle()) == 2
    assert s.settle() == []


def test_completion_fires_once_at_threshold():
    """completion 仅在首跨 0.95 时产出一次。"""
    clock = FakeClock()
    s = _session(clock)
    assert s.note_percent(0.50) is False
    assert s.note_percent(0.95) is True
    assert s.note_percent(0.99) is False, "已跨过阈值不得重复产出"


def test_session_id_is_anonymous_hex_and_unique():
    """会话 id 匿名：16 位十六进制随机串，跨会话不重复。"""
    a = _session(FakeClock()).session_id
    b = _session(FakeClock()).session_id
    assert len(a) == 16 and int(a, 16) >= 0
    assert a != b


def test_settle_without_activity_still_reports_dwell():
    """只打开不交互也要有 dwell（0 交互是真实行为，不能静默丢弃）。"""
    clock = FakeClock()
    s = _session(clock)
    clock.advance(2)
    recs = s.settle()
    assert recs[0] == {"kind": "dwell", "value": 2.0}
    assert recs[1]["kind"] == "read_time" and recs[1]["value"] == 2.0
