"""逐字段真相与写入层 manual 保护（工单 07 切片 3，D2 核心契约）。

真相结构：fields = {path: {value, source}}（source ∈ auto/manual）；
graph_json 只是 snapshot_from_fields 的可重建快照。

写入层纪律（L611/L618-620、CONTEXT.md 关系图）：
- source=manual 的字段永不被自动提取覆盖：apply_auto_extraction 拒写并记 refused。
- manual 写入（merge_manual_overrides）先过路径白名单与 validate_relation_graph
  （候选快照必须过图契约），拒绝发生在写入层。
- changed_by 记录逐字段写入者与来源快照标识（D2：合并结果记 changed_by）。
"""

from __future__ import annotations

from typing import Any

from .graph import empty_graph, validate_relation_graph

_WRITABLE_CHAR_FIELDS = ("role", "age_group", "traits")
_WRITABLE_EDGE_FIELDS = ("type", "mechanism", "intensity", "consent")
_WRITABLE_TOP_FIELDS = ("topology", "power_balance", "evolution")
_WRITABLE_EVIDENCE_FIELDS = ("confidence", "source", "basis")


def _parse_path(path: str) -> tuple[str, Any, str | None]:
    """解析字段路径 → (kind, key, attr)；未知形状在写入层拒绝。"""
    if path.startswith("characters[") and "]" in path:
        end = path.index("]")
        cid, attr = path[11:end], path[end + 2:]
        if attr in _WRITABLE_CHAR_FIELDS:
            return ("char", cid, attr)
    elif path.startswith("edges[") and "]" in path and "->" in path[6:path.index("]")]:
        end = path.index("]")
        src, dst = path[6:end].split("->", 1)
        attr = path[end + 2:]
        if attr in _WRITABLE_EDGE_FIELDS:
            return ("edge", (src, dst), attr)
    elif path in _WRITABLE_TOP_FIELDS:
        return ("top", path, None)
    elif path.startswith("evidence.") and path[9:] in _WRITABLE_EVIDENCE_FIELDS:
        return ("ev", path[9:], None)
    raise ValueError(f"unknown_field_path: {path} 不在契约字段白名单内")


def _edge_key(edge: dict[str, Any]) -> str:
    return f"edges[{edge.get('from')}->{edge.get('to')}]"


def graph_field_paths(graph: dict[str, Any]) -> list[str]:
    """枚举契约内全部可编辑字段路径（边的身份端点不是字段，不在列）。"""
    paths: list[str] = []
    for i, char in enumerate(graph.get("characters") or []):
        cid = char.get("id")
        if cid is None:
            raise ValueError(f"missing_id at characters[{i}]: id 是字段枚举的前提")
        for f in _WRITABLE_CHAR_FIELDS:
            paths.append(f"characters[{cid}].{f}")
    for edge in graph.get("edges") or []:
        for f in _WRITABLE_EDGE_FIELDS:
            paths.append(f"{_edge_key(edge)}.{f}")
    for f in _WRITABLE_TOP_FIELDS:
        paths.append(f)
    for f in _WRITABLE_EVIDENCE_FIELDS:
        paths.append(f"evidence.{f}")
    return paths


