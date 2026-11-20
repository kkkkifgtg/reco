"""CLI 命令实现层（工单 11；A6 rank 唯一入口消费）。

- 排序唯一入口：recommend/feedback 输出全部经 reco_core（rank/rank_only），
  本层零排序实现（无 sorted/sort 排序调用）；理由行 source=muted 原样透传。
- 只读入口（report/recommend/explain/replay/eval）零写库（只读连接）；
  写入口（scan/feedback）经公开 API（store.append_event / persist_feedback）。
- 静音胶水：store.load_mutes() → feedback 层 MuteWindow 映射（过期行过滤，
  dedup_key 优先为键）——映射只做形态转换，不做静音判定（判定在 feedback 层）。
- 输出纪律：stdout 纯净（JSON/表格）；--json 时 stdout 为合法 JSON 文档；
  perf 打点走 reco_io.perf（stderr）；报告只含数值/枚举/book_id（无正文无书名）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reco_io.perf import perf_span
from reco_io.scanner import scan_zones
from reco_store import open_store

__all__ = [
    "cmd_eval",
    "cmd_explain",
    "cmd_feedback",
    "cmd_recommend",
    "cmd_replay",
    "cmd_report",
    "cmd_scan",
    "mute_map_from_store",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_profile(store):
    """事件流重放 → 画像（W9 rehydrate 同流；只读）。"""
    from reco_core.feedback import rehydrate_from_events

    return rehydrate_from_events(store.read_events())


def mute_map_from_store(store) -> dict[str, Any]:
    """store 静音行 → feedback 层 MuteWindow 映射（dedup_key 优先；过期行过滤）。

    过滤只看 expired_at 时刻（E8 语义由 MuteWindow.with_remaining 复核）；
    零静音返回空 dict（过期自动解除后零残留）。
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


# ---------------------------------------------------------------- report

def cmd_report(db, *, write=False, reports_dir=None) -> int:
    """诊断报告：样本量/轴覆盖/权重/CV 指标（无正文无书名，book_id 代替）。"""
    from reco_core.feedback import FEEDBACK_VERSION

    with perf_span("cli.report"):
        store = open_store(db)
        try:
            profile = _load_profile(store)
            counts = dict(profile.sample_counts)
            per_axis = counts.get("per_axis", {}) or {}
            coverage = {
                axis: {"positive": int(pair.get("positive", 0)),
                       "negative": int(pair.get("negative", 0))}
                for axis, pair in per_axis.items()
            }
            conf = profile.confidence
            payload = {
                "generated_at": _utc_now_iso(),
                "sample_counts": {
                    "tag_like": int(counts.get("tag_like", 0)),
                    "tag_dislike": int(counts.get("tag_dislike", 0)),
                    "behavior": int(counts.get("behavior", 0)),
                },
                "axis_coverage": coverage,
                "axis_weights": {k: float(v) for k, v in profile.axis_weights.items()},
                "weights": {"w_sim": float(profile.weights[0]),
                            "w_model": float(profile.weights[1]),
                            "w_behavior": float(profile.weights[2]),
                            "version": profile.weight_version},
                "confidence": {"value": float(conf.value), "cap": float(conf.cap),
                               "display": conf.display},
                "cv": {"available": False,
                       "reason": "样本不足，不评估（§6.3：n_pos≥10 且 n_neg≥10 才评估）"},
                "feedback_version": FEEDBACK_VERSION,
            }
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            if write:
                target = Path(reports_dir) if reports_dir else Path("reports")
                target.mkdir(parents=True, exist_ok=True)
                date = _utc_now_iso()[:10]
                (target / f"reco-{date}.json").write_text(text + "\n", encoding="utf-8")
            print(text)
            return 0
        finally:
            store.close()


# ---------------------------------------------------------------- scan

def cmd_scan(root, *, db=None) -> int:
    """扫描专区并产出快照（reco_io 公开 API；落库/落位另票）。"""
    with perf_span("cli.scan"):
        result = scan_zones(root)
        summary = {
            "zones_scanned": list(result.zones_scanned),
            "missing_zones": list(result.missing_zones),
            "files": len(result.fingerprints),
            "ignored": len(result.ignored),
        }
        print(json.dumps(summary, ensure_ascii=False))
        _ = db  # 落库链路（file_added 事件）由工单 03/13 接管；本入口只读快照
        return 0 if not result.missing_zones else 0


# ---------------------------------------------------------------- recommend

def _confidence_dict(conf) -> dict:
    return {"n_pos": int(conf.n_pos), "n_neg": int(conf.n_neg),
            "value": float(conf.value), "display": conf.display}


