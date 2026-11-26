"""propose_tags / accept / reject（工单 06；规格「自动发现」行）。

- propose 只写 proposals(pending) + tag_proposed 事件，**绝不创建 tags 行**。
- 显式确认 accept_proposal 才建标签（created_by=user；#83 起值行显示名即参与识别）。
- cooccurrence 是外部传入的共现统计（W4/后续统计侧），本模块不计算共现。
"""
from __future__ import annotations

from dataclasses import dataclass

from reco_labels.clock import resolve
from reco_labels.lifecycle import add_tag, set_aliases
from reco_labels.store import LabelsStore, TagRow


@dataclass(frozen=True)
class TagProposal:
    candidate: str
    count: int
    status: str
    proposal_id: str
    kind: str = "tag"  # proposals 表 kind 列（§7.2）：tag/alias/relation


def propose_tags(store: LabelsStore, cooccurrence, *, threshold: int,
                 now: str = "") -> tuple:
    at = resolve(now)
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
            created_at=at,
        )
        store.append_event("tag_proposed", {
            "candidate": candidate, "count": count, "threshold": threshold,
        }, occurred_at=at)
        proposals.append(TagProposal(candidate=candidate, count=count,
                                     status="pending", proposal_id=proposal_id,
                                     kind="tag"))
    return tuple(proposals)


def _is_one_char_variant(a: str, b: str) -> bool:
    """等长且恰有一字不同（谐音/形近防和谐变体的廉价形判；不做模糊匹配语义）。"""
    if len(a) != len(b) or len(a) < 2:
        return False
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def variant_alias_candidates(unknown, taxonomy) -> dict[str, tuple[str, int]]:
    """未知高频词中，与某 active 值标签匹配词（显示名或别名）仅差一字的 → {词: (tag_id, 计数)}。

    只做「等长差一字」的形近判定（作者防和谐的谐音/形近写法常见形态；基准含
    别名词，如「洗脑」的变体也能挂回 mech.hypnosis）；判定仅用于「提议该词为
    原标签别名」，采纳与否仍由用户在提案页决定——匹配本身依旧只认词表精确
    命中，绝不模糊猜（Q11/§L 不变）。同词多候选按 (tag_id, 键) 字典序取最小。
    """
    from reco_labels.taxonomy import match_keys

    bases: list[tuple[str, str]] = []  # (匹配词, tag_id)；tag_id 本身不作形近基准
    for row in taxonomy.rows:
        if row.status != "active" or "." not in row.tag_id:
            continue
        for key in match_keys(row):
            k = str(key).strip()
            if k and k != str(row.tag_id):
                bases.append((k, row.tag_id))
    out: dict[str, tuple[str, int]] = {}
    for word, count in dict(unknown).items():
        w = str(word).strip()
        if len(w) < 2:
            continue
        for key, tag_id in sorted(bases):
            if w != key and _is_one_char_variant(w, key):
                out[w] = (tag_id, int(count))
                break
    return out


def propose_alias_tags(store, candidates, *, threshold: int,
                       now: str = "") -> tuple:
    """别名提案：{词: (目标tag_id, 计数)} 中 count>=threshold → proposals(kind=alias)。

    与 propose_tags 同一套双层边界：只写提案不改别名；显式 accept 才 set_aliases。
    已在 pending（任意 kind）或已是该标签别名的词跳过（幂等）。
    """
    at = resolve(now)
    existing_pending = {
        r.candidate for r in store.iter_proposals() if r.status == "pending"
    }
    known_aliases = {
        (str(r.tag_id), str(a))
        for r in store.iter_tags() for a in r.aliases
    }
    proposals = []
    for candidate in sorted(candidates):
        tag_id, count = candidates[candidate]
        if int(count) < threshold or candidate in existing_pending:
            continue
        if (str(tag_id), candidate) in known_aliases:
            continue
        proposal_id = store.insert_proposal(
            kind="alias", candidate=candidate,
            payload={"candidate": candidate, "count": int(count),
                     "threshold": int(threshold), "tag_id": str(tag_id)},
            created_at=at,
        )
        store.append_event("tag_proposed", {
            "candidate": candidate, "count": int(count), "threshold": int(threshold),
        }, occurred_at=at)
        proposals.append(TagProposal(candidate=candidate, count=int(count),
                                     status="pending", proposal_id=proposal_id,
                                     kind="alias"))
    return tuple(proposals)


def accept_proposal(store: LabelsStore, candidate: str, *, axis: str,
                    tag_id: str | None = None, now: str = "") -> TagRow:
    """显式确认提案：tag 类建 user 标签；alias 类把词挂为目标标签的别名（#83）。

    #83 起值行显示名参与识别：tag 类采纳即自动可识别；alias 类采纳后该词
    经 set_aliases 升级为原标签的识别词——两条路都不再需要二次补 alias。
    """
    at = resolve(now)
    pending = [
        r for r in store.iter_proposals()
        if r.candidate == candidate and r.status == "pending"
    ]
    if not pending:
        raise ValueError(f"无 pending 提案: {candidate}")
    row = pending[0]
    if str(getattr(row, "kind", "tag")) == "alias":
        target = str((row.payload or {}).get("tag_id") or "")
        if not target:
            raise ValueError(f"别名提案缺目标标签: {candidate}")
        current = next(r for r in store.iter_tags() if r.tag_id == target)
        # #83 追加语义：新变体并入既有别名表，绝不整体替换（保护种子/已确认别名）
        set_aliases(store, target, (*current.aliases, candidate))
        store.update_proposal_status(row.proposal_id, "accepted")
        store.append_event("proposal_accepted", {
            "candidate": candidate, "tag_id": target, "kind": "alias",
        }, occurred_at=at)
        return next(r for r in store.iter_tags() if r.tag_id == target)
    final_tag_id = tag_id or _auto_tag_id(store, candidate, axis)
    add_tag(store, tag_id=final_tag_id, axis=axis, label=candidate,
            parent=None, now=at)
    store.update_proposal_status(pending[0].proposal_id, "accepted")
    store.append_event("proposal_accepted", {
        "candidate": candidate, "tag_id": final_tag_id,
    }, occurred_at=at)
    return next(r for r in store.iter_tags() if r.tag_id == final_tag_id)


def reject_proposal(store: LabelsStore, candidate: str,
                    now: str = "") -> None:
    at = resolve(now)
    pending = [
        r for r in store.iter_proposals()
        if r.candidate == candidate and r.status == "pending"
    ]
    if not pending:
        raise ValueError(f"无 pending 提案: {candidate}")
    store.update_proposal_status(pending[0].proposal_id, "rejected")
    store.append_event("proposal_rejected", {"candidate": candidate}, occurred_at=at)


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