"""#76 回归：候选池必须能搜索——否则 1329 本被截断的书无法访问。

背景：#75 已让状态栏提示「可用搜索缩小范围」，但当时并无搜索功能（误导用户）。
本票实现搜索框：按书名过滤，能定位到 200 行之外的书。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-22T14:00:00+00:00"
_SEQ = {"n": 9000}


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store
    s = open_store(tmp_path / "search.db")
    yield s
    s.close()


def _add(store, zone, name):
    """落一本书；size 用 ino 保证唯一（同 size+同名会被 dedup 合并成一本书）。"""
    _SEQ["n"] += 1
    ino = _SEQ["n"]
    store.append_event("file_added", payload={
        "path": f"F:/data/{zone}/{name}", "zone": zone, "ext": ".txt",
        "size_bytes": ino, "mtime_ns": ino, "st_dev": 1, "st_ino": ino,
        "display_name": name, "normalized_name": name, "dedup_key": f"k{ino}"},
        occurred_at=NOW, config_fingerprint="t/1", actor="user")


def test_search_filters_by_name(store, qapp):
    """搜索框输入关键词 → 只显示匹配的行。"""
    from reco_ui.pages import ZonePage

    for name in ("剑来.txt", "雪中悍刀行.txt", "剑道独尊.txt"):
        _add(store, "candidate", name)

    page = ZonePage("candidate", store=store, data_root="F:/data")
    page.apply_rows(_rows_for(store, "candidate"), total=3)

    assert page.row_count() == 3, "前置条件：初始应显示全部"

    page.search_input.setText("剑")
    page.apply_search()

    names = [page._model.data(page._model.index(r, 1), 0) for r in range(page.row_count())]
    assert len(names) == 2, f"应只剩 2 本含「剑」的书，实际 {names}"
    assert all("剑" in n for n in names), f"实际 {names}"


def test_search_can_reach_beyond_limit(store, qapp):
    """关键：搜索能定位到 limit(200) 之外的书。"""
    from reco_ui.pages import ZonePage

    # 300 本，第 250 本名字特殊
    for i in range(300):
        name = f"{i:03d}普通书.txt" if i != 250 else "ZZZ特藏书.txt"
        _add(store, "candidate", name)

    page = ZonePage("candidate", store=store, data_root="F:/data")
    page.apply_rows(_rows_for(store, "candidate", limit=200), total=300)
    assert page.row_count() == 200, "前置条件：界面只装 200 行"

    page.search_input.setText("ZZZ特藏")
    page.apply_search()

    assert page.row_count() == 1, f"应搜到 1 本，实际 {page.row_count()}"
    name = page._model.data(page._model.index(0, 1), 0)
    assert "ZZZ特藏" in str(name), f"实际 {name}"


def test_empty_search_restores_full_list(store, qapp):
    """清空搜索框 → 恢复原列表。"""
    from reco_ui.pages import ZonePage

    for name in ("甲.txt", "乙.txt"):
        _add(store, "candidate", name)

    page = ZonePage("candidate", store=store, data_root="F:/data")
    page.apply_rows(_rows_for(store, "candidate"), total=2)

    page.search_input.setText("甲")
    page.apply_search()
    assert page.row_count() == 1

    page.search_input.setText("")
    page.apply_search()
    assert page.row_count() == 2, "清空搜索应恢复全部"


def test_search_no_match_shows_honest_empty(store, qapp):
    """无匹配时诚实说明，不显示残留旧行。"""
    from reco_ui.pages import ZonePage

    _add(store, "candidate", "剑来.txt")
    page = ZonePage("candidate", store=store, data_root="F:/data")
    page.apply_rows(_rows_for(store, "candidate"), total=1)

    page.search_input.setText("不存在的书名XYZ")
    page.apply_search()

    assert page.row_count() == 0, "无匹配应清空"
    assert "未找到" in page._status.text() or "无匹配" in page._status.text(),         f"应诚实提示，实际 {page._status.text()!r}"


def _rows_for(store, zone, limit=200):
    from reco_ui.pages import zone_page_rows
    return zone_page_rows(store, zone, limit=limit, lib_ms=99999)
