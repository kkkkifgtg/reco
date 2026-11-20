"""存储层默认拒绝（工单 07 切片 4b，D2 纵深，L611）。

save_relation_fields 默认只收 source=auto；manual 只能走显式 edit=True 的
编辑字段路径（「用户的显式编辑动作」是唯一 manual 入口）。
rebuild_graph_from_store 是快照重建的键迁移形态：EdgeKey 变更时 manual
标注原样保留、source 永不变 auto（D2：重建不得把 manual 改写成 auto）。
"""

from __future__ import annotations

from typing import Any

from .fields import _parse_path


def save_relation_fields(
    store: dict[str, Any],
    updates: dict[str, Any],
    source: str = "auto",
    edit: bool = False,
) -> None:
    """把字段写进存储 dict（in-memory 形态；SQLite 侧由工单 05 落表）。

    - 默认 source=auto（自动提取写它的路径）。
    - source="manual" 必须 edit=True（显式编辑动作），否则写入层拒绝。
    - 每个路径的形状合法性由统一解析器把关（未知路径直接拒绝）。
    """
    if source == "manual" and not edit:
        raise ValueError("manual_write_requires_edit: source=manual 只能走显式编辑字段路径 (edit=True)")
    if source not in ("auto", "manual"):
        raise ValueError(f"unknown_source: {source!r}")
    fields = store.setdefault("fields", {})
    for path, value in updates.items():
        _parse_path(path)  # 未知路径形状 → ValueError
        fields[path] = {"value": value, "source": source}


def rebuild_graph_from_store(
    store: dict[str, Any],
    key_migration: dict[str, str] | None = None,
) -> dict[str, Any]:
    """重建真相结构：EdgeKey 变更时迁移字段，manual 标注原样保留（D2）。"""
    migration = dict(key_migration) if key_migration else {}
    fields: dict[str, dict[str, Any]] = {}
    for path, cell in (store.get("fields") or {}).items():
        kind, key, attr = _parse_path(path)
        if kind == "edge":
            edge_key = f"edges[{key[0]}->{key[1]}]"
            new_key = migration.get(edge_key, edge_key)
            inner = new_key[6:-1]
            src, dst = inner.split("->", 1)
            new_path = f"edges[{src}->{dst}].{attr}"
        else:
            new_path = path
        fields[new_path] = {"value": cell["value"], "source": cell["source"]}
    return {"fields": fields, "changed_by": list((store.get("changed_by") or [])),
            "refused": list((store.get("refused") or [])),
            "field_sources": {p: f["source"] for p, f in fields.items()}}
