"""#18：proposals 读面回到 store 侧（排序、pending 过滤、payload 解析只此一份）。"""
from __future__ import annotations

import json

import pytest

from reco_store import open_store
from reco_store.proposals import iter_proposals, pending_proposals

pytestmark = pytest.mark.integration

T0 = "2026-11-21T00:00:00+00:00"


def _proposal(store, *, candidate, count, kind="tag"):
    store.append_event("proposal_created", payload={
        "kind": kind,
        "payload_json": json.dumps({"candidate": candidate, "count": count,
                                   "threshold": 3}, ensure_ascii=False),
        "evidence_json": "{}",
    }, occurred_at=T0, config_fingerprint="t18/1")


@pytest.fixture()
def seeded(tmp_path):
    store = open_store(tmp_path / "proposals.db")
    try:
        _proposal(store, candidate="alpha", count=5)
        _proposal(store, candidate="beta", count=9)
        _proposal(store, candidate="gamma", count=9)
        yield store
    finally:
        store.close()


def test_pending_proposals_order_is_count_desc_then_candidate_asc(seeded):
    rows = pending_proposals(seeded)
    assert [r["candidate"] for r in rows] == ["beta", "gamma", "alpha"]
    assert rows[0]["payload"]["threshold"] == 3
    assert all(r["status"] == "pending" for r in rows)


def test_iter_proposals_hides_nothing_and_filters_on_demand(seeded):
    assert len(iter_proposals(seeded)) == 3
    accepted = pending_proposals(seeded)[0]["proposal_id"]
    seeded.append_event("proposal_updated", payload={"proposal_id": int(accepted), "status": "accepted"},
                        occurred_at=T0, config_fingerprint="t18/1")
    assert len(pending_proposals(seeded)) == 2
    assert len(iter_proposals(seeded)) == 3


def test_missing_payload_yields_empty_object(tmp_path):
    store = open_store(tmp_path / "bare.db")
    try:
        store.append_event("proposal_created", payload={"kind": "tag"},
                           occurred_at=T0, config_fingerprint="t18/1")
        row = pending_proposals(store)[0]
        assert row["payload"] == {} and row["candidate"] == ""
    finally:
        store.close()