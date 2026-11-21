"""W16 红相：F2 打开文件夹 + F3 开始推荐（TDD；实现前必然转红）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "ux.db")
    yield s
    s.close()


# —— F2 打开文件夹 ——

def test_open_folder_button_on_both_zone_pages(qapp, tmp_path):
    from reco_ui.pages import ZonePage

    for zone in ("liked", "candidate"):
        page = ZonePage(zone, store=None, data_root=str(tmp_path))
        assert page.btn_open_folder.text() == "打开文件夹", f"{zone} 缺打开文件夹按钮"


def test_open_zone_folder_helper_maps_all_three_zones(tmp_path):
    from reco_ui.pages import open_zone_folder

    calls = []
    for zone in ("liked", "candidate", "推荐专区"):
        open_zone_folder(str(tmp_path), zone, opener=calls.append)
    assert calls == [str(tmp_path / "liked"), str(tmp_path / "candidate"), str(tmp_path / "推荐专区")]


def test_open_folder_button_calls_startfile_with_zone_dir(qapp, tmp_path, monkeypatch):
    """点按钮 → os.startfile(专区目录)；目录不存在时 ensure_zones 先创建。"""
    from reco_ui import pages
    from reco_ui.pages import ZonePage

    page = ZonePage("liked", store=None, data_root=str(tmp_path))
    assert not (tmp_path / "liked").exists()  # 前置：目录未建
    calls = []
    monkeypatch.setattr(pages.os, "startfile", lambda p: calls.append(str(p)))
    page.btn_open_folder.click()
    assert calls == [str(tmp_path / "liked")]
    assert (tmp_path / "liked").is_dir()  # ensure_zones 已创建


def test_recommend_page_has_open_folder_button(qapp, store, tmp_path):
    """推荐专区页同样有「打开文件夹」（zone=推荐专区 映射）。"""
    from reco_ui.app import RecommendPage

    page = RecommendPage(store, data_root=str(tmp_path))
    assert page.btn_open_folder.text() == "打开文件夹"


# —— 配置注入 ——

def test_default_data_root_constant():
    from reco_ui.app import DEFAULT_DATA_ROOT

    assert DEFAULT_DATA_ROOT.replace("\\", "/") == "F:/date/adult-reco/data"


def test_mainwindow_injects_data_root(qapp, store, tmp_path):
    from reco_ui.app import MainWindow

    win = MainWindow(store=store, config={"data_root": str(tmp_path)})
    assert win.page("liked")._data_root == str(tmp_path)
    win2 = MainWindow(store=store)
    assert win2.page("liked")._data_root.replace("\\", "/") == "F:/date/adult-reco/data"


# —— F3 开始推荐 ——

def test_recommend_button_on_candidate_page_only(qapp, tmp_path):
    from reco_ui.pages import ZonePage

    liked = ZonePage("liked", store=None, data_root=str(tmp_path))
    cand = ZonePage("candidate", store=None, data_root=str(tmp_path))
    assert not hasattr(liked, "btn_recommend")
    assert cand.btn_recommend.text() == "开始推荐"


def test_start_recommend_triggers_rank_loader_and_switches(qapp, store, tmp_path, monkeypatch):
    """点「开始推荐」→ RankLoader（W13 生产接线复用）启动 + 自动切到推荐专区页。"""
    from reco_ui.app import MainWindow
    from reco_ui.pages import RankLoaderSignals
    from reco_ui.qt import QRunnable

    class FakeRankLoader(QRunnable):
        instances: list = []

        def __init__(self, s):
            super().__init__()
            self.signals = RankLoaderSignals()
            FakeRankLoader.instances.append(self)

        def run(self) -> None:
            pass

    monkeypatch.setattr("reco_ui.app.RankLoader", FakeRankLoader)
    win = MainWindow(store=store, config={"data_root": str(tmp_path)})
    FakeRankLoader.instances.clear()
    win._inflight.clear()  # 首屏预载仍在 in-flight：模拟其完成（坑 2 去重在此期间正确生效）
    win._pages["candidate"].btn_recommend.click()
    from reco_ui.qt import QApplication

    QApplication.processEvents()
    assert len(FakeRankLoader.instances) == 1, "开始推荐必须触发 RankLoader"
    assert win._nav.currentRow() == 2, "完成后自动切到推荐专区页"
    win.close()
