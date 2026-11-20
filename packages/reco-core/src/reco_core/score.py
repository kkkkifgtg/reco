"""工单 08——相似度打分（rank 唯一排序入口）（reco_core.score 模块级接缝）。

架构依据 ARCHITECTURE.md §6.3/§6.5：
- score/rank 是 CLI/GUI 唯一打分排序入口（纯函数；第二条排序路径出现即 blocker，
  §6.5 反例 6）；RankRow = {book_id, score, rank, reasons, is_exploration}。
- 权重由 profile model 给（Profile.weights），score 不自己算衰减、不读事件流、
  不解析标签、不推断关系（§6.5 反例 1-3：传入即 ValueError，测试守护）。
- 相似度 = 手算余弦（正样本质心），黄金期望 0.9494740323391176 由独立直算冻结；
  sklearn 只允许在 train_pairwise / evaluate_pairwise 首次调用时延迟导入
  （F5：import reco_core.score 不得触发 sklearn，子进程测试守护）。
- 探索位确定性（建议项 3 + §17 Q7）：候选集指纹（fingerprint_of：排序后 book_id
  序列哈希）决定旋转起点，固定次序（book_id 升序）决定行走方向，同输入必得同
  探索位；舒适区顶部（相似度并列最高）不作探索位。
- 排序稳定性：同分按 book_id 升序（§6.3 硬性行为）。
- 状态诚实：no_candidates / degraded_* / ok 经 rank._last_status 公布；
  空候选返回空 + 明确状态；缺失向量跳过并计数（_last_skipped），不静默当 0 分。
"""
from __future__ import annotations

import bisect
import hashlib
import math
from dataclasses import dataclass, field
from typing import NamedTuple

__all__ = [
    "Reason",
    "RankRow",
    "Profile",
    "WeightSet",
    "ModelHandle",
    "FingerprintTooShort",
    "similarity_scores",
    "weight_schedule",
    "score",
    "rank",
    "explain",
    "train_pairwise",
    "evaluate_pairwise",
    "fingerprint_of",
]

_NORM_TOL = 1e-6          # §6.3 失败模式：|Σw-1| < 1e-6，否则报错而非继续
_N_POS_MIN = 10           # §6.3 硬性行为：n_pos<10 → w_model=0
_PAIRWISE_MIN = 10        # train_pairwise 仅正负各 ≥10 才返回句柄
_CV_FOLDS = 5             # evaluate_pairwise 折数上限（不超过少数类样本量）
_DEFAULT_EXPLORATION = 2  # §17 Q7 已裁定：探索位默认 2 条
_MODEL_RAMP_SPAN = 60.0   # w_model 爬坡跨度（样本量），相邻样本量差有上限
_BANNED_CONFIG_KEYS = {
    "tag_definitions": "TAGS",
    "tag_aliases": "TAGS",
    "tag_taxonomy": "TAGS",
    "relation_summary": "RELATIONS",
    "relation_graph": "RELATIONS",
    "events": "EVENTS",
    "event_stream": "EVENTS",
}


class FingerprintTooShort(ValueError):
    """候选集指纹无法生成（空候选集没有可哈希的稳定身份）。"""


@dataclass(frozen=True)
class Reason:
    """理由行：命中轴 + 权重贡献 + 来源机制（explain/rank 共用）。"""

    axis: str
    contribution: float
    source: str = ""


class RankRow(NamedTuple):
    """排序行（CLI/GUI 唯一排序来源的数据契约）。"""

    book_id: str
    score: float
    rank: int
    reasons: tuple
    is_exploration: bool


@dataclass(frozen=True)
class WeightSet:
    """weight_schedule 的产出（权重来源与版本可追溯，§6.5 反例 4）。"""

    w_sim: float
    w_model: float
    w_behavior: float
    version: str = "weights/1"
    reason: str = ""


@dataclass(frozen=True)
class ModelHandle:
    """train_pairwise 的产出；score 只读其 status（注入用）。"""

    status: str
    version: str = "pairwise/1"
    estimator: object = None
    output: float = 0.0


