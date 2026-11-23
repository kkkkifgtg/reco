"""#83——extract 接线自动打标：liked 打标+画像事件+提案；candidate 不动（集成）。"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from reco_cli import main
from reco_io.tokenizer import available_engines

# jieba 为可选依赖：缺失时挖掘切分降级 ngram，提案计数断言不稳定 → 跳过
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not available_engines()["jieba"],
                       reason="jieba 未安装（可选依赖，ngram 兜底切分不同）"),
]

NOW = "2026-11-21T00:00:00+00:00"
CFG = "t2/cli"


def _seed(store_module, db: Path, path: str, *, ino: int, size: int,
          dedup: str, zone: str) -> None:
    store = store_module.open_store(db)
    try:
        store.append_event(
            "file_added",
            payload={
                "path": path, "zone": zone, "ext": ".txt",
                "size_bytes": int(size), "mtime_ns": 1, "st_dev": 1, "st_ino": ino,
                "display_name": os.path.basename(path),
                "normalized_name": os.path.basename(path),
                "dedup_key": dedup,
            },
            occurred_at=NOW, config_fingerprint=CFG,
        )
    finally:
        store.close()


def _event_kinds(db: Path) -> list[str]:
    from reco_store import open_store

    store = open_store(db)
    try:
        return [e.event_type for e in store.read_events()]
    finally:
        store.close()


def _seed_taxonomy(store_module, db: Path) -> None:
    """种子分类群落库（生产库已有 164 标签；tmp 库需显式播种后打标链路才可用）。"""
    from reco_labels.auto_tag import StoreAdapter
    from reco_labels.seed import build_seed_rows

    store = store_module.open_store(db)
    try:
        adapter = StoreAdapter(store)
        for row in build_seed_rows():
            adapter.upsert_tag(row)
    finally:
        store.close()


def test_extract_autotags_liked_book_and_proposes(tmp_path, capsys):
    from reco_store import store as store_module

    db = tmp_path / "cli.db"
    book = tmp_path / "liked.txt"
    book.write_text(
        "他催眠了她。催眠的力量强大。调教与驯化交织。"
        "摄心术无处不在。摄心术再次出现。摄心术第三次。",
        encoding="utf-8",
    )
    _seed(store_module, db, str(book), ino=10, size=book.stat().st_size,
          dedup="k-liked", zone="liked")
    _seed_taxonomy(store_module, db)

    rc = main(["extract", "--db", str(db)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tagged"] == 1 and summary["proposals"] == 1

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT tag_id, source FROM book_tags").fetchall()
        assert {"mech.hypnosis", "mech.调教", "mech.驯化"} <= {t for t, _s in rows}
        assert all(s == "auto" for _t, s in rows)
        # proposals 表无 candidate 列（candidate 在 payload_json 里）——按 payload 解析
        props = conn.execute(
            "SELECT kind, payload_json FROM proposals WHERE status='pending'").fetchall()
        pend = [(json.loads(pj or "{}").get("candidate"), k) for k, pj in props]
        # 未知高频词（jieba 对「摄心术」的切分可能是「心术」）→ 新词提案，待用户确认
        assert len(pend) == 1 and pend[0][1] == "tag"
    finally:
        conn.close()
    assert _event_kinds(db).count("tag_hit") == 3  # 催眠/调教/驯化 各一条画像事件


def test_extract_tags_candidate_books_without_profile_events(tmp_path, capsys):
    """#83：candidate 也打标（book_tags + 提案），但不落 tag_hit（画像不被未确认偏好污染）。"""
    from reco_store import store as store_module

    db = tmp_path / "cli.db"
    book = tmp_path / "cand.txt"
    book.write_text("他催眠了她。催眠的力量强大。", encoding="utf-8")
    _seed(store_module, db, str(book), ino=10, size=book.stat().st_size,
          dedup="k-cand", zone="candidate")
    _seed_taxonomy(store_module, db)

    assert main(["extract", "--db", str(db)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["tagged"] == 1 and summary["proposals"] == 0
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT tag_id, source FROM book_tags").fetchall()
        assert ("mech.hypnosis", "auto") in [(t, s) for t, s in rows]
    finally:
        conn.close()
    assert _event_kinds(db).count("tag_hit") == 0  # candidate 不折叠画像
