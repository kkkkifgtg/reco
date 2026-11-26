"""#99：依据定位的轴前缀必须对得上生产 seed 造的 tag id（缩写）。

修复前 explain 的表用全称（topology./power./consent./evolution.），而 seed 造的是
top./pow./cons./evo./arch.——五个轴恒返回空列表，而老夹具用的是全称，绿测掩护了空输出。
这里直接用 seed 词表的缩写形态构造依据，钉住「有依据」这件事本身。
"""
from __future__ import annotations

import pytest

import reco_relations as rr

CFG = {"evidence_floor": 0.35}

# seed 五域 25 轴里，落在关系图八维度上的缩写前缀举例（值取自 seed 词表）
SEED_WEIGHTS = {
    "top.1v1": 0.9,
    "pow.强攻强受": 0.6,
    "cons.自愿": 0.5,
    "evo.稳定": 0.4,
    "arch.警察": 0.8,
    "mech.催眠": 0.7,
    "role.攻": 0.9,
}


def _graph():
    return rr.extract_relation_graph({"character_count": 2}, dict(SEED_WEIGHTS), CFG)


@pytest.mark.parametrize("axis", [
    "topology", "power_balance", "edge.consent", "evolution", "traits",
    "edge.mechanism", "role",
])
def test_each_seeded_axis_has_evidence(axis):
    assert rr.explain_relation(_graph(), axis), f"{axis} 恒无依据——前缀表与 seed 不一致"


def test_evidence_refs_are_the_seed_tag_ids():
    graph = _graph()
    assert [e["ref"] for e in rr.explain_relation(graph, "topology")] == ["top.1v1"]
    assert [e["ref"] for e in rr.explain_relation(graph, "power_balance")] == ["pow.强攻强受"]
    assert [e["ref"] for e in rr.explain_relation(graph, "evolution")] == ["evo.稳定"]
    assert [e["ref"] for e in rr.explain_relation(graph, "traits")] == ["arch.警察"]