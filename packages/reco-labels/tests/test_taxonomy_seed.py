"""工单 06——种子分类群：六域 25 轴 133 值，逐字来自原始规格包 tags.md（只读）。"""
from __future__ import annotations

from reco_labels import load_taxonomy
from reco_labels.seed import build_seed_rows
from reco_labels.store import InMemoryLabelsStore

EXPECTED_AXES = frozenset({
    "topology", "power", "consent", "evolution",          # D1
    "role", "body", "archetype", "age", "species",        # D2
    "mechanism", "coercion", "transformation", "body_part", "setting",  # D3
    "explicitness", "pov", "length", "style", "pace",     # D4
    "ending", "tone", "emotion_arc",                      # D5
    "language", "completion", "quality_flag",             # D6
})

EXPECTED_DOMAINS = {
    "d1": "关系结构", "d2": "人物属性", "d3": "核心机制",
    "d4": "呈现方式", "d5": "情绪与结局", "d6": "元信息",
}

# 逐轴值数（从规格包逐行数出，手算断言 E3）
EXPECTED_AXIS_VALUE_COUNTS = {
    "topology": 6, "power": 6, "consent": 5, "evolution": 5,
    "role": 5, "body": 6, "archetype": 11, "age": 4, "species": 5,
    "mechanism": 10, "coercion": 5, "transformation": 6, "body_part": 5, "setting": 7,
    "explicitness": 4, "pov": 3, "length": 4, "style": 6, "pace": 4,
    "ending": 5, "tone": 7, "emotion_arc": 4,
    "language": 3, "completion": 3, "quality_flag": 4,
}


def _seeded() -> InMemoryLabelsStore:
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def test_seed_totals_domains_axes_values():
    rows = build_seed_rows()
    domains = [r for r in rows if r.kind == "domain"]
    axes = [r for r in rows if r.kind == "axis"]
    values = [r for r in rows if r.kind == "value"]
    assert len(domains) == 6
    assert len(axes) == 25
    assert len(values) == 133
    assert len(rows) == 164
    assert len({r.tag_id for r in rows}) == 164  # id 全局唯一


def test_seed_axis_names_exact():
    axes = {r.label for r in build_seed_rows() if r.kind == "axis"}
    assert axes == set(EXPECTED_AXES)


def test_seed_axis_value_counts_match_spec():
    rows = build_seed_rows()
    counts: dict[str, int] = {}
    for r in rows:
        if r.kind == "value":
            assert r.axis in EXPECTED_AXES, r
            counts[r.axis] = counts.get(r.axis, 0) + 1
    assert counts == EXPECTED_AXIS_VALUE_COUNTS


def test_seed_hierarchy_three_tiers():
    rows = build_seed_rows()
    by_id = {r.tag_id: r for r in rows}
    for r in rows:
        if r.kind == "domain":
            assert r.parent is None and r.axis is None
            assert r.label == EXPECTED_DOMAINS[r.tag_id]
        elif r.kind == "axis":
            assert r.parent in EXPECTED_DOMAINS and r.axis is None
        else:  # value
            assert r.axis in EXPECTED_AXES
            parent = by_id[r.parent]
            assert parent.kind == "axis" and parent.label == r.axis


def test_seed_hypnosis_verbatim_from_spec():
    """规格包 JSON 示例逐字：id/label/aliases/parent/status/created_by。"""
    rows = build_seed_rows()
    hyp = next(r for r in rows if r.tag_id == "mech.hypnosis")
    assert hyp.label == "催眠"
    assert hyp.aliases == ("精神控制", "洗脑", "mind control")
    assert hyp.parent == "mech"
    assert hyp.axis == "mechanism"
    assert hyp.status == "active"
    assert hyp.created_by == "user"
    assert hyp.created_at == "2026-11-20"


def test_seed_only_hypnosis_is_matchable():
    """种子只给 hypnosis 配 alias（规格唯一示例）；其余值行 alias 空=暂不自动识别。"""
    rows = build_seed_rows()
    with_alias = [r for r in rows if r.kind == "value" and r.aliases]
    assert [r.tag_id for r in with_alias] == ["mech.hypnosis"]


def test_seed_labels_not_identifiers():
    """标签是显示名，不是机器身份（改 label 不影响历史）——双性/催眠等 label 不入别名。"""
    rows = build_seed_rows()
    for r in rows:
        if r.kind == "value":
            assert r.label not in r.aliases


def test_seed_deterministic():
    assert build_seed_rows() == build_seed_rows()


def test_load_taxonomy_from_store():
    store = _seeded()
    taxonomy = load_taxonomy(store)
    assert len(taxonomy.rows) == 164
    assert taxonomy.by_id("mech.hypnosis").label == "催眠"
    assert taxonomy.by_id("mech.nosuch") is None


def test_seed_conflict_pair_exists_for_user_alias_tests():
    """role 与 body_part 都有值「双性」（跨轴同名值真实存在于种子）。"""
    rows = build_seed_rows()
    shuang = [r for r in rows if r.kind == "value" and r.label == "双性"]
    assert sorted(r.tag_id for r in shuang) == ["bpart.双性", "role.双性"]
