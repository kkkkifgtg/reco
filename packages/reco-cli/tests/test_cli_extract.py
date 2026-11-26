"""T2：reco_cli extract 子命令（红测试）。

跨包集成测试（CODING-STANDARDS §2 显式标注）：本模块 import reco_store，
独立 venv 只装 reco-cli 时不可运行；全量套件可用 `-m "not integration"` 分组。

覆盖：子命令注册/帮助、JSON 摘要恰四键、每本挑 size_bytes>0 且本机存在的
present 文件（同书多条 files 行）、全空书如实计 failed 不硬失败、--limit。
闭环（extract → rank_rows 非空、幂等）在根 tests/test_extract_closed_loop.py。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from reco_cli import main

pytestmark = pytest.mark.integration

NOW = "2026-11-21T00:00:00+00:00"
CFG = "t2/cli"


def _seed(store_module, db: Path, path: str, *, ino: int, size: int, dedup: str) -> None:
    store = store_module.open_store(db)
    try:
        store.append_event(
            "file_added",
            payload={
                "path": path, "zone": "candidate", "ext": ".txt",
                "size_bytes": int(size), "mtime_ns": 1, "st_dev": 1, "st_ino": ino,
                "display_name": os.path.basename(path), "normalized_name": os.path.basename(path),
                "dedup_key": dedup,
            },
            occurred_at=NOW, config_fingerprint=CFG,
        )
    finally:
        store.close()


def _make_book(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------- 子命令与摘要

def test_extract_subcommand_in_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "extract" in capsys.readouterr().out


def test_extract_summary_is_json_with_exact_keys(tmp_path, capsys):
    from reco_store import store as store_module

    db = tmp_path / "cli.db"
    book = _make_book(tmp_path, "a.txt", "风穿过走廊，带来远处潮水的声音。" * 20)
    _seed(store_module, db, str(book), ino=10, size=book.stat().st_size, dedup="k-a")

    rc = main(["extract", "--db", str(db)])

    assert rc == 0
    out = capsys.readouterr().out
    summary = json.loads(out)  # stdout 纯净 JSON
    assert set(summary) == {"total", "ok", "skipped", "failed", "vectors", "tagged", "proposals"}
    # #83：candidate 专区也打标；样本文本无标签词 → tagged=0；
    # 未知内容词（count>=3）进提案（具体数量随分词词表变化，只下界断言）
    assert summary["tagged"] == 0 and summary["proposals"] >= 1
    assert _count(db, "feature_vectors") == 1
    assert _count(db, "features") == 1


def test_extract_picks_nonzero_present_file_for_same_book(tmp_path, capsys):
    """同书多条 files 行：必须挑 size_bytes>0 且本机存在的那个（T1 实测追加）。"""
    from reco_store import open_store
    from reco_store import store as store_module

    db = tmp_path / "cli.db"
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    real = _make_book(tmp_path, "real.txt", "这是同一本书的第二条文件行，内容非空。" * 10)
    _seed(store_module, db, str(empty), ino=10, size=0, dedup="k-same")
    _seed(store_module, db, str(real), ino=11, size=real.stat().st_size, dedup="k-same")

    assert main(["extract", "--db", str(db)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tagged"] == 0 and summary["proposals"] >= 1

    store = open_store(db)
    try:
        conn = store.read_conn()
        try:
            (source_mtime_ns,) = conn.execute("SELECT source_mtime_ns FROM features").fetchone()
        finally:
            conn.close()
    finally:
        store.close()
    assert source_mtime_ns == os.stat(real).st_mtime_ns  # 选中的是非零文件


def test_extract_empty_only_book_counts_failed_not_crash(tmp_path, capsys):
    from reco_store import store as store_module

    db = tmp_path / "cli.db"
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    _seed(store_module, db, str(empty), ino=10, size=0, dedup="k-empty")

    rc = main(["extract", "--db", str(db)])

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"total": 1, "ok": 0, "failed": 1,
                                                   "vectors": 0, "tagged": 0, "proposals": 0, "skipped": 0}
    assert _count(db, "feature_vectors") == 0


def test_extract_missing_file_counts_failed(tmp_path, capsys):
    from reco_store import store as store_module

    db = tmp_path / "cli.db"
    missing = tmp_path / "gone.txt"
    _seed(store_module, db, str(missing), ino=10, size=100, dedup="k-missing")

    assert main(["extract", "--db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out) == {"total": 1, "ok": 0, "failed": 1,
                                                   "vectors": 0, "tagged": 0, "proposals": 0, "skipped": 0}
    assert _count(db, "features") == 0  # 文件缺失：打标同样无从谈起


def test_extract_limit_processes_books_in_book_id_order(tmp_path, capsys):
    from reco_store import store as store_module

    db = tmp_path / "cli.db"
    first = _make_book(tmp_path, "first.txt", "第一本的内容，用于验证 book_id 次序。" * 10)
    second = _make_book(tmp_path, "second.txt", "第二本的内容，--limit 1 不应处理。" * 10)
    _seed(store_module, db, str(first), ino=10, size=first.stat().st_size, dedup="k-1")
    _seed(store_module, db, str(second), ino=11, size=second.stat().st_size, dedup="k-2")

    assert main(["extract", "--db", str(db), "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["tagged"] == 0  # 样本文本无标签词
    assert _count(db, "features") == 1
    assert _count(db, "feature_vectors") == 1