"""#79 双击行 → 书号绑定：可见行的 book_id 必须在点击瞬间固定，不得按行号重查列表。

用户报告「点『标为喜欢』把整排/别的书都标记了」。逐条排查后（多选点击、Shift/Ctrl
范围选、推荐页 rank 重复行、book_id_at(-1)、批量写入口）都只影响 1 本；唯一可复现的
「点 A 标 B」机制在这里：

    ZonePage._on_double_clicked 把**行号**交给 BookSourceWorker；worker 线程里重新
    zone_page_rows(store, zone)（默认 limit=200 / offset=0 / 不带搜索关键词）后按行号
    取书。用户搜索过滤后，视图第 0 行是 A，worker 却按未过滤列表取到第 0 行的 B ——
    阅读页打开的是 B；用户在阅读页点「标为喜欢」，被标记的就是 B（他从未选过的书）。

修复契约：双击的书号来自当前模型行（ZoneTableModel.book_id_at），worker 只按
book_id 查源路径，不再做「行号 → 列表」的位置解析。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-23T10:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "rec-rowbind.db")
    yield s
    s.close()


def _seed_candidates(store, tmp_path, names) -> dict:
    """把若干书名落进 candidate 专区，返回 name → book_id。"""
    from reco_store import dedup_key

    zone_dir = tmp_path / "data" / "candidate"
    zone_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in names:
        f = zone_dir / name
        f.write_text("正文内容。", encoding="utf-8")
        st = f.stat()
        store.append_event(
            "file_added",
            payload={
                "path": str(f), "zone": "candidate", "ext": ".txt",
                "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
                "st_dev": 1, "st_ino": hash(name) & 0xFFFF,
                "display_name": name, "normalized_name": name,
                "dedup_key": dedup_key(st.st_size, name),
            },
            occurred_at=NOW, config_fingerprint="t/1", actor="user",
        )
        conn = store.read_conn()
        try:
            bid = conn.execute(
                "SELECT f.book_id FROM files f JOIN books b ON b.book_id = f.book_id"
                " WHERE b.display_name = ? ORDER BY f.book_id DESC LIMIT 1", (name,)).fetchone()[0]
        finally:
            conn.close()
        out[name] = int(bid)
    return out


def _drain(ms: int = 8000) -> None:
    import time

    from reco_ui.qt import QApplication, QThreadPool

    pool = QThreadPool.globalInstance()
    if pool is not None:
        pool.waitForDone(ms)
    app = QApplication.instance()
    for _ in range(40):
        if app is not None:
            app.processEvents()
        time.sleep(0.01)


def test_zone_double_click_opens_displayed_book_after_search(store, tmp_path, qapp):
    """搜索过滤后双击第 0 行 → 打开的必须是该行显示的书（不得漂移到未过滤首行）。"""
    from reco_ui.pages import ZonePage

    names = [f"书{i:02d}.txt" for i in range(1, 26)]
    deep_name = "深藏阿尔法.txt"
    ids = _seed_candidates(store, tmp_path, names + [deep_name])

    page = ZonePage("candidate", store=store, data_root=tmp_path / "data")
    page.load_now()
    _drain()
    assert page._model.rowCount() == 26

    # 未过滤首行 ≠ 搜索命中行 —— 这正是原实现按行号重查列表时漂移的条件
    assert page._model._rows[0][0] == ids["书01.txt"]

    page.search_input.setText("深藏")
    _drain()
    assert page._model.rowCount() == 1, "搜索必须命中唯一一本"
    assert int(page._model._rows[0][0]) == ids[deep_name]

    opened = []
    page.reader_requested.connect(lambda path, bid: opened.append(int(bid)))
    page._on_double_clicked(page._model.index(0, 0))
    _drain()

    assert opened == [ids[deep_name]], (
        f"双击显示的书 {ids[deep_name]}（深藏阿尔法）却打开了 {opened} —— "
        "行号漂移到未过滤列表首行，用户随后『标为喜欢』会标记他从未选中的书")


def test_book_source_worker_resolves_by_book_id_not_by_row(store, tmp_path, qapp):
    """worker 契约：入参是 book_id，结果必须是这本书（与列表位置/偏移无关）。"""
    from reco_ui.pages import BookSourceWorker

    names = [f"候选{i:02d}.txt" for i in range(1, 6)]
    ids = _seed_candidates(store, tmp_path, names)
    target = ids["候选05.txt"]  # 未过滤列表第 4 行

    got = []
    worker = BookSourceWorker(store, "candidate", target)
    worker.signals.done.connect(lambda bid, path, name, err: got.append((int(bid), name, err)))
    worker.run()

    assert got and got[0][0] == target, f"worker 解析到 {got}，期望 book_id={target}"
    assert got[0][1] == "候选05.txt"


def test_zone_model_book_id_at_is_bounds_safe(store, tmp_path, qapp):
    """越界行号一律 None（不得负索引回绕到末行）。"""
    from reco_ui.pages import ZoneTableModel

    model = ZoneTableModel(rows=[(7, "甲.txt", 10, ""), (9, "乙.txt", 12, "")])
    assert model.book_id_at(0) == 7
    assert model.book_id_at(1) == 9
    assert model.book_id_at(-1) is None
    assert model.book_id_at(2) is None
    assert model.book_id_at(None) is None
