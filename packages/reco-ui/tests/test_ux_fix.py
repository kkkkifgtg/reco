"""W18 红相：U1 控制台窗口 / U2 候选页无响应 / U3 按下态反馈 / U4 双缓冲守卫。

U2 复现（排障纪律：先造能红的复现）：_LOAD_LIB_MS=200ms 对真实库规模
过激——0.3s 慢库即每次加载超时 → mark_failed 后无任何重试入口 → 死页。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "ux18.db")
    from reco_ui.glue import seed_books

    seed_books(s, books=6, liked=3, candidate=2, now="2026-11-21T04:00:00+00:00")
    yield s
    s.close()


class SlowStore:
    """0.3s 慢库桩：旧 lib_ms=200ms 下必超时（U2 复现载体）。"""

    def __init__(self, real) -> None:
        self._real = real

    def read_conn(self):
        time.sleep(0.3)
        return self._real.read_conn()

    def __getattr__(self, name):
        return getattr(self._real, name)


class BrokenStore:
    """read_conn 恒失败桩：重试入口机制验证用。"""

    def __init__(self, real) -> None:
        self._real = real

    def read_conn(self):
        raise RuntimeError("database is locked (simulated)")

    def __getattr__(self, name):
        return getattr(self._real, name)


def _drain(ms=8000):
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(ms)
    QApplication.processEvents()


# —— U1 黑色控制台窗口 ——

def test_launcher_defaults_to_pythonw_no_console():
    """bat 默认用 pythonw.exe（无控制台）；python.exe 只在 RECO_PERF/RECO_PERF_TABS 分支。"""
    bat = (REPO / "scripts" / "reco-ui.bat").read_text(encoding="utf-8")
    assert "-m reco_ui" in bat, "启动器必须仍以 python -m reco_ui 启动"
    lines = [l.strip() for l in bat.splitlines()]
    launch = [l for l in lines if "python" in l.lower() and "-m reco_ui" in l]
    assert launch, "找不到启动行"
    main_line = launch[0]
    assert "pythonw.exe" in main_line, f"启动行默认必须是 pythonw.exe：{main_line}"
    # python.exe 分支必须被 RECO_PERF/RECO_PERF_TABS 守卫（打点需要 stdout）
    py_lines = [l for l in lines if "python.exe" in l and "pythonw" not in l]
    assert py_lines, "必须保留 python.exe 分支（RECO_PERF/RECO_PERF_TABS 打点走 stdout）"
    guarded = any(("RECO_PERF" in l) for l in lines if "python.exe" in l and "pythonw" not in l)
    assert guarded, "python.exe 分支必须由 RECO_PERF/RECO_PERF_TABS 守卫"


# —— U2 候选页无响应 / 一直「待加载」 ——

def test_zone_load_survives_300ms_query(qapp, store):
    """复现转红：0.3s 查询在旧 lib_ms=200ms 下必超时 → 死页。修复后必须正常装载。"""
    from reco_ui.app import MainWindow

    win = MainWindow(store=SlowStore(store))
    win._start_load("candidate")
    _drain()
    page = win.page("candidate")
    assert page.loaded is True, "0.3s 查询必须能装载完成（分级超时口径必须覆盖真实库规模）"
    assert page.row_count() == 2
    win.close()


def test_zone_click_shows_loading_feedback(qapp, store):
    """点击后立即有可见反馈：状态行「加载中…」而非停在「待加载」。"""
    from reco_ui.app import MainWindow

    win = MainWindow(store=SlowStore(store))
    win._start_load("candidate")
    page = win.page("candidate")
    assert page._status.text() == "加载中…", f"点击后状态行必须立即可见加载中（当前：{page._status.text()}）"
    _drain()
    win.close()


def test_zone_failure_offers_retry_button(qapp, store):
    """加载失败必须提供「重试」入口（此前 mark_failed 只改文字=死页）。"""
    from reco_ui.app import MainWindow

    win = MainWindow(store=BrokenStore(store))
    win.show()  # offscreen 可显示；isHidden 断言才可靠
    win._start_load(win.page("liked"))
    _drain(3000)
    page = win.page("liked")
    assert not page.btn_retry.isHidden(), "失败后「重试」按钮必须可见"
    assert page.loaded is False  # 坑 3：失败不置位
    win.btn_retry_clicks = 0
    page.reload_requested.connect(lambda: setattr(win, "btn_retry_clicks", win.btn_retry_clicks + 1))
    page.btn_retry.click()
    from reco_ui.qt import QApplication

    QApplication.processEvents()
    assert win.btn_retry_clicks == 1, "点重试必须重新发起加载"
    _drain(3000)
    win.close()


# —— U3 按下态反馈 ——

def test_qss_has_pressed_state(qapp):
    """QSS 必须含 QPushButton:pressed 块（颜色加深/位移）与导航/表格 active 态。"""
    from reco_ui.theme import DARK_QSS

    assert "QPushButton:pressed" in DARK_QSS, "缺按钮按下态样式"
    assert "::item:selected:active" in DARK_QSS or ":active" in DARK_QSS, "缺导航/表格 active 态"


def test_pressed_state_screenshot_pair(qapp, tmp_path):
    """按下态截图对照：同一按钮 up/down 两帧落盘（美观验收）。"""
    from reco_ui.app import MainWindow
    from reco_store import open_store

    s = open_store(tmp_path / "shot18.db")
    from reco_ui.glue import seed_books

    seed_books(s, books=3, liked=1, candidate=1, now="2026-11-21T04:00:00+00:00")
    win = MainWindow(store=s)
    win.resize(1080, 720)
    win.show()
    btn = win.page("candidate").btn_open_folder
    out = tmp_path / "shots"
    out.mkdir()
    btn.setDown(False)
    win.grab().save(str(out / "button-up.png"))
    btn.setDown(True)
    from reco_ui.qt import QApplication

    QApplication.processEvents()
    win.grab().save(str(out / "button-pressed.png"))
    btn.setDown(False)
    assert (out / "button-up.png").exists() and (out / "button-pressed.png").exists()
    assert (out / "button-pressed.png").stat().st_size != (out / "button-up.png").stat().st_size, \
        "按下态两帧必须实际不同（样式生效）"
    win.close()
    s.close()


# —— U4 双缓冲/局部重绘守卫 ——

def test_no_forced_sync_repaint_or_full_viewport_update():
    """Qt 视图默认双缓冲：禁止强制同步重绘（repaint）与整视口更新模式。"""
    src = REPO / "packages" / "reco-ui" / "src" / "reco_ui"
    offenders = []
    for p in sorted(src.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        if ".repaint(" in text:
            offenders.append(f"{p.name}: .repaint(")
        if "FullViewportUpdate" in text:
            offenders.append(f"{p.name}: FullViewportUpdate")
    assert offenders == [], f"存在强制重绘点：{offenders}"
