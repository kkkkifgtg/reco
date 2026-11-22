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
- 候选域（T4/T6）：推荐候选 = candidate 专区 present 文件的书（按 book_id 去重）；
  liked 只作正样本（positive_vectors 口径不变）；只要该书拥有任一 present liked
  文件（同书双区也算），即从候选排除；推荐专区（输出落位）不进候选。
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
    "load_candidate_books",
    "load_eval_samples",
    "load_profile",
    "load_vectors",
    "mute_map_from_store",
    "persist_rank_run",
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
    """全库身份：(book_id, dedup_key) 按 book_id 升序（正样本口径/静音键匹配来源）。

    候选域筛选见 load_candidate_books（T4：liked 不进候选）。
    """
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT book_id, dedup_key FROM books ORDER BY book_id"
        ).fetchall()
    finally:
        conn.close()
    return [(int(r[0]), None if r[1] is None else str(r[1])) for r in rows]


def load_candidate_books(store) -> list[tuple[int, str | None]]:
    """候选身份：candidate 专区 present 文件的书（按 book_id 去重、升序）。

    候选域 = candidate 专区，且排除「任何拥有 present liked 文件的 book_id」：
    liked 是正样本，同书双区（liked + candidate 各一条 present 文件）也不得被
    推荐（T6 修复）；liked 文件 present=0（已移除）不算拥有。推荐专区是输出
    落位，同样不进候选——避免把已推荐的书再当候选。
    """
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT b.book_id, b.dedup_key FROM books b"
            " JOIN files f ON f.book_id = b.book_id"
            " WHERE f.zone = 'candidate' AND f.present = 1"
            "   AND NOT EXISTS ("
            "       SELECT 1 FROM files lf"
            "       WHERE lf.book_id = b.book_id AND lf.zone = 'liked' AND lf.present = 1"
            "   )"
            " ORDER BY b.book_id"
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

    候选域 = candidate 专区（T4；liked 只作正样本，推荐专区为输出落位）。
    返回 {rank_rows, profile, muted, names, n_candidates, n_vectors, n_skipped}；
    n_candidates = 候选书数，n_vectors = 库级有向量书数（空态区分「是否已生产
    向量」），n_skipped = 候选缺向量书数。排序由 reco_core 完成，本层零排序；
    无向量候选照常进候选装配（score 侧跳过并计数——§6.3 不静默当 0 分）。
    """
    from reco_core.feedback import rank_only

    vectors = load_vectors(store)
    books = load_books(store)  # 全库身份：正样本向量口径（liked 仍参与正样本）
    candidate_books = load_candidate_books(store)  # 候选域：candidate 专区
    candidates = [
        (f"b{bid}", {"dedup_key": key, "vector": vectors.get(bid, {})})
        for bid, key in candidate_books
    ]
    positive_vectors = tuple(
        vectors[bid] for bid, _key in books if vectors.get(bid)
    )
    candidate_vector_count = sum(1 for bid, _key in candidate_books if vectors.get(bid))
    profile = load_profile(store)
    mute = mute_map_from_store(store)
    out = rank_only(candidates, profile, mute=mute, positive_vectors=positive_vectors)
    out["names"] = load_book_names(store)
    out["n_candidates"] = len(candidates)
    out["n_vectors"] = len(positive_vectors)  # 库级：空态区分「是否已生产向量」
    out["n_skipped"] = max(0, len(candidate_books) - candidate_vector_count)
    return out


# ---------------------------------------------------------------- 推荐落库（#70）
def persist_rank_run(store, out, *, now: str, actor: str,
                     run_id: str | None = None, profile_version=None) -> str:
    """把 rank 输出写入 rec_runs + recommendations（架构文档 §721 的承诺）。

    - 唯一写入路径：GUI 与 CLI 共用（避免两套逻辑漂移）；
    - run_id 未给则按时间戳+候选数生成；给定则复用它（便于幂等重写）；
    - 文本/评分原样落库，reasons 序列化为 JSON（零正文，只含轴名与数值）。
    """
    import json
    from datetime import datetime, timezone

    rows = list(out.get("rank_rows") or ())
    if run_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        run_id = f"run-{stamp}-{len(rows)}"

    items = []
    for row in rows:
        reasons = [
            {"axis": r.axis, "contribution": float(r.contribution), "source": r.source}
            for r in (getattr(row, "reasons", None) or ())
        ]
        items.append({
            "book_id": str(row.book_id),
            "rank": int(row.rank),
            "score": float(row.score),
            "reasons_json": json.dumps(reasons, ensure_ascii=False),
            "is_exploration": bool(getattr(row, "is_exploration", False)),
        })

    store.append_event(
        "rec_generated",
        payload={
            "run_id": str(run_id),
            "candidate_count": int(out.get("n_candidates") or 0),
            "exploration_count": sum(1 for i in items if i["is_exploration"]),
            "items_json": json.dumps(items, ensure_ascii=False),
            "profile_version": profile_version,
        },
        occurred_at=str(now),
        config_fingerprint="rec/persist/1",
        actor=str(actor),
    )
    return str(run_id)

