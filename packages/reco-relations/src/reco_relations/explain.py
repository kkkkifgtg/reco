"""依据定位（工单 07 切片 4d，§6.1 explain_relation）。

Evidence = {kind, ref}：从 evidence.basis 解析（alias:<id> / stat:<name>）；
权重不在 §6.1 图契约内，explain 不虚构权重。不含原文片段（B1）。
"""

from __future__ import annotations

from typing import Any

from .extract import _KNOWN_PREFIXES

# 轴名 → basis 前缀映射（traits 覆盖两个前缀；边轴带 edge. 前缀）
_AXIS_PREFIXES: dict[str, tuple[str, ...]] = {
    "role": ("role",),
    "age_group": ("age",),
    "traits": ("archetype", "body"),
    "edge.type": ("rel",),
    "edge.mechanism": ("mech",),
    "edge.consent": ("consent",),
    "topology": ("topology",),
    "power_balance": ("power",),
    "evolution": ("evolution",),
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
