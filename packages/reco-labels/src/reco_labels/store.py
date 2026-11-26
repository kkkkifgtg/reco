"""标签持久化协议与内存实现（工单 06）。

依赖纪律（§4.2）：reco-labels 只挂 reco-core——本模块不 import reco-store。
W5（t7）落地 SQLite 后，其连接按 LabelsStore 协议对接即可。

D1 硬防线（audit D1）：source=manual/user 的 book_tags 行绝不被 auto 覆盖，
拒绝发生在**写入层**（record_book_tag 抛 ManualTagProtected），非调用方自觉。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Protocol, Sequence, runtime_checkable

# #104：词表与校验只此一份（vocab.py），内存实现与 SQLite 适配器共用。
from reco_labels.vocab import (  # noqa: E402  (常量，导入位置不影响语义)
    EVENT_KINDS,
    validate_proposal_status,
)



class ManualTagProtected(Exception):
    """试图用 auto 来源覆盖 manual/user 标注行（写入层拒绝）。"""


@dataclass(frozen=True)
class TagRow:
    tag_id: str
    axis: str | None
    label: str
    aliases: tuple[str, ...]
    parent: str | None
    status: str  # active | merged | archived
    merged_into: str | None
    created_by: str  # user | auto
    created_at: str


@dataclass(frozen=True)
class SeedRow:
    """种子三层层级行：domain（d1…d6）/ axis / value（mech.hypnosis 形态）。"""

    kind: str  # domain | axis | value
    tag_id: str
    axis: str | None
    label: str
    aliases: tuple[str, ...]
    parent: str | None
    status: str
    created_by: str
    created_at: str
    merged_into: str | None = None

    def as_tag_row(self) -> TagRow:
        return TagRow(
            tag_id=self.tag_id, axis=self.axis, label=self.label,
            aliases=self.aliases, parent=self.parent, status=self.status,
            merged_into=self.merged_into, created_by=self.created_by,
            created_at=self.created_at,
        )


@dataclass(frozen=True)
class BookTagRow:
    book_id: int
    tag_id: str
    source: str  # auto | manual | user
    confidence: float
    basis_json: str
    updated_at: str


@dataclass(frozen=True)
class EventRow:
    kind: str
    payload: Mapping[str, object]
    occurred_at: str


@dataclass(frozen=True)
class ProposalRow:
    proposal_id: str
    kind: str  # tag | alias | relation
    payload: Mapping[str, object]
    status: str  # pending | accepted | rejected
    created_at: str
    candidate: str


@runtime_checkable
class LabelsStore(Protocol):
    """窄协议：reco_labels 运行所需的全部存储能力。"""

    def iter_tags(self) -> Iterable[TagRow]: ...
    def iter_aliases(self) -> Iterable[tuple[str, str, str]]: ...  # (alias, tag_id, source)
    def iter_book_tags(self) -> Iterable[BookTagRow]: ...
    def iter_events(self) -> Iterable[EventRow]: ...
    def iter_proposals(self) -> Iterable[ProposalRow]: ...
    def upsert_tag(self, row: TagRow) -> None: ...
    def record_book_tag(self, book_id: int, tag_id: str, *, source: str,
                        confidence: float, basis_json: str, updated_at: str) -> None: ...
    def redirect_book_tag_rows(self, source_tag: str, target_tag: str) -> int: ...
    def append_event(self, kind: str, payload: Mapping[str, object],
                     occurred_at: str, *, book_id: int | None = None) -> None: ...
    def insert_proposal(self, kind: str, candidate: str,
                        payload: Mapping[str, object], created_at: str) -> str: ...
    def update_proposal_status(self, proposal_id: str, status: str) -> None: ...


@dataclass
class _StoreState:
    tags: dict[str, TagRow] = field(default_factory=dict)
    book_tags: dict[tuple[int, str], BookTagRow] = field(default_factory=dict)
    events: list[EventRow] = field(default_factory=list)
    proposals: dict[str, ProposalRow] = field(default_factory=dict)
    event_seq: int = 0
    proposal_seq: int = 0


class InMemoryLabelsStore:
    """测试与 v1 内嵌用内存实现；线程不安全（GUI 写队列前无需并发）。"""

    def __init__(self) -> None:
        self._s = _StoreState()

    # ---- 读 ----

    def iter_tags(self) -> Iterable[TagRow]:
        return iter(self._s.tags.values())

    def iter_aliases(self) -> Iterable[tuple[str, str, str]]:
        for tag_id, row in self._s.tags.items():
            for alias in row.aliases:
                yield (alias, tag_id, "seed")

    def iter_book_tags(self) -> Iterable[BookTagRow]:
        return iter(self._s.book_tags.values())

    def iter_events(self) -> Iterable[EventRow]:
        return iter(self._s.events)

    def iter_proposals(self) -> Iterable[ProposalRow]:
        return iter(self._s.proposals.values())

    # ---- 写 ----

    def upsert_tag(self, row: TagRow) -> None:
        self._s.tags[row.tag_id] = row

    def record_book_tag(self, book_id: int, tag_id: str, *, source: str,
                        confidence: float, basis_json: str, updated_at: str) -> None:
        key = (book_id, tag_id)
        existing = self._s.book_tags.get(key)
        # D1 语义（audit 第 7 条裁定）：禁令施动者是「自动提取」——仅 auto 被拒；
        # 人工互可接管（manual↔user），否则 GUI 二次编辑在首次 manual 落位后死锁。
        if existing is not None and existing.source in ("manual", "user") and source == "auto":
            raise ManualTagProtected(
                f"book_tags[{book_id},{tag_id}] source={existing.source} 不允许被 auto 覆盖"
            )
        self._s.book_tags[key] = BookTagRow(
            book_id=book_id, tag_id=tag_id, source=source,
            confidence=confidence, basis_json=basis_json, updated_at=updated_at,
        )

    def redirect_book_tag_rows(self, source_tag: str, target_tag: str) -> int:
        """合并搬迁：历史逐行改指向，source/confidence/basis 原样保留（工单 06 验收 1）。

        #104：manual/user 行一起搬（D1 保护的是 auto 覆盖人工，合并是用户动作）；
        SQLite 适配器经 label_moved 事件实现同一语义。
        """
        moved = 0
        for key, row in list(self._s.book_tags.items()):
            if row.tag_id != source_tag:
                continue
            self._s.book_tags[key] = replace(row, tag_id=target_tag)
            moved += 1
        return moved

    def append_event(self, kind: str, payload: Mapping[str, object],
                     occurred_at: str, *, book_id: int | None = None) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"未知事件类型: {kind}")
        self._s.event_seq += 1
        self._s.events.append(EventRow(
            kind=kind, payload=dict(payload), occurred_at=occurred_at,
        ))

    def insert_proposal(self, kind: str, candidate: str,
                        payload: Mapping[str, object], created_at: str) -> str:
        self._s.proposal_seq += 1
        proposal_id = f"prop-{self._s.proposal_seq:04d}"
        self._s.proposals[proposal_id] = ProposalRow(
            proposal_id=proposal_id, kind=kind, payload=dict(payload),
            status="pending", created_at=created_at, candidate=candidate,
        )
        return proposal_id

    def update_proposal_status(self, proposal_id: str, status: str) -> None:
        validate_proposal_status(status)  # #104：与适配器同一守卫
        if proposal_id not in self._s.proposals:
            raise KeyError(proposal_id)
        row = self._s.proposals[proposal_id]
        self._s.proposals[proposal_id] = replace(row, status=status)