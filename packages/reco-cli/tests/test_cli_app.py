"""工单 11：CLI 入口测试（红→绿）。

跨包集成测试（CODING-STANDARDS §2 显式标注）：本模块 import reco_store/reco_core，
独立 venv 只装 reco-cli 时不可运行；全量套件可用 `-m "not integration"` 分组。

验收映射（.scratch/reco-impl/issues/11-cli.md + auditor W11 预案）：
  C1 六子命令 + exit 语义        C2 UTF-8 输出回归（修 T0 占位乱码）
  C3 report 诊断 JSON 无正文    C4 recommend JSON 纯净 + C6 小样本文案
  C5 feedback C7 文案透传 + 静音持久化闭环（真实 Store）
  C6 A6 rank 唯一入口（CLI 无第二条排序）  C7 只读入口不写库
  C8 RECO_PERF 阶段毫秒 stderr  C9 eval 小样本诚实（不伪造 AUC）
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from reco_cli import main

pytestmark = pytest.mark.integration

NOW = "2026-11-21T06:00:00+00:00"
CFG_FP = "t15/cli"


def _fresh_db(tmp_path: Path) -> Path:
    from reco_store import open_store

    store = open_store(tmp_path / "cli.db")
    store.append_event(
        "file_added",
        payload={"path": "\\\\synth\\\\s1.txt", "zone": "candidate", "ext": "txt",
                 "size_bytes": 100, "mtime_ns": 1, "st_dev": 1, "st_ino": 1,
                 "display_name": "合成样本一", "normalized_name": "合成样本一",
                 "dedup_key": "k1"},
        occurred_at=NOW, config_fingerprint=CFG_FP,
    )
    store.close()
    return tmp_path / "cli.db"


def _events_count(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


# ---------- C1：六子命令与 exit 语义 ----------

def test_C1_help_lists_six_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in ("report", "scan", "recommend", "feedback", "explain", "replay", "eval"):
        assert name in out


def test_C1_unknown_subcommand_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["nope"])
    assert exc.value.code != 0


# ---------- C2：UTF-8 输出回归（英文 locale 控制台不乱码） ----------

def test_C2_utf8_output_survives_cp1252_pipe(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "reco_cli", "--help"],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},  # 模拟西文 locale 管道
    )
    assert proc.returncode == 0
    text = proc.stdout.decode("utf-8")  # 修复前：cp1252 编码下中文乱码/炸编码
    assert "reco-cli" in text
    assert "\ufffd" not in text


# ---------- C3：report 诊断（样本量/轴覆盖/权重/CV；无正文无书名） ----------

def test_C3_report_empty_db_and_write_json(tmp_path):
    db = _fresh_db(tmp_path)
    reports_dir = tmp_path / "reports"
    rc = main(["report", "--db", str(db), "--write", "--reports-dir", str(reports_dir)])
    assert rc == 0
    files = list(reports_dir.glob("reco-*.json"))
    assert len(files) == 1
    assert re.fullmatch(r"reco-\d{4}-\d{2}-\d{2}\.json", files[0].name)
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    for key in ("generated_at", "sample_counts", "axis_coverage", "axis_weights", "confidence", "cv"):
        assert key in payload
    text = files[0].read_text(encoding="utf-8")
    assert "合成样本" not in text  # 无书名（B 类）


# ---------- C4：recommend 纯净 JSON + C6 小样本文案 ----------

def test_C4_recommend_empty_db_explicit_state_and_c6(capsys, tmp_path):
    db = _fresh_db(tmp_path)
    rc = main(["recommend", "--db", str(db), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)  # stdout 纯 JSON
    assert payload["items"] == []
    assert payload["display"]
    text = json.dumps(payload, ensure_ascii=False)
    assert "仅供参考" in text  # C6 文案（n_pos<10）
    assert "还不够准" in text


def test_C4_recommend_json_has_no_prose_fields(tmp_path, capsys):
    db = _fresh_db(tmp_path)
    main(["recommend", "--db", str(db), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"items", "display", "profile"}  # 纯净契约面
    assert set(payload["profile"]) <= {"n_pos", "n_neg", "confidence", "value", "display"}


# ---------- C5：feedback 文案透传 + 静音持久化闭环（真实 Store） ----------

def test_C5_feedback_add_to_pool_on_real_store(tmp_path, capsys):
    db = _fresh_db(tmp_path)
    rc = main(["feedback", "add_to_pool", "--db", str(db), "--book-id", "1"])
    assert rc == 0  # 修复前：真库抛「未知事件类型: pool_added」
    conn = sqlite3.connect(db)
    try:
        types = [r[0] for r in conn.execute("SELECT event_type FROM events").fetchall()]
        assert types.count("pool_added") == 1
        assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0  # 不进口味流
    finally:
        conn.close()


def test_C5_feedback_dislike_persists_mute_and_c7_display(tmp_path, capsys):
    db = _fresh_db(tmp_path)
    rc = main(["feedback", "dislike", "--db", str(db), "--book-id", "1", "--axis", "mood.dark"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "已调整" in out and "下调" in out  # C7 文案透传
    from reco_store import open_store

    store = open_store(db)
    try:
        types = [ev.event_type for ev in store.read_events()]
        assert types.count("rec_feedback") == 1
        mutes = store.load_mutes()
        assert len(mutes) == 1  # 静音表持久化（工单 11 强制必办②经 CLI 闭环）
        assert mutes[0]["axis"] == "mood.dark"
        assert mutes[0]["remaining_days"] == 90.0  # tag 分档
    finally:
        store.close()


def test_C5_feedback_like_no_mute(tmp_path):
    db = _fresh_db(tmp_path)
    assert main(["feedback", "like", "--db", str(db), "--book-id", "1"]) == 0
    from reco_store import open_store

    store = open_store(db)
    try:
        assert store.load_mutes() == []
        types = [ev.event_type for ev in store.read_events()]
        assert types.count("rec_feedback") == 1
    finally:
        store.close()


def test_C5_mute_map_glue_maps_feedback_layer(tmp_path):
    # store 行 → feedback 层 MuteWindow 映射（过期行被过滤；键优先 dedup_key）
    from datetime import datetime, timedelta, timezone

    from reco_core.feedback import MuteWindow
    from reco_store import open_store

    db = _fresh_db(tmp_path)
    store = open_store(db)
    try:
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=9)).isoformat()
        store.record_mute(1, dedup_key="k1", axis="x", kind="dislike",
                          expired_at=past, remaining_days=0.0,
                          occurred_at=NOW, config_fingerprint=CFG_FP)  # 已过期（先）
        store.record_mute(1, dedup_key="k1", axis="mood.dark", kind="dislike",
                          expired_at=future, remaining_days=9.0,
                          occurred_at=NOW, config_fingerprint=CFG_FP)  # 替换为未过期
        from reco_cli.app import mute_map_from_store

        mute = mute_map_from_store(store)
        assert set(mute) == {"k1"}  # 只留未过期；dedup_key 优先
        assert isinstance(mute["k1"], MuteWindow)
        assert mute["k1"].remaining_days > 0
    finally:
        store.close()


# ---------- C6：A6 rank 唯一入口（CLI 无第二条排序） ----------

def test_C6_no_second_ranking_path_in_cli_source():
    import inspect

    import reco_cli.app as app_module

    src = inspect.getsource(app_module)
    assert "rank_only" in src or "score_rank" in src or "rank(" in src  # 唯一入口消费
    assert "sorted(" not in src  # CLI 层零排序实现
    assert "reco_ui" not in src  # 无 UI 依赖


# ---------- C7：只读入口不写库 ----------

def test_C7_readonly_entries_leave_events_untouched(tmp_path, capsys):
    db = _fresh_db(tmp_path)
    before = _events_count(db)
    assert main(["report", "--db", str(db)]) == 0
    assert main(["recommend", "--db", str(db)]) == 0
    assert main(["explain", "--db", str(db), "--book-id", "1"]) == 0
    assert main(["replay", "--db", str(db)]) == 0
    assert main(["eval", "--db", str(db)]) == 0
    assert _events_count(db) == before  # 只读入口零写入


# ---------- C9：eval 小样本诚实 + replay 漂移检出 ----------

def test_C9_eval_small_sample_honest(tmp_path, capsys):
    db = _fresh_db(tmp_path)
    rc = main(["eval", "--db", str(db)])
    assert rc == 0
    out = capsys.readouterr().out
    assert ("不足以判定" in out) or ("不够" in out)  # 不伪造 AUC（§6.3）


def test_C9_replay_detects_drift_exit_nonzero(tmp_path):
    db = _fresh_db(tmp_path)
    assert main(["replay", "--db", str(db)]) == 0  # 干净库 ok
    conn = sqlite3.connect(db)  # 绕过事件流直改投影 → 漂移
    conn.execute("INSERT INTO books(display_name, normalized_name, dedup_key, size_bytes, ext, first_seen_at) VALUES('ghost','ghost','g',1,'txt','2026-01-01')")
    conn.commit()
    conn.close()
    assert main(["replay", "--db", str(db)]) != 0  # 漂移 → 非 0


# ---------- C8：RECO_PERF=1 阶段毫秒 stderr ----------

def test_C8_perf_spans_on_stderr(tmp_path):
    db = _fresh_db(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "reco_cli", "recommend", "--db", str(db), "--json"],
        capture_output=True, env={**os.environ, "RECO_PERF": "1"},
    )
    assert proc.returncode == 0
    assert "phase=cli." in proc.stderr.decode("utf-8", "replace")
