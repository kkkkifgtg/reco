"""工单 10——不合口味反馈闭环（reco_core.feedback 模块级接缝）。

架构依据 ARCHITECTURE §13/§15/§5.5/§6.5 反例 1-3/6、工单 10 验收项：
- 负反馈 = 静音（mute）：带权衰减非硬排除（C7）。机制（纯契约通道，无第二条
  排序路径）：静音候选经 W8 公开 similarity_scores 取 sim(bid)，feedback 层把
  权重三元组按 95/5 分配出惩罚通道（w_sim'=0.95·w_sim，w_behavior'=w_behavior+
  0.05·w_sim，Σ=1 不变），并写负行为信号 S(bid)=-0.9·w_sim·sim/w_behavior'
  （§5.5 dislike 负信号的静音强度）。W8 rank 合成恒得
  score_muted = 0.05·w_sim·sim（降权 95%+，>0 不归零，§13 饱和语义）。
  候选向量保持原样（身份诚实；负样本仍完整进候选池/特征库，唯一硬过滤是去重）。
- 静音候选经 W8 rank 的 exploration_exclude 排除出探索位（不得被捞回）；
  表现层理由行 source=muted（不改分数/顺序）。
- 静音时长按 W9 分档（Q1）：tag 90d / signal 30d / stable(subject./relation.)
  180d，过期自动解除（解除后三元组与信号零残留）；同书重复反馈 = 替换窗口。
- 画像秒级更新：复用 W9 update_profile 折叠 rec_feedback（clip |Δ|≤0.05）；
  display 文案由折叠方向给出（下调/上调）；轴来源 = 画像主导轴（top_tags 首位）。
- 事件 append-only：persist_feedback 产出 rec_feedback/pool_added 事件交调用方
  store.append（唯一写入口，追溯/重复插入由 store 拒绝 → InvalidFeedback 透传）；
  pool_added 属交互类事件，不进口味事件流（画像折叠忽略，§7 隔离）；
  rehydrate_from_events 用 W9 build_profile 同流重建（重放基线恒 v1）。
- 排序唯一入口（§6.5 反例 6）：本模块零排序实现，全部输出经 W8 rank；
  权重来源与版本：三元组源自 ProfileData.weights，静音视图的 95/5 分配由本模块
  拥有并写入 docstring（score 只读，不改权重）。
- B 类安全：事件 payload 全部数值/枚举/标签 id（W5 白名单子集，B6）；零正文。
- 依赖方向（§4.2）：只 import 同包 reco_core.score/profile，零跨包引用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Mapping

from reco_core.profile import (
    build_profile, persist_profile_snapshot, sample_counts_for_scoring, update_profile,
)
from reco_core.score import Profile as ScoreProfile
from reco_core.score import Reason as ScoreReason
from reco_core.score import rank as score_rank
from reco_core.score import similarity_scores as score_similarity

__all__ = [
    "FEEDBACK_VERSION",
    "FEEDBACK_MUTE_RATIO",
    "InvalidFeedback",
    "MuteWindow",
    "feedback_effects",
    "handle_feedback",
    "persist_feedback",
    "checkpoint_profile",
    "record_feedback",
    "rehydrate_from_events",
    "rank_only",
]

FEEDBACK_VERSION = "feedback/1"
FEEDBACK_MUTE_RATIO = 0.05          # 静音保留 5% 有效权重 → 降权 95%+（非硬排除）
_MUTE_KEEP = FEEDBACK_MUTE_RATIO    # 惩罚后保留份额
_MUTE_DAMP = 1.0 - FEEDBACK_MUTE_RATIO  # 惩罚份额（95%）
_DEFAULT_TIERS = {"tag": 90.0, "signal": 30.0, "stable": 180.0}
_VALID_KINDS = ("dislike", "like", "add_to_pool")


class InvalidFeedback(ValueError):
    """反馈语义非法（未知 kind / dislike 缺轴 / append-only 违规）。"""


@dataclass(frozen=True)
class MuteWindow:
    """静音窗（带权衰减非硬排除的载体；过期自动解除）。"""

    kind: str
    expired_at: str
    remaining_days: float
    axis: str

    def with_remaining(self, now=None) -> "MuteWindow":
        """按当前时刻重算剩余天数（E8 时钟注入；≤0 即过期）。"""
        now_dt = _coerce_now(now)
        expired = datetime.fromisoformat(self.expired_at)
        if expired.tzinfo is None:
            expired = expired.replace(tzinfo=timezone.utc)
        remaining = (expired - now_dt).total_seconds() / 86400.0
        if remaining <= 0:
            return MuteWindow(self.kind, self.expired_at, 0.0, self.axis)
        return MuteWindow(self.kind, self.expired_at, remaining, self.axis)


def _coerce_now(now):
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(now))


def _duck_fields(ev):
    """鸭子事件协议（与 W9 同构）：Event/dict → (type, id, occurred_at, payload, book)。"""
    if isinstance(ev, Mapping):
        return (ev.get("event_type"), ev.get("event_id"), ev.get("occurred_at"),
                ev.get("payload"), ev.get("book_id"))
    return (getattr(ev, "event_type", None), getattr(ev, "event_id", None),
            getattr(ev, "occurred_at", None),
            getattr(ev, "payload", getattr(ev, "payload_json", None)),
            getattr(ev, "book_id", None))


def _payload_of(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw or {})


def _axis_tier_days(axis, config):
    """静音时长分档（Q1，与 W9 同规则）；非法配置回退默认。"""
    cfg = config if isinstance(config, Mapping) else {}
    tiers = cfg.get("half_life_days")
    tiers = tiers if isinstance(tiers, Mapping) else {}
    if not axis:
        key = "tag"
    elif axis.startswith("subject.") or axis.startswith("relation."):
        key = "stable"
    elif axis.startswith("signal."):
        key = "signal"
    else:
        key = "tag"
    days = tiers.get(key, _DEFAULT_TIERS[key])
    try:
        days = float(days)
    except (TypeError, ValueError):
        days = _DEFAULT_TIERS[key]
    if not days > 0:
        days = _DEFAULT_TIERS[key]
    return days


def feedback_effects(event, now=None, config=None) -> dict:
    """rec_feedback → 画像折叠权重 + 静音窗 + 展示文案（工单 10 A1/A3）。

    fold_weight = 0.5^(age/tier) × |value|（与 W9 fold_hit/fold_dislike 同源）；
    静音窗 expired_at = now + tier；重复反馈由调用方以新窗口替换旧窗口。
    非 rec_feedback 事件返回空 dict（不臆造效应）。
    """
    etype, _eid, occ, raw_payload, book = _duck_fields(event)
    if etype != "rec_feedback" or book is None:
        return {}
    now_dt = _coerce_now(now)
    payload = _payload_of(raw_payload)
    kind = str(payload.get("kind") or "dislike")
    if kind not in ("dislike", "like"):
        raise InvalidFeedback(f"rec_feedback kind 非法：{kind!r}")
    axis = payload.get("axis")
    if kind == "dislike" and not axis:
        raise InvalidFeedback("dislike 反馈必须携带 axis（画像折叠目标）")
    value = payload.get("value", 1.0)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 1.0
    tier_days = _axis_tier_days(axis if isinstance(axis, str) else None, config)
    occurred = _coerce_now(occ) if occ else now_dt
    age_days = max(0.0, (now_dt - occurred).total_seconds() / 86400.0)
    fold_weight = (0.5 ** (age_days / tier_days)) * abs(value)
    expired_dt = now_dt + timedelta(days=tier_days)
    effects = {
        "book_id": book,
        "axis": axis,
        "kind": kind,
        "fold_weight": fold_weight,
        "display": (f"已调整：{axis} 维度权重已下调" if kind == "dislike"
                    else f"已调整：{axis} 维度权重已上调"),
    }
    if kind == "dislike":
        window = MuteWindow(kind, expired_dt.isoformat(), tier_days, axis)
        effects["mute_window"] = window
        effects["mute_expired_at"] = window.expired_at
        effects["remaining_days"] = window.remaining_days
    return effects


def persist_feedback(kind, book_id, axis=None, value=1.0, store=None, now=None, event_id=None):
    """反馈 → 事件记录（append-only 唯一写入口；工单 10 A3/A4）。

    dislike/like → rec_feedback（口味事件流，画像可折叠）；
    add_to_pool → pool_added（交互类，画像折叠忽略，§7 隔离）。
    store 为 None：仅返回事件不落库；提供 store 时调 store.append，
    追溯/重复插入由 store 拒绝（InvalidFeedback 透传）。
    event_id 缺省由时间戳派生（秒级）；并发落库请显式传 id 或交存储层分配。
    """
    if kind not in _VALID_KINDS:
        raise InvalidFeedback(f"反馈类型非法：{kind!r}（允许 {_VALID_KINDS}）")
    if kind == "dislike" and not axis:
        raise InvalidFeedback("dislike 反馈必须携带 axis")
    now_dt = _coerce_now(now)
    if kind == "add_to_pool":
        event = {"event_type": "pool_added", "payload": {"kind": "add_to_pool"}}
    else:
        event = {"event_type": "rec_feedback",
                 "payload": {"axis": axis, "kind": kind, "value": float(value)}}
    event["event_id"] = int(event_id) if event_id is not None else int(now_dt.timestamp())
    event["occurred_at"] = now_dt.isoformat()
    event["book_id"] = book_id
    appended = False
    if store is not None:
        assigned = store.append(event)
        # 存储层会重新分配 event_id；把落库后的 id 记回事件，快照锚点据此对齐，
        # 不再回头读「库尾事件」推断（#108：并发写会让库尾变成别人的事件）。
        if isinstance(assigned, Mapping):
            assigned = assigned.get("event_id")
        if isinstance(assigned, int) and not isinstance(assigned, bool):
            event["event_id"] = int(assigned)
        appended = True
    return {"event": event, "appended": appended, "kind": kind, "book_id": book_id}


def _event_type_of(event) -> str:
    """事件类型：兼容 Event 对象与 dict 两种重放输入。"""
    value = getattr(event, "event_type", None)
    if value is None and isinstance(event, Mapping):
        value = event.get("event_type")
    return str(value or "")


def _event_id_of(event):
    """事件 id：兼容 Event 对象与 dict 两种重放输入。"""
    value = getattr(event, "event_id", None)
    if value is None and isinstance(event, Mapping):
        value = event.get("event_id")
    return int(value) if value is not None else None


def _without_event(events, event):
    """从事件流里剔除「刚由 persist_feedback 落库的那条事件」，得到折叠基线。

    优先按 event_id 精确剔除（id 由 store 返回，见 persist_feedback）；
    只有在鸭子 store 不返回 id 时才退回「末条同形」启发式（#108 之前的行为）。
    """
    wanted = _event_id_of(event)
    if wanted is not None:
        for index in range(len(events) - 1, -1, -1):
            if _event_id_of(events[index]) == wanted:
                return list(events[:index]) + list(events[index + 1:])
    return _without_tail_lookalike(events, event)


def _without_tail_lookalike(events, event):
    """兜底：按 (类型, 发生时间, book_id) 认最后一条并剔除。"""
    if not events:
        return events
    last = events[-1]
    same = (
        _event_type_of(last) == _event_type_of(event)
        and str(getattr(last, "occurred_at", "")) == str(
            event.get("occurred_at") if isinstance(event, Mapping) else getattr(event, "occurred_at", ""))
        and getattr(last, "book_id", None) == (
            event.get("book_id") if isinstance(event, Mapping) else getattr(event, "book_id", None))
    )
    return list(events[:-1]) if same else list(events)


def checkpoint_profile(store, event, *, config=None, now=None, config_fingerprint=None, axes=None):
    """把「折叠 event 之后」的画像版本快照写回事件流（#71）。

    返回 (ProfileData, snapshot_event_id|None)。

    口径：event 此时已由 persist_feedback 落库，因此基线必须在**剔除该事件**的
    历史流上重放，再折叠一次 —— 否则同一事件被折叠两遍，快照与 load_profile 不一致。
    版本号取「事件流里已有快照数 + 1」，使 profile_versions 历史行的 reason 序号连续
    （rehydrate 基线恒为 v1，不随快照数变化）。
    """
    events = list(store.read_events())
    prior = sum(1 for ev in events if _event_type_of(ev) == "profile_updated")
    base = replace(rehydrate_from_events(_without_event(events, event),
                                         config=config, now=now, axes=axes),
                   version=prior)
    profile = update_profile(base, event, config=config, now=now, axes=axes)
    return profile, persist_profile_snapshot(store, profile,
                                             config_fingerprint=config_fingerprint)


def record_feedback(kind, book_id, axis=None, value=1.0, store=None, now=None,
                    event_id=None, config=None, config_fingerprint=None, axes=None):
    """反馈写入口（CLI/GUI 共用）：落反馈事件 + 画像版本快照（#71）。

    在 persist_feedback 之上补一步：like/dislike 折叠进画像后把 profile_updated
    快照 append 进事件流，profile_versions 因此有历史版本可追溯。
    add_to_pool 不进口味流（§7 隔离），故既不折叠也不落快照。
    """
    result = persist_feedback(kind, book_id=book_id, axis=axis, value=value,
                              store=store, now=now, event_id=event_id)
    result["profile"] = None
    result["profile_event_id"] = None
    if store is not None and kind in ("like", "dislike"):
        # 折叠「刚落库的那条事件」：persist_feedback 已把 store 分配的 event_id
        # 写回 event，快照锚点不再依赖库尾读取（#108）。
        result["profile"], result["profile_event_id"] = checkpoint_profile(
            store, result["event"], config=config,
            now=_coerce_now(now), config_fingerprint=config_fingerprint, axes=axes)
    return result


def rehydrate_from_events(events, config=None, now=None, axes=None):
    """口味事件流 → 画像（W9 build_profile 同流重建；重放基线恒 v1，C2）。

    axes：分类法声明的全部轴，供覆盖度分母使用；缺省时退回已见轴数。
    """
    return build_profile(events, config=config, now=now, axes=axes)


def _normalize_mute(mute):
    """静音表统一为 {key: MuteWindow}；dict 形态（存储往返）兼容。"""
    if not mute:
        return {}
    out = {}
    for key, window in mute.items():
        if isinstance(window, MuteWindow):
            out[str(key)] = window
        elif isinstance(window, Mapping):
            out[str(key)] = MuteWindow(
                str(window.get("kind") or "dislike"),
                str(window.get("expired_at")),
                float(window.get("remaining_days") or 0.0),
                str(window.get("axis") or ""),
            )
        else:
            raise InvalidFeedback(f"静音窗形态非法：{type(window).__name__}")
    return out


def _candidate_vector_and_key(candidate):
    """候选 → (book_id, 去重键|None, 向量)；dict 形态支持 {dedup_key, vector}。"""
    book_id, spec = candidate
    if isinstance(spec, Mapping):
        if "vector" in spec or "dedup_key" in spec:
            key = spec.get("dedup_key")
            return book_id, (str(key) if key else None), spec.get("vector", {})
        return book_id, None, spec  # 裸向量 dict
    return book_id, None, spec


def _active_mutes(candidate_features, mute_map, now):
    """解析当前仍生效的静音 → ({book_id: window}, muted_ids)；过期自动解除。"""
    active = {}
    muted_ids = []
    for candidate in candidate_features:
        book_id, key, _vec = _candidate_vector_and_key(candidate)
        window = None
        if key is not None and key in mute_map:
            window = mute_map[key]
        elif book_id in mute_map:
            window = mute_map[book_id]
        if window is not None and window.with_remaining(now).remaining_days > 0.0:
            if book_id not in active:
                muted_ids.append(book_id)
            active[book_id] = window
    return active, tuple(muted_ids)


def _score_feedback_aware(candidate_features, profile, config=None, mute_by_key=None,
                          positive_vectors=(), now=None):
    """静音感知打分输入构造（W8 唯一入口复用；工单 10 A1/A2）。

    返回 (candidates, ScoreProfile, muted_ids)：候选向量原样透传（身份诚实，
    负样本仍进候选池/特征库）；静音惩罚走 score 契约的行为信号通道：
    - 三元组 95/5 分配：w_sim'=0.95·w_sim、w_behavior'=w_behavior+0.05·w_sim（Σ=1）；
    - 负信号 S(bid) = -0.9·w_sim·sim(bid)/w_behavior'，其中 sim 由 W8 公开
      similarity_scores 计算 → score_muted = 0.05·w_sim·sim（恒 >0，§13 不归零）。
    无静音时三元组与信号零残留（过期自动解除）。
    """
    mute_map = _normalize_mute(mute_by_key)
    active, muted_ids = _active_mutes(candidate_features, mute_map, now)
    candidates = [(bid, vec) for bid, _key, vec in
                  (_candidate_vector_and_key(c) for c in candidate_features)]  # 归一为裸向量
    w_sim, w_model, w_behavior = (float(x) for x in getattr(profile, "weights", (1.0, 0.0, 0.0)))
    signals = dict(getattr(profile, "behavior_signals", None) or {})
    weights = (w_sim, w_model, w_behavior)
    if muted_ids:
        # 95/5 分配：把写死的 0.05 换成 _MUTE_KEEP，份额与常量同源
        # （#101：同一算术不再手抄两遍，也不再改比例漏改一处）。
        w_behavior_view = w_behavior + _MUTE_KEEP * w_sim
        w_sim_view = w_sim - _MUTE_KEEP * w_sim
        weights = (w_sim_view, w_model, w_behavior_view)
        if w_behavior_view > 0 and w_sim > 0:
            sims = dict(score_similarity(candidates, tuple(positive_vectors or ())))
            for bid in muted_ids:
                sim = sims.get(bid, 0.0)
                signals[bid] = -(_MUTE_DAMP - _MUTE_KEEP) * w_sim * sim / w_behavior_view
    sp = ScoreProfile(
        weights=weights,
        sample_counts=sample_counts_for_scoring(getattr(profile, "sample_counts", None)),
        behavior_signals=signals,
        positive_vectors=tuple(positive_vectors or ()),
    )
    return candidates, sp, muted_ids


def _mark_muted_reasons(rows, muted_ids):
    """表现层标注：静音候选理由行 source=muted（不改分数/顺序，§6.5-6 安全）。"""
    muted = set(muted_ids or ())
    if not muted:
        return tuple(rows)
    out = []
    for row in rows:
        replace = getattr(row, "_replace", None)
        if row.book_id in muted and replace is not None:
            reasons = tuple(
                ScoreReason(axis=r.axis, contribution=r.contribution, source="muted")
                for r in (row.reasons or ())
            )
            out.append(replace(reasons=reasons))
        else:
            out.append(row)
    return tuple(out)


def handle_feedback(kind, book_id, candidate_features, profile, config=None, *,
                    mute_by_key=None, positive_vectors=(), now=None, event_id=None):
    """反馈闭环入口：画像折叠 + 静音 + 复用 W8 rank 唯一入口（工单 10 全链路）。

    - 画像秒级更新：rec_feedback 经 W9 update_profile 折叠（clip 上限）。
    - 静音：dislike 生成 MuteWindow（分档时长；重复反馈=替换）；静音候选
      降权 95%+ 沉底、排除探索位、行为信号惩罚，但仍在输出里（非硬排除）。
    - 展示文案：单候选 → 「已调整：X 维度权重已下调/已上调」；
      静音满 2 个 → 「口味可能已变：多个候选已被静音」（§13 退路）。
    - like 为回流入口（不静音）；add_to_pool 走 persist_feedback（不进口味流）。
    """
    if kind not in ("dislike", "like"):
        raise InvalidFeedback(f"handle_feedback 只接受 dislike/like：{kind!r}")
    now_dt = _coerce_now(now)
    axis = None
    top_tags = getattr(profile, "top_tags", None) or ()
    if top_tags:
        axis = str(top_tags[0][0])
    if not axis:
        raise InvalidFeedback("画像无主导轴（top_tags 为空），无法折叠反馈")
    effects = feedback_effects(
        {"event_type": "rec_feedback", "event_id": event_id,
         "occurred_at": now_dt.isoformat(),
         "payload": {"axis": axis, "kind": kind, "value": 1.0},
         "book_id": book_id},
        now=now_dt, config=config)
    event = {"event_type": "rec_feedback", "event_id": event_id,
             "occurred_at": now_dt.isoformat(),
             "payload": {"axis": axis, "kind": kind, "value": 1.0},
             "book_id": book_id}
    new_profile = update_profile(profile, event, config, now=now_dt)  # 秒级更新（W9）
    mute_map = _normalize_mute(mute_by_key)
    if kind == "dislike":
        mute_map[str(book_id)] = effects["mute_window"]  # 替换而非累积
    candidates, sp, muted_ids = _score_feedback_aware(
        candidate_features, new_profile, config, mute_by_key=mute_map,
        positive_vectors=positive_vectors, now=now_dt)
    rows = score_rank(candidates, sp, {}, now=now_dt,
                      exploration_exclude=frozenset(muted_ids))  # 唯一入口（W8）
    rows = _mark_muted_reasons(rows, muted_ids)
    display = effects["display"]
    if len(muted_ids) >= 2:
        display = "口味可能已变：多个候选已被静音"  # §13 负样本饱和退路
    window = mute_map.get(str(book_id)) if kind == "dislike" else None
    return {
        "rank_rows": rows,
        "display": display,
        "profile": new_profile,
        "mute_window": window,
        "mute_expired_at": window.expired_at if window else None,
        "muted": muted_ids,
        "kind": kind,
        "book_id": book_id,
    }


def rank_only(candidate_features, profile, mute=None, config=None, *,
              positive_vectors=(), now=None):
    """静音感知只读重排（不折叠画像；静音过期自动解除；工单 10 A1 过期语义）。"""
    candidates, sp, muted_ids = _score_feedback_aware(
        candidate_features, profile, config, mute_by_key=mute,
        positive_vectors=positive_vectors, now=now)
    rows = score_rank(candidates, sp, config, now=now,  # #82：透传 config（exploration_count）
                      exploration_exclude=frozenset(muted_ids))
    rows = _mark_muted_reasons(rows, muted_ids)
    return {
        "rank_rows": rows,
        "display": "",
        "profile": profile,
        "mute_window": None,
        "mute_expired_at": None,
        "muted": muted_ids,
    }