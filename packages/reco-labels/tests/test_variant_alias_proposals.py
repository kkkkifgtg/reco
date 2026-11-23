"""#83——谐音/形近变体的 alias 提案与采纳闭环（双层边界不破）。"""
from __future__ import annotations

from types import SimpleNamespace

from reco_labels import InMemoryLabelsStore, accept_proposal, load_taxonomy, match_tags
from reco_labels.auto_tag import auto_tag_book
from reco_labels.proposals import variant_alias_candidates
from reco_labels.seed import build_seed_rows


def _seeded() -> InMemoryLabelsStore:
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def _stats(counts) -> SimpleNamespace:
    return SimpleNamespace(alias_counts=counts)


def test_variant_detection_finds_one_char_diff_label():
    store = _seeded()
    tax = load_taxonomy(store)
    assert variant_alias_candidates({"调较": 9}, tax) == {"调较": ("mech.调教", 9)}
    # 基准含别名词：「洗恼」与 mech.hypnosis 的别名「洗脑」仅差一字 → 挂回原标签
    assert variant_alias_candidates({"洗恼": 9}, tax) == {"洗恼": ("mech.hypnosis", 9)}
    assert variant_alias_candidates({"催眠": 9}, tax) == {}   # 已是识别词（显示名）
    assert variant_alias_candidates({"摄心术": 9}, tax) == {}  # 无形近标签


def test_variant_word_goes_to_alias_proposal_not_new_tag():
    store = _seeded()
    before = {r.tag_id for r in store.iter_tags()}
    outcome = auto_tag_book(store, 7, "liked", _stats({"调较": 9}),
                            now="2026-11-21T00:00:00")
    assert outcome.hits == () and outcome.inserted == ()
    assert outcome.proposed == ()                # 不走新词提案
    assert outcome.proposed_aliases == ("调较",)  # 走别名提案
    pending = [r for r in store.iter_proposals() if r.status == "pending"]
    assert len(pending) == 1 and pending[0].kind == "alias"
    assert pending[0].payload["tag_id"] == "mech.调教"
    assert {r.tag_id for r in store.iter_tags()} == before  # tags 零改动


def test_non_variant_unknown_still_tag_proposal():
    store = _seeded()
    outcome = auto_tag_book(store, 8, "liked", _stats({"摄心术": 9}),
                            now="2026-11-21T00:00:00")
    assert outcome.proposed == ("摄心术",) and outcome.proposed_aliases == ()
    pending = [r for r in store.iter_proposals() if r.status == "pending"]
    assert all(r.kind == "tag" for r in pending)


def test_below_threshold_variant_not_proposed():
    store = _seeded()
    outcome = auto_tag_book(store, 7, "liked", _stats({"调较": 1}),
                            now="2026-11-21T00:00:00")
    assert outcome.proposed_aliases == ()
    assert not [r for r in store.iter_proposals() if r.status == "pending"]


def test_accept_alias_proposal_registers_alias_and_enables_match():
    store = _seeded()
    auto_tag_book(store, 7, "liked", _stats({"调较": 9}), now="2026-11-21T00:00:00")
    tag = accept_proposal(store, "调较", axis="mechanism", now="2026-11-21T02:00:00")
    assert tag.tag_id == "mech.调教"
    assert "调较" in tag.aliases
    hits = match_tags(_stats({"调较": 2}), load_taxonomy(store))
    assert [h.tag_id for h in hits] == ["mech.调教"]  # 采纳后该词可命中原标签
    proposal = next(r for r in store.iter_proposals() if r.candidate == "调较")
    assert proposal.status == "accepted"


def test_accept_alias_appends_without_wiping_existing():
    """采纳变体 = 追加进既有别名表，绝不整体替换（保护种子/已确认别名）。"""
    store = _seeded()
    auto_tag_book(store, 7, "liked", _stats({"洗恼": 9}), now="2026-11-21T00:00:00")
    tag = accept_proposal(store, "洗恼", axis="mechanism", now="2026-11-21T02:00:00")
    assert tag.tag_id == "mech.hypnosis"
    assert set(tag.aliases) == {"精神控制", "洗脑", "mind control", "洗恼"}
