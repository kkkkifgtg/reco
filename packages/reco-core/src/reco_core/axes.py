"""轴名 → tag_id 前缀的唯一词表（#99）。

标签 id 由 seed 用缩写前缀拼（top.1v1 / pow.强攻强受 / cons.自愿 / evo.稳定 /
arch.警察），而关系图的八维度用全称（topology / power / consent / evolution /
archetype）。两边各写一张表，缩写改一处就漏一处——explain_relation 曾因此对
五个轴恒返回空依据（#68 只修了 extract 一侧）。

这张表放在 reco-core：reco-labels 与 reco-relations 都向下依赖它，seed 造 id、
extract 认前缀、explain 找依据，三处都从这里取，不再各抄一份。
"""
from __future__ import annotations

__all__ = ["AXIS_PREFIX", "axis_of_prefix", "prefix_of"]

# 轴全称 → 缩写（取轴名前缀去 vowel 或惯用缩写，避免跨轴歧义）
AXIS_PREFIX: dict[str, str] = {
    "topology": "top", "power": "pow", "consent": "cons", "evolution": "evo",
    "role": "role", "body": "body", "archetype": "arch", "age": "age", "species": "spec",
    "mechanism": "mech", "coercion": "coer", "transformation": "trans",
    "body_part": "bpart", "setting": "set",
    "explicitness": "expl", "pov": "pov", "length": "len", "style": "style", "pace": "pace",
    "ending": "end", "tone": "tone", "emotion_arc": "emo",
    "language": "lang", "completion": "comp", "quality_flag": "qual",
}

_PREFIX_AXIS: dict[str, str] = {prefix: name for name, prefix in AXIS_PREFIX.items()}


def prefix_of(axis_name: str) -> str:
    """轴全称 → 前缀；未登记的轴名原样返回（调用方按未知处理）。"""
    return AXIS_PREFIX.get(str(axis_name), str(axis_name))


def axis_of_prefix(prefix: str) -> str | None:
    """前缀 → 轴全称；未知前缀回 None，让调用方显式决定接受还是忽略。"""
    return _PREFIX_AXIS.get(str(prefix))