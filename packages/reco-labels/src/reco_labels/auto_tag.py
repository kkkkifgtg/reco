"""liked 专区自动打标签（RR2/t3）——match_tags 真数据接线的编排层。

边界与裁定（G2r 审计基线 §L1-L7；工单 RR2/t3 验收口径）：
- 触发专区（AutoTagOutcome.ZONES）= liked + candidate（#83 报告人裁定：candidate
  1529 本也要打标）。画像防污染边界不变：tag_hit 画像事件只对 liked 落（candidate
  的 book_tags 仅供展示与人工确认，不折叠进 axis_weights），折叠与否由调用方裁定；
  推荐专区等其他 zone 仍 ValueError 拒绝（零写入）。
- 双层（工单 RR2 ②）：已存在标签（taxonomy alias 命中）→ record_tag_hits 直写
  book_tags(source=auto)（D1+F2 双保护继承：manual/user 行在写入层拒绝覆盖）；
  未知词不自动建 tags 行 → propose_tags 进 proposals(pending) 待用户显式确认
  （accept_proposal 才建 user 标签，工单 06 语义）。
- 识别只靠「域→轴→值」taxonomy + aliases + 值行显示名命中（§L 不从正文推断新
  标签值；#83 起显示名参与，谐音/形近变体走 alias 提案，仍不自动建标签）。
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
from datetime import datetime, timezone
from typing import Iterable, Mapping

from reco_labels.match import TagHit, match_tags
from reco_labels.taxonomy import match_keys
from reco_labels.proposals import propose_alias_tags, propose_tags, variant_alias_candidates
from reco_labels.record import RecordResult, record_tag_hits
from reco_labels.store import BookTagRow, LabelsStore, ProposalRow, TagRow

# 自动提议阈值（工单 06 proposals 语义沿用：count≥3 才提议）
TAG_PROPOSE_THRESHOLD = 3

# StoreAdapter 写路径指纹（事件流里标识来源）与 labels 事件折叠规则
_ADAPTER_FP = "rr2/adapter/1"
_PROPOSAL_STATUSES = frozenset({"pending", "accepted", "rejected"})
# 已在对应写方法里落库的 labels 事件：append_event 收到时不再重复写，
# 也绝不误映射成 proposal_created。
_EVENTS_FOLDED_ELSEWHERE = frozenset({
    "tag_created", "tag_proposed", "proposal_accepted", "proposal_rejected",
    "tag_conflict",
})


def _now_iso() -> str:
    """提案状态事件的 occurred_at 兜底（协议签名不带时间戳时）：UTC 秒级。

    重放照事件里记录的 occurred_at 逐位回放，写一次即永久固定——不影响确定性；
    与 reco_store.store._now_iso 同一口径（此处不 import store：§4.2 依赖方向）。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_json_object(raw) -> dict:
    """payload_json → 字典；损坏/非对象一律降级为空字典（读路径不抛）。"""
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


