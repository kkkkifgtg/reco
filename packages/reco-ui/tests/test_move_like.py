"""N2 红相（TDD）：「我喜欢」真移动——文件系统 + db zone + 刷新联动。

契约（工单 19 / N2，用户点名）：
- move_book_to_liked 真移动：move_file_across_zones（os.replace 跨目录同卷
  原子移动；目标冲突确定性改名 book(1).txt）→ file_moved（path→new_path+zone）
  落库（zone 变更唯一事件形态）；
- #80：「标为喜欢」入口迁到阅读界面（ReaderPage）——列表层（推荐页按钮、
  候选池右键）已移除；真移动语义、源优先取 candidate 行（PR #78）随之迁移；
- 「不合口味」维持负样本（不移动文件）；
- 刷新契约：移动后 liked/candidate 页从 db 重查（行随 zone 实况走）。
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


def _read_events(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT event_type, payload_json FROM events ORDER BY event_id").fetchall()
    finally:
        conn.close()


def _seed_zone_file(store, tmp_path, zone, name, content="合成正文，不含真实作品。"):
    """在指定专区落真实文件 + file_added（与 import/scan 同构）。同 size+同规范名
    → dedup_key 相同 → 复用同一 book_id，用于构造「同书双区各一份」的真实形态。"""
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

    return ReaderPage(store, data_root=str(tmp_path / "data"))


def _open_and_wait(page, path, book_id, from_zone="candidate"):
    """打开并等首帧装完（加载回调会清状态行，先等它完成）。"""
    page.open_book(str(path), book_id, from_zone=from_zone)
    assert _pump(page, lambda: page.loaded), "open 超时"


def _pump(page, predicate, timeout: float = 15.0) -> bool:
    import time

    from reco_ui.qt import QApplication, QThreadPool

    QThreadPool.globalInstance().waitForDone(15000)
    deadline = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        if predicate():
            return True
        if time.monotonic() > deadline:
            return predicate()


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
    import json as _json

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
    kinds = [e[0] for e in events]
    assert kinds.count("file_moved") == 1, f"恰好一条 file_moved，实况: {kinds}"
    moved_payload = _json.loads(events[kinds.index("file_moved")][1])
    assert moved_payload["path"] == str(src)
    assert moved_payload["new_path"] == str(moved)
    assert moved_payload["zone"] == "liked"


def test_dislike_keeps_negative_sample_without_move(qapp, store, tmp_path):
    """「不合口味」维持现状：rec_feedback 落库，文件系统与 zone 不动（验收②）。"""
    from reco_ui.glue import record_feedback

    src = _seed_physical_candidate(store, tmp_path)
    record_feedback(store, "dislike", book_id=1, axis="tag", now=NOW)

    assert src.exists(), "负样本不移动文件"
    rows = _read_files(store)
    assert rows == [(str(src), "candidate")], "zone 保持 candidate"


def test_move_book_to_liked_with_explicit_book_id_is_zone_checked(qapp, store, tmp_path):
    """显式给 book_id 时仍校验源在 from_zone 内——越区源不移动（no-op 防护）。"""
    from reco_ui.pages import move_book_to_liked

    src, book_id = _seed_zone_file(store, tmp_path, "liked", "in-liked.txt")
    out = move_book_to_liked(store, src, str(tmp_path / "data"),
                             from_zone="candidate", book_id=book_id)
    assert str(out) == str(src), "源不在 from_zone → no-op，不移动"
    assert src.exists()
    assert _read_files(store) == [(str(src), "liked")]


def test_reader_like_moves_file_and_refreshes_zone_pages(qapp, store, tmp_path):
    """#80：阅读页「标为喜欢」真移动 + 专区页重查（行随 zone 实况走）。"""
    from reco_ui.app import MainWindow

    src, book_id = _seed_zone_file(store, tmp_path, "candidate", "moved.txt")
    win = MainWindow(store=store, config={"data_root": str(tmp_path / "data")})
    cand = win.page("candidate")
    liked = win.page("liked")
    cand.load_now()
    liked.load_now()
    assert cand.row_count() == 1 and liked.row_count() == 0

    reader = win.reader_page
    _open_and_wait(reader, src, book_id)
    reader.like_button.click()
    assert _pump(reader, lambda: reader.status_text == "已移到喜欢专区"), reader.status_text

    dst = tmp_path / "data" / "liked" / "moved.txt"
    assert not src.exists() and dst.is_file(), "源文件必须真移动进 liked/"
    rows = _read_files(store)
    assert rows == [(str(dst), "liked")], f"db zone 变更，实况: {rows}"
    # 主窗收到 zone_moved 信号 → 两页重查（worker 内查库）
    assert _pump(reader, lambda: liked.row_count() == 1), "liked 页应重查出行"
    assert cand.row_count() == 0, "candidate 页应重查后为空"
    win.close()


