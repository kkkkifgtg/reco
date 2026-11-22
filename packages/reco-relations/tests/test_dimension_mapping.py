"""#68 回归：关系提取 8 个维度必须与真实标签 id 对得上。

根因（修复前）：
- 前缀错配：真实标签用缩写（top./cons./pow./evo./arch.），代码期望全称
- 值错配：_best 返回中文末段（"攻"/"年上"），映射表键却是英文（attack/peer）

本测试用**真实标签 id**（取自 seed 数据）驱动，逐个维度验证映射生效。
"""
from __future__ import annotations

import pytest

from reco_relations.extract import extract_relation_graph


def _graph(weights, stats=None):
    return extract_relation_graph(stats or {}, weights)


# ───────────────────────── 维度 8a：topology（缩写前缀 top.）─────────────────
@pytest.mark.parametrize("tag_id,expected", [
    ("top.1v1", "1v1"),
    ("top.1vN", "1vN"),
    ("top.NvN", "NvN"),
])
def test_topology_prefix_and_value(tag_id, expected):
    g = _graph({tag_id: 1.0})
    assert g["topology"] == expected, f"{tag_id} 应映射为 {expected}，实际 {g['topology']}"


# ───────────────────────── 维度 1/3：role / age（人物需 count≥1）────────────
def test_role_maps_chinese_suffix():
    g = _graph({"role.攻": 1.0}, {"character_count": 2})
    assert g["characters"], "有 character_count 时应生成人物"
    assert g["characters"][0]["role"] == "攻", f"实际 {g['characters'][0]['role']}"


def test_age_maps_chinese_suffix():
    g = _graph({"age.年上": 1.0}, {"character_count": 2})
    assert g["characters"][0]["age_group"] == "年上", f"实际 {g['characters'][0]['age_group']}"


# ───────────────────────── 维度 2：traits（arch. 缩写前缀）──────────────────
def test_archetype_prefix():
    g = _graph({"arch.医生": 1.0}, {"character_count": 1})
    assert "arch.医生" in g["characters"][0]["traits"], f"实际 {g['characters'][0]['traits']}"


# ───────────────────────── 维度 7：consent（cons. 缩写前缀）─────────────────
def test_consent_prefix_and_value():
    g = _graph({"cons.自愿": 1.0}, {"character_count": 2})
    assert g["edges"], "两人以上应生成边"
    assert g["edges"][0]["consent"] == "自愿", f"实际 {g['edges'][0]['consent']}"


# ───────────────────────── 维度 8b/8c：power / evolution（pow./evo.）────────
def test_power_prefix_and_value():
    g = _graph({"pow.对等": 1.0})
    assert g["power_balance"] == "对等", f"实际 {g['power_balance']}"


def test_evolution_prefix_and_value():
    g = _graph({"evo.救赎": 1.0})
    assert g["evolution"] == "救赎", f"实际 {g['evolution']}"


# ───────────────────────── 维度 5：mechanism（mech. 本来就对）───────────────
def test_mechanism_still_works():
    g = _graph({"mech.催眠": 1.0}, {"character_count": 2})
    assert "mech.催眠" in g["edges"][0]["mechanism"], f"实际 {g['edges'][0]['mechanism']}"


# ───────────────────────── 低于阈值仍留空（不瞎填）─────────────────────────
def test_below_floor_stays_unknown():
    g = _graph({"top.1v1": 0.1})
    assert g["topology"] != "1v1", "低于 evidence_floor 不应填值"
