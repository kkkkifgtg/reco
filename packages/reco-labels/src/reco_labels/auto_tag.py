"""liked 专区自动打标签（RR2/t3）——match_tags 真数据接线的编排层。

边界与裁定（G2r 审计基线 §L1-L7；工单 RR2/t3 验收口径）：
- 只对 zone=liked 触发（AutoTagOutcome.ZONES）：candidate/推荐专区不自动打标、
  不提议——candidate 未读未确认，若自动打标会把「未确认偏好」混进画像正样本
  （tag_hit 折叠进 axis_weights），污染轴权重与推荐依据，因此零动作。
- 双层（工单 RR2 ②）：已存在标签（taxonomy alias 命中）→ record_tag_hits 直写
  book_tags(source=auto)（D1+F2 双保护继承：manual/user 行在写入层拒绝覆盖）；
  未知词不自动建 tags 行 → propose_tags 进 proposals(pending) 待用户显式确认
  （accept_proposal 才建 user 标签，工单 06 语义）。
- 识别只靠「域→轴→值」taxonomy + aliases 命中（§L：不从正文推断新标签值）。
- B 类安全：事件/提议 payload 只含别名/计数/阈值/标签 id/zone/book_id——零正文；
  basis 由 match_tags 构造（只含别名/计数/裁定证据，绝无正文片段）。
- W4 接缝（stats.alias_counts 鸭子协议）：extract_features(extract_statistics) 的
  alias_counts 直接喂入；降级结果 alias_counts={} → 零命中零提议不抛异常。
- 画像联动（工单 RR2 ③）：调用方对 outcome.hits 逐个 append tag_hit 事件
  （payload: axis/tag_id/confidence——数值+枚举），W9 update_profile 折叠
  （clip |Δ|≤0.05），liked 24 本真实书打标后画像轴权重非空（验收④）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Mapping

from reco_labels.match import TagHit, match_tags
from reco_labels.proposals import propose_tags
from reco_labels.record import RecordResult, record_tag_hits
from reco_labels.store import BookTagRow, LabelsStore, ProposalRow, TagRow

# 自动提议阈值（工单 06 proposals 语义沿用：count≥3 才提议）
TAG_PROPOSE_THRESHOLD = 3


class StoreAdapter:
    """reco_store.Store → LabelsStore 窄协议适配（W5 对接位；只读视图 + 写转发）。

    Store 无 iter_tags 等方法——本适配从投影表读出并转换为 labels 侧行类型；
    写方法直接转发 store.append_event（单写者队列，零第二条写路径）。依赖方向：
    本类零 import reco-store——store 对象鸭子传入（§4.2，reco-labels 只挂 reco-core）。
    """

    def __init__(self, store) -> None:
        self._store = store

    def read_conn(self):
        """透传独立只读连接（脚本/调用方读取用；本适配自身读经 _store.read_conn）。"""
        return self._store.read_conn()

    def close(self) -> None:
        """透传底层 store 关闭（单写者队列优雅停机）。"""
        self._store.close()

    # ---- 读（投影表 → labels 行类型）----

    def iter_tags(self) -> Iterable[TagRow]:
        conn = self._store.read_conn()
        try:
            tags = conn.execute(
                "SELECT tag_id, axis, label, status, merged_into, created_by, created_at"
                " FROM tags"
            ).fetchall()
            aliases: dict[str, list[str]] = {}
            for alias, tag_id in conn.execute(
                "SELECT alias, tag_id FROM tag_aliases"
            ).fetchall():
                aliases.setdefault(str(tag_id), []).append(str(alias))
        finally:
            conn.close()
        for tag_id, axis, label, status, merged_into, created_by, created_at in tags:
            yield TagRow(
                tag_id=str(tag_id), axis=None if axis in (None, "") else str(axis),
                label=str(label), aliases=tuple(aliases.get(str(tag_id), ())),
                parent=None, status=str(status),
                merged_into=None if merged_into in (None, "") else str(merged_into),
                created_by=str(created_by or ""), created_at=str(created_at),
            )

    def iter_aliases(self) -> Iterable[tuple[str, str, str]]:
        conn = self._store.read_conn()
        try:
            rows = conn.execute("SELECT alias, tag_id, source FROM tag_aliases").fetchall()
        finally:
            conn.close()
        for alias, tag_id, source in rows:
            yield (str(alias), str(tag_id), str(source or ""))

    def iter_book_tags(self) -> Iterable[BookTagRow]:
        conn = self._store.read_conn()
        try:
            rows = conn.execute(
                "SELECT book_id, tag_id, source, confidence, basis_json, updated_at"
                " FROM book_tags"
            ).fetchall()
        finally:
            conn.close()
        for book_id, tag_id, source, confidence, basis_json, updated_at in rows:
            yield BookTagRow(
                book_id=int(book_id), tag_id=str(tag_id), source=str(source),
                confidence=float(confidence or 0.0), basis_json=str(basis_json or "{}"),
                updated_at=str(updated_at),
            )

    def iter_proposals(self) -> Iterable[ProposalRow]:
        conn = self._store.read_conn()
        try:
            rows = conn.execute(
                "SELECT proposal_id, kind, payload_json, status, created_at, candidate"
                " FROM proposals ORDER BY proposal_id"
            ).fetchall()
        finally:
            conn.close()
        for proposal_id, kind, payload_json, status, created_at, candidate in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except (TypeError, ValueError):
                payload = {}
            yield ProposalRow(
                proposal_id=str(proposal_id), kind=str(kind),
                payload=payload if isinstance(payload, Mapping) else {},
                status=str(status), created_at=str(created_at), candidate=str(candidate or ""),
            )

    def iter_events(self) -> Iterable:
        return iter(())  # auto_tag 链路不消费 events（标签事件经 write_conn 事务内落库）

    # ---- 写（转发 append_event，单写者队列）----

    def upsert_tag(self, row: TagRow) -> None:
        payload = {"tag_id": row.tag_id, "axis": row.axis or "", "label": row.label,
                   "parent": row.parent, "created_by": row.created_by}
        if row.aliases:
            payload["aliases"] = [str(a) for a in row.aliases]
        self._store.append_event(
            "tag_added", payload=payload,
            occurred_at=row.created_at, config_fingerprint="rr2/adapter/1",
        )

    def record_book_tag(self, book_id: int, tag_id: str, *, source: str,
                        confidence: float, basis_json: str, updated_at: str) -> None:
        self._store.append_event(
            "label_set",
            payload={"tag_id": str(tag_id), "source": str(source),
                     "confidence": float(confidence), "basis_json": str(basis_json)},
            book_id=int(book_id), occurred_at=updated_at,
            config_fingerprint="rr2/adapter/1",
        )

    def redirect_book_tag_rows(self, source_tag: str, target_tag: str) -> int:
        raise NotImplementedError("redirect 走 tag_merged 事件（lifecycle），本适配不直写")

    def append_event(self, kind: str, payload: Mapping, occurred_at: str) -> None:
        if kind == "tag_conflict":
            self._store.append_event(
                "tag_conflict", payload=dict(payload), occurred_at=str(occurred_at),
                config_fingerprint="rr2/adapter/1",
            )
            return
        # labels 协议事件（tag_proposed/proposal_accepted/…）→ proposal_created 投影
        candidate = str(payload.get("candidate") or "")
        self._store.append_event(
            "proposal_created",
            payload={"kind": str(payload.get("kind") or "tag"),
                     "candidate": candidate,
                     "payload_json": json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                     "evidence_json": json.dumps(
                         {"count": payload.get("count"), "threshold": payload.get("threshold")},
                         ensure_ascii=False, sort_keys=True)},
            occurred_at=str(occurred_at), config_fingerprint="rr2/adapter/1",
        )

    def insert_proposal(self, kind: str, candidate: str,
                        payload: Mapping, created_at: str) -> str:
        self.append_event("tag_proposed", {
            "kind": str(kind), "candidate": str(candidate),
            **{k: v for k, v in dict(payload).items() if k != "kind"},
        }, str(created_at))
        return f"prop-{str(candidate)}"

    def update_proposal_status(self, proposal_id: str, status: str) -> None:
        raise NotImplementedError("提案状态变更走 proposal_accepted/rejected 事件（后续票）")


@dataclass(frozen=True)
class AutoTagOutcome:
    """一次自动打标的完整摘要（数值/枚举/标签 id——B 类，无正文）。

    zone 裁定内嵌：AutoTagOutcome.ZONES 只含 liked；非 liked 一律 zero-out
    （skipped=True），不写 book_tags、不产提议。
    """

    ZONES = ("liked",)  # 触发专区白名单：只有 liked（已确认喜欢）自动打标

    book_id: int
    zone: str
    skipped: bool            # True = 非 liked 专区（或零命中零提议的空转）
    hits: tuple[TagHit, ...]
    record: RecordResult
    proposed: tuple[str, ...]  # 新词提案（proposals pending，待用户确认）

    @property
    def inserted(self) -> tuple:
        return self.record.inserted

    @property
    def updated(self) -> tuple:
        return self.record.updated

    @property
    def skipped_manual(self) -> tuple:
        return self.record.skipped_manual


def aliases_for_match(store: LabelsStore) -> tuple[str, ...]:
    """自动识别词表：active 标签的 tag_id + aliases（label 不参与，archived 不参与）。

    与 load_taxonomy 的身份键完全一致（taxonomy.py：tag_id 本身 + 全部 alias）。
    """
    return tuple(
        key
        for row in store.iter_tags()
        if row.status == "active"
        for key in (row.tag_id, *row.aliases)
        if str(key).strip()
    )


def _unknown_counts(store: LabelsStore, hits: tuple[TagHit, ...],
                    stats) -> dict[str, int]:
    """stats 里未命中任何标签的正计数（进 proposals；0/负计数一律忽略）。"""
    from reco_labels.match import _stats_alias_counts

    hit_aliases = {
        str(m.get("alias"))
        for hit in hits
        for m in (hit.basis.get("matches") or ())
    }
    out: dict[str, int] = {}
    for alias, count in dict(_stats_alias_counts(stats)).items():
        try:
            count_i = int(count)
        except (TypeError, ValueError):
            continue
        if count_i <= 0:
            continue
        if str(alias) not in hit_aliases:
            out[str(alias)] = count_i
    return out


def auto_tag_book(
    store: LabelsStore,
    book_id: int,
    zone: str,
    stats,
    *,
    now: str,
    propose_threshold: int = TAG_PROPOSE_THRESHOLD,
) -> AutoTagOutcome:
    """单本 liked 书的自动打标编排：match → record(auto) → propose(新词)。

    - zone 不在 ZONES：零动作（candidate/推荐专区不触发——未读不污染画像）；
    - taxonomy alias 命中 → record_tag_hits 落 book_tags(source=auto)
      （manual/user 由写入层 ManualTagProtected 拒绝 → skipped_manual）；
    - 未知正计数词 → propose_tags 进 proposals(pending)，tags 表零改动。
    返回 AutoTagOutcome；调用方据 outcome.hits 落 tag_hit 事件供 W9 折叠画像。
    """
    if zone not in AutoTagOutcome.ZONES:
        raise ValueError(
            f"自动打标仅限 {AutoTagOutcome.ZONES} 专区，收到 {zone!r}"
            "（candidate 未读不污染画像——RR2 裁定）"
        )
    taxonomy_rows = tuple(store.iter_tags())
    taxonomy = _load_taxonomy(store)
    hits = match_tags(stats, taxonomy)
    unknown = _unknown_counts(store, hits, stats)
    record = record_tag_hits(store, book_id, hits, now=now)
    proposed: tuple[str, ...] = ()
    if unknown:
        proposals = propose_tags(
            store, unknown, threshold=propose_threshold, now=now,
        )
        proposed = tuple(p.candidate for p in proposals)
    skipped = not hits and not proposed
    return AutoTagOutcome(
        book_id=book_id, zone=zone, skipped=skipped, hits=hits,
        record=record, proposed=proposed,
    )


def _load_taxonomy(store: LabelsStore):
    from reco_labels.taxonomy import load_taxonomy

    return load_taxonomy(store)
