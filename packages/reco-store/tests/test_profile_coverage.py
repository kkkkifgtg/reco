from __future__ import annotations

from reco_store import open_store
from reco_store.recommend import load_profile

AT = "2026-11-21T00:00:00+00:00"


def _tag_added(store, tag_id, parent, axis=""):
    store.append_event(
        "tag_added",
        payload={"tag_id": tag_id, "axis": axis, "label": tag_id, "parent": parent},
        occurred_at=AT,
        config_fingerprint="test/1",
    )


def test_load_profile_uses_all_declared_axes_as_coverage_denominator(tmp_path):
    store = open_store(tmp_path / "coverage.db")
    try:
        for axis in ("top", "mech", "tone"):
            _tag_added(store, axis, "d1")
        _tag_added(store, "mech.x", "mech", axis="mechanism")
        store.append_event(
            "tag_hit",
            book_id=1,
            payload={"axis": "mech", "tag_id": "mech.x", "confidence": 1.0},
            occurred_at=AT,
            config_fingerprint="test/1",
        )

        profile = load_profile(store)
        assert profile.sample_counts["n_axes"] == 3
        # 1 个有样本轴 / 3 个声明轴：coverage=1/3，不再是 1
        assert profile.confidence.value < 0.3 * (0.4 / 40 + 0.35)
    finally:
        store.close()
