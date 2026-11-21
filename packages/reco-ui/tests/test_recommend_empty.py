"""T4 红测试：推荐页空态提示（区分「缺向量」与「候选为空」，离屏 QPA）。"""

from __future__ import annotations

import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from reco_core.score import RankRow, Reason

NO_VECTORS = "暂无推荐：库中缺少特征向量，请运行提取（reco_cli extract）"
NO_CANDIDATES = "暂无推荐：候选为空"
NOW = "2026-11-21T00:00:00+00:00"

GOLD_ROWS = [
    RankRow(book_id="b1", score=0.9, rank=1,
            reasons=(Reason(axis="tag", contribution=0.5, source="similarity"),),
            is_exploration=False),
]


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _page():
    from reco_ui.app import RecommendPage

    return RecommendPage()


def test_empty_output_without_vectors_shows_extract_hint(qapp):
    page = _page()
    page.apply_rank({"rank_rows": (), "names": {}, "n_vectors": 0, "n_candidates": 0})
    assert page.row_count() == 0
    assert page.empty_visible() is True
    assert page.empty_text() == NO_VECTORS


def test_empty_output_with_vectors_shows_no_candidates(qapp):
    page = _page()
    page.apply_rank({"rank_rows": [], "names": {}, "n_vectors": 5, "n_candidates": 0})
    assert page.empty_visible() is True
    assert page.empty_text() == NO_CANDIDATES


def test_nonempty_output_hides_empty_hint(qapp):
    page = _page()
    page.apply_rank({"rank_rows": GOLD_ROWS, "names": {"b1": "a.txt"},
                     "n_vectors": 5, "n_candidates": 1})
    assert page.row_count() == 1
    assert page.empty_visible() is False
    assert page.empty_text() == ""


def test_load_now_real_store_applies_empty_state(qapp, tmp_path):
    """生产装配路径（load_now → rank_candidates）同样驱动空态文案。"""
    from reco_store import open_store

    from reco_ui.app import RecommendPage

    store = open_store(tmp_path / "empty.db")
    try:
        store.append_event("file_added", payload={
            "path": "Z:/synthetic/candidate/a.txt", "zone": "candidate", "ext": ".txt",
            "size_bytes": 100, "mtime_ns": 1, "st_dev": 1, "st_ino": 1,
            "display_name": "a.txt", "normalized_name": "a.txt", "dedup_key": "k-empty",
        }, occurred_at=NOW, config_fingerprint="t4/empty/1")
        page = RecommendPage(store)
        page.load_now()
        assert page.row_count() == 0
        assert page.empty_visible() is True
        assert page.empty_text() == NO_VECTORS

        dim = 2
        blob = struct.pack(">2i", 1, 2) + struct.pack(">2d", 0.6, 0.8)
        store.append_event("vector_computed", book_id=1, payload={
            "vector_blob_hex": blob.hex(), "dim": dim, "idf_version": "t4/1", "norm": 1.0,
        }, occurred_at=NOW, config_fingerprint="t4/empty/1")
        page.load_now()
        assert page.row_count() == 1
        assert page.empty_visible() is False
    finally:
        store.close()
