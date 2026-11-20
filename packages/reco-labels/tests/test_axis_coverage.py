"""工单 06——axis_coverage / axis_vector（手算断言 E3；未覆盖轴缺席=权重 0 语义）。"""
from __future__ import annotations

from reco_labels import TagHit, axis_coverage, axis_vector


def _hit(tag_id, confidence=1.0):
    return TagHit(tag_id=tag_id, confidence=confidence,
                  basis={"axis": tag_id.split(".", 1)[0], "matches": (), "total": 1, "evidence": ()})


def test_axis_coverage_hand_computed():
    """手算：5 本书中 mech 轴命中 3 本（书 1/2/4）、tone 轴命中 2 本（书 1/4）→ 3/5 与 2/5。"""
    books = {
        1: (_hit("mech.hypnosis", 1.0), _hit("tone.爽", 0.8)),
        2: (_hit("mech.调教", 0.9),),
        3: (),
        4: (_hit("mech.催眠乙", 1.0), _hit("tone.爽", 0.5)),
        5: (),
    }
    mech = axis_coverage(books, "mech")
    assert (mech.covered, mech.total) == (3, 5)
    tone = axis_coverage(books, "tone")
    assert (tone.covered, tone.total) == (2, 5)
    ending = axis_coverage(books, "ending")
    assert (ending.covered, ending.total) == (0, 5)


def test_axis_coverage_empty_corpus():
    cov = axis_coverage({}, "mech")
    assert (cov.covered, cov.total) == (0, 0)


def test_axis_coverage_result_is_honest_for_sparse():
    """1/N 照实显示——Coverage 只携带数字，无任何放大/隐藏逻辑。"""
    books = {1: (_hit("mech.hypnosis", 1.0),), 2: (), 3: (), 4: (), 5: (), 6: (), 7: ()}
    cov = axis_coverage(books, "mech")
    assert (cov.covered, cov.total) == (1, 7)


def test_axis_vector_uncovered_axes_absent():
    """未覆盖轴缺席（=权重 0 语义）；缺失轴不猜默认值。"""
    weights = {"mech": 0.8, "tone": 0.5, "ending": 0.3}
    hits = (_hit("mech.hypnosis", 1.0), _hit("tone.爽", 0.6))
    vec = axis_vector(hits, weights)
    assert vec == {"mech": 0.8, "tone": 0.3}
    assert "ending" not in vec  # 缺席，而非 0.0 占位——下游以缺席=0 读取


def test_axis_vector_mean_confidence_hand_computed():
    """手算：mech 两命中 conf 0.9/1.0 → 均值 0.95 × 权重 0.8 = 0.76。"""
    weights = {"mech": 0.8, "tone": 0.5}
    hits = (_hit("mech.hypnosis", 0.9), _hit("mech.调教", 1.0), _hit("tone.爽", 0.6))
    vec = axis_vector(hits, weights)
    assert vec["mech"] == 0.76
    assert vec["tone"] == 0.3


def test_axis_vector_empty_hits_empty_dict():
    assert axis_vector((), {"mech": 0.8}) == {}


def test_axis_vector_ignores_unknown_axis_hits():
    """命中携带未知轴（非 weights 键）→ 忽略，绝不臆造权重。"""
    hits = (_hit("mech.hypnosis", 1.0), _hit("mystery.未知", 1.0))
    vec = axis_vector(hits, {"mech": 0.8})
    assert vec == {"mech": 0.8}
