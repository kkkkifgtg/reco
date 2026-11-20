"""工单 12 验收：RECO_PERF_TABS=1 自动遍历所有页面后退出（离屏）+ 表格快照导出。"""
from __future__ import annotations

import contextlib
import io
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_perf_tabs_traverses_and_exports(monkeypatch, tmp_path):
    """RECO_PERF_TABS=1：遍历全部页面、各页渲染打点、快照落盘、进程内自动收尾。"""
    monkeypatch.setenv("RECO_PERF_TABS", "1")
    from reco_io import perf as perf_mod

    monkeypatch.setattr(perf_mod, "perf_enabled", True)
    from reco_ui.glue import seed_books
    from reco_store import open_store
    from reco_ui.app import MainWindow, run_tabs

    s = open_store(tmp_path / "tabs.db")
    seed_books(s, books=6, liked=3, candidate=2, now="2026-11-21T04:00:00+00:00")
    snap_path = tmp_path / "tabs-snapshot.json"
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_tabs(MainWindow(store=s), snapshot_path=str(snap_path))
    assert code == 0
    marks = err.getvalue()
    for pid in ("liked", "candidate", "recommend", "profile"):
        assert f"phase=ui.tab.{pid} ms=" in marks, f"缺少 {pid} 遍历打点"
    assert snap_path.exists()
    content = snap_path.read_text(encoding="utf-8")
    assert '"page"' in content and '"rows"' in content
    s.close()
