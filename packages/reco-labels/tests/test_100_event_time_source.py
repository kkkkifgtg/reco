"""#100：标签事件的时间只来自调用方或真实时钟，不再写死占位日期。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reco_labels import (
    InMemoryLabelsStore,
    add_tag,
    archive_tag,
    merge_tags,
    reject_proposal,
)


_PINNED = {"2026-11-20", "2026-11-21"}


def _times(store):
    return [e.occurred_at for e in store.iter_events()]


def test_lifecycle_without_now_uses_real_clock():
    store = InMemoryLabelsStore()
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    add_tag(store, tag_id="mech.甲", axis="mechanism", label="甲", parent="mech")
    add_tag(store, tag_id="mech.乙", axis="mechanism", label="乙", parent="mech")
    merge_tags(store, "mech.甲", "mech.乙")
    archive_tag(store, "mech.乙")

    times = _times(store)
    assert times, "生命周期事件必须落一条时间戳"
    assert not (_PINNED & set(times)), f"事件时间不得是占位日期: {times}"
    for raw in times:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        assert parsed >= started, f"事件时间必须来自真实时钟: {raw}"


def test_explicit_now_is_preserved():
    store = InMemoryLabelsStore()
    fixed = "2026-01-02T03:04:05+00:00"
    add_tag(store, tag_id="mech.甲", axis="mechanism", label="甲", parent="mech", now=fixed)
    assert _times(store) == [fixed]


def test_proposal_creation_and_rejection_use_real_clock():
    store = InMemoryLabelsStore()
    store.insert_proposal(kind="tag", candidate="词",
                          payload={"candidate": "词", "count": 9, "threshold": 3},
                          created_at="2026-11-21")
    reject_proposal(store, "词")
    times = _times(store)
    assert times and not (_PINNED & set(times))