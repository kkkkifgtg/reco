"""#121：GUI 里给一本书改标签（加 / 删 / 人工优先）。

缺陷：标签只能悬停看，右键没有任何入口；写入层为人工行留了位置和保护，
但全仓没有调用方。本文件钉住三件事：
1. 书行右键菜单里有「编辑标签…」；
2. 改动能落库（加 = user 行；删 = auto/user 行），即时反映到悬停；
3. 人工行不会被后续自动打标覆盖，manual 行不能被 GUI 删掉。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-25T00:00:00+00:00"


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "tags.db")
    yield s
    s.close()


def _落书(store, tmp_path, name="带标签书.txt"):
    from reco_store import dedup_key

    f = tmp_path / name
    f.write_text("正文。", encoding="utf-8")
    st = f.stat()
    store.append_event(
        "file_added",
        payload={
            "path": str(f), "zone": "liked", "ext": ".txt",
            "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
            "st_dev": 1, "st_ino": 1, "display_name": name,
            "normalized_name": name, "dedup_key": dedup_key(st.st_size, name),
        },
        occurred_at=NOW, config_fingerprint="t/1", actor="user",
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
    finally:
        conn.close()


def _加标签(store, book_id, tag_id, label, *, source="auto"):
    store.append_event(
        "tag_added",
        payload={"tag_id": tag_id, "axis": tag_id.split(".")[0], "label": label},
        occurred_at=NOW, config_fingerprint="t/1",
    )
    store.append_event(
        "label_set",
        payload={"tag_id": tag_id, "source": source, "confidence": 1.0,
                 "basis_json": "{}"},
        book_id=book_id, occurred_at=NOW, config_fingerprint="t/1", actor="user",
    )


def _source_of(store, book_id, tag_id):
    conn = store.read_conn()
    try:
        row = conn.execute(
            "SELECT source FROM book_tags WHERE book_id = ? AND tag_id = ?",
            (book_id, tag_id),
        ).fetchone()
        return None if row is None else str(row[0])
    finally:
        conn.close()


def _加一个可选的标签(store):
    store.append_event(
        "tag_added",
        payload={"tag_id": "rom.纯爱", "axis": "rom", "label": "纯爱"},
        occurred_at=NOW, config_fingerprint="t/1",
    )


def test_edit_adds_user_tag_and_shows_in_tooltip(store, tmp_path):
    """加标签：落成 user 行，悬停立刻能看到。"""
    from reco_ui.pages import apply_tag_edits, book_tags_display

    book_id = _落书(store, tmp_path)
    _加标签(store, book_id, "mech.催眠术", "催眠术", source="auto")
    _加一个可选的标签(store)

    apply_tag_edits(store, book_id, add=("rom.纯爱",), now=NOW)
    assert _source_of(store, book_id, "rom.纯爱") == "user"
    assert "纯爱" in book_tags_display(store, book_id)


def test_edit_removes_auto_tag(store, tmp_path):
    """删标签：自动打的能删掉。"""
    from reco_ui.pages import apply_tag_edits, book_tags_display

    book_id = _落书(store, tmp_path)
    _加标签(store, book_id, "mech.催眠术", "催眠术", source="auto")

    apply_tag_edits(store, book_id, remove=("mech.催眠术",), now=NOW)
    assert _source_of(store, book_id, "mech.催眠术") is None
    assert "催眠术" not in book_tags_display(store, book_id)


def test_edit_removes_own_user_tag(store, tmp_path):
    """删标签：自己贴的也能删（人工优先不等于不能反悔）。"""
    from reco_ui.pages import apply_tag_edits

    book_id = _落书(store, tmp_path)
    _加标签(store, book_id, "mech.催眠术", "催眠术", source="user")

    apply_tag_edits(store, book_id, remove=("mech.催眠术",), now=NOW)
    assert _source_of(store, book_id, "mech.催眠术") is None


def test_edit_cannot_remove_manual_tag(store, tmp_path):
    """裁定：manual 行（非 GUI 来源的人工权威）不许在界面删，且如实报告。"""
    from reco_ui.pages import apply_tag_edits

    book_id = _落书(store, tmp_path)
    _加标签(store, book_id, "mech.催眠术", "催眠术", source="manual")

    outcome = apply_tag_edits(store, book_id, remove=("mech.催眠术",), now=NOW)
    assert "mech.催眠术" in outcome.blocked
    assert _source_of(store, book_id, "mech.催眠术") == "manual"


def test_auto_tag_cannot_overwrite_user_row(store, tmp_path):
    """人工优先：用户改过的行，后续自动打标不得覆盖。"""
    from reco_ui.pages import apply_tag_edits

    book_id = _落书(store, tmp_path)
    _加一个可选的标签(store)
    apply_tag_edits(store, book_id, add=("rom.纯爱",), now=NOW)
    store.append_event(
        "label_set",
        payload={"tag_id": "rom.纯爱", "source": "auto", "confidence": 0.4,
                 "basis_json": "{}"},
        book_id=book_id, occurred_at=NOW, config_fingerprint="t/1",
    )
    assert _source_of(store, book_id, "rom.纯爱") == "user"


def test_label_cleared_default_still_only_touches_auto(store, tmp_path):
    """老事件（payload 无 sources）语义不变：只清 auto，人工行留下。"""

    book_id = _落书(store, tmp_path)
    _加标签(store, book_id, "mech.催眠术", "催眠术", source="user")
    store.append_event(
        "label_cleared",
        payload={"tag_id": "mech.催眠术"},
        book_id=book_id, occurred_at=NOW, config_fingerprint="t/1",
    )
    assert _source_of(store, book_id, "mech.催眠术") == "user"




def test_recommend_page_menu_offers_tag_edit(store, tmp_path):
    """推荐专区：右键推荐行同样能改标签（推荐是标签错误最刺眼的地方）。"""
    from reco_ui.app import RecommendPage
    from reco_core.score import RankRow, Reason

    book_id = _落书(store, tmp_path)
    page = RecommendPage(store, data_root=str(tmp_path))
    page.apply_rank({
        "rank_rows": [RankRow(book_id=f"b{book_id}", score=0.9, rank=1,
                               reasons=(Reason(axis="tag", contribution=0.5, source="similarity"),),
                               is_exploration=False)],
        "names": {},
    })
    texts = [a.text() for a in page.build_context_menu(book_id).actions()]
    assert "编辑标签…" in texts


def test_zone_context_menu_offers_tag_edit(store, tmp_path):
    """书行右键菜单里有「编辑标签…」（#121 的主症状）。"""
    from reco_ui.pages import ZonePage, zone_page_rows

    book_id = _落书(store, tmp_path)
    page = ZonePage("liked", store)
    page.apply_rows(zone_page_rows(store, "liked"))
    texts = [a.text() for a in page.build_context_menu(book_id).actions()]
    assert "编辑标签…" in texts