@dataclass(frozen=True)
class Profile:
    """score 的只读输入契约（由 profile model 产出；score 只读，§6.4）。

    weights: (w_sim, w_model, w_behavior)，profile model 给定，score 不改写；
    behavior_signals: {book_id: 数值信号}（已折算软标签，不含事件流）；
    positive_vectors: 正样本向量序列（相似度质心来源）；
    model_handle: 可选注入的 ModelHandle/dict（status != "ready" 时不参与）。
    """

    weights: tuple = (1.0, 0.0, 0.0)
    sample_counts: dict = field(default_factory=dict)
    behavior_signals: dict = field(default_factory=dict)
    positive_vectors: tuple = ()
    model_handle: object = None


def fingerprint_of(book_ids):
    """候选集指纹：排序后 book_id 序列的 blake2b 哈希（§6.3 建议项 3）。

    同输入必得同指纹；序列顺序无关（排序后比较）；空候选集无稳定身份，拒绝。
    """
    ordered = sorted(book_ids)
    if not ordered:
        raise FingerprintTooShort("候选集指纹：空候选集没有可哈希的稳定身份")
    payload = ",".join(ordered).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def _reject_semantic_config(config):
    """§6.5 反例 1-3 守卫：score 不消费标签/关系/事件语义输入。

    守卫是纯函数：只拒绝非法键，不改写调用方的 config（score 本身须纯）。
    """
    if not config:
        return
    for key in tuple(config):
        banned = _BANNED_CONFIG_KEYS.get(key)
        if banned:
            raise ValueError(
                f"{banned}: score 不消费 {key}——该语义属各自模型"
                "（§6.5 反例 1-3），score 只读合成输入"
            )


def _as_dense(vector):
    """{dim: weight} / [(i, w), ...] / ((i, ...), (w, ...)) 三种形态 → dict。"""
    if isinstance(vector, dict):
        return {int(k): float(w) for k, w in vector.items()}
    seq = list(vector)
    if not seq:
        return {}
    if all(
        isinstance(p, (tuple, list)) and len(p) == 2
        and isinstance(p[0], int) and not isinstance(p[0], bool)
        and isinstance(p[1], float) and not isinstance(p[1], bool)
        for p in seq
    ):
        return {int(i): float(w) for i, w in seq}
    if len(seq) == 2 and all(isinstance(part, (tuple, list)) for part in seq):
        indices, weights = seq
        return {int(i): float(w) for i, w in zip(indices, weights)}
    raise ValueError(f"不支持的向量形态：{type(vector).__name__}")


def similarity_scores(candidate_vectors, positive_vectors, config=None):
    """欧氏距离核相似度【换核演练变体】sim = 1/(1+d²)（手算金标 0.8333333333333334）。

    【W14 换核演练】替换自余弦实现——不同度量 + 不同计算路径：
    质心 → 逐候选平方欧氏距离 d² = Σ(质心_d − 候选_d)² → 核相似 1/(1+d²)。
    保序性：单位向量下 1/(3−2cosθ) 随 cosθ 严格单调增——排序契约不变；
    数值契约按变体重冻结（SIM_GOLDEN_E=0.8333333333333334，venv stdlib 独立直算）。
    零范数候选：d 为到质心的全距离（诚实保序，最高分=与质心重合），
    无正样本 → 全 0.0；无 sklearn（F5 安全）；config 签名保留不消费。
    """
    if isinstance(positive_vectors, dict):
        positive_vectors = [positive_vectors]
    dense_pos = [_as_dense(v) for v in (positive_vectors or [])]
    if not dense_pos:
        return [(book_id, 0.0) for book_id, _vec in candidate_vectors]
    centroid: dict = {}
    for dense in dense_pos:
        for dim, weight in dense.items():
            centroid[dim] = centroid.get(dim, 0.0) + weight
    n = len(dense_pos)
    centroid = {dim: weight / n for dim, weight in centroid.items()}
    out = []
    for book_id, vec in candidate_vectors:
        dense = _as_dense(vec)
        d2 = 0.0
        for dim, cw in centroid.items():
            diff = cw - dense.get(dim, 0.0)
            d2 += diff * diff
        for dim, vw in dense.items():
            if dim not in centroid:
                d2 += vw * vw
        out.append((book_id, 1.0 / (1.0 + d2)))
    return out


