"""摊平与相似度（工单 07 切片 4a，§6.1 relation_axes/relation_similarity）。

- relation_axes：图 → 可比对轴值字典。顶层轴恒在（unknown 如实呈现）；
  边轴只收非空且非 unknown 的值（空边集不参与）。
- relation_similarity：同轴同值 × 权重求和。unknown 不参与打分（§6.1）；
  轴权重缺省 0.1；fsum 消除浮点求和次序误差。
"""

from __future__ import annotations

import math
from typing import Any

_DEFAULT_AXIS_WEIGHT = 0.1

_TOP_AXES = ("topology", "power_balance", "evolution")
_EDGE_AXES = ("type", "consent")  # mechanism/intensity 是列表/数值轴，按需扩展


def relation_axes(graph: dict[str, Any]) -> dict[str, Any]:
    """把图谱摊平成可比对的轴值（§6.1 relation_axes）。"""
    axes: dict[str, Any] = {}
    characters = graph.get("characters") or []
    primary = characters[0] if characters else None
    axes["role"] = primary.get("role", "unknown") if primary else "unknown"
    axes["age_group"] = primary.get("age_group", "unknown") if primary else "unknown"
    traits = sorted({t for c in characters for t in (c.get("traits") or [])})
    if traits:
        axes["traits"] = tuple(traits)
    for edge in graph.get("edges") or []:
        for f in _EDGE_AXES:
            value = edge.get(f)
            if value not in (None, "unknown"):
                axes[f"edge.{f}"] = value
    mech = sorted({m for e in (graph.get("edges") or []) for m in (e.get("mechanism") or [])})
    if mech:
        axes["edge.mechanism"] = tuple(mech)
    intensity_values = [e.get("intensity") for e in (graph.get("edges") or []) if e.get("intensity") is not None]
    if intensity_values:
        axes["edge.intensity"] = max(intensity_values)
    for f in _TOP_AXES:
        axes[f] = graph.get(f, "unknown")
    return axes


def _matched(axis: str, a: dict[str, Any], b: dict[str, Any]) -> float:
    """同轴两侧都出现且同值 → 1；否则 0（unknown/缺轴不参与，恒 0）。"""
    va, vb = a.get(axis), b.get(axis)
    if va is None or vb is None or va == "unknown" or vb == "unknown":
        return 0.0
    if isinstance(va, tuple) or isinstance(vb, tuple):
        sa = set(va) if isinstance(va, tuple) else {va}
        sb = set(vb) if isinstance(vb, tuple) else {vb}
        return 1.0 if sa == sb else 0.0
    return 1.0 if va == vb else 0.0


def relation_similarity(
    graph_a: dict[str, Any],
    graph_b: dict[str, Any],
    axis_weights: dict[str, float] | None,
) -> float:
    """同轴匹配度 × 权重的加权和（§6.1 relation_similarity）。"""
    weights = dict(axis_weights) if axis_weights else {}
    axes_a, axes_b = relation_axes(graph_a), relation_axes(graph_b)
    names = set(axes_a) | set(axes_b)
    total = math.fsum(
        weights.get(name, _DEFAULT_AXIS_WEIGHT) * _matched(name, axes_a, axes_b)
        for name in names
    )
    return round(total, 12)
