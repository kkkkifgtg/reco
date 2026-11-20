"""工单 09 性能冒烟：千事件重放 + 单事件增量更新 + 批量 decay_weight（RECO 口径）。"""
import sys, time
from datetime import datetime, timedelta, timezone
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, "packages/reco-core/src")
from reco_core.profile import build_profile, update_profile, decay_weight

BASE = datetime(2026, 11, 21, tzinfo=timezone.utc)
NOW = BASE + timedelta(days=200)

events = []
for i in range(1000):
    kind = ("tag_hit", "rec_feedback", "signal_recorded")[i % 3]
    payload = {"axis": f"subject.s{i % 40}", "tag_id": f"subject.s{i % 40}"} if kind == "tag_hit" else (
        {"axis": f"plot.p{i % 20}", "kind": "dislike", "value": 1.0} if kind == "rec_feedback" else
        {"kind": "dwell", "value": 300.0})
    events.append({"event_id": i + 1, "event_type": kind, "book_id": i % 50,
                   "payload": payload, "occurred_at": (BASE + timedelta(days=i % 190)).isoformat()})

t0 = time.perf_counter()
p = build_profile(events, now=NOW)
t1 = time.perf_counter()
upd = update_profile(p, {"event_id": 1001, "event_type": "rec_feedback", "book_id": 7,
                         "payload": {"axis": "subject.s3", "kind": "dislike", "value": 1.0},
                         "occurred_at": NOW.isoformat()}, now=NOW)
t2 = time.perf_counter()
for k in range(100_000):
    decay_weight(k % 365, 90)
t3 = time.perf_counter()

replay_ms = (t1 - t0) * 1000
update_ms = (t2 - t1) * 1000
decay_us = (t3 - t2) * 1000 / 100_000
print(f"replay 1000 events: {replay_ms:.1f} ms")
print(f"single update: {update_ms*1000:.0f} us")
print(f"decay_weight: {decay_us:.2f} us/call")
assert replay_ms < 2000, "千事件重放超预算"
assert update_ms * 1000 < 50_000, "单事件更新超 50ms（秒级更新承诺受威胁）"
print("PERF-SMOKE-OK")
