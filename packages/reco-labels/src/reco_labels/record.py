"""record_tag_hits：把 TagHit 落 book_tags（工单 06；D1 写入层保护的主入口）。

PK(book_id, tag_id) 冲突时的语义（§7.2 + audit D1）：
- existing.source ∈ {manual, user} → 跳过该行（ManualTagProtected 在 store 层拦截，
  这里转为 RecordResult.skipped_manual），**绝不覆盖**；
- existing.source = auto → 更新 confidence/basis/updated_at；
- 无行 → 插入。
冲突裁定的命中附带写 tag_conflict 事件（payload 只含 alias/tag_id/计数——B 类）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from reco_labels.match import TagHit
from reco_labels.store import LabelsStore, ManualTagProtected


@dataclass(frozen=True)
class RecordResult:
    inserted: tuple
    updated: tuple
    skipped_manual: tuple


def record_tag_hits(store: LabelsStore, book_id: int, hits,
                    *, now: str) -> RecordResult:
    existing_by_tag: dict = {}
    for row in store.iter_book_tags():
        if row.book_id == book_id:
            existing_by_tag[row.tag_id] = row

    inserted: list = []
    updated: list = []
    skipped: list = []
    for hit in hits:
        tag_id = hit.tag_id
        basis_json = _serialize_basis(hit.basis)
        conflict = hit.basis.get("conflict")
        if conflict:
            store.append_event("tag_conflict", {
                "alias": conflict.get("alias"),
                "candidates": sorted(conflict.get("candidates", ())),
                "winner": conflict.get("winner"),
                "book_id": book_id,
            }, occurred_at=now)
        existing = existing_by_tag.get(tag_id)
        if existing is not None and existing.source in ("manual", "user"):
            skipped.append(tag_id)  # D1：manual/user 永不被 auto 覆盖
            continue
        try:
            store.record_book_tag(
                book_id, tag_id, source="auto",
                confidence=float(hit.confidence),
                basis_json=basis_json, updated_at=now,
            )
        except ManualTagProtected:
            skipped.append(tag_id)
            continue
        if existing is None:
            inserted.append(tag_id)
        else:
            updated.append(tag_id)
    return RecordResult(
        inserted=tuple(inserted), updated=tuple(updated),
        skipped_manual=tuple(skipped),
    )


def _serialize_basis(basis) -> str:
    import json

    return json.dumps(_plain(basis), ensure_ascii=False, sort_keys=True)


def _plain(value):
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)  # 罕见对象退化为表示串——绝不展开正文类内容
