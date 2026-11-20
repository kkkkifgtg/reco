"""match_tags / TagHit / explain_tag_hit（工单 06；§6.2 出参契约）。

- 鸭子读 stats.alias_counts（W4 接缝：extract_statistics/extract_features 产出；
  降级结果 alias_counts={} → 零命中不抛异常；缺失字段同样零命中）。
- TagHit.confidence：alias 精确命中即事实 → 1.0（不臆造分级）。
- basis 只含别名/计数/裁定证据——绝无正文片段（B1/B4）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from reco_labels.taxonomy import Taxonomy, _norm


@dataclass(frozen=True)
class TagHit:
    tag_id: str
    confidence: float
    basis: Mapping[str, object]


@dataclass(frozen=True)
class Evidence:
    kind: str  # alias_exact | merged_redirect | alias_conflict_resolved
    alias: str | None
    count: int
    detail: str = ""


def _stats_alias_counts(stats: object) -> Mapping[str, int]:
    counts = getattr(stats, "alias_counts", None)
    if counts is None and isinstance(stats, Mapping):
        counts = stats.get("alias_counts")
    if counts is None:
        return {}
    return counts


def match_tags(stats: object, taxonomy: Taxonomy) -> tuple[TagHit, ...]:
    """alias 计数 → TagHit 列表（同标签多 alias 合并；确定性 tag_id 升序）。"""
    counts = _stats_alias_counts(stats)
    if not counts:
        return ()
    per_tag: dict[str, list[dict[str, object]]] = {}
    conflict_seen: dict[str, dict[str, object]] = {}
    for alias, count in counts.items():
        try:
            count_i = int(count)
        except (TypeError, ValueError):
            continue
        if count_i <= 0:
            continue
        norm = _norm(str(alias))
        if not norm:
            continue
        cands = taxonomy._alias_index.get(norm)
        if not cands:
            continue  # 未知词不臆造（进 proposals 由上层处理）
        best_tag = min(cands, key=lambda c: (-c[1], c[0]))[0]
        via_chain = any(c[2] and c[0] == best_tag for c in cands)
        multi = len({c[0] for c in cands}) > 1
        per_tag.setdefault(best_tag, []).append({
            "alias": str(alias), "count": count_i, "via_chain": via_chain,
        })
        if multi:
            conflict_seen.setdefault(norm, {
                "alias": str(alias),
                "candidates": sorted({c[0] for c in cands}),
                "winner": best_tag,
            })

    hits: list[TagHit] = []
    for tag_id in sorted(per_tag):
        matches = tuple(
            {"alias": m["alias"], "count": m["count"]}
            for m in sorted(per_tag[tag_id], key=lambda m: str(m["alias"]))
        )
        total = sum(int(m["count"]) for m in matches)
        evidence = []
        for m in per_tag[tag_id]:
            evidence.append({
                "kind": "merged_redirect" if m["via_chain"] else "alias_exact",
                "alias": m["alias"], "count": m["count"],
            })
        conflict = conflict_seen.get(next(
            (norm for norm, c in conflict_seen.items()
             if any(mm["alias"] == c["alias"] for mm in per_tag[tag_id])),
            None,
        ))
        basis: dict[str, object] = {
            "axis": tag_id.split(".", 1)[0] if False else _axis_of(taxonomy, tag_id),
            "matches": matches,
            "total": total,
            "evidence": tuple(evidence),
        }
        if conflict is not None and conflict["winner"] == tag_id:
            basis["conflict"] = conflict
        hits.append(TagHit(tag_id=tag_id, confidence=1.0, basis=basis))
    return tuple(hits)


def _axis_of(taxonomy: Taxonomy, tag_id: str) -> str:
    row = taxonomy.by_id(tag_id)
    if row is not None and row.axis:
        return row.axis
    return tag_id.split(".", 1)[0]


def explain_tag_hit(hit: TagHit) -> tuple[Evidence, ...]:
    """把 basis 展开为证据列表（供 CLI/GUI explain；只含元数据）。"""
    out: list[Evidence] = []
    for m in hit.basis.get("matches", ()):
        out.append(Evidence(kind="alias_exact", alias=str(m.get("alias")),
                            count=int(m.get("count", 0))))
    for e in hit.basis.get("evidence", ()):
        kind = str(e.get("kind", ""))
        if kind == "merged_redirect":
            out.append(Evidence(kind="merged_redirect", alias=str(e.get("alias")),
                                count=int(e.get("count", 0)),
                                detail="命中经 merged_into 链解析到当前标签"))
    conflict = hit.basis.get("conflict")
    if conflict:
        out.append(Evidence(
            kind="alias_conflict_resolved", alias=str(conflict.get("alias")),
            count=0,
            detail="别名冲突：候选 " + ",".join(sorted(conflict.get("candidates", ())))
            + "，裁定 " + str(conflict.get("winner")),
        ))
    return tuple(out)
