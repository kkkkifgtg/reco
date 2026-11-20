"""图契约与校验（工单 07 切片 1）。

§6.1 产出契约的载体与 validate_relation_graph：
- empty_graph() 给出契约缺省形态（unknown/空集/confidence=0，低置信留空语义的底座）。
- validate_relation_graph 枚举 Finding（code/path/message），拒绝悬空边、
  枚举越界、强度越界、缺 id/重复 id、自环；拒绝发生在校验层，不静默修正。
- message 只含字段路径与取值 repr（数值/枚举/标签 id），绝不含原文片段（B1）。
"""

from __future__ import annotations

from typing import Any

# §6.1 枚举注册表（unknown 为合法缺省，不猜语义）
ROLE_VALUES = ("攻", "受", "互攻", "双性", "无明确分工", "unknown")
AGE_GROUP_VALUES = ("同龄", "年上", "年下", "跨代", "unknown")
EDGE_TYPE_VALUES = ("支配-服从", "对等", "保护", "利用", "unknown")
CONSENT_VALUES = ("自愿", "半自愿", "非自愿", "被迫转化", "unknown")
TOPOLOGY_VALUES = ("1v1", "1vN", "NvN", "群像", "三角", "unknown")
POWER_BALANCE_VALUES = ("对等", "强攻弱受", "弱攻强受", "双向支配", "unknown")
EVOLUTION_VALUES = ("稳定", "单向恶化", "双向沉沦", "救赎", "反转", "unknown")
EVIDENCE_SOURCE_VALUES = ("auto", "manual")

GRAPH_FIELDS = ("characters", "edges", "topology", "power_balance", "evolution", "evidence")


def empty_graph() -> dict[str, Any]:
    """契约缺省形态：无人物无互动 → topology=unknown 且低置信留空。"""
    return {
        "characters": [],
        "edges": [],
        "topology": "unknown",
        "power_balance": "unknown",
        "evolution": "unknown",
        "evidence": {"confidence": 0.0, "source": "auto", "basis": []},
    }


def _finding(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_relation_graph(graph: dict[str, Any]) -> list[dict[str, str]]:
    """校验图契约；返回 Finding 列表（code/path/message），空列表 = 通过。"""
    findings: list[dict[str, str]] = []

    known_ids: set[str] = set()
    for i, char in enumerate(graph.get("characters") or []):
        cid = char.get("id")
        if cid is None:
            findings.append(_finding("missing_id", f"characters[{i}].id", "character 缺少稳定 id"))
        elif str(cid) in known_ids:
            findings.append(_finding("duplicate_id", f"characters[{i}].id", f"重复人物 id: {cid}"))
        else:
            known_ids.add(str(cid))
        if char.get("role") not in ROLE_VALUES:
            findings.append(_finding("enum_out_of_range", f"characters[{i}].role", f"role 越界: {char.get('role')!r}"))
        for j, trait in enumerate(char.get("traits") or []):
            if not isinstance(trait, str):
                findings.append(_finding("trait_not_string", f"characters[{i}].traits[{j}]", "trait 必须是标签 id 字符串"))
        if char.get("age_group") not in AGE_GROUP_VALUES:
            findings.append(_finding("enum_out_of_range", f"characters[{i}].age_group", f"age_group 越界: {char.get('age_group')!r}"))

    for i, edge in enumerate(graph.get("edges") or []):
        src, dst = edge.get("from"), edge.get("to")
        if src is None or dst is None:
            findings.append(_finding("dangling_edge", f"edges[{i}]", "边缺少端点"))
        else:
            for side, ref in (("from", src), ("to", dst)):
                if str(ref) not in known_ids:
                    findings.append(_finding("dangling_edge", f"edges[{i}].{side}:{ref}", f"边端点未在 characters 中定义: {ref}"))
            if str(src) == str(dst):
                findings.append(_finding("self_loop", f"edges[{i}]", "边端点相同（自环）"))
        if edge.get("type") not in EDGE_TYPE_VALUES:
            findings.append(_finding("enum_out_of_range", f"edges[{i}].type", f"edge type 越界: {edge.get('type')!r}"))
        for j, mech in enumerate(edge.get("mechanism") or []):
            if not isinstance(mech, str):
                findings.append(_finding("mechanism_not_string", f"edges[{i}].mechanism[{j}]", "mechanism 必须是标签 alias 字符串"))
        intensity = edge.get("intensity")
        if intensity is not None and (not _is_number(intensity) or not 0.0 <= intensity <= 1.0):
            findings.append(_finding("intensity_out_of_range", f"edges[{i}].intensity", f"intensity 越界: {intensity!r}"))
        if edge.get("consent") not in CONSENT_VALUES:
            findings.append(_finding("enum_out_of_range", f"edges[{i}].consent", f"consent 越界: {edge.get('consent')!r}"))

    for field, values in (("topology", TOPOLOGY_VALUES), ("power_balance", POWER_BALANCE_VALUES), ("evolution", EVOLUTION_VALUES)):
        if graph.get(field) not in values:
            findings.append(_finding("enum_out_of_range", field, f"{field} 越界: {graph.get(field)!r}"))

    evidence = graph.get("evidence") or {}
    confidence = evidence.get("confidence")
    if not _is_number(confidence) or not 0.0 <= confidence <= 1.0:
        findings.append(_finding("evidence_out_of_range", "evidence.confidence", f"confidence 越界: {confidence!r}"))
    if evidence.get("source") not in EVIDENCE_SOURCE_VALUES:
        findings.append(_finding("evidence_out_of_range", "evidence.source", f"source 越界: {evidence.get('source')!r}"))
    for j, item in enumerate(evidence.get("basis") or []):
        if not isinstance(item, str):
            findings.append(_finding("basis_not_string", f"evidence.basis[{j}]", "basis 必须是统计量/别名引用"))

    return findings
