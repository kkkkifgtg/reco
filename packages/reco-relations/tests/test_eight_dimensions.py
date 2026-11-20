"""工单 07 切片 2：八维度抽取 extract_relation_graph。

输入为纯数值 duck-typed 映射（W4 alias_counts 接缝）：stats 只含数值统计量，
alias_weights 只含标签 id → 0-1 置信权重；正文绝不进入本包（B1/B2）。
期望值全部手算/固定黄金值（E4）；每维度独立测试（ADR-0001 / 票内验收）。
D4：低于 evidence_floor 的字段留空，不瞎填；依据仍记入 basis。
"""

from __future__ import annotations

import reco_relations as rr

CFG = {"evidence_floor": 0.35}


def test_extractor_duck_typed_plain_dicts() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2}, {"role.attack": 1.0}, CFG
    )
    assert graph["characters"][0]["role"] == "攻"


def test_dimension_1_role() -> None:
    graph = rr.extract_relation_graph({"character_count": 2}, {"role.attack": 0.9}, CFG)
    assert [c["role"] for c in graph["characters"]] == ["攻", "unknown"]
    assert graph["topology"] == "1v1"  # count 兜底
    assert graph["power_balance"] == "unknown"
    assert graph["evolution"] == "unknown"


def test_dimension_2_traits() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 1},
        {"archetype.ceo": 0.8, "body.tall": 0.6},
        CFG,
    )
    assert graph["characters"][0]["traits"] == ["archetype.ceo", "body.tall"]  # 按标签 id 排序
    assert graph["characters"][0]["role"] == "unknown"  # 其余维度不受影响


def test_dimension_3_age_group() -> None:
    graph = rr.extract_relation_graph({"character_count": 2}, {"age.older": 0.7}, CFG)
    assert graph["characters"][0]["age_group"] == "年上"
    assert graph["characters"][1]["age_group"] == "unknown"


def test_dimension_4_edge_type() -> None:
    graph = rr.extract_relation_graph({"character_count": 2}, {"rel.dominance": 0.9}, CFG)
    assert graph["edges"][0]["from"] == "c1"
    assert graph["edges"][0]["to"] == "c2"
    assert graph["edges"][0]["type"] == "支配-服从"
    assert graph["edges"][0]["consent"] == "unknown"
    assert graph["edges"][0]["intensity"] is None  # stat 缺失 → 留空


def test_dimension_5_mechanism() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2},
        {"mech.hypnosis": 0.8, "mech.training": 0.9},
        CFG,
    )
    assert graph["edges"][0]["mechanism"] == ["mech.hypnosis", "mech.training"]


def test_dimension_6_intensity_from_stat() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2, "edge_intensity": 0.25}, {}, CFG
    )
    assert graph["edges"][0]["intensity"] == 0.25
    assert graph["edges"][0]["type"] == "unknown"  # 仅强度不足以捏造类型
    assert "stat:edge_intensity" in graph["evidence"]["basis"]


def test_dimension_7_consent() -> None:
    graph = rr.extract_relation_graph({"character_count": 2}, {"consent.non": 0.95}, CFG)
    assert graph["edges"][0]["consent"] == "非自愿"


def test_dimension_8_topology_tag_beats_count() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2}, {"topology.triangle": 0.5}, CFG
    )
    assert graph["topology"] == "三角"  # 标签命中优先于 count 兜底


def test_dimension_power_balance() -> None:
    graph = rr.extract_relation_graph({"character_count": 2}, {"power.strong_top": 0.85}, CFG)
    assert graph["power_balance"] == "强攻弱受"


def test_dimension_evolution() -> None:
    graph = rr.extract_relation_graph({"character_count": 2}, {"evolution.reversal": 0.75}, CFG)
    assert graph["evolution"] == "反转"


def test_topology_falls_back_to_character_count() -> None:
    assert rr.extract_relation_graph({"character_count": 3}, {}, CFG)["topology"] == "1vN"
    assert rr.extract_relation_graph({"character_count": 6}, {}, CFG)["topology"] == "群像"
    assert rr.extract_relation_graph({}, {}, CFG)["topology"] == "unknown"


def test_below_floor_field_left_blank_d4() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2},
        {"role.attack": 0.9, "consent.voluntary": 0.2},  # 0.2 < 0.35
        CFG,
    )
    assert graph["edges"][0]["consent"] == "unknown"  # 留空，不瞎填
    assert "alias:consent.voluntary" in graph["evidence"]["basis"]  # 依据仍记录
    assert graph["evidence"]["confidence"] == 0.9  # 置信度 = 最强单信号，如实低报不虚高


def test_confidence_is_max_weight_hand_computed() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2}, {"rel.equal": 0.3, "consent.voluntary": 0.9}, CFG
    )
    assert graph["evidence"]["confidence"] == 0.9


def test_empty_inputs_return_blank_graph() -> None:
    graph = rr.extract_relation_graph(None, None, CFG)
    assert graph == rr.empty_graph()


def test_no_characters_drops_character_fields() -> None:
    graph = rr.extract_relation_graph({"character_count": 0}, {"role.attack": 1.0}, CFG)
    assert graph["characters"] == []
    assert "alias:role.attack" in graph["evidence"]["basis"]  # 依据保留


def test_no_edge_without_second_character() -> None:
    graph = rr.extract_relation_graph({"character_count": 1}, {"rel.dominance": 0.9}, CFG)
    assert graph["edges"] == []


def test_invalid_intensity_left_blank_not_clamped_into_graph() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 2, "edge_intensity": 1.5}, {}, CFG
    )
    assert graph["edges"][0]["intensity"] is None  # 越界统计量不入图


def test_extraction_output_passes_own_contract() -> None:
    graph = rr.extract_relation_graph(
        {"character_count": 3, "edge_intensity": 0.6},
        {"role.attack": 0.8, "rel.dominance": 0.9, "mech.hypnosis": 0.7},
        CFG,
    )
    assert rr.validate_relation_graph(graph) == []


def test_golden_graph_hand_written() -> None:
    # E4 黄金值：整图手写期望，不从实现导出
    graph = rr.extract_relation_graph(
        {"character_count": 2, "edge_intensity": 0.5},
        {"role.attack": 1.0, "rel.dominance": 0.9, "consent.non": 0.6},
        CFG,
    )
    assert graph == {
        "characters": [
            {"id": "c1", "role": "攻", "traits": [], "age_group": "unknown"},
            {"id": "c2", "role": "unknown", "traits": [], "age_group": "unknown"},
        ],
        "edges": [
            {
                "from": "c1",
                "to": "c2",
                "type": "支配-服从",
                "mechanism": [],
                "intensity": 0.5,
                "consent": "非自愿",
            }
        ],
        "topology": "1v1",
        "power_balance": "unknown",
        "evolution": "unknown",
        "evidence": {
            "confidence": 1.0,  # max(1.0, 0.9, 0.6) = 1.0
            "source": "auto",
            "basis": [
                "alias:consent.non",
                "alias:rel.dominance",
                "alias:role.attack",
                "stat:character_count",
                "stat:edge_intensity",
            ],
        },
    }
