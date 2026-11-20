"""工单 13——CLI/GUI 排序逐位一致（红测试；架构 §10/§13，A6 rank 唯一入口）。

同一合成候选池（全合成数据）：真实向量接线后的 CLI recommend 输出
与 GUI 生产装配路径（同一 rank_candidates）逐位相等——
book_id 顺序 / score / rank / is_exploration / reasons 全字段。
"""
from __future__ import annotations

import base64
import json
import math
import os
import struct
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# 根 tests/ 不吃 packages/reco-ui/tests/conftest.py（testpaths 语义）——
# Qt 生命周期两条铁律在此复刻（0xC0000409 崩面根治实录）：
# ①QApplication 必须被 Python 侧整场持有；②每测试后线程池泄洪+泵事件。
_app_holder: list = []


@pytest.fixture(autouse=True, scope="session")
def _qapp_session():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    _app_holder.append(app)  # 整场会话持引用，防 PyQt GC 销毁 C++ 对象
    yield
    from reco_ui.qt import QThreadPool

    pool = QThreadPool.globalInstance()
    if pool is not None:
        pool.waitForDone(5000)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


@pytest.fixture(autouse=True)
def _drain_after_each_test():
    yield
    from reco_ui.qt import QApplication, QThreadPool

    pool = QThreadPool.globalInstance()
    if pool is not None:
        pool.waitForDone(3000)
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-11-21T00:00:00+00:00"

CLI_PROBE = r"""
import sys, io, json, contextlib
sys.stdout.reconfigure(encoding="utf-8")
from reco_cli.app import cmd_recommend
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    cmd_recommend(sys.argv[1], as_json=True)
sys.stdout.write(buf.getvalue())
"""

# 一行 -c 载荷无法安全压行 with 块——统一经 exec 包装（PARAMS 由外层拼接注入）。
EXEC_WRAP = "exec(%r)  # 一行探针经 exec 展开多语句\n"


def _one_liner(code: str) -> str:
    """多语句探针压成 -c 单行：exec(编译单元源码)。"""
    return EXEC_WRAP % code

GUI_PROBE = r"""
import os, sys, json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8")
from reco_ui.qt import QApplication
_qapp = QApplication.instance() or QApplication([])  # 子进程内整场持有
from reco_store import open_store
from reco_ui.app import MainWindow
store = open_store(sys.argv[1])
try:
    win = MainWindow(store=store)
    page = win.page("recommend")
    page.load_now()  # 生产装配路径（与 CLI 同一 rank_candidates）
    rows = page._model._rows
    out = [[r.book_id, float(r.score), int(r.rank), bool(r.is_exploration),
            [[x.axis, float(x.contribution), str(x.source)] for x in r.reasons]]
           for r in rows]
    print(json.dumps(out, ensure_ascii=False))
finally:
    store.close()
"""


