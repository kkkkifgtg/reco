"""反馈写入口（#101）：effects → 静音落库 → 文案，一次完成。

过去 CLI 与 GUI 各写一遍同样的编排（record_feedback → feedback_effects →
record_mute，各自带一份 fingerprint 常量），改一处漏两处；静音逻辑也没有
单一接口可测。现在只有这一个入口，调用方只渲染回执。

依赖方向：reco-store 可以向下依赖 reco_core（与本包 recommend 模块同向）——
编排需要 core 的折叠/静音窗契约，但写库仍是 store 自己的单写者队列。
"""
from __future__ import annotations

from typing import Mapping

__all__ = ["DEFAULT_FEEDBACK_FP", "record_feedback", "release_feedback"]

DEFAULT_FEEDBACK_FP = "store/feedback/1"


def record_feedback(
    store, kind, book_id, *, axis=None, value=1.0, now=None,
    event_id=None, config=None, config_fingerprint: str = DEFAULT_FEEDBACK_FP,
    axes=None,
) -> dict:
    """写反馈并返回回执：{kind, book_id, event_id, profile_event_id, display, mute_window}。

    dislike/like → rec_feedback + 画像版本快照；dislike 另落静音窗（替换语义）；
    add_to_pool → pool_added（不折叠、不静音）。静音窗的 expired_at 由显式
    now/config 算出，不读墙钟——测试与重放拿到同一个窗口。
    """
    from reco_core.feedback import feedback_effects, record_feedback as _record

    result = _record(kind, book_id=int(book_id), axis=axis, value=value,
                     store=store, now=now, event_id=event_id, config=config,
                     config_fingerprint=config_fingerprint, axes=axes)
    event = dict(result["event"])
    receipt = {
        "kind": str(kind),
        "book_id": int(book_id),
        "event_id": int(event["event_id"]),
        "profile_event_id": result.get("profile_event_id"),
        "display": "",
        "mute_window": None,
    }
    if kind == "add_to_pool":
        return receipt
    effects = feedback_effects(event, now=now, config=config)
    receipt["display"] = str(effects.get("display") or "")
    window = effects.get("mute_window") if kind == "dislike" else None
    if window is not None:
        store.record_mute(
            int(book_id),
            dedup_key=_dedup_key_of(store, int(book_id)),
            axis=str(axis), kind="dislike",
            expired_at=str(window.expired_at),
            remaining_days=float(window.remaining_days),
            occurred_at=str(event["occurred_at"]),
            config_fingerprint=config_fingerprint,
        )
        receipt["mute_window"] = window
    return receipt


def release_feedback(
    store, book_id, *, now=None, config_fingerprint: str = DEFAULT_FEEDBACK_FP,
    actor: str = "user",
) -> dict:
    """人工解除静音：mute_released 事件 + 投影零残留；返回回执。

    静音到期是读侧过滤（feedback 层判定），但「用户改主意、想提前放出来」
    需要显式出口——#111 清点时 release_mute 在生产里没有任何调用方，
    静音只能等过期。解除走同一入口，CLI/GUI 只渲染回执。
    """
    at = _now_iso(now)
    event_id = store.release_mute(
        int(book_id), occurred_at=at, config_fingerprint=config_fingerprint,
        actor=actor,
    )
    return {
        "kind": "unmute",
        "book_id": int(book_id),
        "event_id": int(event_id),
        "display": "已解除静音：这本可以重新出现在推荐里",
    }


def _now_iso(now) -> str:
    if now is None:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
    return now.isoformat() if hasattr(now, "isoformat") else str(now)


def _dedup_key_of(store, book_id: int) -> str | None:
    from reco_store.reads import dedup_key_of

    return dedup_key_of(store, book_id)