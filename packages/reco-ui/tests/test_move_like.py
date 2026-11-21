"""N2 红相（TDD）：「我喜欢」真移动——文件系统 + db zone + GUI 双表刷新。

契约（工单 19 / N2，用户点名）：
- candidate 专区右键「标为喜欢」→ move_book_to_liked 真移动：
  move_file_across_zones（os.replace 跨目录同卷原子移动；目标冲突确定性改名
  book(1).txt）→ file_moved（path→new_path+zone）落库（zone 变更唯一事件形态）；
- 推荐专区「标为喜欢」同接真移动：按钮点击 → LikeMoveWorker →
  book_id → (源 path, 源 zone) → 源文件移动进 liked/ 目录；推荐行书名 →
  liked/ 现有产物名（book_id 名空间不同，不猜）；
- 「不合口味」维持负样本（不移动文件）；liked 页右键无「标为喜欢」；
- 刷新契约：candidate/liked 页 apply_rows 从 db 重查（行随 zone 实况走）。
逆向验证：删 pages.move_book_to_liked 中的 append_event("file_moved") 调用，
test_zone_move_updates_filesystem_and_db_zone 转红（zone 仍 candidate）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

NOW = "2026-11-21T05:00:00+00:00"


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "move.db")
    yield s
    s.close()


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _seed_physical_candidate(store, tmp_path, name="novel-a.txt", content="合成正文，不含真实作品。"):
    """candidate 目录真实文件 + file_added 落库（payload 与 import/scan 同构）。"""
    from pathlib import Path

    from reco_store import dedup_key, normalize_book_name

    cand = tmp_path / "data" / "candidate"
    cand.mkdir(parents=True, exist_ok=True)
    f = cand / name
    f.write_text(content, encoding="utf-8")
    st = f.stat()
    store.append_event("file_added", payload={
        "path": str(f),
        "zone": "candidate",
        "ext": ".txt",
        "size_bytes": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "st_dev": int(st.st_dev),
        "st_ino": int(st.st_ino),
        "display_name": name,
        "normalized_name": normalize_book_name(name),
        "dedup_key": dedup_key(int(st.st_size), name),
    }, occurred_at=NOW, config_fingerprint="gui/seed/1", actor="system")
    return f


def _read_files(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT path, zone FROM files WHERE present = 1 ORDER BY file_id").fetchall()
    finally:
        conn.close()


def test_zone_move_updates_filesystem_and_db_zone(qapp, store, tmp_path):
    """真移动：os.replace 跨目录 → candidate 无、liked 有 + db zone 变更（验收实况）。"""
    from reco_ui.pages import move_book_to_liked

    src = _seed_physical_candidate(store, tmp_path)
    liked_dir = tmp_path / "data" / "liked"
    assert not liked_dir.exists() or not (liked_dir / src.name).exists()

    move_book_to_liked(store, src, str(tmp_path / "data"))

    assert not src.exists(), "candidate 源文件必须消失（真移动，非硬链接落位）"
    dst = liked_dir / src.name
    assert dst.is_file(), "liked 目录必须出现同名文件"
    rows = _read_files(store)
    assert rows == [(str(dst), "liked")], f"db zone 必须变更为 liked，实况: {rows}"


def test_zone_move_conflict_deterministic_rename(qapp, store, tmp_path):
    """liked 已有同名不同内容 → 确定性改名 book(1).txt，不覆盖（W3 冲突语义对齐）。"""
    from reco_ui.pages import move_book_to_liked

    src = _seed_physical_candidate(store, tmp_path, name="novel-a.txt", content="候选版本。")
    liked_dir = tmp_path / "data" / "liked"
    liked_dir.mkdir(parents=True, exist_ok=True)
    existing = liked_dir / "novel-a.txt"
    existing.write_text("liked 版本。", encoding="utf-8")

    move_book_to_liked(store, src, str(tmp_path / "data"))

    assert not src.exists()
    moved = liked_dir / "novel-a(1).txt"
    assert moved.is_file(), "同名冲突必须改名落位"
    assert existing.read_text(encoding="utf-8") == "liked 版本。", "已有文件不得被覆盖"
    rows = _read_files(store)
    assert rows == [(str(moved), "liked")]
    events = _read_events(store)
    assert events[0][0] == "file_moved" and events[0][1] == str(src) and events[0][2] == str(moved) and events[0][3] == "liked"


def _read_events(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT event_type, payload_json FROM events ORDER BY event_id").fetchall()
    finally:
        conn.close()


def test_zone_move_moves_row_between_tables(qapp, store, tmp_path):
    """GUI 双表同步：candidate 行消失、liked 行出现（apply_rows 契约刷新）。"""
    from reco_ui.pages import ZonePage

    _seed_physical_candidate(store, tmp_path)
    cand = ZonePage("candidate", store, data_root=str(tmp_path / "data"))
    liked = ZonePage("liked", store, data_root=str(tmp_path / "data"))
    cand.load_now()
    liked.load_now()
    assert cand.row_count() == 1 and liked.row_count() == 0

    ok = cand.move_selected_book_to_liked(0)
    assert ok, "移动发起必须成功"
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()

    assert cand.row_count() == 0, "candidate 表格行必须消失"
    assert liked.row_count() == 1, "liked 表格行必须出现"
    rows = _read_files(store)
    assert rows[0][1] == "liked"


def test_zone_move_no_selection_is_noop(qapp, store, tmp_path):
    """无选中（空表/越界行）：返回 False，零移动零落库。"""
    from reco_ui.pages import ZonePage

    cand = ZonePage("candidate", store, data_root=str(tmp_path / "data"))
    cand.load_now()
    assert not cand.move_selected_book_to_liked(0)
    assert not (tmp_path / "data" / "liked").exists()
    assert _read_files(store) == []


def test_dislike_keeps_negative_sample_without_move(qapp, store, tmp_path):
    """「不合口味」维持现状：rec_feedback 落库，文件系统与 zone 不动（验收②）。"""
    from reco_ui.glue import record_feedback

    src = _seed_physical_candidate(store, tmp_path)
    record_feedback(store, "dislike", book_id=1, axis="tag", now=NOW)

    assert src.exists(), "负样本不移动文件"
    rows = _read_files(store)
    assert rows == [(str(src), "candidate")], "zone 保持 candidate"


def test_move_worker_reports_error_and_keeps_row(qapp, store, tmp_path, monkeypatch):
    """移动失败：状态条报错、db/文件系统零变化、行保留（诚实失败，不静默）。"""
    import reco_ui.pages as pages
    from reco_ui.pages import ZonePage

    _seed_physical_candidate(store, tmp_path)
    cand = ZonePage("candidate", store, data_root=str(tmp_path / "data"))
    cand.load_now()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(pages.os, "replace", boom)
    ok = cand.move_selected_book_to_liked(0)
    assert ok
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()

    assert "失败" in cand._status.text()
    assert cand.row_count() == 1, "失败后行保留，可重试"
    rows = _read_files(store)
    assert rows == [(str(tmp_path / "data" / "candidate" / "novel-a.txt"), "candidate")]


def test_move_then_move_again_finds_no_source(qapp, store, tmp_path):
    """重复移动防护：源已不在 candidate zone → 返回 False，零副作用。"""
    from reco_ui.pages import move_book_to_liked, ZonePage

    src = _seed_physical_candidate(store, tmp_path)
    cand = ZonePage("candidate", store, data_root=str(tmp_path / "data"))
    cand.load_now()
    assert cand.move_selected_book_to_liked(0)
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()
    # 第二次：db 里唯一文件已 zone=liked，candidate 行消失 → 选行不存在
    assert not cand.move_selected_book_to_liked(0)
    assert _read_files(store) == [(str(tmp_path / "data" / "liked" / src.name), "liked")]


def test_recommend_like_moves_physical_file_from_recommend_zone(qapp, store, tmp_path):
    """推荐专区「标为喜欢」→ 真移动（验收③）：从「推荐专区」目录移入 liked/。"""
    from pathlib import Path

    from reco_store import dedup_key, normalize_book_name
    from reco_ui.app import RecommendPage

    rec_dir = tmp_path / "data" / "推荐专区"
    rec_dir.mkdir(parents=True, exist_ok=True)
    f = rec_dir / "rec-book.txt"
    f.write_text("合成正文，不含真实作品。", encoding="utf-8")
    st = f.stat()
    store.append_event("file_added", payload={
        "path": str(f),
        "zone": "推荐专区",
        "ext": ".txt",
        "size_bytes": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "st_dev": int(st.st_dev),
        "st_ino": int(st.st_ino),
        "display_name": "rec-book.txt",
        "normalized_name": normalize_book_name("rec-book.txt"),
        "dedup_key": dedup_key(int(st.st_size), "rec-book.txt"),
    }, occurred_at=NOW, config_fingerprint="gui/seed/1", actor="system")

    page = RecommendPage(store, data_root=str(tmp_path / "data"))
    page.load_now()
    page.table.selectRow(0)
    page.btn_like.click()
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()

    dst = tmp_path / "data" / "liked" / "rec-book.txt"
    assert not f.exists() and dst.is_file(), "推荐源文件必须真移动进 liked/"
    rows = _read_files(store)
    assert rows == [(str(dst), "liked")], "db zone 变更（推荐专区 → liked）"
    assert page._status.text() == "已记录"


def test_recommend_like_falls_back_to_liked_products_on_name_mismatch(qapp, store, tmp_path):
    """书名在 liked/ 无同名产物 → 回退用 liked/ 现有产物名（book_id 名空间不同，不猜）。"""
    from pathlib import Path

    from reco_store import dedup_key, normalize_book_name
    from reco_ui.app import RecommendPage

    rec_dir = tmp_path / "data" / "推荐专区"
    rec_dir.mkdir(parents=True, exist_ok=True)
    f = rec_dir / "unrelated-name.txt"
    f.write_text("合成正文。", encoding="utf-8")
    st = f.stat()
    store.append_event("file_added", payload={
        "path": str(f),
        "zone": "推荐专区",
        "ext": ".txt",
        "size_bytes": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "st_dev": int(st.st_dev),
        "st_ino": int(st.st_ino),
        "display_name": "unrelated-name.txt",
        "normalized_name": normalize_book_name("unrelated-name.txt"),
        "dedup_key": dedup_key(int(st.st_size), "unrelated-name.txt"),
    }, occurred_at=NOW, config_fingerprint="gui/seed/1", actor="system")

    liked_dir = tmp_path / "data" / "liked"
    liked_dir.mkdir(parents=True, exist_ok=True)
    (liked_dir / "some-liked-product.txt").write_text("已有 liked 产物。", encoding="utf-8")

    page = RecommendPage(store, data_root=str(tmp_path / "data"))
    page.load_now()
    page.table.selectRow(0)
    page.btn_like.click()
    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(8000)
    QApplication.processEvents()

    dst = liked_dir / "some-liked-product.txt"
    assert dst.is_file(), "回退目标必须存在（liked/ 产物名）"
    assert not f.exists(), "推荐源文件仍被真移动（源 zone 不限，推荐源也移动）"
    rows = _read_files(store)
    assert rows == [(str(dst), "liked")]
