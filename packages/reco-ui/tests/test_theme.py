"""工单 12 切片 1：集中 QSS 深色主题 + PyQt6 许可隔离层（§11.5/§11.6）。

红相（TDD）：reco_ui.theme / reco_ui.qt / reco_ui.app 尚不存在 → 导入失败转红。
"""
from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pytest

os_environ_set = __import__("os").environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SRC = Path(__file__).resolve().parents[1] / "src" / "reco_ui"


def _py_sources():
    return sorted(SRC.glob("*.py"))


def test_dark_qss_has_design_tokens():
    """§11.6：深色底、统一前景/强调色阶、圆角 6/8px、间距栅格。"""
    from reco_ui.theme import DARK_QSS

    assert "border-radius: 6px" in DARK_QSS
    assert "border-radius: 8px" in DARK_QSS
    # 深色底 + 前景 + 强调色阶（集中一份 QSS，不散落）
    for token in ("#1e1f22", "#2b2d31", "#e6e6e6", "#4f8cff"):
        assert token in DARK_QSS, f"缺少设计色板 token: {token}"
    pads = {int(m) for m in re.findall(r"padding: (\d+)px", DARK_QSS)}
    assert len(pads & {4, 8, 12, 16}) >= 2, "间距栅格必须来自 4/8/12/16"


def test_no_scattered_setstylesheet():
    """§11.6/坑4：全部控件走集中 QSS，theme.py 之外禁止 setStyleSheet 调用。"""
    offenders = []
    for p in _py_sources():
        if p.name == "theme.py":
            continue
        if re.search(r"\.\s*setStyleSheet\s*\(", p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert offenders == []


def test_pyqt6_imports_confined_to_adapter():
    """§11.5：PyQt6 导入只允许出现在薄适配层 reco_ui/qt.py。"""
    offenders = []
    for p in _py_sources():
        if p.name == "qt.py":
            continue
        if re.search(r"(^|\n)\s*(from|import)\s+PyQt6", p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert offenders == []


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_skeleton_and_first_frame_mark(qapp, monkeypatch):
    """三板块导航（喜欢/候选/推荐）+ 画像页；RECO_PERF=1 下首帧骨架打点 <500ms。"""
    monkeypatch.setenv("RECO_PERF", "1")
    from reco_io import perf as perf_mod

    monkeypatch.setattr(perf_mod, "perf_enabled", True)  # 模块导入期快照，测试中显式打开
    from reco_ui.app import MainWindow

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        win = MainWindow(store=None)
    assert win.page_ids() == ["liked", "candidate", "recommend", "profile"]
    labels = {win.page_label(pid) for pid in win.page_ids()}
    assert {"喜欢专区", "候选池专区", "推荐专区", "画像"} <= labels
    marks = err.getvalue()
    assert "phase=ui.first_frame ms=" in marks
    ms = float(re.search(r"phase=ui.first_frame ms=([\d.]+)", marks).group(1))
    assert ms < 500.0, f"首屏骨架 {ms}ms 超出 <500ms 口径（F5）"


def test_launch_reports_cli_fallback_when_pyqt6_missing(monkeypatch, capsys):
    """F4/R14 退路：PyQt6 不可用时 GUI 停用、明确报错并指向 CLI 全功能路径。"""
    import reco_ui

    monkeypatch.setattr(reco_ui, "_pyqt6_available", lambda: False)
    code = reco_ui.main([])
    assert code == 2
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "CLI" in out
