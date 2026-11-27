"""#10 界面面：提案首屏默认折叠人名/停用词，勾选后全量可见、可采纳。

Qt 生命周期由本目录 conftest 守护（QApplication 整场持有 + worker 泄洪）。
"""
from __future__ import annotations

from reco_labels import propose_tags
from reco_labels.auto_tag import StoreAdapter
from reco_labels.seed import build_seed_rows
from reco_store import open_store
from reco_ui.proposals_page import ProposalsPage


def _seeded(tmp_path):
    s = open_store(tmp_path / "noise.db")
    adapter = StoreAdapter(s)
    for row in build_seed_rows():
        adapter.upsert_tag(row)
    return s, adapter


def _legacy(adapter, candidate: str, count: int) -> None:
    """模拟修复前的库：历史行绕过降噪直接插入。"""
    adapter.insert_proposal(
        kind="tag", candidate=candidate,
        payload={"candidate": candidate, "count": count, "threshold": 3},
        created_at="2026-11-21T00:00:00",
    )


def test_proposals_page_folds_noise_until_toggled(tmp_path):
    s, adapter = _seeded(tmp_path)
    _legacy(adapter, "杨延亭", 5643)
    propose_tags(adapter, {"摄心术": 9, "催眠": 5}, threshold=3, now="2026-11-21T00:00:00")

    page = ProposalsPage(store=s)
    page.refresh()
    assert page.row_count() == 2, "首屏只显示两个真候选"
    assert "杨延亭" not in page.table_text()
    assert "已折叠 1 条" in page.status_text()

    page.show_noise.setChecked(True)
    assert page.row_count() == 3
    assert "杨延亭" in page.table_text()
    assert "已折叠" not in page.status_text()


def test_search_still_reaches_folded_noise(tmp_path):
    s, adapter = _seeded(tmp_path)
    _legacy(adapter, "凌皓", 5410)
    page = ProposalsPage(store=s)
    page.refresh()

    page.search_input.setText("凌皓")
    assert page.row_count() == 0, "默认折叠：搜到也不进首屏"
    page.show_noise.setChecked(True)
    page.search_input.setText("凌皓")
    assert page.row_count() == 1 and "凌皓" in page.table_text()


def test_noise_folding_does_not_change_order(tmp_path):
    s, adapter = _seeded(tmp_path)
    propose_tags(adapter, {"甲词": 30, "乙词": 5}, threshold=3, now="2026-11-21T00:00:00")
    _legacy(adapter, "徐明闻", 5844)
    page = ProposalsPage(store=s)
    page.refresh()
    page.show_noise.setChecked(True)
    assert page.row_count() == 3
    assert page.table_text().split()[:2] == ["徐明闻", "5844"], "显示顺序仍是 count 降序"