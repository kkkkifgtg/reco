"""工单 04——extract_statistics 手算期望值（红测试，S1–S7）。

期望值唯一来源：.scratch/reco-impl/notes/04-handcomputed-samples.md。
期望值独立手算冻结，绝不调用实现内部函数复算（audit E3/E4）。
"""
from __future__ import annotations

import pytest

from reco_io.features import extract_statistics


def test_S1_char_count_and_cjk_ratio():
    r = extract_statistics("Hi 你好\n \t世界 X")
    assert r["char_count"] == 7
    assert r["cjk_char_count"] == 4
    assert r["cjk_char_ratio"] == pytest.approx(4 / 7, abs=1e-6)


def test_S2_paragraphs():
    r = extract_statistics("第一段三四五。\n\n第二段六。\n\n\n第三段七八九。\n\n")
    assert r["paragraph_count"] == 3
    assert r["avg_paragraph_chars"] == pytest.approx(19 / 3, abs=1e-6)


def test_S3_dialogue_ratio_and_turns():
    text = (
        "他推门进来。\n\n"
        "“你来了。”她说。\n"
        "“嗯。”他应了一声。\n\n"
        "窗外起风了。\n\n"
        "——走吧，天不早了。\n"
    )
    r = extract_statistics(text)
    assert r["char_count"] == 41
    assert r["dialogue_char_ratio"] == pytest.approx(16 / 41, abs=1e-6)
    assert r["dialogue_turn_count"] == 3


def test_S3b_unclosed_quote_ignored():
    r = extract_statistics("他说“你好")
    assert r["char_count"] == 5
    assert r["dialogue_char_ratio"] == 0.0


def test_S3c_quote_across_lines():
    r = extract_statistics("“你好\n朋友”再见")
    assert r["char_count"] == 8
    assert r["dialogue_char_ratio"] == pytest.approx(0.5, abs=1e-9)


def test_S3d_corner_quotes():
    r = extract_statistics("「进来吧。」他说")
    assert r["char_count"] == 8
    assert r["dialogue_char_ratio"] == pytest.approx(0.5, abs=1e-9)


def test_S4_punctuation_family():
    r = extract_statistics("好！行！真的？等等……好吧。")
    assert r["char_count"] == 14
    assert r["punct_count"] == 6
    assert r["punct_density"] == pytest.approx(6 / 14, abs=1e-6)
    assert r["exclamation_ratio"] == pytest.approx(2 / 6, abs=1e-6)
    assert r["question_ratio"] == pytest.approx(1 / 6, abs=1e-6)
    assert r["ellipsis_ratio"] == pytest.approx(2 / 6, abs=1e-6)


def test_S4b_no_punctuation_zero_not_filled():
    r = extract_statistics("纯文字没有标点")
    assert r["punct_count"] == 0
    assert r["punct_density"] == 0.0
    assert r["exclamation_ratio"] == 0.0
    assert r["question_ratio"] == 0.0
    assert r["ellipsis_ratio"] == 0.0


def test_S5_line_stats_nearest_rank_p95():
    lines = ["字" * 4] * 10 + ["字" * 6] * 9 + ["字" * 20]
    r = extract_statistics("\n\n".join(lines))
    assert r["paragraph_count"] == 20
    assert r["line_len_mean"] == pytest.approx(5.7, abs=1e-9)
    assert r["line_len_p95"] == 6  # nearest-rank；线性插值会给 6.7——口径由此钉死


def test_S6_char_freq_topk_tie_by_codepoint():
    r2 = extract_statistics("a a b b c 中中中\n", topk=2)
    assert r2["char_freq"] == (("中", 3), ("a", 2))
    r512 = extract_statistics("a a b b c 中中中\n", topk=512)
    assert r512["char_freq"] == (("中", 3), ("a", 2), ("b", 2), ("c", 1))


def test_S7_repeat_phrase_ratio():
    assert extract_statistics("哈哈哈哈哈")["repeat_phrase_ratio"] == pytest.approx(1.0)
    assert extract_statistics("好好学习天天向上")["repeat_phrase_ratio"] == 0.0
    assert extract_statistics("ababab")["repeat_phrase_ratio"] == pytest.approx(1 / 3, abs=1e-6)


def test_S14_alias_probe_counts():
    r = extract_statistics("他唤她师尊。师尊笑了。", alias_probe=("师尊", "徒儿"))
    assert r["alias_counts"] == {"师尊": 2, "徒儿": 0}  # probe 内每个 alias 都出现：真零记 0（W6 覆盖度语义）
    assert extract_statistics("师尊", alias_probe=())["alias_counts"] == {}


def test_S14b_alias_casefold_nonoverlap():
    assert extract_statistics("ai AI Ai", alias_probe=("AI",))["alias_counts"] == {"AI": 3}
    assert extract_statistics("aaa", alias_probe=("aa",))["alias_counts"] == {"aa": 1}