class StoreAdapter:
    """reco_store.Store → LabelsStore 窄协议适配（W5 对接位；只读视图 + 写转发）。

    Store 无 iter_tags 等方法——本适配从投影表读出并转换为 labels 侧行类型。
    写路径：能映射成 reco-store 合法事件词汇的 labels 事件（tag_archived / label_set /
    label_cleared）直接转发 append_event（单写者队列）；tag_created、tag_proposed、
    proposal_accepted/rejected、tag_conflict 在对应写方法里已落库，append_event
    收到时折叠为 no-op，绝不误映射成 proposal_created。

    T8（store 票 #40/#41/#42）已把事件契约的三处缺口补齐，本适配随之全部走事件：
    ① proposal_created 事件携带 payload_json/evidence_json（白名单已收录，标量
       JSON 字符串），投影同事务落 proposals 两列——不再队列补写；
    ② tag_merged 事件携带 into（白名单已收录），合并态（tags.status/merged_into +
       合并别名）由 store 投影完成——不再有 _patch_merged_tag 旁路；book_tags 的
       auto 行仍走 label_set + label_cleared 事件（可重放）；
    ③ 提案状态变更经 proposal_updated 事件（payload = proposal_id/status）落库，
       重放恢复 proposals.status——不再直改投影。
    三处投影现在都是事件流的纯函数（ADR-0003）：绕过事件直改会被 verify_replay 发现。
    依赖方向：本类零 import reco-store——store 对象鸭子传入（§4.2）。
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
        """读 proposals 投影：candidate 不在真实列里（schema 无该列），从 payload_json 取。"""
        conn = self._store.read_conn()
        try:
            rows = conn.execute(
                "SELECT proposal_id, kind, payload_json, status, created_at"
                " FROM proposals ORDER BY proposal_id"
            ).fetchall()
        finally:
            conn.close()
        for proposal_id, kind, payload_json, status, created_at in rows:
            payload = _parse_json_object(payload_json)
            yield ProposalRow(
                proposal_id=str(proposal_id), kind=str(kind), payload=payload,
                status=str(status), created_at=str(created_at),
                candidate=str(payload.get("candidate") or ""),
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
        """合并搬迁：auto 行经 label_set + label_cleared 事件改写（事件流可重放）。

        manual/user 行不搬——reco-store 无「重写人工行」事件（D1/F2 写入层双保护），
        其身份经 tags.merged_into 链解析；彻底搬迁需 store 增补专用事件。
        """
        conn = self._store.read_conn()
        try:
            rows = conn.execute(
                "SELECT book_id, source, confidence, basis_json, updated_at"
                " FROM book_tags WHERE tag_id = ?", (str(source_tag),)
            ).fetchall()
        finally:
            conn.close()
        moved = 0
        for book_id, source, confidence, basis_json, updated_at in rows:
            if str(source) in ("manual", "user"):
                continue
            self.record_book_tag(
                int(book_id), str(target_tag), source=str(source),
                confidence=float(confidence or 0.0),
                basis_json=str(basis_json or "{}"), updated_at=str(updated_at),
            )
            self._store.append_event(
                "label_cleared", book_id=int(book_id),
                payload={"tag_id": str(source_tag)},
                occurred_at=str(updated_at), config_fingerprint=_ADAPTER_FP,
            )
            moved += 1
        return moved

    def append_event(self, kind: str, payload: Mapping, occurred_at: str,
                     *, book_id: int | None = None) -> None:
        """labels 事件 → reco-store 事件词汇分派（payload 只送白名单键，不误映射）。"""
        at = str(occurred_at)
        if kind == "relation_updated":
            # 关系图事件：必须带 book_id（relation_graphs 以 book_id 为主键）
            if book_id is None:
                raise ValueError("relation_updated 事件缺 book_id")
            self._store.append_event(
                "relation_updated",
                payload={
                    "graph_json": str(payload.get("graph_json") or ""),
                    "extractor_version": str(payload.get("extractor_version") or ""),
                    "confidence": float(payload.get("confidence") or 0.0),
                },
                book_id=int(book_id),
                occurred_at=at, config_fingerprint=_ADAPTER_FP,
            )
        elif kind == "tag_archived":
            self._store.append_event(
                "tag_archived", payload={"tag_id": str(payload.get("tag_id") or "")},
                occurred_at=at, config_fingerprint=_ADAPTER_FP,
            )
        elif kind == "tag_merged":
            src = str(payload.get("source") or payload.get("tag_id") or "")
            dst = str(payload.get("target") or payload.get("into") or "")
            if not src or not dst:
                raise ValueError("tag_merged 事件缺 source/target")
            # #41 之后 into 进白名单：合并态经事件投影（tags.status/merged_into +
            # 合并别名同一事务）。存在性先守——投影 UPDATE 对不存在的标签空转，
            # 而调用方（merge_tags 之外的裸事件写入）需要「标签不存在」的诚实报错。
            if not self._tag_exists(src):
                raise KeyError(f"标签不存在: {src}")
            self._store.append_event(
                "tag_merged", payload={"tag_id": src, "into": dst},
                occurred_at=at, config_fingerprint=_ADAPTER_FP,
            )
        elif kind in _EVENTS_FOLDED_ELSEWHERE:
            # 见类 docstring：tag_created/tag_proposed 已由写方法落库；提案状态由
            # update_proposal_status 落 proposal_updated 事件；tag_conflict 证据随
            # basis_json 落 book_tags（reco-store 无冲突事件投影）。
            # 不重复写、也绝不改写成 proposal_created。
            return
        else:
            raise ValueError(f"未知 labels 事件类型: {kind!r}")

    def _tag_exists(self, tag_id: str) -> bool:
        conn = self._store.read_conn()
        try:
            return conn.execute(
                "SELECT 1 FROM tags WHERE tag_id = ?", (str(tag_id),),
            ).fetchone() is not None
        finally:
            conn.close()

    def insert_proposal(self, kind: str, candidate: str,
                        payload: Mapping, created_at: str) -> str:
        """写提案：proposal_created 事件一体携带投影两列（#40 后事件即真相源）。

        payload_json/evidence_json 已在白名单内：candidate/count/threshold 作为标量
        JSON 字符串随事件落库，store 投影在同一事务写入 proposals 两列，
        iter_proposals 从 payload_json 读 candidate。propose_tags 随后补发的
        tag_proposed 已在本次落库（append_event 折叠为 no-op）。
        """
        body = {"candidate": str(candidate),
                **{k: v for k, v in dict(payload).items() if k != "candidate"}}
        evidence = {"count": payload.get("count"), "threshold": payload.get("threshold")}
        self._store.append_event(
            "proposal_created",
            payload={"kind": str(kind) or "tag",
                     "payload_json": json.dumps(body, ensure_ascii=False, sort_keys=True),
                     "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True)},
            occurred_at=str(created_at), config_fingerprint=_ADAPTER_FP,
        )
        conn = self._store.read_conn()
        try:
            row = conn.execute("SELECT MAX(proposal_id) FROM proposals").fetchone()
        finally:
            conn.close()
        return str(int(row[0]))

    def update_proposal_status(self, proposal_id: str, status: str) -> None:
        """改 proposals.status：经 proposal_updated 事件落库（#42，事件即真相源）。

        枚举校验与存在性校验都在事件面之前——非法输入绝不留坏事件（否则重放会把
        坏状态再演一遍）。投影 UPDATE 由 store 在同一事务完成；重放逐位恢复状态。
        """
        if status not in _PROPOSAL_STATUSES:
            raise ValueError(
                f"非法提案状态: {status!r}（只允许 pending/accepted/rejected）"
            )
        pid = int(str(proposal_id))
        conn = self._store.read_conn()
        try:
            exists = conn.execute(
                "SELECT 1 FROM proposals WHERE proposal_id = ?", (pid,),
            ).fetchone() is not None
        finally:
            conn.close()
        if not exists:
            raise KeyError(f"提案不存在: {proposal_id}")
        self._store.append_event(
            "proposal_updated", book_id=None,
            payload={"proposal_id": pid, "status": str(status)},
            occurred_at=_now_iso(), config_fingerprint=_ADAPTER_FP,
        )


@dataclass(frozen=True)
class AutoTagOutcome:
    """一次自动打标的完整摘要（数值/枚举/标签 id——B 类，无正文）。

    zone 裁定内嵌：AutoTagOutcome.ZONES 只含 liked；非 liked 一律 zero-out
    （skipped=True），不写 book_tags、不产提议。
    """

    ZONES = ("liked", "candidate")  # 触发专区白名单（#83：candidate 也打标；画像折叠仅 liked）

    book_id: int
    zone: str
    skipped: bool            # True = 非 liked 专区（或零命中零提议的空转）
    hits: tuple[TagHit, ...]
    record: RecordResult
    proposed: tuple[str, ...]  # 新词提案（proposals pending，待用户确认）
    proposed_aliases: tuple[str, ...] = ()  # #83：谐音/形近变体的别名提案（挂原标签）

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
    """自动识别词表：active 标签的 tag_id + aliases + 值行显示名（archived 不参与）。

    与 load_taxonomy 的身份键完全一致（taxonomy.match_keys；#83 起值行显示名参与，
    单字/纯 ASCII 显示名仍不参与——子串探针精度护栏）。
    """
    return tuple(
        key
        for row in store.iter_tags()
        if row.status == "active"
        for key in match_keys(row)
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
    """单本 liked/candidate 书的自动打标编排：match → record(auto) → propose(新词)。

    - zone 不在 ZONES：ValueError 拒绝（推荐专区等不触发）；
      画像折叠（tag_hit）只应对 liked 落——candidate 的标注不进画像正样本；
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
    taxonomy = _load_taxonomy(store)
    hits = match_tags(stats, taxonomy)
    unknown = _unknown_counts(store, hits, stats)
    record = record_tag_hits(store, book_id, hits, now=now)
    proposed: tuple[str, ...] = ()
    proposed_aliases: tuple[str, ...] = ()
    if unknown:
        # #83：与既有值标签显示名仅差一字的谐音/形近变体 → alias 提案（挂原标签，
        # 不拆碎分类）；其余未知词仍走新词提案（双层边界不变）。
        variants = variant_alias_candidates(unknown, taxonomy)
        rest = {w: c for w, c in unknown.items() if w not in variants}
        if variants:
            alias_props = propose_alias_tags(
                store, variants, threshold=propose_threshold, now=now,
            )
            proposed_aliases = tuple(p.candidate for p in alias_props)
        if rest:
            proposals = propose_tags(
                store, rest, threshold=propose_threshold, now=now,
            )
            proposed = tuple(p.candidate for p in proposals)
    skipped = not hits and not proposed and not proposed_aliases
    # 关系图：有标签命中才提取并落库（无标签不造假图）
    if hits:
        _persist_relation_graph(store, book_id, stats, hits, now=now)
    return AutoTagOutcome(
        book_id=book_id, zone=zone, skipped=skipped, hits=hits,
        record=record, proposed=proposed, proposed_aliases=proposed_aliases,
    )


