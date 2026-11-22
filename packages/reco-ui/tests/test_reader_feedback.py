"""#80 契约测试：反馈按钮只在阅读界面出现（从「我喜欢」专区进阅读不显示）。

契约（issue #80）：
- 「标为喜欢」「不合口味」两个入口只在 ReaderPage 出现（列表层已移除）；
- 从「我喜欢」专区（liked）进入阅读 → 两个按钮不显示；
- 从推荐专区/候选池进入阅读 → 两个按钮显示；
- 点「不合口味」→ 写库（rec_feedback，池线程，UI 线程零阻塞）；
- 点「标为喜欢」→ 真移动（文件进 liked/ + db zone 变更），源优先取 candidate 行；
- 按钮 in-flight 期间禁用（连点防护）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-22T12:00:00+00:00"


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "reader-feedback.db")
    yield s
    s.close()


def _seed_zone_file(store, tmp_path, zone, name, content="合成正文，不含真实作品。"):
    """在指定专区落真实文件 + file_added（与 import/scan 同构）。"""
    from pathlib import Path

    from reco_store import dedup_key, normalize_book_name

    d = tmp_path / "data" / zone
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(content, encoding="utf-8")
    st = f.stat()
    store.append_event("file_added", payload={
        "path": str(f), "zone": zone, "ext": f.suffix,
        "size_bytes": int(st.st_size), "mtime_ns": int(st.st_mtime_ns),
        "st_dev": int(st.st_dev), "st_ino": int(st.st_ino),
        "display_name": name, "normalized_name": normalize_book_name(name),
        "dedup_key": dedup_key(int(st.st_size), name),
    }, occurred_at=NOW, config_fingerprint="gui/seed/1", actor="system")
    conn = store.read_conn()
    try:
        book_id = int(conn.execute(
            "SELECT book_id FROM books WHERE dedup_key = ?",
            (dedup_key(int(st.st_size), name),)).fetchone()[0])
    finally:
        conn.close()
    return f, book_id


def _reader(store, tmp_path):
    from reco_ui.reader_page import ReaderPage

    page = ReaderPage(store, data_root=str(tmp_path / "data"))
    return page


def _pump(page, predicate, timeout: float = 15.0) -> bool:
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(15000)
    import time

    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return predicate()


def _open_and_wait(page, path, book_id, from_zone="candidate"):
    """打开并等首帧装完（避免加载回调覆盖反馈状态行）。"""
    page.open_book(str(path), book_id, from_zone=from_zone)
    assert _pump(page, lambda: page.loaded), "open 超时"


def _read_files(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT path, zone FROM files WHERE present = 1 ORDER BY file_id").fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------- 显隐规则


def test_reader_shows_both_buttons_from_candidate(store, tmp_path):
    """从候选池进入阅读 → 「标为喜欢」「不合口味」都显示。"""
    f, bid = _seed_zone_file(store, tmp_path, "candidate", "cand.txt")
    page = _reader(store, tmp_path)
    page.open_book(str(f), bid, from_zone="candidate")
    assert page.like_button.isVisibleTo(page)
    assert page.dislike_button.isVisibleTo(page)
    page.close_book()


def test_reader_shows_buttons_from_recommend(store, tmp_path):
    """从推荐专区进入阅读 → 两个按钮显示。"""
    f, bid = _seed_zone_file(store, tmp_path, "candidate", "rec.txt")
    page = _reader(store, tmp_path)
    page.open_book(str(f), bid, from_zone="recommend")
    assert page.like_button.isVisibleTo(page)
    assert page.dislike_button.isVisibleTo(page)
    page.close_book()


def test_reader_hides_buttons_from_liked(store, tmp_path):
    """从「我喜欢」专区进入阅读 → 两个按钮不显示（#80 排除项）。"""
    f, bid = _seed_zone_file(store, tmp_path, "liked", "liked.txt")
    page = _reader(store, tmp_path)
    page.open_book(str(f), bid, from_zone="liked")
    assert not page.like_button.isVisibleTo(page)
    assert not page.dislike_button.isVisibleTo(page)
    page.close_book()


def test_reader_recomputes_visibility_per_book(store, tmp_path):
    """常驻实例换书复用：每次 open_book 必须重算显隐（liked → candidate）。"""
    lf, lbid = _seed_zone_file(store, tmp_path, "liked", "a.txt")
    cf, cbid = _seed_zone_file(store, tmp_path, "candidate", "b.txt")
    page = _reader(store, tmp_path)
    page.open_book(str(lf), lbid, from_zone="liked")
    assert not page.like_button.isVisibleTo(page)
    page.open_book(str(cf), cbid, from_zone="candidate")
    assert page.like_button.isVisibleTo(page)
    page.close_book()


