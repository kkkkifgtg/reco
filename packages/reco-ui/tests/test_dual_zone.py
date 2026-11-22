"""#74 回归：同时存在于多个专区的书，专区列表应给出提示（不擅自处理文件）。

修复前：23 本书同时出现在 liked 与 candidate，界面无任何提示。
修复后：行数据带「另见其他专区」信息，界面备注列可见。
"""
from __future__ import annotations

import pytest

NOW = "2026-11-22T12:00:00+00:00"


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store
    s = open_store(tmp_path / "dual.db")
    yield s
    s.close()


def _add(store, name, zone, ino):
    """同一 dedup_key（同 size+名字）→ 合并为同一 book；不同 zone → 两条 files。"""
    store.append_event("file_added", payload={
        "path": f"F:/data/{zone}/{name}", "zone": zone, "ext": ".txt",
        "size_bytes": 100, "mtime_ns": ino, "st_dev": 1, "st_ino": ino,
        "display_name": name, "normalized_name": name,
        "dedup_key": "same-key"},   # 同一 dedup_key → 同一 book
        occurred_at=NOW, config_fingerprint="t/1", actor="user")


def test_dual_zone_book_flagged_in_zone_rows(store):
    """双区书：zone_rows 返回行应标记「另见」其他专区。"""
    from reco_ui.glue import zone_rows

    _add(store, "双区书.txt", "liked", 1)
    _add(store, "双区书.txt", "candidate", 2)

    liked = zone_rows(store, "liked", limit=10)
    assert len(liked) == 1, "同一 book 在 liked 应只出现一行"
    row = liked[0]
    assert len(row) > 3, "行应带额外信息（另见专区）"
    assert "候选" in str(row[3]) or "candidate" in str(row[3]).lower(), f"实际 {row}"


def test_single_zone_book_has_no_other_zone_note(store):
    """单区书：不应有「另见」标记。"""
    from reco_ui.glue import zone_rows

    _add(store, "单区书.txt", "liked", 3)
    liked = zone_rows(store, "liked", limit=10)
    assert len(liked) == 1
    note = str(liked[0][3]) if len(liked[0]) > 3 else ""
    assert "另见" not in note, f"单区书不应有另见标记，实际 {note}"


def test_dual_zone_does_not_delete_files(store):
    """提示不等于处理：不得删除任何 files 行或文件。"""
    from reco_ui.glue import zone_rows

    _add(store, "双区书.txt", "liked", 4)
    _add(store, "双区书.txt", "candidate", 5)
    zone_rows(store, "liked", limit=10)   # 触发查询

    conn = store.read_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM files WHERE present=1").fetchone()[0]
    finally:
        conn.close()
    assert n == 2, "查询不得改变 files 行数"



def test_zone_page_rows_passes_note_through(store):
    """#74 关键回归：zone_page_rows 必须透传备注标注（此前硬编码空串丢弃）。

    这是真实 bug：glue.zone_rows 算出了「另见」标注，但 zone_page_rows 用
    [(r[0], r[1], r[2], "")] 把它丢掉了 —— 单元测试测 glue 层会通过，
    而 GUI 实际走的是 zone_page_rows，标注永远看不到。
    """
    from reco_ui.pages import zone_page_rows

    _add(store, "双区书.txt", "liked", 6)
    _add(store, "双区书.txt", "candidate", 7)

    rows = zone_page_rows(store, "liked", limit=10, lib_ms=99999)
    assert len(rows) == 1
    assert len(rows[0]) == 4, "行必须是 4 元组"
    assert rows[0][3], "备注不能为空（双区书必须带标注）"
    assert "候选" in rows[0][3], f"实际备注 {rows[0][3]!r}"
