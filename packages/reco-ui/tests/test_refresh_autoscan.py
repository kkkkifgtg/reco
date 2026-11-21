"""N1——刷新按钮 + 定时自动扫描（UI 接线，offscreen）。

验收口径（任务 t1）：刷新按钮存在 + 点击落库 + 表格更新；
QTimer 默认关、scan_interval_s>0 才启动；后台重扫零阻塞（池线程）。
真实文件 + 真实 os.stat（编码契约在纯层测试钉死，此处测接线）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from reco_store import open_store


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def store(tmp_path):
    s = open_store(tmp_path / "n1.db")
    yield s
    s.close()


@pytest.fixture()
def data_root(tmp_path) -> Path:
    root = tmp_path / "data"
    for zone in ("liked", "candidate"):
        (root / zone).mkdir(parents=True)
    return root


def _write(root: Path, zone: str, name: str) -> Path:
    p = root / zone / name
    p.write_bytes(b"hello")
    return p


def _drain(ms=8000):
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(ms)
    QApplication.processEvents()


# —— 刷新按钮存在 ——

def test_zone_pages_have_refresh_button(qapp, store, data_root):
    from reco_ui.app import MainWindow

    win = MainWindow(store=store, config={"data_root": str(data_root)})
    for pid in ("liked", "candidate"):
        page = win.page(pid)
        assert hasattr(page, "btn_refresh"), f"{pid} 页缺「刷新」按钮"
        assert page.btn_refresh.text() == "刷新"
    assert hasattr(win.page("recommend"), "btn_refresh"), "推荐专区页缺「刷新」按钮"
    win.close()


# —— 点击落库 + 表格更新（真实文件） ——

def test_refresh_click_lands_events_and_updates_table(qapp, store, data_root):
    from reco_ui.app import MainWindow

    _write(data_root, "liked", "b1.txt")
    win = MainWindow(store=store, config={"data_root": str(data_root)})
    _drain()
    page = win.page("liked")
    assert page.row_count() == 0, "未刷新前库中无此书（根因复现：GUI 从不重扫目录）"
    page.btn_refresh.click()
    _drain()
    assert page.row_count() == 1, "刷新后表格必须出现新文件"
    _write(data_root, "liked", "b2.txt")
    page.btn_refresh.click()
    _drain()
    assert page.row_count() == 2
    assert "新增 1" in page._status.text()
    kinds = [e.event_type for e in store.read_events()]
    assert kinds.count("file_added") == 2, "刷新必须走事件落库（Store 单写者）"
    win.close()


def test_refresh_removes_missing_files(qapp, store, data_root):
    from reco_ui.app import MainWindow

    _write(data_root, "liked", "gone.txt")
    win = MainWindow(store=store, config={"data_root": str(data_root)})
    page = win.page("liked")
    page.btn_refresh.click()
    _drain()
    assert page.row_count() == 1
    (data_root / "liked" / "gone.txt").unlink()
    page.btn_refresh.click()
    _drain()
    assert page.row_count() == 0, "文件删除后刷新必须从表格移除"
    assert "移除 1" in page._status.text()
    kinds = [e.event_type for e in store.read_events()]
    assert kinds.count("file_removed") == 1
    win.close()


def test_recommend_page_refresh_lands_events_and_reloads_rank(qapp, store, data_root):
    from reco_ui.app import MainWindow

    (data_root / "推荐专区").mkdir()
    _write(data_root, "推荐专区", "r1.txt")
    win = MainWindow(store=store, config={"data_root": str(data_root)})
    _drain()
    page = win.page("recommend")
    calls: list[str] = []
    orig_start_load = win._start_load

    def spy_start_load(page_id, *a, **k):
        calls.append(str(page_id))
        return orig_start_load(page_id, *a, **k)

    win._start_load = spy_start_load  # 构造期预热已结束：之后的调用只来自刷新回调
    page.btn_refresh.click()
    _drain()
    kinds = [ev.event_type for ev in store.read_events()]
    assert kinds.count("file_added") == 1, "推荐专区页刷新必须走事件落库"
    assert "新增 1" in page._status.text()
    assert calls == ["recommend"], "刷新完成必须触发 rank 唯一装配重绘（A6）"
    # 空画像 + 零向量时 rank_rows 合法为空（rank_only 契约）；表格刷新路径本身不得炸
    assert page.row_count() == 0
    win.close()


# —— 定时自动扫描（默认关） ——

def test_auto_scan_off_by_default(qapp, store, data_root):
    from reco_ui.app import MainWindow

    win = MainWindow(store=store, config={"data_root": str(data_root)})
    assert win._auto_scan is None, "默认必须不启动定时自动扫描"
    win.close()


def test_auto_scan_timer_starts_with_config(qapp, store, data_root):
    from reco_ui.qt import QApplication

    from reco_ui.app import MainWindow

    _write(data_root, "candidate", "c1.txt")
    win = MainWindow(store=store, config={"data_root": str(data_root), "scan_interval_s": 1})
    assert win._auto_scan is not None and win._auto_scan.isActive()
    assert win._auto_scan.interval() == 1000
    win._nav.setCurrentRow(1)  # 切到候选页并装载（loaded 后才会被后台刷新）
    _drain()
    deadline = time.monotonic() + 5.0
    while win.page("candidate").row_count() == 0 and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    assert win.page("candidate").row_count() == 1, "定时扫描必须把新文件落库并更新表格"
    kinds = [e.event_type for e in store.read_events()]
    assert kinds.count("file_added") == 1
    win.close()


def test_auto_scan_skips_when_previous_round_pending(qapp, store, data_root):
    from reco_ui.app import MainWindow

    win = MainWindow(store=store, config={"data_root": str(data_root), "scan_interval_s": 3600})
    win._auto_pending = 1
    win._on_auto_scan()
    assert win._auto_pending == 1, "上轮未完成必须跳过本轮（防重入）"
    win._auto_pending = 0
    win._on_auto_scan()
    assert win._auto_pending == 3, "三个专区各派一个同步 worker"
    _drain()
    assert win._auto_pending == 0, "全部完成后在途计数归零"
    win.close()