# ---------------------------------------------------------- 点击生效


def test_dislike_click_writes_rec_feedback(store, tmp_path):
    """点「不合口味」→ rec_feedback 落库（池线程写库，UI 线程零阻塞）。"""
    f, bid = _seed_zone_file(store, tmp_path, "candidate", "dislike.txt")
    page = _reader(store, tmp_path)
    _open_and_wait(page, f, bid)
    page.dislike_button.click()
    assert _pump(page, lambda: page.status_text == "已记录"), page.status_text
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE event_type='rec_feedback'").fetchall()
    finally:
        conn.close()
    assert rows, "应落一条 rec_feedback 事件"
    assert "dislike" in rows[0][0]
    assert f.exists(), "负样本不移动文件"
    page.close_book()


def test_like_click_moves_file_into_liked(store, tmp_path):
    """点「标为喜欢」→ 真移动：文件进 liked/ + db zone 变更。"""
    f, bid = _seed_zone_file(store, tmp_path, "candidate", "like.txt")
    page = _reader(store, tmp_path)
    _open_and_wait(page, f, bid)
    page.like_button.click()
    assert _pump(page, lambda: page.status_text == "已移到喜欢专区"), page.status_text
    dst = tmp_path / "data" / "liked" / "like.txt"
    assert not f.exists() and dst.is_file(), "源文件必须真移动进 liked/"
    assert _read_files(store) == [(str(dst), "liked")]
    page.close_book()


def test_like_click_prefers_candidate_row_when_dual_zone(store, tmp_path):
    """双区同书：移动的是 candidate 那份（PR #78 口径）。"""
    lf, _bid = _seed_zone_file(store, tmp_path, "liked", "dup.txt", "同内容。")
    cf, bid = _seed_zone_file(store, tmp_path, "candidate", "dup.txt", "同内容。")
    conn = store.read_conn()
    try:
        bids = conn.execute(
            "SELECT DISTINCT book_id FROM files WHERE present=1").fetchall()
    finally:
        conn.close()
    assert len(bids) == 1, "两份文件必须共享同一 book_id"
    page = _reader(store, tmp_path)
    _open_and_wait(page, cf, bid)
    page.like_button.click()
    assert _pump(page, lambda: page.status_text == "已移到喜欢专区"), page.status_text
    assert not cf.exists(), "candidate 副本必须被移出"
    assert lf.exists(), "liked 原件保留"
    assert all(z == "liked" for _p, z in _read_files(store))
    page.close_book()


def test_like_click_without_source_degrades_honestly(store, tmp_path):
    """无源文件（外部删除）→ 诚实降级：报错、不移动、不落移动事件。"""
    f, bid = _seed_zone_file(store, tmp_path, "candidate", "gone.txt")
    store.append_event("file_removed", payload={"path": str(f)},
                       occurred_at=NOW, config_fingerprint="gui/seed/1", actor="user")
    page = _reader(store, tmp_path)
    _open_and_wait(page, f, bid)
    page.like_button.click()
    assert _pump(page, lambda: "失败" in page.status_text), page.status_text
    assert not (tmp_path / "data" / "liked").exists(), "无源绝不猜目标移动"
    page.close_book()


# ---------------------------------------------------------- 列表层入口已移除


def test_recommend_page_has_no_like_dislike_buttons(store):
    """推荐页不再提供「标为喜欢」「不合口味」（只留「加入候选池」）。"""
    from reco_ui.app import RecommendPage

    page = RecommendPage(store)
    assert not hasattr(page, "btn_like")
    assert not hasattr(page, "btn_dislike")
    assert page.btn_pool.text() == "加入候选池"
    page.deleteLater()


def test_zone_context_menu_has_no_feedback_actions(store):
    """专区右键菜单不再有「标为喜欢」「不合口味」。"""
    from reco_ui.pages import ZonePage

    for zone in ("candidate", "liked"):
        page = ZonePage(zone, store)
        menu = page.build_context_menu()
        labels = [a.text() for a in menu.actions()]
        assert "标为喜欢" not in labels
        assert "不合口味" not in labels
        menu.deleteLater()
        page.deleteLater()
