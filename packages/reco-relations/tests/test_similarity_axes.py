"""工单 07 切片 4a：摊平与相似度（手算黄金值，E4 禁自洽）。

轴语义（§6.1）：unknown 值不参与打分——即使权重表给了权重也贡献 0；
轴不在两侧同时出现同样贡献 0。轴缺省权重 0.1。
手算注意：character_count=2 会兜底 topology=1v1，两侧同值即参与 +缺省权重。
"""

from __future__ import annotations

import reco_relations as rr

CFG = {"evidence_floor": 0.35}


def _graph_a() -> dict:
    return rr.extract_relation_graph(
        {"character_count": 2},
        {"role.attack": 1.0, "consent.non": 0.9, "topology.one_on_one": 0.5},
        CFG,
    )


def test_relation_axes_flat_values() -> None:
    axes = rr.relation_axes(_graph_a())
    assert axes["role"] == "攻"
    assert axes["topology"] == "1v1"
    assert axes["power_balance"] == "unknown"  # 顶层轴恒在（unknown 如实呈现）
    edge_axes = {k: v for k, v in axes.items() if k.startswith("edge.")}
    assert edge_axes == {"edge.consent": "非自愿"}  # 边轴只收非空非 unknown 值


def test_relation_similarity_identical_is_one() -> None:
    weights = {"role": 0.5, "topology": 0.2, "edge.consent": 0.3}
    assert rr.relation_similarity(_graph_a(), _graph_a(), weights) == 1.0


def test_relation_similarity_hand_computed_partial() -> None:
    # B：role=受、topology=1v1、edge.consent 轴 B 侧 unknown 不参与
    # 手算 = role 0.5×0 + topology 0.2×1 = 0.2
    graph_b = rr.extract_relation_graph(
        {"character_count": 2},
        {"role.receive": 0.9, "topology.one_on_one": 0.5},
        CFG,
    )
    weights = {"role": 0.5, "topology": 0.2, "edge.consent": 0.3}
    assert rr.relation_similarity(_graph_a(), graph_b, weights) == 0.2


def test_relation_similarity_missing_axis_uses_default_weight() -> None:
    # 缺省权重 0.1：topology 兜底 1v1 两侧同值 +0.1；power/evolution 双侧 unknown 不参与
    graph_b = rr.extract_relation_graph(
        {"character_count": 2},
        {"role.receive": 0.9},
        CFG,
    )
    assert rr.relation_similarity(_graph_a(), graph_b, None) == 0.1


def test_relation_similarity_single_sided_mismatch_is_zero() -> None:
    # 唯一有权重的轴 topology 失配 → 0
    graph_b = rr.extract_relation_graph({"character_count": 2}, {"topology.triangle": 0.6}, CFG)
    graph_a2 = rr.extract_relation_graph({"character_count": 2}, {"topology.one_on_one": 0.6}, CFG)
    assert rr.relation_similarity(graph_a2, graph_b, {"topology": 0.5}) == 0.0


def test_similarity_is_symmetric() -> None:
    graph_b = rr.extract_relation_graph(
        {"character_count": 2}, {"role.receive": 0.9, "topology.one_on_one": 0.5}, CFG
    )
    weights = {"role": 0.5, "topology": 0.2}
    assert rr.relation_similarity(_graph_a(), graph_b, weights) == rr.relation_similarity(graph_b, _graph_a(), weights)


def test_similarity_permutation_invariant() -> None:
    graph_b = rr.extract_relation_graph({"character_count": 2}, {"role.receive": 0.9}, CFG)
    weights = {"role": 0.5, "topology": 0.2}
    base = rr.relation_similarity(_graph_a(), graph_b, weights)
    assert base == rr.relation_similarity(dict(reversed(list(_graph_a().items()))), graph_b, weights)


def test_similarity_unknown_axis_weight_zero_not_default() -> None:
    # unknown 不参与打分（§6.1）：character_count=1 时 topology 兜底不触发（留 unknown）
    graph_a = rr.extract_relation_graph({"character_count": 1}, {}, CFG)  # 全轴 unknown
    graph_b = rr.extract_relation_graph({"character_count": 1}, {}, CFG)
    assert rr.relation_similarity(graph_a, graph_b, {"evolution": 0.9}) == 0.0


def test_similarity_ignores_evidence_confidence() -> None:
    # evidence 不是可比轴；手算 = role 0.5×1 + topology 兜底 1v1 同值 0.1 = 0.6
    graph_a = rr.extract_relation_graph({"character_count": 2}, {"role.attack": 0.4}, CFG)
    graph_b = rr.extract_relation_graph({"character_count": 2}, {"role.attack": 0.9}, CFG)
    assert rr.relation_similarity(graph_a, graph_b, {"role": 0.5}) == 0.6


def test_similarity_confidence_diff_only_is_zero_with_no_axes() -> None:
    # 无人物（count=0）：除 evidence 外无任何轴可比 → 0
    graph_a = rr.extract_relation_graph({}, {"role.attack": 0.4}, CFG)
    graph_b = rr.extract_relation_graph({}, {"role.attack": 0.9}, CFG)
    assert rr.relation_similarity(graph_a, graph_b, {"role": 0.5}) == 0.0
