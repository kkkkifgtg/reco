"""工单 12 性能验收：model/view 批更新 ≥100 行 <50ms（§11.2 坑 6）+ 首帧骨架口径复核。"""
from __future__ import annotations

import os
import time

os_environ_set = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _rows(n: int):
    from reco_core.score import RankRow, Reason

    return [
        RankRow(book_id=str(i), score=1.0 - i / n, rank=i + 1,
                reasons=(Reason(axis="tag", contribution=0.1, source="similarity"),),
                is_exploration=(i == n - 1))
        for i in range(n)
    ]


def test_150_rows_batch_refresh_under_50ms(qapp):
    """150 行整批 set_rows 刷新 <50ms（§11.2 坑 6 model/view 批更新口径）。"""
    from reco_ui.tables import RankTableModel

    model = RankTableModel([], {})
    names = {str(i): f"book-{i}.txt" for i in range(150)}
    t0 = time.perf_counter()
    model.set_rows(_rows(150), names)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert model.rowCount() == 150
    assert elapsed_ms < 50.0, f"150 行批更新 {elapsed_ms:.1f}ms 超出 <50ms 口径"


def test_first_frame_mark_again_under_500ms(qapp, tmp_path):
    """首帧骨架 <500ms 口径复核（切片 3 页面加入后仍须达标）。"""
    from reco_io import perf as perf_mod
    from reco_store import open_store
    from reco_ui.app import MainWindow
    from reco_ui.glue import seed_books

    s = open_store(tmp_path / "perf.db")
    seed_books(s, books=3, liked=1, candidate=1, now="2026-11-21T04:00:00+00:00")
    prev = perf_mod.perf_enabled
    perf_mod.perf_enabled = True
    try:
        t0 = time.perf_counter()
        win = MainWindow(store=s)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
    finally:
        perf_mod.perf_enabled = prev
    assert win.page_ids() == ["liked", "candidate", "recommend", "profile"]
    assert elapsed_ms < 500.0, f"首帧骨架 {elapsed_ms:.1f}ms 超出 <500ms 口径"
    s.close()
