"""proposals 投影的读面（#18）。

待确认列表的 SQL、排序（#90：count 降序、candidate 升序）与 payload 解析过去住在
reco-labels 的 StoreAdapter，UI 为了读列表反过来 import 业务包；现在 schema 与排序
知识回到 store 侧，labels 与 UI 都只依赖这里。

只读、零写库；payload 解析失败给空 dict（不猜、不抛）。
"""
from __future__ import annotations

import json

__all__ = ["iter_proposals", "pending_proposals"]


def _parse_object(raw) -> dict:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def iter_proposals(store, *, status: str | None = None) -> list[dict]:
    """提案行：[{proposal_id, kind, payload, status, created_at, candidate}]。

    排序由 SQL 保证（count 降序、candidate 升序）；status 给定时只取该状态。
    """
    sql = ("SELECT proposal_id, kind, payload_json, status, created_at"
           " FROM proposals")
    params: tuple = ()
    if status is not None:
        sql += " WHERE status = ?"
        params = (str(status),)
    sql += (" ORDER BY json_extract(payload_json, '$.count') DESC,"
            " json_extract(payload_json, '$.candidate') ASC")
    conn = store.read_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for proposal_id, kind, payload_json, row_status, created_at in rows:
        payload = _parse_object(payload_json)
        out.append({
            "proposal_id": str(proposal_id),
            "kind": str(kind),
            "payload": payload,
            "status": str(row_status),
            "created_at": str(created_at),
            "candidate": str(payload.get("candidate") or ""),
        })
    return out


def pending_proposals(store) -> list[dict]:
    """待确认提案（status=pending），顺序同 iter_proposals。"""
    return iter_proposals(store, status="pending")