def weight_schedule(n_pos, n_neg, cv_auc, config=None):
    """三路权重调度（§6.3：平滑连续，相邻样本量差有上限）。

    - 门：n_pos<10 或 n_neg<10 → w_model=0（§6.3 硬性行为；单类别禁分类器）。
    - CV AUC 严格低于 0.5（比随机差）→ ValueError（拒绝坏评估，不静默给权重）。
    - 质量 = sigmoid((cv_auc-0.5)/0.1)；爬坡 = clip((min(n_pos,n_neg)-10)/60, 0, 1)
      ——每样本斜率被 cap×quality/60 界定，无跳变。
    - w_model = cap × 质量 × 爬坡；w_behavior = clip((n_pos+n_neg)/40, 0, 0.25)；
      w_sim = 1 − w_model − w_behavior（恒 >0：cap≤0.5、behavior≤0.25）。
    """
    cap = 0.5
    cfg = config or {}
    try:
        cap = float(cfg.get("model_weight_cap", 0.5))
    except (TypeError, ValueError):
        cap = 0.5
    cap = min(max(cap, 0.0), 0.5)
    if cv_auc < 0.5:
        raise ValueError(f"CV AUC={cv_auc} 低于随机水平 0.5，拒绝据此赋权（§6.3）")
    if n_pos < _N_POS_MIN or n_neg < _N_POS_MIN:
        w_model = 0.0
    else:
        quality = 1.0 / (1.0 + math.exp(-(cv_auc - 0.5) / 0.1))
        ramp = min(max((min(n_pos, n_neg) - _N_POS_MIN) / _MODEL_RAMP_SPAN, 0.0), 1.0)
        w_model = cap * quality * ramp
    w_behavior = min(max((n_pos + n_neg) / 40.0, 0.0), 0.25)
    w_sim = 1.0 - w_model - w_behavior
    reason = f"n_pos={n_pos},n_neg={n_neg},cv_auc={cv_auc},cap={cap}"
    return WeightSet(w_sim=w_sim, w_model=w_model, w_behavior=w_behavior,
                     version="weights/1", reason=reason)


def _handle_status(handle):
    if handle is None:
        return None
    if isinstance(handle, dict):
        return handle.get("status")
    return getattr(handle, "status", None)


def _resolve_status(profile):
    """决定打分状态（§6.3 硬性行为；模型文件缺失优先于样本量门）。"""
    counts = profile.sample_counts or {}
    n_pos = int(counts.get("n_pos", 0) or 0)
    n_neg = int(counts.get("n_neg", 0) or 0)
    if _handle_status(profile.model_handle) == "file_missing":
        return "degraded_model_file_missing"
    if n_pos < _N_POS_MIN or n_neg < _N_POS_MIN:
        if n_pos == 0:
            return "degraded_no_positives"
        if n_neg == 0:
            return "degraded_no_negatives"
        return "degraded_small_sample"
    return "ok"


def _effective_weights(profile, status):
    """生效权重：先断言归一（报错而非继续），再按状态降级重归一。"""
    w_sim, w_model, w_behavior = (float(x) for x in profile.weights)
    total = w_sim + w_model + w_behavior
    if abs(total - 1.0) > _NORM_TOL:
        raise ValueError(
            f"权重不归一：|Σw-1|={abs(total - 1.0):.2e} 超容差 1e-6，拒绝合成（§6.3 失败模式）"
        )
    if status.startswith("degraded"):
        rest = w_sim + w_behavior
        if rest <= 0.0:
            raise ValueError("降级后相似度/行为权重全为 0，无法合成")
        return (w_sim / rest, 0.0, w_behavior / rest)
    return (w_sim, w_model, w_behavior)


