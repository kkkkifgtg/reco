"""八维度抽取（工单 07 切片 2，§6.1 extract_relation_graph）。

输入 duck-typed：stats 是纯数值统计量映射（W4 extract_statistics 产物），
alias_weights 是标签 id → 0-1 置信权重（W4 alias_counts 接缝的归一形态）。
本包不 import 任何上游包（ADR-0001 独立接缝），正文绝不进入（B1/B2）。

维度前缀（八维度 + traits 副维度，全部走标签 alias 命中）：
  role.* → characters[0].role；age.* → characters[0].age_group；
  archetype.*/body.* → characters[].traits；rel.* → edges[0].type；
  mech.* → edges[0].mechanism；consent.* → edges[0].consent；
  topology.* → topology；power.* → power_balance；evolution.* → evolution。
stat:edge_intensity → edges[0].intensity（越界统计量不入图，不钳位）。

D4 纪律：权重低于 evidence_floor（默认 0.35）的字段留空（unknown/空/null），
不瞎填；依据仍记入 evidence.basis（alias:<id> / stat:<name>，整体排序）。
confidence = 已消费信号权重最大值（如实低报，不虚高）。
"""

from __future__ import annotations

from typing import Any

from .graph import empty_graph

DEFAULT_EVIDENCE_FLOOR = 0.35

# 前缀 → 值映射（value 取 alias 末段；unknown 兜底由留空逻辑保证）
# —— 值映射表 ——
# 键 = 标签 id 的末段（与 seed 里的真实中文/digit 末段一致），值 = 展示名。
# 修复前这里用英文键（attack/peer/one_on_one...），而真实标签末段是中文，
# 导致 8 维度中无一能映射上（issue #68）。英文键保留为兼容别名。
_ROLE_MAP = {
    "攻": "攻", "受": "受", "互攻": "互攻", "双性": "双性", "无明确分工": "无明确分工",
    "attack": "攻", "receive": "受", "switch": "互攻", "both": "双性", "neutral": "无明确分工",
}
_AGE_MAP = {
    "同龄": "同龄", "年上": "年上", "年下": "年下", "跨代": "跨代",
    "peer": "同龄", "older": "年上", "younger": "年下", "gap": "跨代",
}
_EDGE_TYPE_MAP = {
    "支配-服从": "支配-服从", "对等": "对等", "保护": "保护", "利用": "利用",
    "dominance": "支配-服从", "equal": "对等", "protective": "保护", "exploitative": "利用",
}
_CONSENT_MAP = {
    "自愿": "自愿", "半自愿": "半自愿", "非自愿": "非自愿", "被迫转化": "被迫转化", "逐渐沦陷": "逐渐沦陷",
    "voluntary": "自愿", "semi": "半自愿", "non": "非自愿", "forced_shift": "被迫转化",
}
_TOPOLOGY_MAP = {
    "1v1": "1v1", "1vN": "1vN", "NvN": "NvN", "群像": "群像", "三角": "三角",
    "one_on_one": "1v1", "one_to_n": "1vN", "n_to_n": "NvN", "ensemble": "群像", "triangle": "三角",
}
_POWER_MAP = {
    "对等": "对等", "强攻弱受": "强攻弱受", "弱攻强受": "弱攻强受", "双向支配": "双向支配", "反转型": "反转型",
    "equal": "对等", "strong_top": "强攻弱受", "strong_bottom": "弱攻强受", "mutual_dom": "双向支配",
}
_EVOLUTION_MAP = {
    "稳定": "稳定", "单向恶化": "单向恶化", "双向沉沦": "双向沉沦", "救赎": "救赎", "反转": "反转",
    "stable": "稳定", "worsen": "单向恶化", "sink": "双向沉沦", "redemption": "救赎", "reversal": "反转",
}

# 本模型认识的前缀（其余交给标签/画像模型，不入关系依据）
_KNOWN_PREFIXES = frozenset(
    ("role", "age", "archetype", "body", "rel", "mech", "consent", "topology", "power", "evolution")
)

# 标签 id 前缀 → 维度名：真实 seed 用缩写（top./cons./pow./evo./arch.），
# 代码内部用全称。修复前不认缩写，导致 6/8 维度收不到任何标签（issue #68）。
_PREFIX_ALIASES = {
    "top": "topology",
    "cons": "consent",
    "pow": "power",
    "evo": "evolution",
    "arch": "archetype",
}


def _dimension_of(tag_id: str) -> str:
    """标签 id → 维度名：把缩写前缀规范化为全称（role./age. 等本就一致）。"""
    prefix = tag_id.split(".", 1)[0] if "." in tag_id else ""
    return _PREFIX_ALIASES.get(prefix, prefix)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _best(weights: dict[str, float], prefix: str, floor: float) -> tuple[str | None, float]:
    """取该维度下权重最高且过阈值的 alias 末段；无合格者返回 None。

    weights 是**已经按维度分组**的 {tag_id: 权重}（见 extract_relation_graph 的
    consumed 结构）。因此这里不能再按 prefix 二次过滤——真实标签前缀是缩写
    （top./cons.），与内部全称（topology/consent）不同，过滤会把全部条目滤掉
    （issue #68）。prefix 参数保留仅为可读性与签名兼容。
    """
    best_id: str | None = None
    best_w = -1.0
    for tag_id, w in weights.items():
        if w > best_w:
            best_id, best_w = tag_id, w
    if best_id is None or best_w < floor:
        return None, best_w if best_w >= 0 else 0.0
    return best_id.split(".", 1)[1], best_w


