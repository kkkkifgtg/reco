"""#83——mine_alias_counts：分词挖掘（分词→剔虚词/常用词→词频；正文零落库）。"""
from __future__ import annotations

import pytest

from reco_io.mining import mine_alias_counts
from reco_io.tokenizer import available_engines

# jieba 为可选依赖：缺失时 tokenizer 降级 ngram，切分粒度不同 → 本组断言不跨环境
pytestmark = pytest.mark.skipif(
    not available_engines()["jieba"], reason="jieba 未安装（可选依赖，ngram 兜底切分不同）"
)


def test_mine_counts_content_words_and_filters_stopwords(tmp_path):
    p = tmp_path / "book.txt"
    p.write_text("他催眠了她。催眠的力量强大。调教与驯化交织。的了吗呢。", encoding="utf-8")
    counts = mine_alias_counts(p)
    assert counts["催眠"] == 2
    assert counts["调教"] == 1 and counts["驯化"] == 1
    assert "的" not in counts and "了" not in counts and "他" not in counts
    assert "与" not in counts
    assert all(len(w) >= 2 for w in counts)  # 单字噪声不进候选


def test_mine_unsupported_or_missing_file_returns_empty(tmp_path):
    assert mine_alias_counts(tmp_path / "x.exe") == {}
    assert mine_alias_counts(tmp_path / "nope.txt") == {}


def test_mine_limit_caps_entries(tmp_path):
    p = tmp_path / "book.txt"
    p.write_text("催眠调教驯化奴役药物诅咒寄生契约" * 50, encoding="utf-8")
    counts = mine_alias_counts(p, limit=3)
    assert len(counts) <= 3
