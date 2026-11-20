"""propose_tags / accept / reject（工单 06；规格「自动发现」行）。

- propose 只写 proposals(pending) + tag_proposed 事件，**绝不创建 tags 行**。
- 显式确认 accept_proposal 才建标签（created_by=user、无 alias=暂不识别）。
- cooccurrence 是外部传入的共现统计（W4/后续统计侧），本模块不计算共现。
"""
from __future__ import annotations

from dataclasses import dataclass

from reco_labels.lifecycle import add_tag
from reco_labels.store import LabelsStore, TagRow


@dataclass(frozen=True)
class TagProposal:
    candidate: str
    count: int
    status: str
    proposal_id: str
    kind: str = "tag"  # proposals 表 kind 列（§7.2）：tag/alias/relation


def propose_tags(store: LabelsStore, cooccurrence, *, threshold: int,
                 now: str = "2026-11-21") -> tuple:
    existing_pending = {
        r.candidate for r in store.iter_proposals() if r.status == "pending"
    }
    known_tags = {r.tag_id for r in store.iter_tags()}
    proposals = []
    for candidate in sorted(cooccurrence):
        count = cooccurrence[candidate]
        if count < threshold:
            continue
        if candidate in known_tags or candidate in existing_pending:
            continue  # 已知标签/已在 pending → 不重复写
        proposal_id = store.insert_proposal(
            kind="tag", candidate=candidate,
            payload={"candidate": candidate, "count": count, "threshold": threshold},
            created_at=now,
        )
        store.append_event("tag_proposed", {
            "candidate": candidate, "count": count, "threshold": threshold,
        }, occurred_at=now)
        proposals.append(TagProposal(candidate=candidate, count=count,
                                     status="pending", proposal_id=proposal_id,
                                     kind="tag"))
    return tuple(proposals)


def accept_proposal(store: LabelsStore, candidate: str, *, axis: str,
                    tag_id: str | None = None, now: str = "2026-11-21") -> TagRow:
    """显式确认提案：创建 user 标签（无 alias → 暂不自动识别，规格「新增」行）。"""
    pending = [
        r for r in store.iter_proposals()
        if r.candidate == candidate and r.status == "pending"
    ]
    if not pending:
        raise ValueError(f"无 pending 提案: {candidate}")
    final_tag_id = tag_id or _auto_tag_id(store, candidate, axis)
    add_tag(store, tag_id=final_tag_id, axis=axis, label=candidate,
            parent=None, now=now)
    store.update_proposal_status(pending[0].proposal_id, "accepted")
    store.append_event("proposal_accepted", {
        "candidate": candidate, "tag_id": final_tag_id,
    }, occurred_at=now)
    return next(r for r in store.iter_tags() if r.tag_id == final_tag_id)


def reject_proposal(store: LabelsStore, candidate: str,
                    now: str = "2026-11-21") -> None:
    pending = [
        r for r in store.iter_proposals()
        if r.candidate == candidate and r.status == "pending"
    ]
    if not pending:
        raise ValueError(f"无 pending 提案: {candidate}")
    store.update_proposal_status(pending[0].proposal_id, "rejected")
    store.append_event("proposal_rejected", {"candidate": candidate}, occurred_at=now)


def _auto_tag_id(store: LabelsStore, candidate: str, axis: str) -> str:
    prefix = (axis.split(".")[0][:4] or "user")
    rows = {r.tag_id for r in store.iter_tags()}
    base = f"{prefix}.{candidate}"
    if base not in rows:
        return base
    n = 2
    while f"{base}#{n}" in rows:
        n += 1
    return f"{base}#{n}"
