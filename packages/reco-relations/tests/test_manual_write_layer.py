"""工单 07 切片 3：逐字段真相与 manual 写入层保护（D2 核心契约）。

- relation_fields 是逐字段真相（{path: {value, source}}）；graph_json 只是可重建快照。
- merge_manual_overrides 只合并用户改过字段（manual 优先，逐字段，记 changed_by）。
- source=manual 的字段永不被自动提取覆盖：拒绝发生在写入层（apply_auto_extraction
  拒写并记 refused），不是调用方自觉，更不是事后过滤。
- 写入层同时拒绝：未知路径、破坏图契约的值（悬空边/越界强度/越界置信）。
"""

from __future__ import annotations

import pytest

import reco_relations as rr

CFG = {"evidence_floor": 0.35}


def _auto_graph() -> dict:
    return rr.extract_relation_graph(
        {"character_count": 2, "edge_intensity": 0.5},
        {"role.attack": 0.9, "rel.dominance": 0.8},
        CFG,
    )


def test_merge_returns_field_truth_with_sources() -> None:
    state = rr.merge_manual_overrides(_auto_graph(), {"characters[c1].role": "受"})
    sources = state["field_sources"]
    assert sources["characters[c1].role"] == "manual"
    assert sources["topology"] == "auto"
    assert state["fields"]["characters[c1].role"]["value"] == "受"
    # changed_by 记录逐字段写入者（D2：合并结果记 changed_by）
    manual_writes = [e for e in state["changed_by"] if e["by"] == "manual"]
    assert [e["path"] for e in manual_writes] == ["characters[c1].role"]


def test_merge_only_touched_fields_become_manual() -> None:
    state = rr.merge_manual_overrides(_auto_graph(), {"topology": "三角"})
    sources = state["field_sources"]
    assert sources["topology"] == "manual"
    # 用户没改过的字段绝不被标 manual
    assert sources["characters[c1].role"] == "auto"
    assert sources["edges[c1->c2].type"] == "auto"
    assert sum(1 for s in sources.values() if s == "manual") == 1


def test_manual_value_wins_in_snapshot() -> None:
    auto = _auto_graph()  # role=攻
    state = rr.merge_manual_overrides(auto, {"characters[c1].role": "受"})
    graph = rr.snapshot_from_fields(state["fields"])
    assert graph["characters"][0]["role"] == "受"  # manual 覆盖 auto
    assert graph["edges"][0]["type"] == "支配-服从"  # 未编辑字段保持 auto


def test_write_layer_refuses_to_overwrite_manual_field() -> None:
    state = rr.merge_manual_overrides(_auto_graph(), {"characters[c1].role": "受"})
    # 第二次自动提取（relation_updated）：role 的 auto 值又变回 攻
    new_auto = rr.extract_relation_graph(
        {"character_count": 2, "edge_intensity": 0.5},
        {"role.attack": 1.0, "rel.dominance": 0.8},
        CFG,
    )
    result = rr.apply_auto_extraction(state, new_auto)
    assert result["fields"]["characters[c1].role"]["value"] == "受"  # 拒写，manual 保住
    assert result["fields"]["characters[c1].role"]["source"] == "manual"
    assert "characters[c1].role" in result["refused"]
    # 非 manual 字段照常更新
    assert result["fields"]["edges[c1->c2].type"]["source"] == "auto"


def test_rebuild_never_rewrites_manual_to_auto_d2() -> None:
    # D2 硬验收：graph_json 是可重建快照——重建不得把 manual 字段改写成 auto 值
    state = rr.merge_manual_overrides(_auto_graph(), {"edges[c1->c2].consent": "自愿"})
    # 模拟重新提取产生不同的 auto consent
    new_auto = rr.extract_relation_graph(
        {"character_count": 2, "edge_intensity": 0.5},
        {"rel.dominance": 0.8, "consent.non": 0.9},  # auto 想写 非自愿
        CFG,
    )
    result = rr.apply_auto_extraction(state, new_auto)
    graph = rr.snapshot_from_fields(result["fields"])
    assert graph["edges"][0]["consent"] == "自愿"  # 重建后 manual 仍是 manual
    assert "edges[c1->c2].consent" in result["refused"]


def test_write_layer_rejects_unknown_manual_path() -> None:
    # 未知路径在写入层拒绝，不静默新增字段
    with pytest.raises(ValueError, match="characters\\[c9\\]\\.ghost"):
        rr.merge_manual_overrides(_auto_graph(), {"characters[c9].ghost": "x"})


def test_write_layer_rejects_unknown_structural_path() -> None:
    # 端点是边的身份（edges[c1->c2]），不是可编辑字段；写入层拒绝
    with pytest.raises(ValueError, match="edges\\[c1->c2\\]\\.to"):
        rr.merge_manual_overrides(_auto_graph(), {"edges[c1->c2].to": "c7"})


def test_write_layer_refuses_invalid_auto_graph() -> None:
    # 防御纵深：带悬空边的 auto 图在写入层被整体拒绝（validate 前置）
    state = rr.merge_manual_overrides(_auto_graph(), {})
    bad = _auto_graph()
    bad["edges"][0]["to"] = "c7"  # 注入悬空边
    with pytest.raises(ValueError) as excinfo:
        rr.apply_auto_extraction(state, bad)
    assert "dangling_edge" in str(excinfo.value)


def test_manual_intensity_out_of_range_rejected_at_write_layer() -> None:
    with pytest.raises(ValueError, match="intensity_out_of_range"):
        rr.merge_manual_overrides(_auto_graph(), {"edges[c1->c2].intensity": 7.5})


def test_manual_confidence_stays_honest() -> None:
    # D4 精神：manual 改置信度也必须留在 0-1，不能伪装高置信
    with pytest.raises(ValueError, match="evidence_out_of_range"):
        rr.merge_manual_overrides(_auto_graph(), {"evidence.confidence": 3.0})


def test_graph_field_paths_enumerates_contract() -> None:
    paths = rr.graph_field_paths(_auto_graph())
    for expected in (
        "characters[c1].role",
        "characters[c1].age_group",
        "characters[c1].traits",
        "edges[c1->c2].type",
        "edges[c1->c2].mechanism",
        "edges[c1->c2].intensity",
        "edges[c1->c2].consent",
        "topology",
        "power_balance",
        "evolution",
        "evidence.confidence",
        "evidence.source",
        "evidence.basis",
    ):
        assert expected in paths


def test_full_field_truth_survives_two_auto_rounds() -> None:
    # 端到端：merge → auto → auto，manual 值三轮不变；refused 累计留痕
    state = rr.merge_manual_overrides(
        _auto_graph(),
        {"characters[c1].role": "互攻", "topology": "三角"},
    )
    for w in (0.3, 1.0):  # 两轮不同的 auto 信号
        new_auto = rr.extract_relation_graph(
            {"character_count": 2, "edge_intensity": 0.5},
            {"role.attack": w, "topology.one_on_one": w},
            CFG,
        )
        state = rr.apply_auto_extraction(state, new_auto)
    graph = rr.snapshot_from_fields(state["fields"])
    assert graph["characters"][0]["role"] == "互攻"
    assert graph["topology"] == "三角"
    # 两轮 × 两路径 = 4 次拒写，路径集合恰为两条 manual 字段
    assert set(state["refused"]) == {"characters[c1].role", "topology"}
    assert len(state["refused"]) == 4
