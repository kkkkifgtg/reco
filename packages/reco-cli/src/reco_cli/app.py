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

import sys

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from reco_io.perf import perf_span
from reco_io.scanner import scan_zones
from reco_store import open_store

__all__ = [
    "cmd_eval",
    "cmd_explain",
    "cmd_extract",
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
    """事件流重放 → 画像（W13：委托共享装配层，零复制 §13）。"""
    from reco_store.recommend import load_profile

    return load_profile(store)


def mute_map_from_store(store) -> dict[str, Any]:
    """store 静音行 → feedback 层 MuteWindow 映射（W13：委托共享装配层，零复制 §13）。

    旧 CLI 本地实现已收敛进 reco_store.recommend（CLI/GUI 同源）；本别名保留公开契约。
    """
    from reco_store.recommend import mute_map_from_store as _shared

    return _shared(store)


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


# ---------------------------------------------------------------- extract

EXTRACT_CONFIG_FP = "cli/extract/1"
AUTOTAG_CONFIG_FP = "cli/autotag/1"  # #83：liked 自动打标事件的来源指纹
# 提取侧非降级质量位：empty/oversized_streamed 仍是诚实统计（无向量时如实不写）
_EXTRACT_OK_FLAGS = frozenset({"ok", "empty", "oversized_streamed"})


def _present_files_by_book(store) -> dict[int, list[tuple[str, int]]]:
    """present=1 文件行按 book_id 分组（同书可多条；book_id 序 → 确定性处理次序）。"""
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT book_id, path, size_bytes FROM files"
            " WHERE present = 1 AND book_id IS NOT NULL"
            " ORDER BY book_id, path"
        ).fetchall()
    finally:
        conn.close()
    groups: dict[int, list[tuple[str, int]]] = {}
    for book_id, path, size_bytes in rows:
        groups.setdefault(int(book_id), []).append((str(path), int(size_bytes)))
    return groups


def _pick_extract_file(files: list[tuple[str, int]]) -> str | None:
    """每本挑一个 size_bytes>0 且本机存在的 present 文件。

    同书多条 files 行（T1 实测）时跳过 0 字节行；无可用文件 → None（调用方
    如实计 failed，不硬失败）。
    """
    for path, size_bytes in files:
        if size_bytes > 0 and os.path.isfile(path):
            return path
    return None


def _book_zones(store) -> dict[int, set[str]]:
    """book_id → 出现过的专区集合（files 投影；双区同书两侧都算）。"""
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT book_id, zone FROM files WHERE present = 1 AND book_id IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    zones: dict[int, set[str]] = {}
    for book_id, zone in rows:
        zones.setdefault(int(book_id), set()).add(str(zone or ""))
    return zones


def _auto_tag_book_for_zone(store, book_id: int, path: str, zone: str, *, now: str) -> dict:
    """单本自动打标（#83）：正文挖掘 → match/record → （仅 liked）tag_hit 画像事件。

    candidate 也落 book_tags(source=auto)（报告人裁定：1529 本候选书同样打标），
    但不落 tag_hit——未确认偏好不折叠进画像 axis_weights（防污染边界不变）。
    正文只进分词器，计数以外的内容零落库（B 类）。
    返回 {tagged, folded, inserted, proposed, alias_proposed}。
    """
    from reco_io.mining import mine_alias_counts
    from reco_labels.auto_tag import StoreAdapter, auto_tag_book

    counts = mine_alias_counts(path)
    outcome = auto_tag_book(
        StoreAdapter(store), book_id, zone,
        SimpleNamespace(alias_counts=counts), now=now,
    )
    folded = zone == "liked"
    if folded:
        for hit in outcome.hits:
            store.append_event(
                "tag_hit",
                payload={"axis": str(hit.basis.get("axis") or ""),
                         "tag_id": hit.tag_id,
                         "confidence": float(hit.confidence)},
                book_id=int(book_id), occurred_at=now,
                config_fingerprint=AUTOTAG_CONFIG_FP,
            )
    return {"tagged": bool(outcome.inserted or outcome.updated),
            "folded": folded,
            "inserted": list(outcome.inserted),
            "proposed": list(outcome.proposed),
            "alias_proposed": list(outcome.proposed_aliases)}