def _stats_mapping(stats) -> dict:
    """stats → dict：兼容 Mapping / 带 __dict__ 的对象 / None。

    match_tags 收的是 duck-typed stats（真实链路里是 SimpleNamespace 或 dict），
    这里统一转成 extract_relation_graph 需要的纯字典。
    """
    if stats is None:
        return {}
    if isinstance(stats, dict):
        return dict(stats)
    data = getattr(stats, "__dict__", None)
    if isinstance(data, dict):
        return dict(data)
    try:
        return dict(stats)
    except (TypeError, ValueError):
        return {}


def _persist_relation_graph(store, book_id: int, stats, hits, *, now: str) -> None:
    """标签命中 → 关系图 → relation_updated 事件（#69：此前无任何生产调用点）。

    输入按 extract_relation_graph 契约：stats 是数值统计量，alias_weights 是
    标签 id → 0-1 权重（用命中置信度）。同一本书重复提取由 store 投影 upsert
    保证幂等（relation_graphs 以 book_id 为主键）。
    """
    import json

    from reco_relations.extract import extract_relation_graph

    weights = {hit.tag_id: float(hit.confidence) for hit in hits}
    graph = extract_relation_graph(_stats_mapping(stats), weights)
    store.append_event(
        "relation_updated",
        {
            "graph_json": json.dumps(graph, ensure_ascii=False, sort_keys=True),
            "extractor_version": "reco-relations/1",
            "confidence": float(graph.get("evidence", {}).get("confidence", 0.0)),
        },
        now,
        book_id=int(book_id),
    )


def _load_taxonomy(store: LabelsStore):
    from reco_labels.taxonomy import load_taxonomy

    return load_taxonomy(store)
