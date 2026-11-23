"""#82 回归（reco-ui 侧）：推荐专区「生成篇数」控件值必须传到 RankLoader。

修复前：MainWindow._start_load 直接 RankLoader(self._store)，generate_spin.value()
从未被读取——控件形同虚设，探索位恒为默认 2 篇。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytestmark = pytest.mark.integration  # 跨包：reco_ui ↔ reco_store（CODING-STANDARDS §2）


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "gen.db")
    yield s
    s.close()


class _RecordingRankLoader:
    """替身：只记录构造参数（不跑 worker，保持 UI 线程零 DB）。"""

    instances: list = []

    def __new__(cls, *args, **kwargs):
        from reco_ui.qt import QRunnable

        class _Fake(QRunnable):
            def __init__(self):
                super().__init__()
                from reco_ui.pages import RankLoaderSignals

                self.signals = RankLoaderSignals()
                self.args = args
                self.kwargs = dict(kwargs)
                _RecordingRankLoader.instances.append(self)

            def run(self) -> None:
                pass

        return _Fake()


def _main_window(qapp, store, tmp_path, monkeypatch):
    from reco_ui.app import MainWindow

    monkeypatch.setattr("reco_ui.app.RankLoader", _RecordingRankLoader)
    win = MainWindow(store=store, config={"data_root": str(tmp_path)})
    win._inflight.clear()  # 首屏预热仍在 in-flight：清空以模拟其完成
    _RecordingRankLoader.instances.clear()
    return win


def _exploration_count_arg(loader) -> int:
    assert "exploration_count" in loader.kwargs, \
        f"RankLoader 未收到 exploration_count（实际 kwargs={loader.kwargs}）"
    return loader.kwargs["exploration_count"]


def test_generate_spin_value_reaches_rank_loader(qapp, store, tmp_path, monkeypatch):
    """红：控件 setValue(5) 后发起加载 → RankLoader 必须收到 5。"""
    from reco_ui.qt import QApplication

    win = _main_window(qapp, store, tmp_path, monkeypatch)
    win._pages["recommend"].generate_spin.setValue(5)
    win._start_load("recommend")
    QApplication.processEvents()
    assert len(_RecordingRankLoader.instances) == 1, "必须发起一次 rank 装配"
    assert _exploration_count_arg(_RecordingRankLoader.instances[0]) == 5
    win.close()


def test_generate_spin_default_is_two(qapp, store, tmp_path, monkeypatch):
    """默认不设置 → 2 篇（§17 Q7，不回归）。"""
    from reco_ui.qt import QApplication

    win = _main_window(qapp, store, tmp_path, monkeypatch)
    win._start_load("recommend")
    QApplication.processEvents()
    assert _exploration_count_arg(_RecordingRankLoader.instances[0]) == 2
    win.close()


def test_generate_spin_boundary_one_and_ten(qapp, store, tmp_path, monkeypatch):
    """边界：1 与 10 都必须原样传递（控件范围 1-10）。"""
    from reco_ui.qt import QApplication

    win = _main_window(qapp, store, tmp_path, monkeypatch)
    page = win._pages["recommend"]
    for value in (1, 10):
        page.generate_spin.setValue(value)
        win._inflight.clear()
        win._start_load("recommend")
        QApplication.processEvents()
    got = [_exploration_count_arg(i) for i in _RecordingRankLoader.instances]
    assert got == [1, 10], f"控件边界值应原样传递，实际 {got}"
    win.close()
