"""#10：候选词降噪——人名/停用词不进提案首屏，但数据一行不删、排序不变。

修复前：proposals 51,546 行按 count 降序，首屏被「徐明闻 5844 / 杨延亭 5643 /
凌皓 5410」这类人名占满；#90 的排序本身没错，是「高频=高价值」在人名上失效。

界面折叠（ProposalsPage）在 packages/reco-ui/tests/test_proposals_noise.py：
Qt 控件的 QApplication 生命周期由该目录 conftest 守护，根 tests 不碰 QWidget。
"""
from __future__ import annotations

from reco_labels import propose_tags
from reco_labels.noise import (
    is_noise_candidate,
    looks_like_person_name,
    partition_candidates,
)
from reco_labels.auto_tag import StoreAdapter
from reco_labels.seed import build_seed_rows
from reco_store import open_store
from reco_store.proposals import pending_proposals
from reco_ui.pages import proposal_rows


def _seeded(tmp_path):
    s = open_store(tmp_path / "noise.db")
    adapter = StoreAdapter(s)
    for row in build_seed_rows():
        adapter.upsert_tag(row)
    return s, adapter


def test_person_names_and_stopwords_are_noise():
    for name in ("徐明闻", "杨延亭", "凌皓", "王强", "欧阳锋"):
        assert looks_like_person_name(name), name
        assert is_noise_candidate(name), name
    for word in ("的", "自己", "没有", "什么"):
        assert is_noise_candidate(word), word


def test_real_terms_are_not_noise():
    # 常见误判面：姓氏开头的普通词、带数字/字母的术语、抽象标签
    for word in ("摄心术", "催眠", "江山", "林间", "血脉觉醒", "系统流", "NTR", "科幻"):
        assert not is_noise_candidate(word), word


def test_partition_keeps_original_order_inside_groups():
    rows = [{"candidate": "甲"}, {"candidate": "徐明闻"}, {"candidate": "乙"}]
    kept, folded = partition_candidates(rows)
    assert [r["candidate"] for r in kept] == ["甲", "乙"]
    assert [r["candidate"] for r in folded] == ["徐明闻"]


def test_new_proposals_no_longer_include_names(tmp_path):
    s, adapter = _seeded(tmp_path)
    written = propose_tags(
        adapter, {"徐明闻": 5844, "摄心术": 12, "自己": 900, "凌皓": 5410},
        threshold=3, now="2026-11-21T00:00:00",
    )
    assert [p.candidate for p in written] == ["摄心术"], "人名/停用词不进提案"
    assert [r["candidate"] for r in pending_proposals(s)] == ["摄心术"]


def test_legacy_rows_stay_readable_and_sorted(tmp_path):
    """历史噪声行不删：读面照旧 count 降序，只多一个 noise 标记。"""
    s, adapter = _seeded(tmp_path)
    # 直接绕过降噪写一行历史数据（模拟修复前的库）
    adapter.insert_proposal(
        kind="tag", candidate="徐明闻",
        payload={"candidate": "徐明闻", "count": 5844, "threshold": 3},
        created_at="2026-11-21T00:00:00",
    )
    propose_tags(adapter, {"摄心术": 12}, threshold=3, now="2026-11-21T00:00:00")
    rows = proposal_rows(s)
    assert [r["candidate"] for r in rows] == ["徐明闻", "摄心术"], "排序语义不变"
    assert [r["noise"] for r in rows] == [True, False]