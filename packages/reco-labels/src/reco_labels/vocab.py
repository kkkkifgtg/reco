"""labels 侧的枚举与事件词表（#104）：两套存储实现共用同一份词汇。

InMemory 与 StoreAdapter 过去各写各的状态集合（前者不校验、后者校验），
于是「同一个非法状态」在内存里能落、在 SQLite 上被拒。词汇只在这里定义，
两边都从它取——校验函数也放这里，避免第三份判断。
"""
from __future__ import annotations

__all__ = [
    "EVENT_KINDS", "PROPOSAL_KINDS", "PROPOSAL_STATUSES", "TAG_STATUSES",
    "validate_proposal_status",
]

# 标签行状态（tags.status 投影）
TAG_STATUSES = frozenset({"active", "merged", "archived"})
# 提案状态（proposals.status 投影；事件面只允许经 update_proposal_status 变更）
PROPOSAL_STATUSES = frozenset({"pending", "accepted", "rejected"})
# 提案类型（proposals.kind）
PROPOSAL_KINDS = frozenset({"tag", "alias", "relation"})

# 标签事件词表（reco-labels 自有事件，store 侧另有一套投影事件名）
EVENT_KINDS = frozenset({
    "tag_merged", "tag_archived", "tag_created", "tag_conflict",
    "tag_proposed", "proposal_accepted", "proposal_rejected",
    "relation_updated",
})


def validate_proposal_status(status: str) -> str:
    """提案状态守卫：非法值在写入口拒绝，绝不留坏事件/坏投影。"""
    if status not in PROPOSAL_STATUSES:
        raise ValueError(
            f"非法提案状态: {status!r}（只允许 pending/accepted/rejected）"
        )
    return status