"""工单 06——normalize_tag / match_tags / explain_tag_hit（Q11：v1 只做 alias 精确命中）。"""
from __future__ import annotations

from types import SimpleNamespace

from reco_labels import (
    InMemoryLabelsStore,
    TagHit,
    add_tag,
    explain_tag_hit,
    load_taxonomy,
    match_tags,
    normalize_tag,
    set_aliases,
)
from reco_labels.seed import build_seed_rows

SEED_MATCHABLE = ("mech.hypnosis", "催眠", "精神控制", "洗脑", "mind control")


def _store() -> InMemoryLabelsStore:
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def _taxonomy(store=None):
    return load_taxonomy(store if store is not None else _store())


# ---------- normalize_tag：精确命中语义 ----------

def test_normalize_by_alias_exact():
    taxonomy = _taxonomy()
    assert normalize_tag("精神控制", taxonomy) == "mech.hypnosis"
    assert normalize_tag("洗脑", taxonomy) == "mech.hypnosis"


def test_normalize_by_tag_id_exact():
    assert normalize_tag("mech.hypnosis", _taxonomy()) == "mech.hypnosis"


def test_normalize_casefold_and_strip():
    taxonomy = _taxonomy()
    assert normalize_tag("  Mind Control  ", taxonomy) == "mech.hypnosis"
    assert normalize_tag("MIND CONTROL", taxonomy) == "mech.hypnosis"


def test_normalize_value_label_is_recognized():
    """#83：值行显示名参与识别（催眠 → mech.催眠）；ASCII 显示名仍不参与。"""
    # 种子 override：值「催眠」的 tag_id 即 mech.hypnosis；显示名参与识别后命中它
    assert normalize_tag("催眠", _taxonomy()) == "mech.hypnosis"
    assert normalize_tag("HE", _taxonomy()) is None  # 纯 ASCII 显示名不参与


def test_normalize_unknown_is_none_no_fuzzy():
    assert normalize_tag("摄心术", _taxonomy()) is None
    assert normalize_tag("", _taxonomy()) is None
    assert normalize_tag("   ", _taxonomy()) is None
    assert normalize_tag("催眠术", _taxonomy()) is None  # 不模糊猜


def test_normalize_user_tag_needs_alias_upgrade():
    """单字显示名的 user 标签不参与匹配（精度护栏）；补 alias 后升级为可识别。

    #83 起 >=2 字 CJK 显示名直接参与识别（见 test_normalize_value_label_is_recognized）；
    本例用单字显示名「缚」验证 alias 升级路径仍然成立。
    """
    store = _store()
    add_tag(store, tag_id="mech.缚", axis="mechanism", label="缚", parent="mech")
    assert normalize_tag("缚", load_taxonomy(store)) is None  # 单字显示名不参与
    set_aliases(store, "mech.缚", ("契约缚",))
    assert normalize_tag("契约缚", load_taxonomy(store)) == "mech.缚"


def test_normalize_resolves_merged_chain():
    store = _store()
    add_tag(store, tag_id="mech.摄魂", axis="mechanism", label="摄魂", parent="mech")
    set_aliases(store, "mech.摄魂", ("soul capture",))
    from reco_labels import merge_tags

    merge_tags(store, "mech.摄魂", "mech.hypnosis")
    taxonomy = load_taxonomy(store)
    assert normalize_tag("mech.摄魂", taxonomy) == "mech.hypnosis"   # 旧 id 重定向
    assert normalize_tag("soul capture", taxonomy) == "mech.hypnosis"  # merged 行别名经链解析


def test_normalize_archived_not_resolvable():
    from reco_labels import archive_tag

    store = _store()
    archive_tag(store, "mech.hypnosis")
    taxonomy = load_taxonomy(store)
    assert normalize_tag("mech.hypnosis", taxonomy) is None   # 归档 id 不再解析
    assert normalize_tag("mind control", taxonomy) is None    # 归档标签专属别名失效
    # #83：同形词改经各自存活的值标签显示名命中
    assert normalize_tag("精神控制", taxonomy) == "mech.精神控制"
    assert normalize_tag("洗脑", taxonomy) == "mech.洗脑"


def test_normalize_colliding_user_alias_deterministic():
    """用户给两个标签设同一 alias：等长平局 → tag_id 字典序最小者胜（bpart < role）。"""
    store = _store()
    set_aliases(store, "role.双性", ("双性属性",))
    set_aliases(store, "bpart.双性", ("双性属性",))
    taxonomy = load_taxonomy(store)
    assert normalize_tag("双性属性", taxonomy) == "bpart.双性"


# ---------- match_tags：鸭子读 .alias_counts，零正文 ----------

def _stats(counts: dict[str, int]):
    return SimpleNamespace(alias_counts=counts)


