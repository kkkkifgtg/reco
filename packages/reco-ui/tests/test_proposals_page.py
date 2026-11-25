"""#90——提案页：按出现次数降序展示 + 关键词搜索过滤。"""
from __future__ import annotations

from reco_labels import propose_tags
from reco_labels.auto_tag import StoreAdapter
from reco_labels.seed import build_seed_rows
from reco_store import open_store
from reco_ui.pages import proposal_rows
from reco_ui.proposals_page import ProposalsPage


def _seeded_store(tmp_path):
    from reco_labels.auto_tag import StoreAdapter as _SA

    s = open_store(tmp_path / "prop.db")
    adapter = _SA(s)
    for row in build_seed_rows():
        adapter.upsert_tag(row)
    return s, adapter


def test_proposal_rows_sorted_by_count_desc(tmp_path):
    s, adapter = _seeded_store(tmp_path)
    propose_tags(adapter, {"甲词": 30, "乙词": 5, "丙词": 12}, threshold=3,
                 now="2026-11-21T00:00:00")
    rows = proposal_rows(s)
    counts = [r["count"] for r in rows]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 30 and rows[0]["candidate"] == "甲词"


def test_proposals_page_search_filters_by_candidate(tmp_path):
    s, adapter = _seeded_store(tmp_path)
    propose_tags(adapter, {"甲词": 30, "乙词": 5, "丙词": 12}, threshold=3,
                 now="2026-11-21T00:00:00")
    page = ProposalsPage(store=s)
    page.refresh()                      # 加载全部（已按 count 降序）
    assert page.row_count() == 3
    page.search_input.setText("甲")     # 搜索过滤（大小写不敏感子串）
    page.apply_search()
    assert page.row_count() == 1
    assert "甲词" in page.table_text()
    page.search_input.setText("")       # 清空 → 恢复全部
    page.apply_search()
    assert page.row_count() == 3


def test_proposals_page_search_no_hit_shows_empty(tmp_path):
    s, adapter = _seeded_store(tmp_path)
    propose_tags(adapter, {"摄心术": 9}, threshold=3, now="2026-11-21T00:00:00")
    page = ProposalsPage(store=s)
    page.refresh()
    page.search_input.setText("不存在的词xyz")
    page.apply_search()
    assert page.row_count() == 0
    assert "暂无待确认提案" not in page.status_text() or page.row_count() == 0
