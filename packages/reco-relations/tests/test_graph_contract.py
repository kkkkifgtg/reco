"""工单 07 切片 1：图契约与 validate_relation_graph。

- §6.1 产出契约：characters/edges/topology/power_balance/evolution/evidence。
- 票内验收：validate 拒绝悬空边（写入层/校验层拒绝，不静默修正）。
- 枚举越界、强度越界、置信越界、缺 id、重复 id 全部枚举 Finding。
- 断言期望值全部手算/固定（E4：不从实现导出期望）。
"""

from __future__ import annotations

import reco_relations as rr


def _empty_graph() -> dict:
    return rr.empty_graph()


def test_empty_graph_matches_contract_shape() -> None:
    graph = _empty_graph()
    # §6.1 顶层字段固定；缺失时语义：unknown/空集/confidence=0
    assert graph["characters"] == []
    assert graph["edges"] == []
    assert graph["topology"] == "unknown"
    assert graph["power_balance"] == "unknown"
    assert graph["evolution"] == "unknown"
    assert graph["evidence"] == {"confidence": 0.0, "source": "auto", "basis": []}


def test_empty_graph_has_no_findings() -> None:
    assert rr.validate_relation_graph(_empty_graph()) == []


def test_dangling_edge_rejected() -> None:
    graph = _empty_graph()
    graph["edges"] = [{"from": "c9", "to": "c1", "type": "支配-服从",
                       "mechanism": [], "intensity": None, "consent": "unknown"}]
    findings = rr.validate_relation_graph(graph)
    codes = [f["code"] for f in findings]
    assert "dangling_edge" in codes
    # 悬空边是拒绝项，不是警告：必须能定位到具体边
    dangling = [f for f in findings if f["code"] == "dangling_edge"]
    assert any("c9" in f["path"] for f in dangling)


def test_enum_out_of_range_rejected() -> None:
    graph = _empty_graph()
    graph["characters"] = [{"id": "c1", "role": "霸总", "traits": [], "age_group": "unknown"}]
    graph["topology"] = "四角"
    graph["power_balance"] = "碾压"
    graph["evolution"] = "飞升"
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert codes.count("enum_out_of_range") == 4  # role/topology/power_balance/evolution 各一


def test_consent_enum_out_of_range_rejected() -> None:
    graph = _empty_graph()
    graph["characters"] = [
        {"id": "c1", "role": "攻", "traits": [], "age_group": "unknown"},
        {"id": "c2", "role": "受", "traits": [], "age_group": "unknown"},
    ]
    graph["edges"] = [{"from": "c1", "to": "c2", "type": "对等",
                       "mechanism": [], "intensity": 0.5, "consent": "看心情"}]
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert "enum_out_of_range" in codes
    assert any("consent" in f["path"] for f in rr.validate_relation_graph(graph)
               if f["code"] == "enum_out_of_range")


def test_intensity_out_of_range_rejected() -> None:
    graph = _empty_graph()
    graph["characters"] = [
        {"id": "c1", "role": "unknown", "traits": [], "age_group": "unknown"},
        {"id": "c2", "role": "unknown", "traits": [], "age_group": "unknown"},
    ]
    graph["edges"] = [
        {"from": "c1", "to": "c2", "type": "对等", "mechanism": [], "intensity": 1.5, "consent": "自愿"},
    ]
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert "intensity_out_of_range" in codes

    graph["edges"][0]["intensity"] = -0.1
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert "intensity_out_of_range" in codes


def test_evidence_confidence_out_of_range_rejected() -> None:
    graph = _empty_graph()
    graph["evidence"] = {"confidence": 1.2, "source": "auto", "basis": []}
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert "evidence_out_of_range" in codes

    graph["evidence"] = {"confidence": 0.5, "source": "偷看的", "basis": []}
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert "evidence_out_of_range" in codes


def test_missing_and_duplicate_character_ids_rejected() -> None:
    graph = _empty_graph()
    graph["characters"] = [
        {"id": "c1", "role": "unknown", "traits": [], "age_group": "unknown"},
        {"id": "c1", "role": "unknown", "traits": [], "age_group": "unknown"},
        {"role": "unknown", "traits": [], "age_group": "unknown"},
    ]
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert codes.count("duplicate_id") == 1
    assert codes.count("missing_id") == 1


def test_edge_self_loop_rejected() -> None:
    graph = _empty_graph()
    graph["characters"] = [{"id": "c1", "role": "unknown", "traits": [], "age_group": "unknown"}]
    graph["edges"] = [{"from": "c1", "to": "c1", "type": "对等",
                       "mechanism": [], "intensity": None, "consent": "unknown"}]
    codes = [f["code"] for f in rr.validate_relation_graph(graph)]
    assert "self_loop" in codes


def test_findings_carry_path_and_message_without_prose() -> None:
    # B1 金丝雀：Finding 只有 code/path/message，message 不携带任何原文片段
    graph = _empty_graph()
    graph["characters"] = [{"id": "c1", "role": "unknown", "traits": [], "age_group": "unknown"}]
    graph["edges"] = [{"from": "c1", "to": "c8", "type": "对等",
                       "mechanism": [], "intensity": None, "consent": "unknown"}]
    findings = rr.validate_relation_graph(graph)
    assert findings, "悬空边必须产出 Finding"
    for f in findings:
        assert set(f) == {"code", "path", "message"}
        assert isinstance(f["code"], str) and isinstance(f["path"], str)
