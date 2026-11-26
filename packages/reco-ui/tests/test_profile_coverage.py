from __future__ import annotations

import json

from reco_store import open_store

AT = "2026-11-21T04:00:00+00:00"


def test_gui_feedback_snapshot_uses_all_declared_axes(tmp_path):
    from reco_ui.glue import record_feedback, seed_books

    store = open_store(tmp_path / "gui-coverage.db")
    try:
        for axis in ("top", "mech", "tone"):
            store.append_event(
                "tag_added",
                payload={"tag_id": axis, "axis": "", "label": axis, "parent": "d1"},
                occurred_at=AT,
                config_fingerprint="gui/test/1",
            )
        seed_books(store, books=3, liked=1, candidate=1, now=AT)
        record_feedback(store, "like", 3, axis="mech", now=AT)

        conn = store.read_conn()
        try:
            row = conn.execute(
                "SELECT payload_json FROM events WHERE event_type='profile_updated'"
                " ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        counts = json.loads(json.loads(row[0])["sample_counts_json"])
        assert counts["n_axes"] == 3
        assert set(counts["per_axis"]) == {"mech"}
    finally:
        store.close()