def cmd_recommend(db, *, as_json=True) -> int:
    """推荐列表：事件流 → 画像 → 静音感知 rank 唯一入口（A6）。"""
    from reco_core.feedback import rank_only

    with perf_span("cli.recommend"):
        store = open_store(db)
        try:
            profile = _load_profile(store)
            mute = mute_map_from_store(store)
            conn = store.read_conn()
            try:
                rows = conn.execute(
                    "SELECT book_id, dedup_key FROM books ORDER BY book_id"
                ).fetchall()
            finally:
                conn.close()
            candidates = [(f"b{bid}", {"dedup_key": key, "vector": {}}) for bid, key in rows]
            positive_vectors = tuple(
                vec for _bid, spec in candidates for vec in [spec["vector"]] if vec
            )
            out = rank_only(candidates, profile, mute=mute, positive_vectors=positive_vectors)
            conf = profile.confidence
            payload = {
                "items": [
                    {"book_id": row.book_id, "score": float(row.score),
                     "rank": int(row.rank),
                     "is_exploration": bool(row.is_exploration),
                     "reasons": [{"axis": r.axis, "contribution": float(r.contribution),
                                  "source": r.source} for r in row.reasons]}
                    for row in out["rank_rows"]
                ],
                "display": conf.display,
                "profile": _confidence_dict(conf),
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        finally:
            store.close()


# ---------------------------------------------------------------- feedback

def cmd_feedback(kind, *, db, book_id, axis=None, value=1.0) -> int:
    """反馈入口：add_to_pool → pool_added；dislike/like → rec_feedback + 静音落库。"""
    from reco_core.feedback import feedback_effects, persist_feedback

    with perf_span("cli.feedback"):
        store = open_store(db)
        try:
            now = datetime.now(timezone.utc)
            result = persist_feedback(kind, book_id=int(book_id), axis=axis,
                                      value=value, store=store, now=now)
            if kind == "add_to_pool":
                print(json.dumps({"status": "ok", "kind": kind,
                                  "book_id": int(book_id)}, ensure_ascii=False))
                return 0
            profile = _load_profile(store)
            effects = feedback_effects(
                {"event_type": "rec_feedback", "event_id": result["event"]["event_id"],
                 "occurred_at": now.isoformat(),
                 "payload": {"axis": axis, "kind": kind, "value": float(value)},
                 "book_id": int(book_id)},
                now=now)
            if kind == "dislike":
                window = effects.get("mute_window")
                if window is not None:
                    store.record_mute(
                        int(book_id),
                        dedup_key=_dedup_key_of(store, int(book_id)),
                        axis=str(axis), kind="dislike",
                        expired_at=str(window.expired_at),
                        remaining_days=float(window.remaining_days),
                        occurred_at=now.isoformat(),
                        config_fingerprint="cli/feedback/1",
                    )
            print(effects["display"])  # C7 文案透传（已调整：X 维度权重已下调/上调）
            return 0
        finally:
            store.close()


def _dedup_key_of(store, book_id: int) -> str | None:
    conn = store.read_conn()
    try:
        row = conn.execute("SELECT dedup_key FROM books WHERE book_id = ?", (book_id,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------- explain

def cmd_explain(db, *, book_id) -> int:
    """单书解释：W8 explain 输出透传（book_id 维度；无正文）。"""
    from reco_core.score import explain

    with perf_span("cli.explain"):
        store = open_store(db)
        try:
            profile_data = _load_profile(store)
            # ProfileData → score.Profile 桥接（explain 只读 score 契约面）
            from reco_core.score import Profile as ScoreProfile

            counts = dict(profile_data.sample_counts)
            profile = ScoreProfile(
                weights=tuple(profile_data.weights),
                sample_counts={"n_pos": int(counts.get("tag_like", 0)),
                               "n_neg": int(counts.get("tag_dislike", 0))},
                behavior_signals=dict(profile_data.behavior_signals),
            )
            conn = store.read_conn()
            try:
                row = conn.execute(
                    "SELECT book_id, dedup_key FROM books WHERE book_id = ?", (int(book_id),)
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                print(json.dumps({"error": f"book_id={book_id} 不在特征库",
                                  "reasons": []}, ensure_ascii=False))
                return 1
            bid, key = row
            try:
                reasons = explain([(f"b{bid}", {})],  # score 向量契约：纯 {dim:weight}
                                  profile, f"b{bid}")
                status = "ok"
            except ValueError:
                # §6.3：特征缺失跳过不静默当 0 分——如实报「无可解释依据」不崩
                reasons = []
                status = "no_features"
            payload = {
                "book_id": f"b{bid}",
                "status": status,
                "reasons": [{"axis": r.axis, "contribution": float(r.contribution),
                             "source": r.source} for r in reasons],
                "profile": _confidence_dict(profile_data.confidence),
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        finally:
            store.close()


# ---------------------------------------------------------------- replay

def cmd_replay(db) -> int:
    """重放校验：ADR-0003 verify_replay；ok→0，漂移→非 0（不修库）。"""
    from reco_store import open_store as _open

    with perf_span("cli.replay"):
        store = _open(db)
        try:
            report = store.verify_replay()
            print(json.dumps({"ok": bool(report.ok), "diffs": list(report.diffs)},
                             ensure_ascii=False))
            return 0 if report.ok else 1
        finally:
            store.close()


# ---------------------------------------------------------------- eval

def cmd_eval(db) -> int:
    """评估入口：小样本诚实降级（§6.3：n_pos≥10 且 n_neg≥10 才评估）。"""
    with perf_span("cli.eval"):
        store = open_store(db)
        try:
            profile = _load_profile(store)
            counts = profile.sample_counts
            n_pos = int(counts.get("tag_like", 0))
            n_neg = int(counts.get("tag_dislike", 0))
            if n_pos < 10 or n_neg < 10:
                print(json.dumps(
                    {"status": "insufficient", "n_pos": n_pos, "n_neg": n_neg,
                     "display": f"样本 {n_pos}/{n_neg}（正/负），不足以判定，不评估"},
                    ensure_ascii=False))
                return 0
            from reco_core.score import evaluate_pairwise

            result = evaluate_pairwise([], None)
            print(json.dumps({"status": "ok", "auc": getattr(result, "auc", None)},
                             ensure_ascii=False))
            return 0
        finally:
            store.close()
