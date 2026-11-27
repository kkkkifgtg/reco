"""#109：提取重算门——未变的书不追加事件，只重算变了的书。

修复前 extract_and_record 对每个文件无条件重算并追加 features_computed/
vector_computed：source_mtime_ns 字段建了却没人读，事件流随扫描轮次线性膨胀。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from reco_store import dedup_key, normalize_book_name, open_store

pytestmark = pytest.mark.integration

T0 = "2026-11-21T06:00:00+00:00"
FP = "t109/seed/1"
BODY = "正文只用于计算，不落日志。" * 40


def _book(store, tmp_path: Path, index: int, *, zone: str = "candidate") -> tuple[int, Path]:
    path = tmp_path / f"book-{index}.txt"
    path.write_text(BODY + str(index), encoding="utf-8")
    st = path.stat()
    display = path.name
    store.append_event(
        "file_added",
        payload={"path": str(path), "zone": zone, "ext": ".txt",
                 "size_bytes": int(st.st_size), "mtime_ns": int(st.st_mtime_ns),
                 "st_dev": 1, "st_ino": 100 + index, "display_name": display,
                 "normalized_name": normalize_book_name(display),
                 "dedup_key": dedup_key(int(st.st_size), display)},
        occurred_at=T0, config_fingerprint=FP, actor="system")
    conn = store.read_conn()
    try:
        row = conn.execute("SELECT book_id FROM files WHERE path = ?", (str(path),)).fetchone()
    finally:
        conn.close()
    return int(row[0]), path


def _event_count(store) -> int:
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


def _extract(store, book_id: int, path: Path, **kwargs):
    from reco_io.extract import extract_and_record

    return extract_and_record(store, book_id, path, now=T0,
                              config_fingerprint="cli/extract/1", **kwargs)


def test_second_extract_appends_nothing(tmp_path):
    db = tmp_path / "gate.db"
    store = open_store(db)
    try:
        book_id, path = _book(store, tmp_path, 1)
        first = _extract(store, book_id, path)
        assert first.skipped is False
        baseline = _event_count(store)

        second = _extract(store, book_id, path)
        assert second.skipped is True, "文件与提取器都没变，第二轮必须跳过"
        assert _event_count(store) == baseline, "跳过时不得追加任何事件"

        forced = _extract(store, book_id, path, force=True)
        assert forced.skipped is False, "force=True 必须重算"
        assert _event_count(store) > baseline
    finally:
        store.close()


def test_only_changed_book_is_recomputed(tmp_path):
    db = tmp_path / "changed.db"
    store = open_store(db)
    try:
        a_id, a_path = _book(store, tmp_path, 1)
        b_id, b_path = _book(store, tmp_path, 2)
        _extract(store, a_id, a_path)
        _extract(store, b_id, b_path)
        baseline = _event_count(store)

        # 只改一本：内容变了、mtime 也要真的往前走（否则门会认为没变）
        st = b_path.stat()
        b_path.write_text(BODY + "改过", encoding="utf-8")
        os.utime(b_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

        assert _extract(store, a_id, a_path).skipped is True
        changed = _extract(store, b_id, b_path)
        assert changed.skipped is False
        assert changed.vector_written is True
        assert _event_count(store) >= baseline + 1
    finally:
        store.close()


def test_cli_summary_reports_skipped(tmp_path, capsys):
    from reco_cli.app import cmd_extract

    db = tmp_path / "cli.db"
    store = open_store(db)
    try:
        _book(store, tmp_path, 1)
        _book(store, tmp_path, 2)
    finally:
        store.close()

    assert cmd_extract(db) == 0
    first = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first["total"] == 2 and first["skipped"] == 0
    assert first["ok"] == 2

    assert cmd_extract(db) == 0
    second = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert second["total"] == 2 and second["skipped"] == 2, "第二轮全部跳过"
    assert second["ok"] == 0 and second["failed"] == 0