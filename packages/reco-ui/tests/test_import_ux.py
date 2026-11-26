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


@pytest.mark.skipif(not hasattr(os, "startfile"), reason="os.startfile 仅 Windows")
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

def test_default_data_root_prefers_env(monkeypatch, tmp_path):
    """#113：默认数据根可配置，不再钉死某台机器的绝对路径。"""
    from reco_ui.app import default_data_root

    monkeypatch.setenv("RECO_DATA_ROOT", str(tmp_path))
    assert default_data_root() == str(tmp_path)


def test_default_data_root_falls_back_to_user_dir(monkeypatch):
    from pathlib import Path

    from reco_ui.app import default_data_root

    monkeypatch.delenv("RECO_DATA_ROOT", raising=False)
    assert default_data_root() == str(Path.home() / "reco-data")


def test_mainwindow_injects_data_root(qapp, store, tmp_path, monkeypatch):
    from reco_ui.app import MainWindow, default_data_root

    win = MainWindow(store=store, config={"data_root": str(tmp_path)})
    assert win.page("liked")._data_root == str(tmp_path)
    monkeypatch.delenv("RECO_DATA_ROOT", raising=False)
    win2 = MainWindow(store=store)
    assert win2.page("liked")._data_root == default_data_root()


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

        def __init__(self, s, exploration_count=None):
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

# —— F1 拖拽导入（切片 2）——

def test_drag_handlers_override_and_accept_policy(qapp, tmp_path):
    """红：dragEnterEvent/dropEvent 无 override 时拖拽无效（继承 QWidget 默认=忽略）。"""
    from reco_ui.qt import QWidget
    from reco_ui.pages import ZonePage

    page = ZonePage("liked", store=None, data_root=str(tmp_path))
    assert type(page).dragEnterEvent is not QWidget.dragEnterEvent, "dragEnterEvent 未实现（拖拽无效）"
    assert type(page).dropEvent is not QWidget.dropEvent, "dropEvent 未实现（拖拽无效）"

    from reco_ui.qt import QUrl

    class FakeMime:
        def __init__(self, paths):
            self._urls = [QUrl.fromLocalFile(p) for p in paths]

        def hasUrls(self):
            return bool(self._urls)

        def urls(self):
            return self._urls

    class FakeEvent:
        def __init__(self, paths):
            self._mime = FakeMime(paths)
            self.accepted = False

        def mimeData(self):
            return self._mime

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    ok = FakeEvent([str(_make_src(tmp_path, "a.txt"))])
    page.dragEnterEvent(ok)
    assert ok.accepted, "合法 .txt 拖入必须被接受"
    bad = FakeEvent([str(_make_src(tmp_path, "b.exe"))])
    page.dragEnterEvent(bad)
    assert not bad.accepted, "非法扩展拖入必须被忽略"


def _make_src(tmp_path, name, content="合成正文，不含真实作品。"):
    p = tmp_path / "src" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _drain():
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()


def test_import_places_file_and_persists_file_added(qapp, store, tmp_path):
    """拖入 → place_by_hardlink 落位 → file_added 落库（payload 与 scan 同构）→ 刷新行。"""
    from pathlib import Path

    from reco_ui.pages import ZonePage

    src = _make_src(tmp_path, "novel-a.txt")
    page = ZonePage("liked", store=store, data_root=str(tmp_path / "data"))
    page._start_import([str(src)])
    _drain()
    placed = tmp_path / "data" / "liked" / "novel-a.txt"
    assert placed.exists(), "落位产物必须存在于专区目录（硬链接/复制）"
    conn = store.read_conn()
    try:
        files = conn.execute(
            "SELECT path, zone, size_bytes, mtime_ns, st_dev, st_ino FROM files WHERE present=1").fetchall()
        books = conn.execute(
            "SELECT display_name, normalized_name, dedup_key FROM books").fetchall()
    finally:
        conn.close()
    assert len(files) == 1
    path, zone, size, mtime_ns, st_dev, st_ino = files[0]
    assert Path(path) == placed, "payload.path 必须是专区目录产物实际路径"
    assert zone == "liked"
    st = placed.stat()
    assert (size, mtime_ns, st_dev, st_ino) == (st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino)
    assert len(books) == 1 and books[0][0] == "novel-a.txt"
    assert books[0][2]  # dedup_key 非空（同构契约）
    assert page.row_count() == 1, "导入完成后表格已刷新"


def test_import_rejects_invalid_extension_and_directory(qapp, store, tmp_path):
    """非法扩展与目录拒绝：不落位、不落库，状态提示条数。"""
    from reco_ui.pages import ZonePage

    src = _make_src(tmp_path, "evil.exe")
    (tmp_path / "src" / "adir").mkdir()
    page = ZonePage("candidate", store=store, data_root=str(tmp_path / "data"))
    page._start_import([str(src), str(tmp_path / "src" / "adir")])
    _drain()
    conn = store.read_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM files WHERE present=1").fetchone()[0]
    finally:
        conn.close()
    assert n == 0
    assert "拒绝" in page._status.text() and "2" in page._status.text()


def test_import_twice_same_file_skips_no_duplicate(qapp, store, tmp_path):
    """同文件二次导入：place SKIPPED（W3 指纹判等）→ 不重复落库；状态提示跳过数。"""
    from reco_ui.pages import ZonePage

    src = _make_src(tmp_path, "dup.txt")
    page = ZonePage("liked", store=store, data_root=str(tmp_path / "data"))
    page._start_import([str(src)])
    _drain()
    first_status = page._status.text()
    page._start_import([str(src)])
    _drain()
    conn = store.read_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM files WHERE present=1").fetchone()[0]
    finally:
        conn.close()
    assert n == 1, "指纹判等：重复导入不产生第二行"
    assert "跳过" in page._status.text() and "跳过" not in first_status