def _model_output(profile, w_model):
    """模型路输出：仅当句柄注入且状态 ready 且 w_model>0 时非零。

    句柄缺失/文件缺失/样本不足都返回 0.0——不伪造分类器输出（§6.3）。
    """
    if w_model <= 0.0:
        return 0.0
    handle = profile.model_handle
    if _handle_status(handle) != "ready":
        return 0.0
    raw = handle.get("output") if isinstance(handle, dict) else getattr(handle, "output", 0.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _is_empty_vector(vec):
    dense = _as_dense(vec)
    return not dense or all(w == 0.0 for w in dense.values())


def _positives_of(profile):
    """取画像中的正样本向量（无则空——相似度路全 0，不伪造）。"""
    positives = getattr(profile, "positive_vectors", None)
    if not positives:
        return []
    if isinstance(positives, dict):
        return [positives]
    return list(positives)


_last_skipped = 0


def _publish_status(status):
    """把状态公布到两个唯一入口（CLI/GUI 语义一致）。"""
    score._last_status = status
    rank._last_status = status


def _synthesize(candidate_features, profile, config=None):
    """共享合成核（score/rank/explain 复用）：状态 + 权重 + 三路合成。"""
    global _last_skipped
    if not candidate_features:
        _publish_status("no_candidates")
        return []
    status = _resolve_status(profile)
    _publish_status(status)
    w_sim, w_model, w_behavior = _effective_weights(profile, status)
    sims = dict(similarity_scores(candidate_features, _positives_of(profile)))
    model_out = _model_output(profile, w_model)
    _last_skipped = 0
    rows = []
    for book_id, vec in candidate_features:
        if _is_empty_vector(vec):
            _last_skipped += 1  # 特征缺失：跳过并计数，不静默当 0 分（§6.3）
            continue
        s_sim = sims.get(book_id, 0.0)
        contribution_sim = w_sim * s_sim
        contribution_model = w_model * model_out
        signal = 0.0
        if profile.behavior_signals:
            signal = float(profile.behavior_signals.get(book_id, 0.0) or 0.0)
        contribution_behavior = w_behavior * signal
        total = contribution_sim + contribution_model + contribution_behavior
        reasons = []
        if contribution_sim > 0.0:
            reasons.append(Reason("similar", contribution_sim, "cosine-centroid"))
        if contribution_model > 0.0:
            reasons.append(Reason("model", contribution_model, "pairwise-classifier"))
        if contribution_behavior > 0.0:
            reasons.append(Reason("behavior", contribution_behavior, "behavior-signal"))
        rows.append((book_id, total, tuple(reasons), s_sim))
    return rows


def score(candidate_features, profile, config=None, now=None):
    """唯一打分入口（纯函数；§6.3）：list[(book_id, score)]。

    now 仅承载签名契约；score 内不得用它做衰减——衰减属 profile model（§6.5 反例 1）。
    """
    _reject_semantic_config(config)
    return [(bid, total) for bid, total, _reasons, _sim in _synthesize(candidate_features, profile, config)]


def _pick_exploration(book_ids, sims, config, excluded=frozenset()):
    """确定性探索位选取（§17 Q7 + 建议项 3）。

    候选集指纹决定旋转起点，固定次序（book_id 升序）决定行走方向——同输入必得
    同探索位；舒适区顶部（相似度并列最高者）与 excluded 成员不作探索位，
    全并列时池取全体未排除候选；池不大于配额时不探索（全员探索=没有探索）。
    """
    try:
        count = int((config or {}).get("exploration_count", _DEFAULT_EXPLORATION))
    except (TypeError, ValueError):
        count = _DEFAULT_EXPLORATION
    if count <= 0 or not book_ids:
        return set()
    top = max(sims)
    non_top = sorted(bid for bid, s in zip(book_ids, sims)
                     if s < top and bid not in excluded)
    pool = non_top if non_top else sorted(bid for bid in book_ids if bid not in excluded)
    if len(pool) <= count:
        return set()
    start = int(fingerprint_of(book_ids), 16) % len(pool)
    return {pool[(start + i) % len(pool)] for i in range(count)}


def rank(candidate_features, profile, config=None, now=None, *, exploration_exclude=frozenset()):
    """唯一排序入口（§6.3）：list[RankRow]，CLI/GUI 不得有第二条排序路径。

    同分按 book_id 升序稳定次序；探索位按候选集指纹确定性选取（默认 2 条）。
    exploration_exclude：不参与探索位选取的 book_id（通用排除能力，工单 10
    反馈层的静音语义由调用方负责——score 本身不知道「静音」概念，§6.5-1）。
    """
    _reject_semantic_config(config)
    rows = _synthesize(candidate_features, profile, config)
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: (-r[1], r[0]))
    exploration = _pick_exploration([r[0] for r in ordered], [r[3] for r in ordered], config,
                                    excluded=exploration_exclude)
    return [
        RankRow(book_id=bid, score=total, rank=i, reasons=reasons,
                is_exploration=bid in exploration)
        for i, (bid, total, reasons, _sim) in enumerate(ordered, start=1)
    ]


def explain(candidate_features, profile, book_id, config=None):
    """理由解释（§6.3：命中轴/相似样本来源/权重贡献）。"""
    _reject_semantic_config(config)
    for bid, _total, reasons, _sim in _synthesize(candidate_features, profile, config):
        if bid == book_id:
            return list(reasons)
    raise ValueError(f"explain：候选集中无 book_id={book_id!r}")


