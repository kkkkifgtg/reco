"""Taxonomy 视图与 normalize_tag（工单 06；Q11 alias 精确命中；#83 值行显示名参与）。

- 别名身份：strip + casefold 全等才算命中，绝不模糊猜。
- 值行显示名参与匹配（#83）：tag_id 含 "." 的值标签，其显示名（len>=2 且含 CJK）
  进入身份/匹配词表；单字（攻/受/长/爽）与纯 ASCII（HE/BE）不参与——作为子串
  探针精度不可接受，仍走人工/提案路径。
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


def value_label_for_match(row: TagRow) -> str | None:
    """值行可作匹配词的显示名；非值行/单字/纯 ASCII 显示名返回 None。

    #83 精度护栏：单字（攻/受/长/爽）与纯 ASCII（HE/BE）作子串探针会大量误伤，
    不进入匹配词表，仍走人工/提案路径。
    """
    if "." not in row.tag_id:
        return None
    label = str(row.label or "").strip()
    if len(label) >= 2 and any("\u4e00" <= ch <= "\u9fff" for ch in label):
        return label
    return None


def match_keys(row: TagRow) -> tuple[str, ...]:
    """匹配/识别词表：tag_id + aliases；值行（tag_id 含 "."）额外收显示名。

    #83 裁定（报告人：书应有标签、自动打标要可用）：值标签的显示名是自然语言词
    （催眠/调教/强制/校园…），不参与匹配会让词表饥饿（种子 164 行仅 1 个别名）
    → 大部分书零标签、画像单薄。收词条件见 value_label_for_match。
    改显示名会影响其后的匹配；历史 basis_json 已固化当时命中的词串，不受影响。
    """
    keys = [str(row.tag_id), *(str(a) for a in row.aliases)]
    label = value_label_for_match(row)
    if label:
        keys.append(label)
    return tuple(k for k in keys if k.strip())


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
        # 身份/匹配键：tag_id + aliases + 值行显示名（match_keys；#83）
        keys = match_keys(row)
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
