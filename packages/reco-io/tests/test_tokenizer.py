"""多分词器接口（#多分词器）：jieba / pkuseg / 字符 n-gram 三路并存。

用户要求：不同文本的分词方式存在差异，应配置多个分词器；生成词云；
高频词（过滤虚词后）直接入标签表，实现自成长。

设计契约：
- tokenize(text, engine="jieba") -> list[str]：统一入口，引擎可切换
- 三路引擎：jieba（词典+HMM）、pkuseg（领域模型）、ngram（现有字符 2-3gram）
- 引擎不可用时**诚实降级**并说明，不静默假装成功
- 虚词/停用词过滤：代词/助词/连词不入标签候选
"""
from __future__ import annotations

import pytest


def test_tokenize_jieba_available():
    """jieba 引擎可用，能切出有意义的中文词。"""
    from reco_io.tokenizer import tokenize

    words = tokenize("他的精神控制让我无法反抗", engine="jieba")
    assert "精神" in words or "精神控制" in words
    assert all(isinstance(w, str) and w.strip() for w in words)


def test_tokenize_ngram_always_available():
    """字符 n-gram 引擎无外部依赖，必然可用（兜底）。"""
    from reco_io.tokenizer import tokenize

    grams = tokenize("精神控制", engine="ngram")
    assert len(grams) > 0
    assert all(len(g) in (2, 3) for g in grams)


def test_engine_fallback_is_honest():
    """请求不可用引擎时诚实降级，并报告实际用了哪个。"""
    from reco_io.tokenizer import tokenize_with_report

    words, report = tokenize_with_report("测试文本", engine="不存在的引擎")
    assert isinstance(words, list)
    assert report["engine_requested"] == "不存在的引擎"
    assert report["engine_used"] != "不存在的引擎"
    assert report["degraded"] is True


def test_available_engines_reports_truthfully():
    """可用引擎列表必须反映真实安装状态。"""
    from reco_io.tokenizer import available_engines

    engines = available_engines()
    assert "ngram" in engines, "字符 n-gram 是兜底，必须始终可用"
    assert isinstance(engines, dict)


def test_stopwords_filter_function_words():
    """虚词过滤：的/了/是/在 等不应进入候选。"""
    from reco_io.tokenizer import is_function_word

    for w in ("的", "了", "是", "在", "和", "一个", "我们"):
        assert is_function_word(w), f"{w} 应判定为虚词"
    for w in ("精神控制", "催眠", "自愿", "校园"):
        assert not is_function_word(w), f"{w} 不应判定为虚词"
