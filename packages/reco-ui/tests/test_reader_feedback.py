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

# #43 / CODING-STANDARDS §2：本文件跨包触达 reco_store，属集成测试，显式标注。
pytestmark = pytest.mark.integration

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


# ---------------------------------------------------------- t3 findings：F1/F2/F3


def _seed_vector(store, book_id, dim=2):
    """给书落向量，让 rank_candidates 能把它排进推荐列表。"""
    import struct

    blob = struct.pack(">2i", 1, dim) + struct.pack(">2d", 0.6, 0.8)
    store.append_event(
        "vector_computed",
        payload={"vector_blob_hex": blob.hex(), "dim": dim,
                 "idf_version": "t/1", "norm": 1.0},
        book_id=book_id, occurred_at=NOW, config_fingerprint="t/1",
    )


def test_like_move_refreshes_recommend_page(qapp, store, tmp_path):
    """F1：阅读页真移动后推荐页必须立即重算——该行从推荐列表消失。

    不依赖切导航或手动刷新（zone_moved 信号 → 主窗重算 recommend）。
    """
    from reco_ui.app import MainWindow
    from reco_store.recommend import rank_candidates

    f, bid = _seed_zone_file(store, tmp_path, "candidate", "rec-moved.txt")
    _seed_vector(store, bid)
    win = MainWindow(store=store, config={"data_root": str(tmp_path / "data")})
    rec = win.page("recommend")
    rec.apply_rank(rank_candidates(store))
    assert rec.row_count() > 0, "前置条件：推荐页应有该书的行"

    reader = win.reader_page
    _open_and_wait(reader, f, bid, from_zone="recommend")
    reader.like_button.click()
    assert _pump(reader, lambda: reader.status_text == "已移到喜欢专区"), reader.status_text

    assert _pump(reader, lambda: rec.row_count() == 0), (
        "推荐页真移动后必须重算并移除该行，实况行数: "
        f"{rec.row_count()}"
    )
    win.close()


def test_like_move_refreshes_recommend_page_despite_inflight_dedup(qapp, store, tmp_path):
    """F1 边界：推荐页 rank 装配在途时的移动也必须补发重算（in-flight 去重不吞请求）。"""
    from reco_ui.app import MainWindow
    from reco_store.recommend import rank_candidates

    f, bid = _seed_zone_file(store, tmp_path, "candidate", "rec-inflight.txt")
    _seed_vector(store, bid)
    win = MainWindow(store=store, config={"data_root": str(tmp_path / "data")})
    rec = win.page("recommend")
    rec.apply_rank(rank_candidates(store))
    assert rec.row_count() > 0

    reader = win.reader_page
    _open_and_wait(reader, f, bid, from_zone="recommend")
    reader.like_button.click()
    assert _pump(reader, lambda: reader.status_text == "已移到喜欢专区"), reader.status_text

    assert _pump(reader, lambda: rec.row_count() == 0), (
        "在途去重不得吞掉重算请求，实况行数: "
        f"{rec.row_count()}"
    )
    win.close()


def test_like_idempotent_noop_does_not_claim_moved(qapp, store, tmp_path):
    """F2：重复点「标为喜欢」（幂等 no-op）文案不得谎称「已移到喜欢专区」。"""
    f, bid = _seed_zone_file(store, tmp_path, "liked", "already.txt", "已在喜欢专区。")
    page = _reader(store, tmp_path)
    _open_and_wait(page, f, bid, from_zone="candidate")
    page.like_button.click()
    assert _pump(page, lambda: page.status_text != ""), page.status_text

    assert "已移到喜欢专区" not in page.status_text, (
        f"幂等 no-op 不得谎称已移动，实况: {page.status_text}"
    )
    assert "已在喜欢专区" in page.status_text, page.status_text
    assert f.exists(), "幂等 no-op 不移动文件"
    conn = store.read_conn()
    try:
        evs = [r[0] for r in conn.execute(
            "SELECT event_type FROM events ORDER BY event_id").fetchall()]
    finally:
        conn.close()
    assert "file_moved" not in evs, f"幂等 no-op 不落移动事件，实况: {evs}"
    page.close_book()


def test_like_without_data_root_degrades_honestly(qapp, store, tmp_path, monkeypatch):
    """F3：data_root 未注入 → 诚实降级：不移动、不在进程 CWD 凭空建 liked/。"""
    from reco_ui.reader_page import ReaderPage

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    f, bid = _seed_zone_file(store, tmp_path, "candidate", "no-root.txt")
    page = ReaderPage(store)  # 未注入 data_root
    _open_and_wait(page, f, bid, from_zone="candidate")
    page.like_button.click()
    assert _pump(page, lambda: page.status_text != ""), page.status_text

    assert "无法移动" in page.status_text or "未配置数据目录" in page.status_text, (
        f"必须诚实说明降级原因，实况: {page.status_text}"
    )
    assert not (cwd / "liked").exists(), "CWD 不得凭空出现 liked/ 目录"
    assert f.exists(), "不得移动源文件"
    conn = store.read_conn()
    try:
        evs = [r[0] for r in conn.execute(
            "SELECT event_type FROM events ORDER BY event_id").fetchall()]
    finally:
        conn.close()
    assert "file_moved" not in evs, f"降级不落移动事件，实况: {evs}"
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





def test_late_reader_request_from_liked_while_open_hides_buttons(qapp, store, tmp_path):
    from reco_ui.qt import QApplication
    """竞态回归（独立审查发现 + captain 实证）：阅读页开着时收到其他专区的
    迟到 reader_requested，显隐必须跟随**本次请求的真实来源**，而非残留的
    _reader_return_widget 旧来源。

    场景：candidate 进入（正确显示）→ 阅读页开着时 emit liked.reader_requested(liked书)
    → 必须隐藏（这本来自 liked）。当前实现靠 _reader_return_widget 反推且其不更新 → 红。
    """
    from reco_ui.app import MainWindow

    lf, lbid = _seed_zone_file(store, tmp_path, "liked", "race-liked.txt")
    cf, cbid = _seed_zone_file(store, tmp_path, "candidate", "race-cand.txt")

    win = MainWindow(store=store, config={"data_root": str(tmp_path / "data")})
    try:
        # 第一次：从 candidate 正常进入 → 显示
        win._reader_return_widget = win._pages.get("candidate")
        win._open_reader(str(cf), cbid)
        QApplication.processEvents()
        assert win.reader_page.feedback_visible() is True

        # 模拟 liked 页迟到回包：阅读页开着时直接 emit（BookSourceWorker 异步窗口）
        win._pages["liked"].reader_requested.emit(str(lf), lbid)
        QApplication.processEvents()
        # 期望：这本书来自 liked → 隐藏。当前实现会用旧来源(candidate) → 红
        assert win.reader_page.feedback_visible() is False, (
            "迟到 reader_requested 必须按本次真实来源(liked)隐藏反馈按钮")

        # 反向：liked 进入后，迟到 candidate 请求 → 必须显示
        win._reader_return_widget = win._pages.get("liked")
        win._open_reader(str(lf), lbid)
        QApplication.processEvents()
        assert win.reader_page.feedback_visible() is False
        win._pages["candidate"].reader_requested.emit(str(cf), cbid)
        QApplication.processEvents()
        assert win.reader_page.feedback_visible() is True, (
            "迟到 reader_requested 必须按本次真实来源(candidate)显示反馈按钮")
    finally:
        win.close()


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
