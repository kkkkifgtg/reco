"""工单 09——decay_weight 黄金公式（红测试，S1）。

w = 0.5^(age/half)（F2/§17 Q1）；黄金值冻结自 notes/09-freeze-goldens.py（E3/E4）。
"""
from __future__ import annotations

import math

import pytest

from reco_core.profile import decay_weight


def test_w_at_half_life_is_exactly_half_all_tiers():
    assert decay_weight(90, 90) == 0.5   # 标注档
    assert decay_weight(30, 30) == 0.5   # 行为档
    assert decay_weight(180, 180) == 0.5  # 稳定偏好档


def test_golden_values_frozen():
    assert decay_weight(60, 90) == pytest.approx(0.6299605249474366, abs=1e-12)
    assert decay_weight(15, 90) == pytest.approx(0.8908987181403393, abs=1e-12)
    assert decay_weight(15, 30) == pytest.approx(0.7071067811865476, abs=1e-12)
    assert decay_weight(90, 180) == pytest.approx(0.7071067811865476, abs=1e-12)
    assert decay_weight(270, 90) == pytest.approx(0.125, abs=1e-12)


def test_zero_age_full_weight_and_monotonic():
    assert decay_weight(0, 90) == 1.0
    assert decay_weight(30, 90) > decay_weight(60, 90) > decay_weight(120, 90)


def test_exp_equivalence_and_never_negative():
    for age, half in ((60, 90), (17, 31), (365, 180)):
        assert decay_weight(age, half) == pytest.approx(
            math.exp(-math.log(2) * age / half), abs=1e-15
        )
    assert decay_weight(10_000, 90) > 0.0  # 趋零不归零（C7 口径在公式层的根）
