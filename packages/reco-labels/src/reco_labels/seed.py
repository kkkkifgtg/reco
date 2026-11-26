"""种子分类群：六域 25 轴 133 值（工单 06）。

数据源：原始规格包 tags.md「域与轴」表（只读）——本文件是它的逐字转录，
手算断言锚定（audit E3/E4）：25 轴合计 133 值（6+6+5+5 ｜ 5+6+11+4+5 ｜
10+5+6+5+7 ｜ 4+3+4+6+4 ｜ 5+7+4 ｜ 3+3+4 = 164 行含 6 域行）。
唯一 alias：mech.hypnosis ←「精神控制/洗脑/mind control」（规格 JSON 示例逐字）。
层级：值行 parent=轴行 id（如 mech）；轴行 parent=域行 id（d1…d6）；
域行 parent=None。created_at 统一 2026-11-20（静态数据包的时间是内容，
不是事件发生时刻；事件时间一律走 reco_labels.clock.resolve）。
"""
from __future__ import annotations

from reco_labels.store import SeedRow

SEED_DATE = "2026-11-20"
SEED_STATUS = "active"
SEED_CREATED_BY = "user"

# (域 id, 域名, [轴名, [值…]]…) —— 逐字来自规格包
_SPEC: tuple[tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    ("d1", "关系结构", (
        ("topology", ("1v1", "1vN", "NvN", "群像", "三角", "多方混战")),
        ("power", ("对等", "强攻强受", "强弱", "弱攻强受", "双向支配", "反转型")),
        ("consent", ("自愿", "半自愿", "非自愿", "被迫转化", "逐渐沦陷")),
        ("evolution", ("稳定", "单向恶化", "双向沉沦", "救赎", "反转")),
    )),
    ("d2", "人物属性", (
        ("role", ("攻", "受", "互攻", "双性", "无明确分工")),
        ("body", ("肌肉", "壮硕", "精瘦", "稚嫩", "肥胖", "残疾")),
        ("archetype", ("警察", "军人", "体育生", "学生", "教师", "医生",
                        "兽人", "改造人", "怪物", "非人", "神魔")),
        ("age", ("同龄", "年上", "年下", "跨代")),
        ("species", ("人类", "兽人", "怪物", "机械", "混合")),
    )),
    ("d3", "核心机制", (
        ("mechanism", ("催眠", "精神控制", "洗脑", "调教", "驯化", "奴役",
                        "药物", "诅咒", "寄生", "契约")),
        ("coercion", ("强制", "胁迫", "交易", "欺骗", "无知觉")),
        ("transformation", ("恶堕", "堕落", "兽化", "身体改造", "性别转换", "人格改写")),
        ("body_part", ("触手", "兽耳兽尾", "双性", "巨物", "生殖改造")),
        ("setting", ("校园", "军警", "职场", "家庭", "异世界", "末世", "监狱")),
    )),
    ("d4", "呈现方式", (
        ("explicitness", ("清水", "擦边", "直接描写", "极度露骨")),
        ("pov", ("第一人称", "第三人称限知", "全知")),
        ("length", ("超短", "短", "中", "长")),
        ("style", ("叙事流", "对话流", "心理重", "动作重", "白描", "华丽")),
        ("pace", ("慢热", "快节奏", "拖沓", "紧凑")),
    )),
    ("d5", "情绪与结局", (
        ("ending", ("HE", "BE", "开放", "无结局", "番外补完")),
        ("tone", ("爽", "压抑", "猎奇", "温情", "黑暗", "荒诞", "治愈")),
        ("emotion_arc", ("上升", "下沉", "起伏", "平稳")),
    )),
    ("d6", "元信息", (
        ("language", ("中文原创", "中文翻译", "其他")),
        ("completion", ("完结", "连载中", "太监")),
        ("quality_flag", ("成人向", "重口", "女性向", "男性向")),
    )),
)

# 轴名 → 轴缩写（tag_id 前缀；避免跨轴歧义，取轴名前缀去 vowel 或惯用缩写）
_AXIS_PREFIX: dict[str, str] = {
    "topology": "top", "power": "pow", "consent": "cons", "evolution": "evo",
    "role": "role", "body": "body", "archetype": "arch", "age": "age", "species": "spec",
    "mechanism": "mech", "coercion": "coer", "transformation": "trans",
    "body_part": "bpart", "setting": "set",
    "explicitness": "expl", "pov": "pov", "length": "len", "style": "style", "pace": "pace",
    "ending": "end", "tone": "tone", "emotion_arc": "emo",
    "language": "lang", "completion": "comp", "quality_flag": "qual",
}

# 规格唯一 alias 示例（逐字）：mech.hypnosis
_SEED_ALIASES: dict[str, tuple[str, ...]] = {
    "mech.hypnosis": ("精神控制", "洗脑", "mind control"),
}

# 值行 id 方案：默认 <轴前缀>.<中文值名>；规格 JSON 示例逐字给定 mech.hypnosis
# 一个英文 id（id=domain.axis.value 格式的唯一权威样例），以 override 保留。
_VALUE_ID_OVERRIDES: dict[tuple[str, str], str] = {
    ("mech", "催眠"): "mech.hypnosis",
}


def _unique(values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def build_seed_rows() -> tuple[SeedRow, ...]:
    """构建种子行（确定性、幂等；每次返回全新元组）。"""
    rows: list[SeedRow] = []
    seen_ids: set[str] = set()
    for domain_id, domain_label, axes in _SPEC:
        rows.append(SeedRow(
            kind="domain", tag_id=domain_id, axis=None, label=domain_label,
            aliases=(), parent=None, status=SEED_STATUS,
            created_by=SEED_CREATED_BY, created_at=SEED_DATE,
        ))
        seen_ids.add(domain_id)
        for axis_name, values in axes:
            prefix = _AXIS_PREFIX[axis_name]
            axis_id = prefix
            if axis_id in seen_ids:
                raise ValueError(f"轴 id 冲突: {axis_id}")
            rows.append(SeedRow(
                kind="axis", tag_id=axis_id, axis=None, label=axis_name,
                aliases=(), parent=domain_id, status=SEED_STATUS,
                created_by=SEED_CREATED_BY, created_at=SEED_DATE,
            ))
            seen_ids.add(axis_id)
            for value in _unique(values):
                value_id = _VALUE_ID_OVERRIDES.get((prefix, value), f"{prefix}.{value}")
                if value_id in seen_ids:
                    raise ValueError(f"值 id 冲突: {value_id}")
                rows.append(SeedRow(
                    kind="value", tag_id=value_id, axis=axis_name, label=value,
                    aliases=_SEED_ALIASES.get(value_id, ()),
                    parent=axis_id, status=SEED_STATUS,
                    created_by=SEED_CREATED_BY, created_at=SEED_DATE,
                ))
                seen_ids.add(value_id)
    return tuple(rows)