def _run_probe(code: str, db) -> str:
    proc = subprocess.run([sys.executable, "-c", _one_liner(code), str(db)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def _seed_rich(store):
    """合成候选池 + 真实向量 + 口味事件（like/dislike 走 glue.record_feedback 静音语义）。"""
    from reco_ui.glue import seed_books, record_feedback

    names = seed_books(store, books=30, liked=12, candidate=18, now=T0)
    axes = ("subject.g1", "subject.g2", "plot.p")
    for i in range(1, 31):  # 真实向量接线：vector_computed → feature_vectors 投影
        n = 8
        dims = list(range(n))
        weights = [round(0.05 + ((i * 7 + d * 3) % 19) / 40.0, 4) for d in dims]
        blob = struct.pack(f">{n}i", *dims) + struct.pack(f">{n}d", *weights)
        # W5-N 裁定统一 hex 键名（vector_blob_b64 已入白名单外，写入层拒绝）。
        store.append_event(
            "vector_computed", book_id=i,
            payload={"vector_blob_hex": blob.hex(),
                     "dim": n, "idf_version": "t13-idf/1",
                     "norm": math.sqrt(sum(w * w for w in weights))},
            occurred_at=T0, config_fingerprint="t13/vectors/1")
    for k, i in enumerate(range(1, 13)):  # liked → like 反馈（画像轴折叠）
        record_feedback(store, "like", book_id=i, axis=axes[k % len(axes)],
                        value=1.0, now=T0)
    for i in range(13, 17):  # dislike → 负样本 + 静音窗（两入口同见）
        record_feedback(store, "dislike", book_id=i, axis="subject.g1",
                        value=1.0, now=T0)
    for i in range(17, 24):  # 追加 dislike（≥10 门：eval fixture 需 n_neg≥10）
        record_feedback(store, "dislike", book_id=i, axis="subject.g2",
                        value=1.0, now=T0)
    return names


def test_cli_gui_bitwise_consistency(tmp_path):
    from reco_store import open_store

    db = tmp_path / "consistency.db"
    store = open_store(db)
    try:
        _seed_rich(store)
    finally:
        store.close()

    cli = json.loads(_run_probe(CLI_PROBE, db))
    gui = json.loads(_run_probe(GUI_PROBE, db))
    items = cli["items"]
    assert len(items) == len(gui) == 30
    for it, row in zip(items, gui):
        # 逐位：book_id 顺序 / score / rank / is_exploration / reasons 全字段
        assert it["book_id"] == row[0]
        assert it["score"] == row[1]
        assert it["rank"] == row[2]
        assert it["is_exploration"] == row[3]
        assert [[x["axis"], x["contribution"], x["source"]] for x in it["reasons"]] == row[4]
    assert any(it["is_exploration"] for it in items)  # 探索位在两入口一致出现
    assert any(any(x["source"] == "muted" for x in it["reasons"]) for it in items)  # 静音理由透传


def test_profile_rows_real_data(tmp_path):
    """承接②：profile_rows 接真实画像（W9 rehydrate），不再硬编码占位快照。"""
    from reco_store import open_store
    from reco_ui.pages import profile_rows

    store = open_store(tmp_path / "prof.db")
    try:
        assert profile_rows(store) == {"has_profile": False}  # 空库诚实降级
        _seed_rich(store)
        data = profile_rows(store)
        assert data["has_profile"] is True
        assert data["axis_weights"]  # 真实轴权重（非 {"tag": 1.0} 占位）
        assert "tag" not in data["axis_weights"] or len(data["axis_weights"]) > 1
        assert data["sample_counts"]["n_pos"] == 12
        assert data["sample_counts"]["n_neg"] == 11  # 4 candidate + 7 追加 dislike
        assert 0.0 < data["confidence"] <= 1.0
        assert data["version"] == 1
    finally:
        store.close()


def test_eval_real_samples_and_persistence_gate(tmp_path):
    """承接④+W8 接缝②：eval ≥10 真实样本路径 + evaluation_runs 持久化门。"""
    from reco_store import open_store

    db = tmp_path / "eval-ok.db"
    store = open_store(db)
    try:
        _seed_rich(store)
    finally:
        store.close()
    eval_probe = (
        "import sys, json\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "import io, contextlib\n"
        "from reco_cli.app import cmd_eval\n"
        "buf = io.StringIO()\n"
        "with contextlib.redirect_stdout(buf):\n"
        "    cmd_eval(sys.argv[1])\n"
        "sys.stdout.write(buf.getvalue())\n"
    )
    proc = subprocess.run([sys.executable, "-c", _one_liner(eval_probe), str(db)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["n_pos"] == 12 and payload["n_neg"] == 18  # 成对样本口径：12 like + 6 candidate-dislike（zone 标签）
    assert payload["auc"] is not None
    assert payload["eval_run_event_id"] is not None  # 持久化门真实写入
    s2 = open_store(db)
    try:
        n = s2.read_conn().execute(
            "SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
        assert n == 1  # ≥10 门：恰好落一行
    finally:
        s2.close()


def test_eval_insufficient_persists_nothing(tmp_path):
    from reco_store import open_store
    from reco_ui.glue import record_feedback, seed_books

    store2 = open_store(tmp_path / "eval-small2.db")
    try:
        seed_books(store2, books=6, liked=3, candidate=3, now=T0)
        for i in range(1, 4):
            record_feedback(store2, "like", book_id=i, axis="subject.g1", value=1.0, now=T0)
        record_feedback(store2, "dislike", book_id=4, axis="subject.g1", value=1.0, now=T0)
    finally:
        store2.close()
    eval_probe = (
        "import sys, json\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "import io, contextlib\n"
        "from reco_cli.app import cmd_eval\n"
        "buf = io.StringIO()\n"
        "with contextlib.redirect_stdout(buf):\n"
        "    cmd_eval(sys.argv[1])\n"
        "sys.stdout.write(buf.getvalue())\n"
    )
    proc = subprocess.run([sys.executable, "-c", _one_liner(eval_probe), str(tmp_path / "eval-small2.db")],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(proc.stdout)
    assert payload["status"] == "insufficient"
    s3 = open_store(tmp_path / "eval-small2.db")
    try:
        n = s3.read_conn().execute(
            "SELECT COUNT(*) FROM evaluation_runs").fetchone()[0]
        assert n == 0  # <10 门：零写入
    finally:
        s3.close()


def test_perf_tabs_snapshot_stable(tmp_path):
    """⑤：RECO_PERF_TABS 快照稳定性 + recommend 页真实行数（rank 消费接线证据）。"""
    from reco_io import perf as perf_mod
    from reco_store import open_store
    from reco_ui.app import MainWindow, run_tabs
    from reco_ui.glue import seed_books

    import contextlib

    perf_mod.perf_enabled = False
    s = open_store(tmp_path / "snap.db")
    try:
        _seed_rich(s)
        snap1 = tmp_path / "s1.json"
        snap2 = tmp_path / "s2.json"
        with contextlib.redirect_stderr(__import__("io").StringIO()):
            win = MainWindow(store=s)
            assert run_tabs(win, snapshot_path=str(snap1)) == 0
            assert run_tabs(win, snapshot_path=str(snap2)) == 0
        a = json.loads(snap1.read_text(encoding="utf-8"))
        b = json.loads(snap2.read_text(encoding="utf-8"))
        assert a == b  # 同库两次遍历快照逐位一致
        rec = next(p for p in a if p["page"] == "recommend")
        assert rec["rows"] == 30  # rank 输出真实渲染（非 0 占位）
    finally:
        s.close()
