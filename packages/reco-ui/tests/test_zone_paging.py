"""#75 回归：专区列表不得静默截断——必须暴露真实总数与已显示数量。

修复前：candidate 有 1529 本，界面只显示 200 本，无任何提示。
修复后：行数据之外提供 total/truncated 信息，界面可明示「显示 200 / 共 1529」。
"""
from __future__ import annotations

import pytest

NOW = "2026-11-22T13:00:00+00:00"
_SEQ = {"n": 5000}


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store
    s = open_store(tmp_path / "lim.db")
    yield s
    s.close()


def _add(store, zone, name=None):
    _SEQ["n"] += 1
    ino = _SEQ["n"]
    name = name or f"书{ino}.txt"
    store.append_event("file_added", payload={
        "path": f"F:/data/{zone}/{name}", "zone": zone, "ext": ".txt",
        "size_bytes": 10, "mtime_ns": ino, "st_dev": 1, "st_ino": ino,
        "display_name": name, "normalized_name": name, "dedup_key": f"k{ino}"},
        occurred_at=NOW, config_fingerprint="t/1", actor="user")


def test_zone_meta_reports_total_and_truncation(store):
    """total 必须是库内真实数量，truncated 反映是否被截断。"""
    from reco_ui.glue import zone_meta

    for i in range(5):
        _add(store, "candidate")

    meta = zone_meta(store, "candidate", limit=2)
    assert meta["total"] == 5, f"总数应为 5，实际 {meta['total']}"
    assert meta["shown"] == 2, f"已显示应为 2，实际 {meta['shown']}"
    assert meta["truncated"] is True, "5 > 2 应标记截断"


def test_zone_meta_not_truncated_when_all_fit(store):
    """全部能显示时 truncated 为 False。"""
    from reco_ui.glue import zone_meta

    for i in range(3):
        _add(store, "liked")

    meta = zone_meta(store, "liked", limit=100)
    assert meta["total"] == 3
    assert meta["shown"] == 3
    assert meta["truncated"] is False


def test_zone_page_rows_accepts_offset(store):
    """offset 支持翻页：第二页不得与第一页重复。"""
    from reco_ui.pages import zone_page_rows

    for i in range(6):
        _add(store, "candidate", name=f"书{i:02d}.txt")

    page1 = zone_page_rows(store, "candidate", limit=3, lib_ms=99999)
    page2 = zone_page_rows(store, "candidate", limit=3, offset=3, lib_ms=99999)

    assert len(page1) == 3
    assert len(page2) == 3
    ids1 = {r[0] for r in page1}
    ids2 = {r[0] for r in page2}
    assert not (ids1 & ids2), "两页不得有重叠"