def extract_relation_graph(
    stats: dict[str, Any] | None,
    alias_weights: dict[str, float] | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从统计量与标签置信权重产出 §6.1 契约的关系图。"""
    cfg = config or {}
    floor = float(cfg.get("evidence_floor", DEFAULT_EVIDENCE_FLOOR))
    stats = dict(stats) if stats else {}
    weights = {str(k): v for k, v in dict(alias_weights).items()} if alias_weights else {}

    basis: set[str] = set()
    confidence = 0.0

    # 消费全部已识别前缀的 alias：依据必记（含未过阈值者），权重计入置信
    consumed: dict[str, dict[str, float]] = {}
    for tag_id, w in weights.items():
        prefix = _dimension_of(tag_id)  # 缩写前缀（top./cons.…）规范化为全称
        if prefix not in _KNOWN_PREFIXES:
            continue
        basis.add(f"alias:{tag_id}")
        wv = w if (_is_number(w) and 0.0 <= w <= 1.0) else 0.0
        confidence = max(confidence, wv)
        consumed.setdefault(prefix, {})[tag_id] = wv

    if "character_count" in stats:
        basis.add("stat:character_count")
    if "edge_intensity" in stats:
        basis.add("stat:edge_intensity")

    graph = empty_graph()

    # 人物：count 兜底（1 篇也照实输出自己）
    count_raw = stats.get("character_count")
    count = int(count_raw) if _is_number(count_raw) and count_raw >= 1 else 0
    graph["characters"] = [
        {"id": f"c{i}", "role": "unknown", "traits": [], "age_group": "unknown"}
        for i in range(1, count + 1)
    ]

    # 维度 2：traits（archetype/body，过阈值者，按标签 id 排序）
    trait_ids = sorted(
        tag_id
        for prefix in ("archetype", "body")
        for tag_id, w in consumed.get(prefix, {}).items()
        if w >= floor
    )
    for char in graph["characters"]:
        char["traits"] = list(trait_ids)

    if graph["characters"]:
        # 维度 1：role → characters[0]
        role, _ = _best(consumed.get("role", {}), "role", floor)
        if role is not None and role in _ROLE_MAP:
            graph["characters"][0]["role"] = _ROLE_MAP[role]
        # 维度 3：age_group → characters[0]
        age, _ = _best(consumed.get("age", {}), "age", floor)
        if age is not None and age in _AGE_MAP:
            graph["characters"][0]["age_group"] = _AGE_MAP[age]

    # 边：仅两人以上才有边可谈
    if count >= 2:
        edge: dict[str, Any] = {
            "from": "c1",
            "to": "c2",
            "type": "unknown",
            "mechanism": [],
            "intensity": None,
            "consent": "unknown",
        }
        # 维度 4：edge type
        rel, _ = _best(consumed.get("rel", {}), "rel", floor)
        if rel is not None and rel in _EDGE_TYPE_MAP:
            edge["type"] = _EDGE_TYPE_MAP[rel]
        # 维度 5：mechanism（过阈值者，按标签 id 排序）
        edge["mechanism"] = sorted(
            tag_id for tag_id, w in consumed.get("mech", {}).items() if w >= floor
        )
        # 维度 6：intensity（仅来自合法统计量；越界留空不钳位）
        intensity = stats.get("edge_intensity")
        if _is_number(intensity) and 0.0 <= intensity <= 1.0:
            edge["intensity"] = float(intensity)
            confidence = max(confidence, float(intensity))
        # 维度 7：consent
        consent, _ = _best(consumed.get("consent", {}), "consent", floor)
        if consent is not None and consent in _CONSENT_MAP:
            edge["consent"] = _CONSENT_MAP[consent]
        graph["edges"] = [edge]

    # 维度 8a：topology（标签命中优先于 count 兜底）
    topo, _ = _best(consumed.get("topology", {}), "topology", floor)
    if topo is not None and topo in _TOPOLOGY_MAP:
        graph["topology"] = _TOPOLOGY_MAP[topo]
    elif count == 2:
        graph["topology"] = "1v1"
    elif 3 <= count <= 5:
        graph["topology"] = "1vN"
    elif count >= 6:
        graph["topology"] = "群像"

    # 维度 8b/8c：power_balance / evolution
    power, _ = _best(consumed.get("power", {}), "power", floor)
    if power is not None and power in _POWER_MAP:
        graph["power_balance"] = _POWER_MAP[power]
    evo, _ = _best(consumed.get("evolution", {}), "evolution", floor)
    if evo is not None and evo in _EVOLUTION_MAP:
        graph["evolution"] = _EVOLUTION_MAP[evo]

    graph["evidence"] = {
        "confidence": confidence,
        "source": "auto",
        "basis": sorted(basis),
    }
    return graph