def test_reader_like_on_already_moved_book_is_idempotent_noop(qapp, store, tmp_path):
    """已迁移书重复点击（源已在 liked）→ 幂等 no-op：不重复移动、不落 file_moved。"""
    liked_dir = tmp_path / "data" / "liked"
    liked_dir.mkdir(parents=True, exist_ok=True)
    f, book_id = _seed_zone_file(store, tmp_path, "liked", "already-liked.txt", "已在喜欢专区。")

    page = _reader(store, tmp_path)
    _open_and_wait(page, f, book_id)
    page.like_button.click()
    assert _pump(page, lambda: page.status_text in ("已移到喜欢专区",) or
                 "失败" in page.status_text), page.status_text

    assert f.is_file(), "文件保持在 liked/ 原位"
    evs = [e[0] for e in _read_events(store)]
    assert "file_moved" not in evs, f"不重复落移动事件，实况: {evs}"
    page.close_book()


def test_reader_like_dual_zone_moves_candidate_copy(qapp, store, tmp_path):
    """缺陷2：双区同书（同 book_id，liked+candidate 各一份文件），阅读页点「标为喜欢」，
    必须把 candidate 副本真移动出 candidate/ 进 liked/，而非源查询命中 liked 行被
    move_book_to_liked 判为幂等 no-op（用户所见：书在「我喜欢」里但文件没进 liked/）。"""
    lk, _bid = _seed_zone_file(store, tmp_path, "liked", "dup-book.txt", "同内容。")
    cd, book_id = _seed_zone_file(store, tmp_path, "candidate", "dup-book.txt", "同内容。")

    conn = store.read_conn()
    try:
        bids = conn.execute(
            "SELECT DISTINCT book_id FROM files WHERE present=1").fetchall()
    finally:
        conn.close()
    assert len(bids) == 1, f"两份文件必须共享同一 book_id 才是双区同书，实况 {bids}"

    page = _reader(store, tmp_path)
    _open_and_wait(page, cd, book_id)
    page.like_button.click()
    assert _pump(page, lambda: page.status_text == "已移到喜欢专区"), page.status_text

    assert not cd.exists(), "candidate 副本必须被移出 candidate/（真移动）"
    liked = tmp_path / "data" / "liked"
    liked_names = {p.name for p in liked.iterdir()}
    assert "dup-book.txt" in liked_names, f"原 liked 副本保留，实况 {liked_names}"
    assert "dup-book(1).txt" in liked_names, \
        f"candidate 副本按冲突语义改名进 liked/，实况 {liked_names}"
    rows_db = _read_files(store)
    assert [r for r in rows_db if r[1] == "candidate"] == [], f"不得残留 candidate 行，实况 {rows_db}"
    assert all(z == "liked" for _p, z in rows_db), f"该书应全部落 liked，实况 {rows_db}"
    evs = [e[0] for e in _read_events(store)]
    assert evs.count("file_moved") >= 1, f"必须落 file_moved 事件，实况 {evs}"
    page.close_book()


def test_reader_like_only_affects_current_book(qapp, store, tmp_path):
    """只影响当前阅读的那一本：未选中/其他书的文件原地保留（不整排标记）。"""
    f1, b1 = _seed_zone_file(store, tmp_path, "candidate", "book-one.txt", "第一本。")
    f2, _b2 = _seed_zone_file(store, tmp_path, "candidate", "book-two.txt", "第二本。")

    page = _reader(store, tmp_path)
    _open_and_wait(page, f1, b1)
    page.like_button.click()
    assert _pump(page, lambda: page.status_text == "已移到喜欢专区"), page.status_text

    liked = tmp_path / "data" / "liked"
    assert (liked / "book-one.txt").is_file(), "当前书必须真移动进 liked/"
    assert f2.exists(), "其他书的文件必须原地保留"
    zone_by_path = {p: z for p, z in _read_files(store)}
    assert zone_by_path[str(liked / "book-one.txt")] == "liked"
    assert zone_by_path[str(f2)] == "candidate"
    evs = [e[0] for e in _read_events(store)]
    assert evs.count("file_moved") == 1, f"只落一条 file_moved（只动一本），实况: {evs}"
    page.close_book()