def test_dialog_round_trips_checks_to_edit(store, tmp_path):
    """对话框：勾选/取消 → added()/removed() 交给写入层。"""
    from reco_ui.pages import TagEditDialog
    from reco_ui.qt import Qt

    book_id = _落书(store, tmp_path)
    _加标签(store, book_id, "mech.催眠术", "催眠术", source="auto")
    _加一个可选的标签(store)

    dialog = TagEditDialog(store, book_id)
    try:
        for i in range(dialog._list.count()):
            item = dialog._list.item(i)
            tag_id = str(item.data(Qt.ItemDataRole.UserRole))
            if tag_id == "rom.纯爱":
                item.setCheckState(Qt.CheckState.Checked)
            elif tag_id == "mech.催眠术":
                item.setCheckState(Qt.CheckState.Unchecked)
        assert dialog.added() == ("rom.纯爱",)
        assert dialog.removed() == ("mech.催眠术",)
    finally:
        dialog.deleteLater()


def test_tag_edit_choices_mark_current_source(store, tmp_path):
    """对话框数据源：全部标签 + 该书当前来源（manual 标记为不可删）。"""
    from reco_ui.pages import tag_edit_choices

    book_id = _落书(store, tmp_path)
    _加标签(store, book_id, "mech.催眠术", "催眠术", source="manual")
    _加一个可选的标签(store)

    choices = {c.tag_id: c for c in tag_edit_choices(store, book_id)}
    assert choices["mech.催眠术"].source == "manual"
    assert choices["mech.催眠术"].removable is False
    assert choices["rom.纯爱"].source is None
    assert choices["rom.纯爱"].removable is True