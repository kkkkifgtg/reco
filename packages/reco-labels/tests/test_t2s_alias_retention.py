"""#92——繁体原形保留：提案带上原形，采纳后成为标签别名。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from reco_labels import InMemoryLabelsStore, accept_proposal
from reco_labels.auto_tag import auto_tag_book
from reco_labels.seed import build_seed_rows


def _seeded():
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def _stats(counts, variants=None):
    return SimpleNamespace(alias_counts=counts, alias_variants=variants or {})


def test_tag_proposal_carries_traditional_variants():
    store = _seeded()
    outcome = auto_tag_book(store, 7, "liked",
                            _stats({"催眠术": 5}, {"催眠术": ("催眠術",)}),
                            now="2026-11-25T00:00:00")
    assert outcome.proposed == ("催眠术",)
    pending = [r for r in store.iter_proposals() if r.candidate == "催眠术"]
    assert pending and pending[0].payload["variants"] == ["催眠術"]


def test_accept_registers_traditional_variant_as_alias():
    store = _seeded()
    auto_tag_book(store, 7, "liked",
                  _stats({"催眠术": 5}, {"催眠术": ("催眠術",)}),
                  now="2026-11-25T00:00:00")
    tag = accept_proposal(store, "催眠术", axis="mechanism",
                          now="2026-11-25T00:01:00")
    assert "催眠術" in tag.aliases


def test_proposal_without_variants_stays_clean():
    store = _seeded()
    auto_tag_book(store, 7, "liked", _stats({"摄心术": 5}),
                  now="2026-11-25T00:00:00")
    pending = [r for r in store.iter_proposals() if r.candidate == "摄心术"]
    assert "variants" not in pending[0].payload


@pytest.mark.integration
def test_alias_lands_in_tag_aliases_projection(tmp_path):
    from reco_labels.auto_tag import StoreAdapter, tag_book_and_fold
    from reco_store import open_store

    s = open_store(tmp_path / "t2s.db")
    try:
        adapter = StoreAdapter(s)
        for row in build_seed_rows():
            adapter.upsert_tag(row)
        tag_book_and_fold(s, 1, "liked", {"催眠术": 5},
                          alias_variants={"催眠术": ("催眠術",)},
                          now="2026-11-25T00:00:00")
        accept_proposal(adapter, "催眠术", axis="mechanism",
                        now="2026-11-25T00:01:00")
        conn = s.read_conn()
        try:
            aliases = {str(r[0]) for r in conn.execute(
                "SELECT alias FROM tag_aliases WHERE tag_id = ?",
                ("mech.催眠术",),
            ).fetchall()}
        finally:
            conn.close()
        assert "催眠術" in aliases
    finally:
        s.close()