def test_match_single_tag_merges_alias_counts():
    """手算：精神控制×1 + 洗脑×2 → mech.hypnosis 一个命中，total=3，confidence=1.0。"""
    hits = match_tags(_stats({"精神控制": 1, "洗脑": 2, "mind control": 0}), _taxonomy())
    assert len(hits) == 1
    hit = hits[0]
    assert hit.tag_id == "mech.hypnosis"
    assert hit.confidence == 1.0
    assert hit.basis["total"] == 3
    matches = {m["alias"]: m["count"] for m in hit.basis["matches"]}
    assert matches == {"精神控制": 1, "洗脑": 2}


def test_match_zero_counts_no_hit():
    assert match_tags(_stats({"mind control": 0}), _taxonomy()) == ()
    assert match_tags(_stats({}), _taxonomy()) == ()


def test_match_degraded_stats_no_exception():
    """W4 降级结果 alias_counts={}；无该字段的 stats 也零命中不抛（鸭子契约）。"""
    assert match_tags(SimpleNamespace(alias_counts={}), _taxonomy()) == ()
    assert match_tags(SimpleNamespace(), _taxonomy()) == ()
    assert match_tags({"alias_counts": {"洗脑": 1}}, _taxonomy())  # Mapping 形态也支持


def test_match_deterministic_order():
    store = _store()
    set_aliases(store, "tone.爽", ("爽感",))
    set_aliases(store, "mech.调教", ("调教系",))
    taxonomy = load_taxonomy(store)
    stats = _stats({"爽感": 2, "调教系": 5, "洗脑": 1})
    hits1 = match_tags(stats, taxonomy)
    hits2 = match_tags(stats, taxonomy)
    assert [h.tag_id for h in hits1] == ["mech.hypnosis", "mech.调教", "tone.爽"]
    assert hits1 == hits2


def test_match_merged_alias_redirects_to_target():
    store = _store()
    add_tag(store, tag_id="mech.摄魂", axis="mechanism", label="摄魂", parent="mech")
    set_aliases(store, "mech.摄魂", ("soul capture",))
    from reco_labels import merge_tags

    merge_tags(store, "mech.摄魂", "mech.hypnosis")
    hits = match_tags(_stats({"soul capture": 2}), load_taxonomy(store))
    assert [h.tag_id for h in hits] == ["mech.hypnosis"]
    assert any(e["kind"] == "merged_redirect" for e in hits[0].basis["evidence"])


def test_match_colliding_alias_conflict_recorded_in_basis():
    store = _store()
    set_aliases(store, "role.双性", ("双性属性",))
    set_aliases(store, "bpart.双性", ("双性属性",))
    hits = match_tags(_stats({"双性属性": 3}), load_taxonomy(store))
    assert [h.tag_id for h in hits] == ["bpart.双性"]  # 字典序胜者
    conflict = hits[0].basis["conflict"]
    assert conflict["winner"] == "bpart.双性"
    assert sorted(conflict["candidates"]) == ["bpart.双性", "role.双性"]


def test_match_archived_excluded():
    from reco_labels import archive_tag

    store = _store()
    archive_tag(store, "mech.hypnosis")
    # #83：洗脑改经存活值标签 mech.洗脑 命中；归档标签专属别名失效
    hits = match_tags(_stats({"洗脑": 2}), load_taxonomy(store))
    assert [h.tag_id for h in hits] == ["mech.洗脑"]
    assert match_tags(_stats({"mind control": 2}), load_taxonomy(store)) == ()


# ---------- explain_tag_hit ----------

def test_explain_expands_matches_only_metadata():
    hits = match_tags(_stats({"精神控制": 1, "洗脑": 2}), _taxonomy())
    evidence = explain_tag_hit(hits[0])
    kinds = [(e.kind, e.alias, e.count) for e in evidence]
    assert ("alias_exact", "精神控制", 1) in kinds
    assert ("alias_exact", "洗脑", 2) in kinds
    assert all("正文" not in (e.alias or "") for e in evidence)


def test_explain_conflict_hit_discloses_resolution():
    store = _store()
    set_aliases(store, "role.双性", ("双性属性",))
    set_aliases(store, "bpart.双性", ("双性属性",))
    hits = match_tags(_stats({"双性属性": 3}), load_taxonomy(store))
    evidence = explain_tag_hit(hits[0])
    assert any(e.kind == "alias_conflict_resolved" for e in evidence)


# 【W14 验收修正】W4 集成 seam 测试（test_w4_seam_real_extractor_to_hits）移至根
# tests/test_cross_package_integration.py——reco_io→reco_labels 跨包集成不属本包
# 独立可测契约（ADR-0001；labels 独立 venv 无 reco_io）。match_tags 契约由本文件
# 其余用例（合成 stats）完整覆盖。
