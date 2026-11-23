"""工单 09——画像与置信度（时间衰减）（reco_core.profile 模级接缝）。

架构依据 ARCHITECTURE §6.4/§17 Q1/§6.5 反例 1-5、CONTEXT.md 半衰期/置信度词条：
- decay_weight 黄金公式 w = 0.5^(age_days/half_life_days)（w(half)=0.5，F2 裁定）。
- 半衰期分档（Q1）：tag 90d / signal 30d / stable(subject./relation.) 180d；分档可配，
  非法配置（≤0/非数值/非有限）拒绝并回退默认，reason 记入 profile_updated 事件（C4）。
- 折叠语义：事件 → (axis, sign, w)；轴权 = Laplace 平滑 (sp+1)/(sp+sn+2)，显示权重按
  单事件位移 clip |Δ|≤0.05 演化（单次反馈影响上限，防过拟合近期偏好）。
- 旧反馈权重更低：w 随 occurred_at 衰减（tag/sign/stable 分档），E8 时钟注入。
- 置信度（C6）：n_pos<10 → cap=0.3 +「还不够准」；不展示 AUC（AUC 属 W8 评估）。
- weights 三元组鸭子接缝（W8 WeightSet；§6.5-1 反向闭合）：profile 原样回声进
  ProfileData.weights 与 profile_updated payload.weights_json；score 只读，profile 不写。
- eval 持久化门（W8 接缝②，强制）：n_pos≥10 且 n_neg≥10 才写 eval_run 事件。
- 版本化与重放（C2）：build→v1，update→+1；verify_replay 同流重建逐位比对（容差 1e-9）。
- 事件缺口 → completeness="partial"（§6.4 失败模式）；event_id_high_water 供增量续算。
- 负样本带权衰减、永不硬排除（C7）：dislike 折叠进轴负计数，权重趋零不归零。
- 鸭子事件输入：Event/dict 皆可；reco_core 不 import reco_store（依赖方向 §4.2）。
- B 类安全：本模块零正文接触；payload 全部数值/枚举/JSON 数值串（W5 白名单子集，B6）。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import blake2b
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "PROFILE_VERSION",
    "DEFAULT_HALF_LIFE_DAYS",
    "DEFAULT_MAX_AXIS_STEP",
    "InvalidHalfLife",
    "Confidence",
    "AxisTrend",
    "ReplayReport",
    "ProfileData",
    "decay_weight",
    "build_profile",
    "update_profile",
    "profile_confidence",
    "axis_trends",
    "export_profile",
    "verify_replay",
    "merge_signal",
    "persist_evaluation_gate",
]

PROFILE_VERSION = "reco-profile/1"
DEFAULT_HALF_LIFE_DAYS = {"tag": 90.0, "signal": 30.0, "stable": 180.0}
DEFAULT_MAX_AXIS_STEP = 0.05
_GENERATED_PAYLOAD_KEYS = frozenset({
    "axis_weights_json", "sample_counts_json", "top_tags_json", "weights_json",
    "half_life_days_json", "reason", "event_id_high_water", "confidence", "profile_version",
})
_REPLAY_TOL = 1e-9


class InvalidHalfLife(ValueError):
    """半衰期配置非法（≤0/非数值/非有限）；调用方应回退默认并记事件（C4）。"""


@dataclass(frozen=True)
class Confidence:
    """画像可信度自报指标（C6）：只用于诚实展示，不是准确率。"""

    value: float
    n_pos: int
    n_neg: int
    cap: float
    display: str


@dataclass(frozen=True)
class AxisTrend:
    """轴趋势：↑ 上升 / ↓ 下降 / → 稳定，幅度为每版本平均位移。"""

    axis: str
    direction: str
    delta_per_step: float


@dataclass(frozen=True)
class ReplayReport:
    ok: bool
    diffs: tuple


@dataclass(frozen=True)
class ProfileData:
    """画像版本快照（§6.4 产出契约；score 只读 weights/behavior_signals）。"""

    version: int
    updated_at: str
    axis_weights: dict
    sample_counts: dict
    top_tags: tuple
    weights: tuple
    weight_version: str
    weight_reason: str
    half_life_days: dict
    behavior_signals: dict
    completeness: str
    event_id_high_water: int | None
    confidence: Confidence
    generated_events: tuple = ()


# ---------------------------------------------------------------- 公式与配置

def decay_weight(age_days: float, half_life_days: float) -> float:
    """黄金公式：w = 0.5^(age/half)（=exp(-ln2·age/half)，F2/§17 Q1）。

    age<0 按 0（未来事件不加权）；half 非法抛 InvalidHalfLife（C4）。
    """
    half = float(half_life_days)
    if not math.isfinite(half) or half <= 0:
        raise InvalidHalfLife(f"half_life_days 非法：{half_life_days!r}（必须为正有限数值）")
    age = float(age_days)
    if not math.isfinite(age) or age < 0:
        age = 0.0
    return 0.5 ** (age / half)


def _valid_half(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _resolve_config(config):
    """半衰期分档与 clip 校验；非法条目拒绝并回退默认，返回 (tiers, max_step, reason|None)。"""
    cfg = config if isinstance(config, Mapping) else {}
    tiers = dict(DEFAULT_HALF_LIFE_DAYS)
    reason = None
    raw = cfg.get("half_life_days")
    if raw is not None:
        if not isinstance(raw, Mapping):
            reason = "半衰期配置非法（非映射），回退默认分档"
        else:
            bad = sorted(k for k, v in raw.items() if not _valid_half(v))
            good = {k: float(v) for k, v in raw.items() if _valid_half(v)}
            tiers.update(good)
            if bad:
                reason = f"半衰期配置非法（{','.join(bad)}），回退默认分档"
    step_raw = cfg.get("max_axis_step", DEFAULT_MAX_AXIS_STEP)
    if not isinstance(step_raw, (int, float)) or isinstance(step_raw, bool) or not math.isfinite(step_raw) or step_raw <= 0:
        max_step = DEFAULT_MAX_AXIS_STEP
        reason = reason or "max_axis_step 配置非法，回退默认"
    else:
        max_step = float(step_raw)
    return tiers, max_step, reason


def _axis_class(axis: str, tiers: Mapping[str, float]) -> float:
    """Q1 分档：stable(subject./relation.) 180d / signal 30d / 其余 tag 90d。"""
    if axis.startswith("subject.") or axis.startswith("relation."):
        return tiers["stable"]
    if axis.startswith("signal."):
        return tiers["signal"]
    return tiers["tag"]


def _coerce_now(now) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(now))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _coerce_iso(now) -> str:
    return _coerce_now(now).isoformat()


def _event_fields(ev) -> tuple:
    """鸭子事件协议：Event/dict → (event_type, event_id, occurred_at, payload, book_id)。"""
    if isinstance(ev, Mapping):
        etype = ev.get("event_type")
        eid = ev.get("event_id")
        occ = ev.get("occurred_at")
        payload = ev.get("payload")
        book = ev.get("book_id")
    else:
        etype = getattr(ev, "event_type", None)
        eid = getattr(ev, "event_id", None)
        occ = getattr(ev, "occurred_at", None)
        payload = getattr(ev, "payload", None)
        book = getattr(ev, "book_id", None)
        if payload is None:
            payload = getattr(ev, "payload_json", None)
    if isinstance(payload, str):
        payload = json.loads(payload)
    return etype, eid, occ, dict(payload or {}), book


def _event_age_days(occ, now_dt: datetime) -> float:
    if not occ:
        return 0.0
    occ_dt = occ if isinstance(occ, datetime) else datetime.fromisoformat(str(occ))
    if occ_dt.tzinfo is None:
        occ_dt = occ_dt.replace(tzinfo=timezone.utc)
    age = (now_dt - occ_dt).total_seconds() / 86400.0
    return max(0.0, age)


def _config_fingerprint(config) -> str:
    if not config:
        return "profile-default"
    raw = json.dumps(_mapping_of(config), ensure_ascii=False, sort_keys=True, default=str)
    return blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def _mapping_of(obj):
    if isinstance(obj, Mapping):
        return {str(k): _mapping_of(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mapping_of(v) for v in obj]
    return obj


def _weight_triple(weight_set):
    """鸭子 WeightSet（dict 或属性对象）→ (w_sim, w_model, w_behavior, version, reason)。"""
    if weight_set is None:
        return (1.0, 0.0, 0.0), "weights/1", "小样本默认：仅相似度（n_pos<10 → w_model=0）"
    if isinstance(weight_set, Mapping):
        getter = weight_set.get
    else:
        getter = lambda name, default=None: getattr(weight_set, name, default)  # noqa: E731
    triple = (float(getter("w_sim", 1.0)), float(getter("w_model", 0.0)), float(getter("w_behavior", 0.0)))
    version = getter("version", "weights/1")
    reason = getter("reason", "")
    return triple, str(version), str(reason or "")


# ---------------------------------------------------------------- 折叠引擎

class _Folder:
    """事件折叠状态：sums（Laplace 计数）与 disp（显示权重，clip 演化）分离。"""

    def __init__(self, tiers, max_step, now_dt) -> None:
        self.tiers = tiers
        self.max_step = max_step
        self.now_dt = now_dt
        self.sums: dict[str, tuple[float, float]] = {}
        self.disp: dict[str, float] = {}
        self.behavior: dict[Any, float] = {}
        self.n_pos = 0
        self.n_neg = 0
        self.n_behavior = 0
        self.high_water: int | None = None
        self.gap = False

    # -- 折叠原语（显示权重按 clip 演化，语义 a）
    def _step(self, axis: str, d_sp: float, d_sn: float) -> float:
        sp, sn = self.sums.get(axis, (0.0, 0.0))
        disp = self.disp.get(axis, 0.5)
        sp2, sn2 = sp + d_sp, sn + d_sn
        target = (sp2 + 1.0) / (sp2 + sn2 + 2.0)
        delta = max(-self.max_step, min(self.max_step, target - disp))
        self.sums[axis] = (sp2, sn2)
        self.disp[axis] = disp + delta
        return delta

    def fold_hit(self, axis: str, age_days: float, confidence: float) -> float:
        w = decay_weight(age_days, _axis_class(axis, self.tiers)) * max(0.0, float(confidence))
        self._step(axis, w, 0.0)
        self.n_pos += 1
        return w

    def fold_dislike(self, axis: str, age_days: float, value: float) -> float:
        w = decay_weight(age_days, _axis_class(axis, self.tiers)) * abs(float(value))
        self._step(axis, 0.0, w)
        self.n_neg += 1
        return w

    def fold_behavior(self, book_id, weight: float) -> None:
        if book_id is None:
            return
        self.behavior[book_id] = self.behavior.get(book_id, 0.0) + weight
        self.n_behavior += 1

    def observe_event_id(self, eid) -> None:
        if eid is None:
            return
        eid = int(eid)
        if self.high_water is not None and eid > self.high_water + 1:
            self.gap = True
        self.high_water = eid if self.high_water is None else max(self.high_water, eid)

    def fold_event(self, ev, weights_cfg=None) -> None:
        etype, eid, occ, payload, book = _event_fields(ev)
        age = _event_age_days(occ, self.now_dt)
        self.observe_event_id(eid)
        if etype == "tag_hit":
            self.fold_hit(str(payload.get("axis") or payload.get("tag_id") or "unknown"), age, payload.get("confidence", 1.0))
        elif etype == "rec_feedback":
            axis = str(payload.get("axis") or "unknown")
            kind = str(payload.get("kind") or "dislike")
            if kind == "dislike":
                self.fold_dislike(axis, age, payload.get("value", 1.0))
            else:
                self.fold_hit(axis, age, payload.get("value", 1.0))
        elif etype == "signal_recorded":
            for effect in merge_signal(ev, config=weights_cfg, now=self.now_dt):
                self.fold_behavior(effect["book_id"], effect["weight"])

    # -- 从既有 ProfileData 恢复（update 路径；浮点经 dict 往返无损）
    @classmethod
    def restore(cls, profile: "ProfileData", tiers, max_step, now_dt) -> "_Folder":
        f = cls(tiers, max_step, now_dt)
        per_axis = (profile.sample_counts or {}).get("per_axis", {})
        for axis, pair in per_axis.items():
            f.sums[str(axis)] = (float(pair.get("positive", 0.0)), float(pair.get("negative", 0.0)))
        f.disp = {str(k): float(v) for k, v in (profile.axis_weights or {}).items()}
        f.n_pos = int((profile.sample_counts or {}).get("tag_like", 0))
        f.n_neg = int((profile.sample_counts or {}).get("tag_dislike", 0))
        f.n_behavior = int((profile.sample_counts or {}).get("behavior", 0))
        f.behavior = {k: float(v) for k, v in (profile.behavior_signals or {}).items()}
        f.high_water = profile.event_id_high_water
        f.gap = profile.completeness == "partial"
        return f


def merge_signal(event, config=None, now=None) -> list[dict]:
    """行为信号折算软标签效应（§6.4）：dwell/completion/reopen 正向，abandon/dislike 负向。

    strength 归一（dwell 按 200s 满强度）后乘 signal 档衰减；未知 kind 折 0（不臆造）。
    """
    etype, _eid, _occ, payload, book = _event_fields(event)
    now_dt = _coerce_now(now)
    if etype != "signal_recorded" or book is None:
        return []
    kind = str(payload.get("kind") or "")
    value = payload.get("value", 0.0)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if kind == "dwell":
        strength = min(1.0, (value * 0.4) / 200.0)
    elif kind in ("completion", "reopen", "favorite"):
        strength = 1.0 if kind != "completion" or value >= 0.9 else 0.0
    elif kind in ("abandon", "dislike"):
        strength = -1.0
    else:
        strength = 0.0
    cfg = config if isinstance(config, Mapping) else {}
    tiers = cfg.get("half_life_days") if isinstance(cfg.get("half_life_days"), Mapping) else {}
    half = float(tiers.get("signal", DEFAULT_HALF_LIFE_DAYS["signal"]))
    if not _valid_half(half):
        half = DEFAULT_HALF_LIFE_DAYS["signal"]
    age = _event_age_days(_event_fields(event)[2], now_dt)
    weight = strength * decay_weight(age, half)
    return [{"book_id": book, "kind": kind, "weight": weight, "age_days": age}]


# ---------------------------------------------------------------- 置信度

def _confidence_from(n_pos: int, n_neg: int, n_axes: int, n_covered: int) -> Confidence:
    cap = 0.3 if n_pos < 10 else 1.0
    sample_factor = min(1.0, (n_pos + n_neg) / 40.0)
    coverage = (n_covered / n_axes) if n_axes else 0.0
    total = n_pos + n_neg
    consistency = (1.0 - abs(n_pos - n_neg) / total) if total else 0.0
    value = cap * (0.4 * sample_factor + 0.35 * coverage + 0.25 * consistency)
    if n_pos == 0:
        display = f"暂无正样本，画像仅供参考（样本 {n_pos} 篇，还不够准）"
    elif n_pos < 10:
        display = f"样本 {n_pos} 篇，仅供参考，还不够准"
    else:
        display = f"样本 {n_pos} 篇"
    return Confidence(value=value, n_pos=n_pos, n_neg=n_neg, cap=cap, display=display)


def profile_confidence(profile) -> Confidence:
    """从画像的 sample_counts 推导置信度（C6：n_pos<10 → cap 0.3；不给假指标）。"""
    counts = getattr(profile, "sample_counts", None) or {}
    n_pos = int(counts.get("tag_like", 0))
    n_neg = int(counts.get("tag_dislike", 0))
    per_axis = counts.get("per_axis", {}) or {}
    n_axes = len(per_axis)
    n_covered = sum(1 for pair in per_axis.values() if pair.get("positive", 0) > 0 or pair.get("negative", 0) > 0)
    return _confidence_from(n_pos, n_neg, n_axes, n_covered)


# ---------------------------------------------------------------- 组装

def _assemble(folder: _Folder, version: int, now_dt: datetime, weight_triple, weight_version, weight_reason, fallback_reason, fingerprint: str) -> ProfileData:
    axis_weights = {axis: folder.disp[axis] for axis in sorted(folder.disp)}
    per_axis = {axis: {"positive": sp, "negative": sn} for axis, (sp, sn) in sorted(folder.sums.items())}
    sample_counts = {
        "tag_like": folder.n_pos,
        "tag_dislike": folder.n_neg,
        "behavior": folder.n_behavior,
        "per_axis": per_axis,
    }
    top_tags = tuple(sorted(((axis, sp) for axis, (sp, _sn) in folder.sums.items()), key=lambda kv: (-kv[1], kv[0])))
    conf = _confidence_from(folder.n_pos, folder.n_neg, len(per_axis), sum(1 for p in per_axis.values() if p["positive"] > 0 or p["negative"] > 0))
    reason_parts = []
    if fallback_reason:
        reason_parts.append(fallback_reason)
    reason_parts.append(f"profile v{version}: {folder.n_pos} pos / {folder.n_neg} neg / {folder.n_behavior} behavior")
    payload = {
        "axis_weights_json": json.dumps(axis_weights, ensure_ascii=False, sort_keys=True),
        "sample_counts_json": json.dumps({"tag_like": folder.n_pos, "tag_dislike": folder.n_neg, "behavior": folder.n_behavior}, sort_keys=True),
        "top_tags_json": json.dumps([[axis, w] for axis, w in top_tags], ensure_ascii=False),
        "weights_json": json.dumps({"w_sim": weight_triple[0], "w_model": weight_triple[1], "w_behavior": weight_triple[2], "version": weight_version, "reason": weight_reason}, ensure_ascii=False),
        "half_life_days_json": json.dumps(dict(folder.tiers)),
        "reason": "; ".join(reason_parts),
        "event_id_high_water": folder.high_water,
        # #71：置信度也进快照 —— profile_versions.confidence 是 NOT NULL 列，
        # 缺这条时投影层 KeyError，快照写不进去。
        "confidence": float(conf.value),
        "profile_version": PROFILE_VERSION,
    }
    assert set(payload) <= _GENERATED_PAYLOAD_KEYS  # B6：payload 白名单子集（W5）
    event = {
        "event_type": "profile_updated",
        "book_id": None,
        "payload": payload,
        "occurred_at": now_dt.isoformat(),
        "config_fingerprint": fingerprint,
    }
    return ProfileData(
        version=version,
        updated_at=now_dt.isoformat(),
        axis_weights=axis_weights,
        sample_counts=sample_counts,
        top_tags=top_tags,
        weights=weight_triple,
        weight_version=weight_version,
        weight_reason=weight_reason,
        half_life_days=dict(folder.tiers),
        behavior_signals=dict(folder.behavior),
        completeness="partial" if folder.gap else "full",
        event_id_high_water=folder.high_water,
        confidence=conf,
        generated_events=(event,),
    )


def build_profile(events, config=None, now=None, weight_set=None) -> ProfileData:
    """全量折叠事件流 → 画像 v1（§6.4；E8 时钟由 now 注入）。"""
    now_dt = _coerce_now(now)
    tiers, max_step, fallback_reason = _resolve_config(config)
    triple, wver, wreason = _weight_triple(weight_set)
    folder = _Folder(tiers, max_step, now_dt)
    for ev in (events or []):
        folder.fold_event(ev, config)
    return _assemble(folder, 1, now_dt, triple, wver, wreason, fallback_reason, _config_fingerprint(config))


def update_profile(profile, event, config=None, now=None, weight_set=None) -> ProfileData:
    """增量单事件折叠 → 版本 +1（单事件影响上限 clip；§6.4）。"""
    now_dt = _coerce_now(now)
    tiers, max_step, fallback_reason = _resolve_config(config)
    triple, wver, wreason = _weight_triple(weight_set)
    folder = _Folder.restore(profile, tiers, max_step, now_dt)
    folder.fold_event(event, config)
    return _assemble(folder, int(profile.version) + 1, now_dt, triple, wver, wreason, fallback_reason, _config_fingerprint(config))


def persist_profile_snapshot(store, profile, *, config_fingerprint=None, actor="system"):
    """把画像版本快照写回事件流（#71）——profile_updated 是 profile_versions 唯一投影来源。

    store 走鸭子协议（只要求 append_event）；快照事件自身不参与折叠，
    因此追加快照不改变当前画像语义（load_profile 结果不变），只增加历史版本。
    返回最后一个快照 event_id；未产出快照事件时返回 None。
    """
    event_ids = []
    for event in tuple(profile.generated_events or ()):
        event_ids.append(
            store.append_event(
                str(event["event_type"]),
                book_id=event.get("book_id"),
                payload=dict(event.get("payload") or {}),
                occurred_at=str(event["occurred_at"]),
                config_fingerprint=str(event.get("config_fingerprint") or config_fingerprint or "profile/1"),
                actor=actor,
            )
        )
    return event_ids[-1] if event_ids else None


def export_profile(profile) -> dict:
    """画像 JSON 导出（给用户看「依据什么在推荐」；数值/枚举，无正文，B 类安全）。"""
    return {
        "profile_version": PROFILE_VERSION,
        "version": profile.version,
        "updated_at": profile.updated_at,
        "axis_weights": dict(profile.axis_weights),
        "sample_counts": {
            "tag_like": profile.sample_counts.get("tag_like", 0),
            "tag_dislike": profile.sample_counts.get("tag_dislike", 0),
            "behavior": profile.sample_counts.get("behavior", 0),
            "per_axis": {k: dict(v) for k, v in profile.sample_counts.get("per_axis", {}).items()},
        },
        "top_tags": [{"axis": axis, "weight": w} for axis, w in profile.top_tags],
        "weights": {"w_sim": profile.weights[0], "w_model": profile.weights[1], "w_behavior": profile.weights[2], "version": profile.weight_version, "reason": profile.weight_reason},
        "half_life_days": dict(profile.half_life_days),
        "behavior_signals": dict(profile.behavior_signals),
        "completeness": profile.completeness,
        "event_id_high_water": profile.event_id_high_water,
        "confidence": {"value": profile.confidence.value, "n_pos": profile.confidence.n_pos, "n_neg": profile.confidence.n_neg, "cap": profile.confidence.cap, "display": profile.confidence.display},
    }


def _axis_weights_of(snapshot) -> dict:
    if isinstance(snapshot, Mapping):
        return {str(k): float(v) for k, v in (snapshot.get("axis_weights") or {}).items()}
    return {str(k): float(v) for k, v in (getattr(snapshot, "axis_weights", None) or {}).items()}


def _version_of(snapshot) -> int:
    value = snapshot.get("version") if isinstance(snapshot, Mapping) else getattr(snapshot, "version", None)
    return int(value or 0)


def axis_trends(profile_history) -> list:
    """相邻版本逐轴差均值 → AxisTrend（§6.4 trends）。"""
    snaps = sorted((s for s in (profile_history or [])), key=_version_of)
    if len(snaps) < 2:
        return []
    deltas: dict[str, list[float]] = {}
    for prev, cur in zip(snaps, snaps[1:]):
        wp, wc = _axis_weights_of(prev), _axis_weights_of(cur)
        for axis in set(wp) | set(wc):
            deltas.setdefault(axis, []).append(wc.get(axis, 0.0) - wp.get(axis, 0.0))
    out = []
    for axis, series in sorted(deltas.items()):
        mean = sum(series) / len(series)
        direction = "↑" if mean > 1e-12 else ("↓" if mean < -1e-12 else "→")
        out.append(AxisTrend(axis=axis, direction=direction, delta_per_step=mean))
    return out


def verify_replay(events, stored, config=None, now=None) -> ReplayReport:
    """同流重建逐位比对（C2）：axis_weights 容差 1e-9；计数/版本/完整性/置信度必须相等。"""
    rebuilt = build_profile(events, config=config, now=now)
    diffs = []
    stored_weights = _axis_weights_of(stored)
    for axis in set(stored_weights) | set(rebuilt.axis_weights):
        a, b = stored_weights.get(axis), rebuilt.axis_weights.get(axis)
        if a is None or b is None:
            diffs.append(f"axis_weights.{axis}: 存量 {'缺失' if a is None else repr(a)} vs 重放 {'缺失' if b is None else repr(b)}")
        elif abs(a - b) > max(_REPLAY_TOL, abs(a) * _REPLAY_TOL):
            diffs.append(f"axis_weights.{axis}: {a!r} vs {b!r}")
    stored_counts = stored.get("sample_counts") if isinstance(stored, Mapping) else getattr(stored, "sample_counts", None)
    if stored_counts:
        for key in ("tag_like", "tag_dislike", "behavior"):
            if int(stored_counts.get(key, 0)) != int(rebuilt.sample_counts[key]):
                diffs.append(f"sample_counts.{key}: {stored_counts.get(key, 0)} vs {rebuilt.sample_counts[key]}")
    stored_version = _version_of(stored)
    if stored_version and stored_version != rebuilt.version:
        diffs.append(f"version: {stored_version} vs {rebuilt.version}")
    stored_completeness = stored.get("completeness") if isinstance(stored, Mapping) else getattr(stored, "completeness", None)
    if stored_completeness and stored_completeness != rebuilt.completeness:
        diffs.append(f"completeness: {stored_completeness} vs {rebuilt.completeness}")
    stored_conf = stored.get("confidence") if isinstance(stored, Mapping) else getattr(stored, "confidence", None)
    stored_conf_value = stored.get("confidence_value") if isinstance(stored, Mapping) else None
    if isinstance(stored_conf, Mapping):
        stored_conf_value = stored_conf.get("value")
    if stored_conf_value is not None and abs(float(stored_conf_value) - rebuilt.confidence.value) > _REPLAY_TOL:
        diffs.append(f"confidence.value: {stored_conf_value!r} vs {rebuilt.confidence.value!r}")
    return ReplayReport(ok=not diffs, diffs=tuple(diffs))


def persist_evaluation_gate(store, *, method: str, n_pos: int, n_neg: int, folds: int,
                            auc: float, ci_low: float, ci_high: float, now,
                            config_fingerprint: str, notes: str = ""):
    """eval_run 持久化门（W8 接缝②强制）：n_pos≥10 且 n_neg≥10 才写；否则不写返回 None。

    store 鸭子注入（open_store 的 Store 或任何提供 append_event 的对象）；
    reco_core 零 reco-store import（§4.2 依赖方向）。
    """
    if n_pos < 10 or n_neg < 10:
        return None
    payload = {
        "method": str(method),
        "n_pos": int(n_pos),
        "n_neg": int(n_neg),
        "folds": int(folds),
        "auc": float(auc),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "notes": str(notes or ""),
    }
    return store.append_event(
        "eval_run",
        payload=payload,
        occurred_at=_coerce_iso(now),
        config_fingerprint=str(config_fingerprint),
        actor="system",
    )
