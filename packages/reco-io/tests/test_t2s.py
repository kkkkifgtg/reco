"""#92——繁体转简体：转换正确性 + 挖掘归一（繁/简同形合并计数）。"""
from __future__ import annotations

from reco_io.t2s import to_simplified


def test_to_simplified_basic():
    assert to_simplified("催眠術") == "催眠术"
    assert to_simplified("頭髮與眼淚") == "头发与眼泪"
    assert to_simplified("簡體") == "简体"


def test_to_simplified_empty_and_fallback():
    assert to_simplified("") == ""
    assert to_simplified("ABC-123") == "ABC-123"


def test_merge_simplified_counts_merges_variants():
    """繁/简同形合并：计数相加，键归一为简体（#92）。"""
    from reco_io.mining import merge_simplified_counts

    merged = merge_simplified_counts({"催眠術": 2, "頭髮": 1, "头发": 1})
    assert merged == {"催眠术": 2, "头发": 2}


def test_merge_simplified_counts_passthrough():
    from reco_io.mining import merge_simplified_counts

    assert merge_simplified_counts({}) == {}
    assert merge_simplified_counts({"校園": 2}) == {"校园": 2}


def test_mine_alias_counts_applies_t2s(tmp_path):
    """挖掘结果中的繁体键已归一为简体（#92）。"""
    from reco_io.mining import mine_alias_counts

    p = tmp_path / "book.txt"
    p.write_text("他研究催眠術。催眠術很深奧。", encoding="utf-8")
    counts = mine_alias_counts(p)
    assert "催眠術" not in counts
    assert "催眠" in counts or "催眠术" in counts