def _lazy_imports():
    """首次调用才加载 sklearn（F5；import reco_core.score 不触发）。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    return TfidfVectorizer, LogisticRegression


def train_pairwise(labeled_features, config=None):
    """成对分类器训练：仅 n_pos≥10 且 n_neg≥10 返回句柄，否则 None（§6.3）。"""
    labels = [int(lbl) for _bid, _text, lbl in labeled_features]
    n_pos = sum(1 for lbl in labels if lbl == 1)
    n_neg = len(labels) - n_pos
    if n_pos < _PAIRWISE_MIN or n_neg < _PAIRWISE_MIN:
        return None
    vec_cls, clf_cls = _lazy_imports()
    texts = [text for _bid, text, _lbl in labeled_features]
    vectorizer = vec_cls(analyzer="char", ngram_range=(2, 3), min_df=1)
    matrix = vectorizer.fit_transform(texts)
    clf = clf_cls().fit(matrix, labels)
    return ModelHandle(status="ready", version="pairwise/1", estimator=(vectorizer, clf))


def _auc_from_scores(scored):
    """Mann-Whitney AUC（并列计 0.5）。scored = [(label, proba), ...]。"""
    pos = sorted(p for lbl, p in scored if lbl == 1)
    neg = sorted(p for lbl, p in scored if lbl != 1)
    if not pos or not neg:
        raise ValueError("AUC 需要正负两类样本")
    total = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg, p)
        hi = bisect.bisect_right(neg, p)
        total += lo + 0.5 * (hi - lo)
    return total / (len(pos) * len(neg))


def _auc_ci(auc, n_pos, n_neg):
    """Hanley-McNeil 95% 近似置信区间（截断到 [0,1]）。"""
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    numerator = (auc * (1.0 - auc)
                 + (n_pos - 1) * (q1 - auc * auc)
                 + (n_neg - 1) * (q2 - auc * auc))
    variance = numerator / (n_pos * n_neg)
    se = math.sqrt(max(variance, 1e-12))
    return max(0.0, auc - 1.96 * se), min(1.0, auc + 1.96 * se)


def evaluate_pairwise(samples, config=None):
    """成对交叉验证评估（§6.3：正 vs 负、CV、AUC 与 95% 置信区间）。

    样本 = (book_id, text, label)。正负任一为空 → 如实
    {auc: None, verdict: "不足以判定", folds: 0}（禁止伪造 0.5/AUC）。
    折数 = min(5, 少数类样本量)；折内训练集须两类齐全才训。
    CI 覆盖 0.5 → verdict="尚不显著"（§6.3：置信区间跨 0.5 时明示）。
    sklearn 首次调用时才导入（F5）。
    """
    labels = [int(lbl) for _bid, _text, lbl in samples]
    n_pos = sum(1 for lbl in labels if lbl == 1)
    n_neg = len(labels) - n_pos
    insufficient = {
        "auc": None, "ci_low": None, "ci_high": None,
        "folds": 0, "n_pos": n_pos, "n_neg": n_neg,
        "verdict": "不足以判定",
    }
    if n_pos == 0 or n_neg == 0:
        return insufficient
    vec_cls, clf_cls = _lazy_imports()
    texts = [text for _bid, text, _lbl in samples]
    n = len(samples)
    vectorizer = vec_cls(analyzer="char", ngram_range=(2, 3), min_df=1)
    matrix = vectorizer.fit_transform(texts)
    folds = min(_CV_FOLDS, min(n_pos, n_neg))
    scored = []
    for fold in range(folds):
        test_idx = [i for i in range(n) if i % folds == fold]
        train_idx = [i for i in range(n) if i % folds != fold]
        y_train = [labels[i] for i in train_idx]
        if len(set(y_train)) < 2:
            continue
        clf = clf_cls()
        clf.fit(matrix[train_idx], y_train)
        proba = clf.predict_proba(matrix[test_idx])[:, 1]
        scored.extend((labels[i], float(p)) for i, p in zip(test_idx, proba))
    if not scored:
        return insufficient
    auc = _auc_from_scores(scored)
    ci_low, ci_high = _auc_ci(auc, n_pos, n_neg)
    verdict = "尚不显著" if ci_low <= 0.5 <= ci_high else "显著"
    return {
        "auc": auc, "ci_low": ci_low, "ci_high": ci_high,
        "folds": folds, "n_pos": n_pos, "n_neg": n_neg,
        "verdict": verdict,
    }
