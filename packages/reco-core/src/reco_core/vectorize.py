"""工单 04——语料级 char n-gram 哈希桶 TF-IDF（reco_core 特征规整接缝）。

纯数值实现：不 import sklearn/torch/numpy（F5：import reco_core 不得触发，
由 tests/test_import_hygiene.py 子进程守护）。
桶号 = blake2b(ngram, 8B) 大端 mod num_buckets（跨运行稳定，与 reco_io.features 同构）；
输出只有 (index, weight) 数值对，绝不出现 n-gram 字符串（§5.6：特征库不落 n-gram 原文）。
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from hashlib import blake2b
from typing import Callable, Sequence

IDF_VERSION_PREFIX = "reco-idf/1"


@dataclass(frozen=True)
class VectorizationResult:
    vectors: tuple  # 每文档 ((index, ...), (weight, ...))，index 升序
    norms: tuple  # 每文档 L2 范数（归一后恒 1.0；空向量 0.0）
    dim: int
    idf_version: str


def _default_slot(gram: str, num_buckets: int) -> int:
    return int.from_bytes(blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big") % num_buckets


def _gram_counts(text: str, ngram_range: tuple[int, int]) -> Counter:
    stripped = "".join(text.split())
    counts: Counter[str] = Counter()
    lo, hi = ngram_range
    for n in range(lo, hi + 1):
        if n <= 0:
            continue
        for i in range(len(stripped) - n + 1):
            counts[stripped[i : i + n]] += 1
    return counts


def char_ngram_bucket_tfidf(
    texts: Sequence[str],
    *,
    ngram_range: tuple[int, int] = (2, 3),
    min_df: int = 2,
    max_features: int = 200_000,
    sublinear_tf: bool = True,
    num_buckets: int | None = None,
    hash_fn: Callable[[str], int] | None = None,
) -> VectorizationResult:
    """哈希桶 TF-IDF：min_df 过滤 → max_features 按语料总频截断（并列取槽号小者）
    → smooth idf（ln((1+N)/(1+df))+1）→ sublinear tf（1+ln(count)）→ L2 归一。
    空语料/全滤语料诚实返回空向量，idf_version 仍产出，不静默。
    """
    if num_buckets is None:
        num_buckets = max_features
    ngram_range = tuple(ngram_range)
    docs = [_gram_counts(t, ngram_range) for t in texts]

    df: Counter[str] = Counter()
    for counts in docs:
        for gram in counts:
            df[gram] += 1
    kept = {gram for gram, v in df.items() if v >= min_df}

    def slot_of(gram: str) -> int:
        return hash_fn(gram) if hash_fn is not None else _default_slot(gram, num_buckets)

    if len(kept) > max_features:
        totals = {gram: sum(c[gram] for c in docs) for gram in kept}
        ranked = sorted(kept, key=lambda g: (-totals[g], slot_of(g)))
        kept = set(ranked[:max_features])

    idf = {gram: math.log((1 + len(docs)) / (1 + df[gram])) + 1 for gram in kept}

    version_payload = json.dumps(
        [
            IDF_VERSION_PREFIX,
            list(ngram_range),
            min_df,
            max_features,
            num_buckets,
            bool(sublinear_tf),
            sorted([slot_of(g), round(v, 12)] for g, v in idf.items()),
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    idf_version = blake2b(version_payload.encode("utf-8"), digest_size=16).hexdigest()

    vectors: list[tuple[tuple[int, ...], tuple[float, ...]]] = []
    norms: list[float] = []
    for counts in docs:
        raw: dict[int, float] = {}
        for gram, cnt in counts.items():
            if gram not in kept:
                continue
            tf = (1 + math.log(cnt)) if sublinear_tf else float(cnt)
            raw[slot_of(gram)] = tf * idf[gram]
        norm = math.sqrt(sum(v * v for v in raw.values()))
        if norm > 0:
            indices = tuple(sorted(raw))
            weights = tuple(raw[i] / norm for i in indices)
            vectors.append((indices, weights))
            norms.append(math.sqrt(sum(w * w for w in weights)))
        else:
            vectors.append(((), ()))
            norms.append(0.0)

    return VectorizationResult(tuple(vectors), tuple(norms), len(kept), idf_version)
