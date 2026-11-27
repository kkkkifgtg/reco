"""依据定位（工单 07 切片 4d，§6.1 explain_relation）。

Evidence = {kind, ref}：从 evidence.basis 解析（alias:<id> / stat:<name>）；
权重不在 §6.1 图契约内，explain 不虚构权重。不含原文片段（B1）。
"""

from __future__ import annotations

from typing import Any

from reco_core.axes import AXIS_PREFIX as _AXIS_PREFIX

# 轴名 → basis 前缀映射（traits 覆盖两个前缀；边轴带 edge. 前缀）。
# 前缀取自 reco_core.axes（seed 造 id 用的同一张表，#99）——此前这里用全称
# （topology./power./consent./evolution.），而 seed 造的是缩写，五个轴恒无依据。
_AXIS_PREFIXES: dict[str, tuple[str, ...]] = {
    "role": (_AXIS_PREFIX["role"],),
    "age_group": (_AXIS_PREFIX["age"],),
    "traits": (_AXIS_PREFIX["archetype"], _AXIS_PREFIX["body"]),
    # rel.* 是 §6.1 图契约的边类型来源；生产 seed 未建对应轴，故 edge.type 在
    # 真实数据上保持 unknown（此处只认领已存在的依据，不凭空造来源）。
    "edge.type": ("rel",),
    "edge.mechanism": (_AXIS_PREFIX["mechanism"],),
    "edge.consent": (_AXIS_PREFIX["consent"],),
    "topology": (_AXIS_PREFIX["topology"],),
    "power_balance": (_AXIS_PREFIX["power"],),
    "evolution": (_AXIS_PREFIX["evolution"],),
}


def explain_relation(graph: dict[str, Any], axis: str) -> list[dict[str, str]]:
    """列出某轴的识别依据（Evidence 列表；未知轴返回空列表）。"""
    if axis == "edge.intensity":
        return [{"kind": "stat", "ref": "edge_intensity"}] if any(
            e.get("intensity") is not None for e in (graph.get("edges") or [])
        ) else []
    prefixes = _AXIS_PREFIXES.get(axis)
    if prefixes is None:
        return []
    evidences: list[dict[str, str]] = []
    for item in (graph.get("evidence") or {}).get("basis") or []:
        if item.startswith("alias:"):
            tag_id = item[len("alias:"):]
            if tag_id.split(".", 1)[0] in prefixes:
                evidences.append({"kind": "alias", "ref": tag_id})
    return evidences