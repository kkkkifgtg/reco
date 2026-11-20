"""标签生命周期：新增 / 补 alias / 合并 / 归档（工单 06；规格「成长机制」表）。

- 新增：用户直接加，created_by=user，无 alias → 暂不参与自动识别。
- 合并：book_tags 历史逐行改指向（source/confidence/basis 原样保留——D3），
  source 标 status=merged + merged_into，别名原地保留（经链可解析）。
- 归档：status=archived，一切数据保留；不参与识别/打分（normalize 跳过）。
"""
from __future__ import annotations

from dataclasses import dataclass

from reco_labels.store import LabelsStore, TagRow


def add_tag(store: LabelsStore, *, tag_id: str, axis: str, label: str,
            parent: str | None = None, now: str = "") -> TagRow:
    if any(r.tag_id == tag_id for r in store.iter_tags()):
        raise ValueError(f"标签已存在: {tag_id}")
    row = TagRow(
        tag_id=tag_id, axis=axis, label=label, aliases=(), parent=parent,
        status="active", merged_into=None, created_by="user",
        created_at=now or "2026-11-20",
    )
    store.upsert_tag(row)
    store.append_event("tag_created", {
        "tag_id": tag_id, "axis": axis, "created_by": "user",
    }, occurred_at=now or "2026-11-20")
    return row


def set_aliases(store: LabelsStore, tag_id: str,
                aliases) -> TagRow:
    """补 alias = 升级为可识别（规格「升级为可识别」行）。"""
    rows = {r.tag_id: r for r in store.iter_tags()}
    row = rows[tag_id]
    cleaned = tuple(dict.fromkeys(a.strip() for a in aliases if a.strip()))
    updated = TagRow(
        tag_id=row.tag_id, axis=row.axis, label=row.label, aliases=cleaned,
        parent=row.parent, status=row.status, merged_into=row.merged_into,
        created_by=row.created_by, created_at=row.created_at,
    )
    store.upsert_tag(updated)
    return updated


@dataclass(frozen=True)
class MergeResult:
    source: str
    target: str
    redirected_rows: int


def merge_tags(store: LabelsStore, source_tag: str, target_tag: str) -> MergeResult:
    rows = {r.tag_id: r for r in store.iter_tags()}
    src = rows.get(source_tag)
    tgt = rows.get(target_tag)
    if src is None:
        raise ValueError(f"source 标签不存在: {source_tag}")
    if tgt is None:
        raise ValueError(f"target 标签不存在: {target_tag}")
    if source_tag == target_tag:
        raise ValueError("不能自合并")
    if tgt.status != "active":
        raise ValueError(f"target 必须是 active: {target_tag}={tgt.status}")

    redirected = store.redirect_book_tag_rows(source_tag, target_tag)
    merged_row = TagRow(
        tag_id=src.tag_id, axis=src.axis, label=src.label,
        aliases=src.aliases,  # 原地保留：经 merged_into 链仍可解析
        parent=src.parent, status="merged", merged_into=target_tag,
        created_by=src.created_by, created_at=src.created_at,
    )
    store.upsert_tag(merged_row)
    store.append_event("tag_merged", {
        "source": source_tag, "target": target_tag,
        "redirected_rows": redirected,
    }, occurred_at="2026-11-21")
    return MergeResult(source=source_tag, target=target_tag,
                       redirected_rows=redirected)


def archive_tag(store: LabelsStore, tag_id: str, now: str = "2026-11-21") -> TagRow:
    rows = {r.tag_id: r for r in store.iter_tags()}
    row = rows.get(tag_id)
    if row is None:
        raise ValueError(f"标签不存在: {tag_id}")
    archived = TagRow(
        tag_id=row.tag_id, axis=row.axis, label=row.label, aliases=row.aliases,
        parent=row.parent, status="archived", merged_into=row.merged_into,
        created_by=row.created_by, created_at=row.created_at,
    )
    store.upsert_tag(archived)
    store.append_event("tag_archived", {"tag_id": tag_id}, occurred_at=now)
    return archived
