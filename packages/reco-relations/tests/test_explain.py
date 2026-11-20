"""工单 07 切片 4d：explain_relation 依据定位。

Evidence 只含 {kind, ref}（从 evidence.basis 解析；权重不在 §6.1 图契约内，
explain 不虚构权重）。每个轴的依据只含统计量/别名引用（不含原文）。
"""

from __future__ import annotations

import reco_relations as rr

CFG = {"evidence_floor": 0.35}


def _graph() -> dict:
    return rr.extract_relation_graph(
        {"character_count": 2, "edge_intensity": 0.5},
        {"role.attack": 0.9, "mech.hypnosis": 0.8, "consent.non": 0.6},
        CFG,
    )


def test_explain_role_axis() -> None:
    assert rr.explain_relation(_graph(), "role") == [{"kind": "alias", "ref": "role.attack"}]


def test_explain_edge_consent_axis() -> None:
    assert rr.explain_relation(_graph(), "edge.consent") == [{"kind": "alias", "ref": "consent.non"}]


def test_explain_intensity_axis_uses_stat_ref() -> None:
    assert rr.explain_relation(_graph(), "edge.intensity") == [{"kind": "stat", "ref": "edge_intensity"}]


def test_explain_unknown_axis_returns_empty() -> None:
    assert rr.explain_relation(_graph(), "nonexistent") == []


def test_explain_mechanism_axis_sorted() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2},
        {"mech.training": 0.7, "mech.hypnosis": 0.9},
        CFG,
    )
    refs = [e["ref"] for e in rr.explain_relation(graph, "edge.mechanism")]
    assert refs == ["mech.hypnosis", "mech.training"]  # basis 已按 id 排序


def test_explain_traits_axis_covers_two_prefixes() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 1},
        {"archetype.ceo": 0.8, "body.tall": 0.6},
        CFG,
    )
    refs = [e["ref"] for e in rr.explain_relation(graph, "traits")]
    assert refs == ["archetype.ceo", "body.tall"]


def test_explain_age_group_axis() -> None:
    graph = rr.extract_relation_graph({"character_count": 1}, {"age.older": 0.7}, CFG)
    assert rr.explain_relation(graph, "age_group") == [{"kind": "alias", "ref": "age.older"}]
