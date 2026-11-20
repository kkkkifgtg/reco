"""Taxonomy 视图与 normalize_tag（工单 06；Q11：v1 只做 alias 精确命中）。

- 别名身份：strip + casefold 全等才算命中，绝不模糊猜。
- 只在 status=active 标签上识别；merged 行经 merged_into 链解析到目标；
  archived 行完全不解析（不参与打分，CONTEXT.md/§6.2）。
- 冲突（多 active 标签同 alias）：别名最长者胜；等长平局 → tag_id 字典序
  最小者胜（「双性」跨轴场景，预读 §0.1 裁定）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from reco_labels.store import LabelsStore, TagRow


def _norm(text: str) -> str:
    return text.strip().casefold()


@dataclass(frozen=True)
class Taxonomy:
    """不可变分类群视图：行 + 别名索引（构建时一次算好，match 零重建）。"""

    rows: tuple[TagRow, ...]
    _alias_index: Mapping[str, tuple[tuple[str, int, bool], ...]]
    # alias_norm -> ((tag_id, alias_len, via_merged_chain), …) 全候选（含 merged 链）
    _active_alias: Mapping[str, str]
    # alias_norm -> 胜者 tag_id（最长者胜/字典序平局，只在 active 直命中中）

    def by_id(self, tag_id: str) -> TagRow | None:
        for row in self.rows:
            if row.tag_id == tag_id:
                return row
        return None


def load_taxonomy(store: LabelsStore) -> Taxonomy:
    rows = tuple(store.iter_tags())
    by_id = {r.tag_id: r for r in rows}

    def resolve_final(tag_id: str, seen: frozenset[str] = frozenset()) -> str | None:
        """沿 merged_into 链走到最终标签；环检测；缺失/archived 终点返回 None。"""
        if tag_id in seen:
            return None  # 环——数据缺陷，不解析
        row = by_id.get(tag_id)
        if row is None:
            return None
        if row.status == "archived":
            return None
        if row.status == "merged" and row.merged_into:
            return resolve_final(row.merged_into, seen | {tag_id})
        if row.status == "active":
            return tag_id
        return None

    alias_candidates: dict[str, list[tuple[str, int, bool]]] = {}
    for row in rows:
        if row.status == "archived":
            continue
        final = resolve_final(row.tag_id)
        via_chain = final is not None and final != row.tag_id
        if final is None:
            continue
        # 身份键：tag_id 本身 + 全部 alias（显示名 label 不参与——改 label 不影响历史）
        keys = (row.tag_id, *row.aliases)
        for key in keys:
            norm = _norm(key)
            if not norm:
                continue
            alias_candidates.setdefault(norm, []).append(
                (final, len(key), via_chain)
            )

    active_alias: dict[str, str] = {}
    for norm, cands in alias_candidates.items():
        # 最长者胜；等长平局 → tag_id 字典序最小（确定性）
        best = min(cands, key=lambda c: (-c[1], c[0]))
        active_alias[norm] = best[0]

    return Taxonomy(
        rows=rows,
        _alias_index={
            norm: tuple(sorted(cands)) for norm, cands in alias_candidates.items()
        },
        _active_alias=active_alias,
    )


def normalize_tag(raw: str, taxonomy: Taxonomy) -> str | None:
    """精确命中 → 最终 TagId；未命中 → None（绝不模糊猜）。"""
    norm = _norm(raw)
    if not norm:
        return None
    return taxonomy._active_alias.get(norm)
