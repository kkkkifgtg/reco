"""推荐装配层（工单 13；架构 §10/§13、§4.2 依赖方向）。

- 装配而非排序（A6）：本模块零排序实现——不对候选集做任何 sorted/sort 定序；
  rank 调用发生在各入口（reco-cli / reco-ui 的 recommend 路径），输出直接渲染/输出。
- 零复制（§13 核心）：CLI 与 GUI 的「库态 → rank 输入」读取与组装收敛为唯一实现；
  两入口展示顺序逐位一致由同一 rank 输出保证（一致性测试钉死）。
- 向量真实接线（W11 遗留闭合）：feature_vectors.vector_blob 契约 =
  >{dim}i（int32 索引）+ >{dim}d（float64 权重）大端；损坏行跳过不静默当 0 分。
- 依赖方向（§4.2）：reco-store → reco-core 允许（rank_only 只调用不排序复制）；
  cli/ui 平级共同 import 本层，互不依赖。
- 本模块只读（read_conn + load_mutes），零写库；无正文接触（B2/B5）。
"""
from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "decode_vector_blob",
    "load_book_names",
    "load_books",
    "load_eval_samples",
    "load_profile",
    "load_vectors",
    "mute_map_from_store",
    "rank_candidates",
]


def decode_vector_blob(blob: bytes, dim: int) -> dict[int, float]:
    """feature_vectors.vector_blob → {dim_index: weight}（>{dim}i + >{dim}d 大端契约）。

    维度/长度不符 → ValueError（调用方跳过该行，不静默当 0 分）。
    """
    dim = int(dim)
    if dim <= 0 or len(blob) != 4 * dim + 8 * dim:
        raise ValueError(f"vector_blob 形态不符：dim={dim}, bytes={len(blob)}")
    indices = struct.unpack(f">{dim}i", blob[: 4 * dim])
    weights = struct.unpack(f">{dim}d", blob[4 * dim:])
    return {int(i): float(w) for i, w in zip(indices, weights) if w != 0.0}


def load_vectors(store) -> dict[int, dict[int, float]]:
    """全部候选向量（损坏行跳过；W11 遗留的真实向量来源）。"""
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT book_id, vector_blob, dim FROM feature_vectors"
        ).fetchall()
    finally:
        conn.close()
    out: dict[int, dict[int, float]] = {}
    for bid, blob, dim in rows:
        try:
            vec = decode_vector_blob(bytes(blob), int(dim))
        except (ValueError, struct.error):
            continue
        if vec:
            out[int(bid)] = vec
    return out


def load_books(store) -> list[tuple[int, str | None]]:
    """候选身份：(book_id, dedup_key) 按 book_id 升序（静音键匹配与确定性次序来源）。"""
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT book_id, dedup_key FROM books ORDER BY book_id"
        ).fetchall()
    finally:
        conn.close()
    return [(int(r[0]), None if r[1] is None else str(r[1])) for r in rows]


def load_book_names(store) -> dict[str, str]:
    """book_id → 显示名（书名允许显示，F3；无正文）。"""
    conn = store.read_conn()
    try:
        rows = conn.execute("SELECT book_id, display_name FROM books ORDER BY book_id").fetchall()
    finally:
        conn.close()
    return {f"b{int(r[0])}": str(r[1]) for r in rows}


def load_eval_samples(store) -> list[tuple[str, str, int]]:
    """eval 样本装配：zone → label（liked=1 其余 0），文本 = display_name（W8 契约 (id,text,label)）。"""
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT b.book_id, b.display_name, f.zone FROM books b"
            " JOIN files f ON f.book_id = b.book_id"
            " WHERE f.present = 1 ORDER BY b.book_id"
        ).fetchall()
    finally:
        conn.close()
    return [(f"b{int(r[0])}", str(r[1]), 1 if r[2] == "liked" else 0) for r in rows]


def load_profile(store):
    """事件流重放 → 画像（W9 rehydrate 同流；只读）。"""
    from reco_core.feedback import rehydrate_from_events

    return rehydrate_from_events(store.read_events())


def mute_map_from_store(store) -> dict[str, Any]:
    """store 静音行 → feedback 层 MuteWindow 映射（dedup_key 优先；过期行过滤）。

    过滤只看 expired_at 时刻；映射只做形态转换，不做静音判定（判定在 feedback 层）。
    """
    from reco_core.feedback import MuteWindow

    mute: dict[str, Any] = {}
    now = datetime.now(timezone.utc)
    for row in store.load_mutes():
        try:
            expired = datetime.fromisoformat(row["expired_at"])
            if expired.tzinfo is None:
                expired = expired.replace(tzinfo=timezone.utc)
        except ValueError:
            continue  # 时刻非法的行不进映射（诚实过滤，不猜）
        if (expired - now).total_seconds() <= 0:
            continue  # 已过期：不恢复（工单 10 过期自动解除语义）
        key = str(row["dedup_key"]) if row["dedup_key"] else str(row["book_id"])
        mute[key] = MuteWindow(
            kind=row["kind"], expired_at=row["expired_at"],
            remaining_days=float(row["remaining_days"]), axis=row["axis"],
        )
    return mute


def rank_candidates(store) -> dict:
    """唯一装配：库态 → 画像/静音/真实候选向量 → rank_only 唯一入口（A6）。

    返回 {rank_rows, profile, muted, names, n_candidates, n_vectors, n_skipped}；
    排序由 reco_core 完成，本层零排序；无向量候选照常进排序（score 侧如实计
    skipped/相似度 0——§6.3 特征缺失不静默当 0 分）。
    """
    from reco_core.feedback import rank_only

    vectors = load_vectors(store)
    books = load_books(store)
    candidates = [
        (f"b{bid}", {"dedup_key": key, "vector": vectors.get(bid, {})})
        for bid, key in books
    ]
    positive_vectors = tuple(
        vectors[bid] for bid, _key in books if vectors.get(bid)
    )
    profile = load_profile(store)
    mute = mute_map_from_store(store)
    out = rank_only(candidates, profile, mute=mute, positive_vectors=positive_vectors)
    out["names"] = load_book_names(store)
    out["n_candidates"] = len(candidates)
    out["n_vectors"] = len(positive_vectors)
    out["n_skipped"] = max(0, len(candidates) - len(positive_vectors))
    return out
