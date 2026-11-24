"""#73 契约测试：阅读行为信号真落 signals 表（open/close/读完）。

契约（issue #73 + ARCHITECTURE.md §signals）：
- 打开阅读页并开始阅读 → signals 出现 reopen；
- 关闭阅读页 → signals 出现 dwell（value = 本次停留秒数）与 read_time；
- 读到末尾（percent >= 0.95）→ signals 出现 completion（每会话一条）；
- 频率控制：长时间停留不产生每秒一条的信号（dwell/read_time 每会话各一条）；
- 载荷匿名且只经事件流落库：payload 键 ⊆ {kind,value,source,session_id}，
  source = gui，session_id 不含设备信息。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

# #43 / CODING-STANDARDS §2：本文件跨包触达 reco_store，属集成测试，显式标注。
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "reader-signals.db")
    yield s
    s.close()


def _seed_candidate_file(store, tmp_path, name="sig.txt", content=None):
    """candidate 专区真实文件 + file_added（与 scan/import 同构）。"""
    from reco_store import dedup_key, normalize_book_name

    if content is None:
        content = "".join(f"第{i}段：合成正文，不含真实作品。\n" for i in range(1, 400))
    d = tmp_path / "data" / "candidate"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(content, encoding="utf-8")
    st = f.stat()
    store.append_event("file_added", payload={
        "path": str(f), "zone": "candidate", "ext": f.suffix,
        "size_bytes": int(st.st_size), "mtime_ns": int(st.st_mtime_ns),
        "st_dev": int(st.st_dev), "st_ino": int(st.st_ino),
        "display_name": name, "normalized_name": normalize_book_name(name),
        "dedup_key": dedup_key(int(st.st_size), name),
    }, occurred_at="2026-11-22T12:00:00+00:00", config_fingerprint="test/seed/1")
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


def _pump(predicate, timeout: float = 15.0) -> bool:
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


def _signals(store, book_id=None):
    conn = store.read_conn()
    try:
        sql = "SELECT book_id, kind, value, source, session_id FROM signals"
        args: tuple = ()
        if book_id is not None:
            sql += " WHERE book_id = ?"
            args = (book_id,)
        return conn.execute(sql + " ORDER BY signal_id", args).fetchall()
    finally:
        conn.close()


def _signal_payloads(store):
    conn = store.read_conn()
    try:
        return [json.loads(r[0]) for r in conn.execute(
            "SELECT payload_json FROM events WHERE event_type = 'signal_recorded'"
            " ORDER BY event_id")]
    finally:
        conn.close()


def _open(page, f, book_id):
    page.open_book(str(f), book_id, from_zone="candidate")
    assert _pump(lambda: page.loaded), "open 超时"


# ---------------------------------------------------------------- 采集点


def test_open_book_records_reopen_signal(qapp, store, tmp_path):
    """打开并开始阅读 → reopen 落库（source=gui、session 匿名）。"""
    f, bid = _seed_candidate_file(store, tmp_path)
    page = _reader(store, tmp_path)
    _open(page, f, bid)

    assert _pump(lambda: any(r[1] == "reopen" for r in _signals(store, bid))), \
        "打开阅读页未写 reopen 信号"
    row = [r for r in _signals(store, bid) if r[1] == "reopen"][0]
    _book_id, _kind, value, source, session_id = row
    assert value == 1.0
    assert source == "gui"
    assert session_id and len(session_id) == 16


def test_close_book_records_dwell_and_read_time(qapp, store, tmp_path):
    """关闭阅读页 → dwell（停留秒数）+ read_time 落库，且各只有一条。"""
    f, bid = _seed_candidate_file(store, tmp_path)
    page = _reader(store, tmp_path)
    _open(page, f, bid)
    page.viewer.verticalScrollBar().setValue(10)  # 一点交互（活跃区间）
    page.close_book()

    assert _pump(lambda: {"dwell", "read_time"} <= {r[1] for r in _signals(store, bid)}), \
        "关闭阅读页未写 dwell/read_time"
    rows = [r for r in _signals(store, bid) if r[1] in ("dwell", "read_time")]
    kinds = [r[1] for r in rows]
    assert kinds.count("dwell") == 1 and kinds.count("read_time") == 1, \
        "频率控制失效：dwell/read_time 每会话只应各一条"
    dwell = [r for r in rows if r[1] == "dwell"][0][2]
    assert dwell >= 0.0
    assert len({r[4] for r in rows}) == 1, "同一会话的信号应共用 session_id"


def test_reading_to_end_records_completion_once(qapp, store, tmp_path):
    """读到末尾 → completion 落库，且重复保存不重复写。"""
    f, bid = _seed_candidate_file(store, tmp_path)
    page = _reader(store, tmp_path)
    _open(page, f, bid)

    from reco_ui.qt import QTextCursor

    cur = page.viewer.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.End)
    page.viewer.setTextCursor(cur)
    page.flush_progress()
    page.flush_progress()

    assert _pump(lambda: any(r[1] == "completion" for r in _signals(store, bid))), \
        "读到末尾未写 completion 信号"
    completions = [r for r in _signals(store, bid) if r[1] == "completion"]
    assert len(completions) == 1, "completion 每会话只应一条"


def test_signal_payload_is_anonymous_whitelist(qapp, store, tmp_path):
    """信号载荷只含白名单键，绝无设备/路径信息。"""
    f, bid = _seed_candidate_file(store, tmp_path)
    page = _reader(store, tmp_path)
    _open(page, f, bid)
    page.close_book()
    assert _pump(lambda: len(_signal_payloads(store)) >= 2), "信号未落库"

    allowed = {"kind", "value", "source", "session_id"}
    for payload in _signal_payloads(store):
        assert set(payload) <= allowed, f"载荷含白名单外键：{payload!r}"
        assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False), \
            "载荷泄露了本地路径（匿名纪律）"


def test_repeated_scrolling_does_not_flood_signals(qapp, store, tmp_path):
    """长时间停留/反复滚动不产生信号风暴（只在结算点落一条）。"""
    f, bid = _seed_candidate_file(store, tmp_path)
    page = _reader(store, tmp_path)
    _open(page, f, bid)

    bar = page.viewer.verticalScrollBar()
    for v in range(0, 12):
        bar.setValue(min(v * 5, bar.maximum()))
        page.flush_progress()

    kinds = [r[1] for r in _signals(store, bid)]
    assert kinds.count("reopen") == 1
    assert "dwell" not in kinds, "未关闭就写 dwell = 每秒一条的风险"
    assert "read_time" not in kinds


def test_nav_switch_settles_session(qapp, store, tmp_path):
    """#73 采集表「关闭/切走 → dwell」：阅读中点导航切走必须即时结算。

    回归钉住：app.MainWindow._on_nav 在阅读中只 flush_progress 不结算的
    缺口——离开时长会被计入下一次结算，经 merge_signal 归一后放大短
    阅读的正权重。同时钉住结算幂等（二次结算/关窗不双写）。
    """
    f, bid = _seed_candidate_file(store, tmp_path)
    from reco_ui.app import MainWindow

    win = MainWindow(store=store, config={"data_root": str(tmp_path / "data")})
    try:
        win._open_reader(str(f), bid)
        assert _pump(lambda: win.reader_page.loaded), "open 超时"
        assert _pump(lambda: any(r[1] == "reopen" for r in _signals(store, bid))), \
            "打开未写 reopen"

        win._on_nav(0)  # 阅读中点左侧导航（liked 页）＝切走
        assert _pump(lambda: "dwell" in [r[1] for r in _signals(store, bid)]), \
            "切走未结算 dwell"
        kinds = [r[1] for r in _signals(store, bid)]
        assert kinds.count("dwell") == 1, "dwell 每会话只应一条"
        assert "read_time" in kinds, "切走未结算 read_time"

        # 幂等：二次结算与关窗都不双写（会话已置空）
        win._reader.settle_session()
        assert _pump(lambda: True)
        win.close()
        assert _pump(lambda: True)
        assert [r[1] for r in _signals(store, bid)].count("dwell") == 1, \
            "结算后二次结算/关窗双写 dwell"
    finally:
        try:
            win.close()
        except RuntimeError:
            pass
