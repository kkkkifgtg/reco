"""CWE-1333 回归：HTML→文本为线性扫描，病态输入不触发平方级耗时。"""
from __future__ import annotations

import time as _t

from reco_io.features import _html_to_text


def test_blocks_stripped_and_entities_unescaped():
    html = (
        "<html><head><style>p{color:red}</style>"
        "<script>var a = 1;</script></head>"
        "<body><p>你好 &amp; 世界</p></body></html>"
    )
    out = _html_to_text(html)
    assert "var a = 1" not in out
    assert "color:red" not in out
    assert "你好" in out and "世界" in out and "&" in out


def test_unclosed_script_kept_like_regex():
    out = _html_to_text("<p>x</p><script>never closed")
    assert "never closed" in out


def test_empty_tag_angle_brackets_kept():
    out = _html_to_text("a<>b")
    assert "a<>b" in out


def test_pathological_lt_input_is_fast():
    text = "<" * 300_000
    t0 = _t.monotonic()
    out = _html_to_text(text)
    assert _t.monotonic() - t0 < 2.0
    assert out == text