def _fields_from_graph(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for char in graph.get("characters") or []:
        for f in _WRITABLE_CHAR_FIELDS:
            fields[f"characters[{char['id']}].{f}"] = {"value": char.get(f), "source": "auto"}
    for edge in graph.get("edges") or []:
        key = _edge_key(edge)
        for f in _WRITABLE_EDGE_FIELDS:
            fields[f"{key}.{f}"] = {"value": edge.get(f), "source": "auto"}
    for f in _WRITABLE_TOP_FIELDS:
        fields[f] = {"value": graph.get(f), "source": "auto"}
    for f in _WRITABLE_EVIDENCE_FIELDS:
        fields[f"evidence.{f}"] = {"value": (graph.get("evidence") or {}).get(f), "source": "auto"}
    return fields


def _validate_manual_updates(graph: dict[str, Any], updates: dict[str, Any]) -> None:
    """写入层校验：未知路径拒绝；候选快照必须过图契约（悬空边/越界等）。"""
    known = set(graph_field_paths(graph))
    for path in updates:
        if path not in known:
            raise ValueError(f"unknown_field_path: {path} 不在契约字段白名单内")
    candidate = snapshot_from_fields(
        {**_fields_from_graph(graph),
         **{p: {"value": v, "source": "manual"} for p, v in updates.items()}}
    )
    findings = validate_relation_graph(candidate)
    if findings:
        codes = ",".join(f"{f['code']}@{f['path']}" for f in findings)
        raise ValueError(f"manual_update_rejected: 候选快照未过图契约 [{codes}]")


def _apply_updates(
    fields: dict[str, dict[str, Any]],
    updates: dict[str, Any],
    by: str,
    graph_stamp: str,
    force: bool,
    changed_by: list[dict[str, Any]],
    refused: list[str],
) -> None:
    """按 by 写入字段；force=False 时 manual 字段拒写并记 refused。"""
    stamp_map = {e["path"]: e["graph_stamp"] for e in changed_by if e["by"] == by}
    for path, value in updates.items():
        current = fields.get(path)
        if current is None:
            raise ValueError(f"unknown_field_path: {path}")
        if not force and current["source"] == "manual":
            refused.append(path)
            continue
        stamp = stamp_map.get(path, graph_stamp)
        fields[path] = {"value": value, "source": by}
        changed_by.append({"path": path, "by": by, "graph_stamp": stamp})


def merge_manual_overrides(
    auto_graph: dict[str, Any],
    manual_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    """把用户改过的字段逐字段落进真相结构；只合并用户改过字段。"""
    findings = validate_relation_graph(auto_graph)
    if findings:
        codes = ",".join(f"{f['code']}@{f['path']}" for f in findings)
        raise ValueError(f"invalid_auto_graph: 输入图未过图契约 [{codes}]")
    updates = dict(manual_fields) if manual_fields else {}
    _validate_manual_updates(auto_graph, updates)
    fields = _fields_from_graph(auto_graph)
    changed_by: list[dict[str, Any]] = []
    refused: list[str] = []
    _apply_updates(fields, updates, "manual", "initial", True, changed_by, refused)
    return {"fields": fields, "changed_by": changed_by, "refused": refused,
            "field_sources": {p: f["source"] for p, f in fields.items()}}


def apply_auto_extraction(
    state: dict[str, Any],
    new_auto_graph: dict[str, Any],
) -> dict[str, Any]:
    """自动提取结果写入真相结构：manual 字段拒写并记 refused（写入层保护）。"""
    findings = validate_relation_graph(new_auto_graph)
    if findings:
        codes = ",".join(f"{f['code']}@{f['path']}" for f in findings)
        raise ValueError(f"invalid_auto_graph: 新自动图未过图契约 [{codes}]")
    fields = state["fields"]
    incoming = _fields_from_graph(new_auto_graph)
    drift = set(incoming) - set(fields)
    if drift:
        raise ValueError(f"contract_drift: 新图带出未知字段 {sorted(drift)}")
    stamp = f"auto@{new_auto_graph.get('evidence', {}).get('confidence', 0):.4f}"
    changed_by = list(state.get("changed_by") or [])
    refused = list(state.get("refused") or [])
    auto_updates = {p: f["value"] for p, f in incoming.items()}
    _apply_updates(fields, auto_updates, "auto", stamp, False, changed_by, refused)
    return {"fields": fields, "changed_by": changed_by, "refused": refused,
            "field_sources": {p: f["source"] for p, f in fields.items()}}


def snapshot_from_fields(fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """从逐字段真相重建 graph_json 快照（manual 字段原样进入快照）。"""
    chars: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    graph = empty_graph()
    for path, cell in fields.items():
        kind, key, attr = _parse_path(path)
        value = cell["value"]
        if kind == "char":
            chars.setdefault(key, {"id": key, "role": "unknown", "traits": [], "age_group": "unknown"})[attr] = value
        elif kind == "edge":
            edges.setdefault(key, {"from": key[0], "to": key[1], "type": "unknown",
                                   "mechanism": [], "intensity": None, "consent": "unknown"})[attr] = value
        elif kind == "top":
            graph[key] = value
        else:
            graph["evidence"][key] = value
    graph["characters"] = [chars[k] for k in sorted(chars)]
    graph["edges"] = [edges[k] for k in sorted(edges)]
    return graph
