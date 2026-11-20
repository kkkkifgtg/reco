"""轴覆盖与轴向量（工单 06；§6.2 axis_coverage/axis_vector；规格「稀疏数据」节）。

- axis_coverage：诚实计数（covered/total），1/N 照实返回，不放大。
- axis_vector：只为**有命中的轴**产条目，值 = axis_weights[axis] × mean(confidence)；
  未覆盖轴缺席（下游读缺席=权重 0，不猜默认值）。
"""
from __future__ import annotations

from dataclasses import dataclass

from reco_labels.match import TagHit


@dataclass(frozen=True)
class Coverage:
    covered: int
    total: int


def _axis_of_hit(hit: TagHit) -> str:
    axis = hit.basis.get("axis")
    if isinstance(axis, str) and axis:
        return axis
    return hit.tag_id.split(".", 1)[0]


def axis_coverage(tag_hits_by_book, axis: str) -> Coverage:
    books = list(tag_hits_by_book.items())
    total = len(books)
    covered = 0
    for _book_id, hits in books:
        if any(_axis_of_hit(hit) == axis for hit in hits):
            covered += 1
    return Coverage(covered=covered, total=total)


def axis_vector(tag_hits, axis_weights) -> dict:
    by_axis: dict = {}
    for hit in tag_hits:
        axis = _axis_of_hit(hit)
        if axis not in axis_weights:
            continue  # 未知轴绝不臆造权重
        by_axis.setdefault(axis, []).append(float(hit.confidence))
    return {
        axis: axis_weights[axis] * (sum(confs) / len(confs))
        for axis, confs in by_axis.items()
    }
