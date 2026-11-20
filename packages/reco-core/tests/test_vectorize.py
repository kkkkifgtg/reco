"""工单 04——reco_core.vectorize 语料级桶 TF-IDF（红测试，S9/S10）。

金标字面值冻结自 .scratch/reco-impl/notes/04-freeze-literals.py
（venv python + stdlib 独立计算，期望值不依赖实现，E4）。
"""
from __future__ import annotations

import pytest

from reco_core.vectorize import VectorizationResult, char_ngram_bucket_tfidf

S9_SLOTS = {"ab": 88072, "ba": 144826, "aba": 143871, "bab": 55165}


def test_S9_golden_values_real_hash():
    res = char_ngram_bucket_tfidf(["abab", "baba", "abc"], min_df=2)
    assert isinstance(res, VectorizationResult)
    assert res.dim == 4
    idx0, wts0 = res.vectors[0]
    assert list(idx0) == sorted(idx0)
    assert set(idx0) == set(S9_SLOTS.values())
    w1 = dict(zip(idx0, wts0))
    assert w1[S9_SLOTS["ab"]] == pytest.approx(0.6046521283053111, abs=1e-9)
    assert w1[S9_SLOTS["ba"]] == pytest.approx(0.4598535287588349, abs=1e-9)
    assert w1[S9_SLOTS["aba"]] == pytest.approx(0.4598535287588349, abs=1e-9)
    assert w1[S9_SLOTS["bab"]] == pytest.approx(0.4598535287588349, abs=1e-9)
    idx1, wts1 = res.vectors[1]
    w2 = dict(zip(idx1, wts1))
    assert w2[S9_SLOTS["ba"]] == pytest.approx(0.7239481671147272, abs=1e-9)
    assert w2[S9_SLOTS["ab"]] == pytest.approx(0.33205047956690076, abs=1e-9)
    assert w2[S9_SLOTS["aba"]] == pytest.approx(0.4275754496873144, abs=1e-9)
    assert w2[S9_SLOTS["bab"]] == pytest.approx(0.4275754496873144, abs=1e-9)
    assert res.vectors[2] == ((S9_SLOTS["ab"],), (1.0,))
    for (_idx, wts), norm in zip(res.vectors, res.norms):
        assert sum(w * w for w in wts) == pytest.approx(1.0, abs=1e-9)
        assert norm == pytest.approx(1.0, abs=1e-9)


def test_idf_version_deterministic_and_param_sensitive():
    a = char_ngram_bucket_tfidf(["abab", "baba", "abc"], min_df=2)
    b = char_ngram_bucket_tfidf(["abab", "baba", "abc"], min_df=2)
    assert a.idf_version == b.idf_version
    assert a.idf_version
    c = char_ngram_bucket_tfidf(["abab", "baba", "abc"], min_df=1)
    assert c.idf_version != a.idf_version


def test_S10_max_features_truncation_stub_hash():
    stub = {"ab": 9, "ba": 5, "aba": 7, "bab": 3}
    res = char_ngram_bucket_tfidf(
        ["abababab"], min_df=1, max_features=2,
        num_buckets=200_000, hash_fn=stub.get,
    )
    assert res.dim == 2
    idx, wts = res.vectors[0]
    assert set(idx) == {9, 3}  # 总频 ab(4) + 并列 3 中槽号最小者 bab(slot 3)
    w = dict(zip(idx, wts))
    assert w[9] == pytest.approx(0.7509205370498381, abs=1e-9)
    assert w[3] == pytest.approx(0.6603925703979283, abs=1e-9)
    assert res.norms[0] == pytest.approx(1.0, abs=1e-9)


def test_min_df_honest_empty_vector():
    res = char_ngram_bucket_tfidf(["abababab"], min_df=2)
    assert res.dim == 0
    assert res.vectors[0] == ((), ())
    assert res.norms[0] == 0.0
    assert res.idf_version  # 版本仍产出，不静默


def test_empty_corpus_honest():
    res = char_ngram_bucket_tfidf([])
    assert res.vectors == ()
    assert res.norms == ()
    assert res.dim == 0


def test_no_ngram_strings_in_output():
    res = char_ngram_bucket_tfidf(["abab", "baba"], min_df=1)
    for idx, wts in res.vectors:
        assert all(isinstance(i, int) and isinstance(w, float) for i, w in zip(idx, wts))
