"""#83 诊断——匹配词表饥饿红测试（diagnosing-bugs 阶段 1/2）。

症状（报告人）：大部分小说缺标签、画像只剩单轴。
根因（已核实）：匹配词表 = tag_id + aliases（taxonomy.py 身份键；label 不参与），
而种子 164 行只有 mech.hypnosis 带真实词语别名（精神控制/洗脑/mind control）→
值标签的显示名（催眠/调教/强制/校园…）即使逐字出现在文本统计里也永不命中，
auto_tag 链路与 proposals 新词路径在生产上双双饿死。

本文件钉住修复后的契约（报告人 #83 期望：书应有标签，自动打标可用）：
- 值行（tag_id 含 "."）的显示名参与匹配：len>=2 且含 CJK；
- 单字显示名（攻/受/长/爽）与纯 ASCII 显示名（HE/BE）不参与
  （子串探针精度不可接受，走人工/提案路径）；
- 同名显示名跨轴冲突仍走「最长者胜/字典序平局」既有裁定。
"""
from types import SimpleNamespace

from reco_labels import InMemoryLabelsStore, load_taxonomy, match_tags
from reco_labels.seed import build_seed_rows
from reco_labels.auto_tag import aliases_for_match, auto_tag_book


def _seeded() -> InMemoryLabelsStore:
    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return store


def _stats(counts) -> SimpleNamespace:
    return SimpleNamespace(alias_counts=counts)


# ---------------- match 层 ----------------

def test_value_label_word_hits_tag():
    """显示名「催眠/调教」逐字出现在统计里 → 命中对应值标签（修复前永不命中）。"""
    store = _seeded()
    hits = match_tags(_stats({"催眠": 6, "调教": 4}), load_taxonomy(store))
    # 种子 override：值「催眠」的 tag_id 是 mech.hypnosis（seed.py _VALUE_ID_OVERRIDES）
    assert {h.tag_id for h in hits} == {"mech.hypnosis", "mech.调教"}


def test_single_char_label_still_not_matchable():
    """单字显示名（攻/受/长/爽）不作子串探针——精度不可接受，维持人工/提案路径。"""
    store = _seeded()
    hits = match_tags(_stats({"攻": 9, "受": 8, "长": 7, "爽": 6}), load_taxonomy(store))
    assert hits == ()


def test_ascii_label_not_matchable():
    """纯 ASCII 显示名（HE/BE）不做子串匹配（"the/when" 会误命中）。"""
    store = _seeded()
    hits = match_tags(_stats({"he": 5, "be": 4}), load_taxonomy(store))
    assert hits == ()


def test_duplicate_label_across_axes_adjudicated():
    """「双性」同属 role/bpart 两轴值：冲突裁定=等长取 tag_id 字典序最小（bpart.双性）。"""
    store = _seeded()
    hits = match_tags(_stats({"双性": 3}), load_taxonomy(store))
    assert {h.tag_id for h in hits} == {"bpart.双性"}
    assert hits[0].basis["conflict"]["candidates"] == ["bpart.双性", "role.双性"]


# ---------------- probe 词表层 ----------------

def test_probe_vocabulary_includes_value_labels():
    """自动识别词表必须与匹配身份键一致：含值行显示名，不含单字/ASCII 显示名。"""
    store = _seeded()
    vocab = aliases_for_match(store)
    assert "催眠" in vocab and "调教" in vocab and "校园" in vocab
    assert "攻" not in vocab and "HE" not in vocab


# ---------------- auto_tag 链路层 ----------------

def test_liked_book_auto_tagged_via_label_words():
    """liked 书文本含标签显示名 → 自动落 book_tags(source=auto)（报告人症状的直接反转）。"""
    store = _seeded()
    outcome = auto_tag_book(store, 7, "liked", _stats({"催眠": 6, "调教": 4}),
                            now="2026-11-21T00:00:00")
    # 催眠→mech.hypnosis（seed override）、调教→mech.调教
    assert set(outcome.inserted) == {"mech.hypnosis", "mech.调教"}
    rows = [r for r in store.iter_book_tags() if r.book_id == 7]
    assert {r.tag_id for r in rows} == {"mech.hypnosis", "mech.调教"}
    assert all(r.source == "auto" for r in rows)
