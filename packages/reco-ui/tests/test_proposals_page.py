"""#48 红测试：proposals 用户确认 UI（列表 + 采纳/拒绝）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-22T06:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "proposals.db")
    yield s
    s.close()


def _adapter(store):
    from reco_labels.auto_tag import StoreAdapter

    return StoreAdapter(store)


def _seed_proposal(store, candidate: str, count: int) -> None:
    _adapter(store).insert_proposal(
        kind="tag",
        candidate=candidate,
        payload={"candidate": candidate, "count": count, "threshold": 3},
        created_at=NOW,
    )


def test_proposal_rows_reads_pending(store):
    from reco_ui.pages import proposal_rows

    _seed_proposal(store, "催眠", 7)
    rows = proposal_rows(store)
    assert len(rows) == 1
    assert rows[0]["candidate"] == "催眠"
    assert rows[0]["count"] == 7
    assert rows[0]["status"] == "pending"


def test_proposal_rows_empty_is_honest(store):
    from reco_ui.pages import proposal_rows

    assert proposal_rows(store) == []


def test_proposals_page_lists_rows(qapp, store):
    from reco_ui.proposals_page import ProposalsPage

    _seed_proposal(store, "催眠", 7)
    page = ProposalsPage(store)
    page.refresh()
    assert page.row_count() == 1
    assert "催眠" in page.table_text()


def test_proposals_page_empty_state(qapp, store):
    from reco_ui.proposals_page import ProposalsPage

    page = ProposalsPage(store)
    page.refresh()
    assert page.row_count() == 0
    assert "暂无" in page.status_text()


def test_accept_creates_tag_and_marks_accepted(qapp, store):
    from reco_ui.proposals_page import ProposalsPage
    from reco_ui.qt import QApplication, QThreadPool

    _seed_proposal(store, "催眠", 7)
    page = ProposalsPage(store)
    page.refresh()
    page.accept_row(0)
    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()

    adapter = _adapter(store)
    assert any(t.label == "催眠" for t in adapter.iter_tags()), "采纳后应创建标签"
    assert page.row_count() == 0, "采纳后不再出现在 pending 列表"


def test_reject_marks_rejected(qapp, store):
    from reco_ui.proposals_page import ProposalsPage
    from reco_ui.qt import QApplication, QThreadPool

    _seed_proposal(store, "催眠", 7)
    page = ProposalsPage(store)
    page.refresh()
    page.reject_row(0)
    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()

    adapter = _adapter(store)
    assert [r for r in adapter.iter_proposals() if r.status == "pending"] == []
    assert not any(t.label == "催眠" for t in adapter.iter_tags()), "拒绝不得创建标签"