def cmd_extract(db, *, limit=None) -> int:
    """批量提取：每本 features_computed（有向量时再 vector_computed）。

    - 遍历 files present=1（book_id 序）；每本取一个 size_bytes>0 且本机存在的
      文件，逐本调用 reco_io.extract.extract_and_record；
    - #83：liked/candidate 专区书顺手自动打标（正文分词挖掘 → 命中落
      book_tags(source=auto)；liked 再落 tag_hit 画像事件，candidate 不折叠画像；
      未知高频词/谐音形近变体进 proposals 待确认）；
    - 无可用文件/降级（undecodable/corrupt_container/pending_timeout/
      unreadable）如实计 failed，整轮不中断；
    - stdout 只输出 JSON 摘要 {total, ok, failed, vectors, tagged, proposals}（无正文）。
    """
    from reco_io.extract import extract_and_record

    with perf_span("cli.extract"):
        store = open_store(db)
        try:
            groups = _present_files_by_book(store)
            zones = _book_zones(store)
            now = _utc_now_iso()
            total = ok = failed = vectors = tagged = proposals = 0
            for book_id, files in groups.items():
                if limit is not None and total >= limit:
                    break
                total += 1
                path = _pick_extract_file(files)
                if path is None:
                    failed += 1
                    continue
                result = extract_and_record(
                    store, book_id, path, now=now,
                    config_fingerprint=EXTRACT_CONFIG_FP,
                )
                if result.vector_written:
                    vectors += 1
                if result.quality_flag in _EXTRACT_OK_FLAGS:
                    ok += 1
                else:
                    failed += 1
                book_zones = zones.get(book_id, ())
                tag_zone = "liked" if "liked" in book_zones else (
                    "candidate" if "candidate" in book_zones else None)
                if tag_zone:
                    # #83：liked/candidate 书自动打标；tag_hit 画像事件仅 liked；失败不中断整轮
                    try:
                        tag_res = _auto_tag_book_for_zone(store, book_id, path, tag_zone, now=now)
                        tagged += 1 if tag_res["tagged"] else 0
                        proposals += len(tag_res["proposed"]) + len(tag_res["alias_proposed"])
                    except Exception as exc:
                        print(f"warn: 自动打标失败 book={book_id} "
                              f"({type(exc).__name__}: {exc})", file=sys.stderr)
            print(json.dumps({"total": total, "ok": ok, "failed": failed,
                              "vectors": vectors, "tagged": tagged,
                              "proposals": proposals}, ensure_ascii=False))
            return 0
        finally:
            store.close()


# ---------------------------------------------------------------- recommend

def _confidence_dict(conf) -> dict:
    return {"n_pos": int(conf.n_pos), "n_neg": int(conf.n_neg),
            "value": float(conf.value), "display": conf.display}


def _utcnow_iso() -> str:
    """当前 UTC 时间（ISO-8601 带时区），事件落库统一口径。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

def cmd_recommend(db, *, as_json=True, persist=False) -> int:
    """推荐列表：共享装配层 → rank 唯一入口（A6；W13 真实向量接线，零复制 §13）。

    装配（库态→rank 输入）与 GUI 同源（reco_store.recommend.rank_candidates）；
    本入口只做输出序列化，零排序、零装配复制。
    """
    from reco_store.recommend import persist_rank_run, rank_candidates

    with perf_span("cli.recommend"):
        store = open_store(db)
        try:
            out = rank_candidates(store)
            # #70：默认不落库——C7 契约要求 recommend 是只读入口（零事件写入）。
            # 需要留痕时显式 --persist；GUI 侧则因用户主动点击而落库。
            if persist:
                try:
                    persist_rank_run(store, out, now=_utcnow_iso(), actor="cli")
                except Exception as exc:
                    print(f"warn: 推荐落库失败（{type(exc).__name__}: {exc}）",
                          file=sys.stderr)
            conf = out["profile"].confidence
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
    """评估入口：真实样本接线 + 持久化门（W13/W8 接缝②；§6.3 ≥10 才评估）。"""
    from datetime import datetime, timezone as _tz

    from reco_core.profile import persist_evaluation_gate
    from reco_core.score import evaluate_pairwise
    from reco_store.recommend import load_eval_samples, load_profile

    with perf_span("cli.eval"):
        store = open_store(db)
        try:
            profile = load_profile(store)
            counts = profile.sample_counts
            n_pos = int(counts.get("tag_like", 0))
            n_neg = int(counts.get("tag_dislike", 0))
            if n_pos < 10 or n_neg < 10:
                print(json.dumps(
                    {"status": "insufficient", "n_pos": n_pos, "n_neg": n_neg,
                     "display": f"样本 {n_pos}/{n_neg}（正/负），不足以判定，不评估"},
                    ensure_ascii=False))
                return 0
            samples = load_eval_samples(store)  # W11 遗留闭合：真实样本（非 []）
            result = evaluate_pairwise(samples, None)
            now = datetime.now(_tz.utc)
            eval_event_id = persist_evaluation_gate(
                store, method="pairwise_cv",
                n_pos=int(result.get("n_pos", n_pos)), n_neg=int(result.get("n_neg", n_neg)),
                folds=int(result.get("folds", 0)), auc=float(result.get("auc") or 0.0),
                ci_low=float(result.get("ci_low") or 0.0),
                ci_high=float(result.get("ci_high") or 0.0),
                now=now, config_fingerprint="cli/eval/1",
                notes=str(result.get("verdict", "")),
            )  # ≥10 门：不足时内部拒绝写入（W8 接缝②强制）
            print(json.dumps({
                "status": "ok", "auc": result.get("auc"),
                "ci_low": result.get("ci_low"), "ci_high": result.get("ci_high"),
                "verdict": result.get("verdict"),
                "n_pos": result.get("n_pos", n_pos), "n_neg": result.get("n_neg", n_neg),
                "eval_run_event_id": eval_event_id,
            }, ensure_ascii=False))
            return 0
        finally:
            store.close()
