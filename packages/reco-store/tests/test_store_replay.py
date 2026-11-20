"""工单 05 红测试（二）：事件→投影→回放逐位复现 + 篡改必被发现。

契约：ADR-0003（append-only 唯一真相、投影可重建、逐位复现）+
§7.3（axis_weights 相对误差 < 1e-9；集合类逐项相等）+ C2/C3。
"""

from __future__ import annotations

import sqlite3

import pytest


CFG = "half=30:thresh=0.6"


def seed_store(store, store_module):
    """全合成事件序列：覆盖文件/书/标签/标注/信号/关系/画像/推荐。"""
    common = {"st_dev": 2, "zone": "liked", "ext": ".txt", "mtime_ns": 111, "mount_hint": "F:"}
    ev = [
        ("file_added", None, {
            **common,
            "path": "F:/liked/a.txt",
            "size_bytes": 100,
            "st_ino": 10,
            "display_name": "a.txt",
            "normalized_name": "a.txt",
            "dedup_key": store_module.dedup_key(100, "a.txt"),
        }, "2026-01-01T00:00:01"),
        ("file_added", None, {
            **common,
            "path": "F:/liked/b.txt",
            "size_bytes": 200,
            "st_ino": 11,
            "display_name": "b.txt",
            "normalized_name": "b.txt",
            "dedup_key": store_module.dedup_key(200, "b.txt"),
        }, "2026-01-01T00:00:02"),
        ("tag_added", None, {"tag_id": "t-urban", "axis": "背景", "label": "都市", "created_by": "system"}, "2026-01-01T00:00:03"),
        ("label_set", 1, {"tag_id": "t-urban", "source": "auto", "confidence": 0.9, "basis_json": "{}"}, "2026-01-01T00:00:04"),
        ("label_set", 1, {"tag_id": "t-urban", "source": "user", "confidence": 1.0, "basis_json": "{}"}, "2026-01-01T00:00:05"),
        ("signal_recorded", 1, {"kind": "dwell", "value": 120.0, "source": "cli", "session_id": "s1"}, "2026-01-01T00:00:06"),
        ("relation_updated", 1, {"graph_json": "{\"characters\":[],\"edges\":[]}", "extractor_version": "rel-v1", "confidence": 0.4}, "2026-01-01T00:00:07"),
        ("features_computed", 1, {"extractor_version": "feat-v1", "stats_json": "{\"para_count\": 3}", "char_freq_hash_hex": "ab" * 32, "ngram_hash_digest_hex": "cd" * 32, "source_mtime_ns": 111}, "2026-01-01T00:00:08"),
        ("profile_updated", None, {"axis_weights_json": "{\"背景\": 0.6}", "sample_counts_json": "{\"n_pos\": 1}", "confidence": 0.3, "top_tags_json": "[\"t-urban\"]", "weights_json": "{\"w_sim\": 1.0}", "half_life_days_json": "{\"标注\": 30}", "reason": "import", "event_id_high_water": 8}, "2026-01-01T00:00:09"),
        ("rec_generated", None, {"run_id": "r1", "profile_version": 1, "candidate_count": 2, "exploration_count": 0, "items_json": "[{\"book_id\": 2, \"rank\": 1, \"score\": 0.5, \"reasons_json\": \"[]\", \"is_exploration\": false}]"}, "2026-01-01T00:00:10"),
        ("file_removed", None, {"path": "F:/liked/b.txt"}, "2026-01-01T00:00:11"),
    ]
    for event_type, book_id, payload, occurred_at in ev:
        store.append_event(event_type, book_id=book_id, payload=payload, occurred_at=occurred_at, config_fingerprint=CFG)


@pytest.fixture
def seeded(store, store_module):
    seed_store(store, store_module)
    return store


COMPARE_TABLES = (
    "volumes", "files", "books", "features", "feature_vectors", "tags",
    "tag_aliases", "book_tags", "relation_graphs", "relation_fields",
    "signals", "profile_versions", "rec_runs", "recommendations", "events",
)


def rows_of(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    finally:
        conn.close()


def test_rebuild_projections_bitwise_identical(store_module, seeded, tmp_path):
    """写入→投影→回放重建逐位一致（验收项 1 前半）。"""
    events = seeded.read_events()
    assert len(events) == 11
    target = tmp_path / "rebuilt.db"
    store_module.rebuild_projections(target, events)
    for table in COMPARE_TABLES:
        assert rows_of(target, table) == rows_of(seeded.path, table), table


def test_verify_replay_ok_and_schema_fingerprint_roundtrip(store_module, seeded):
    """verify_replay 一致为真；events 与 profile_versions 都带 schema_version
    与 config_fingerprint（C3）。"""
    report = seeded.verify_replay()
    assert report.ok, report.diffs
    conn = sqlite3.connect(seeded.path)
    try:
        ev = conn.execute(
            "SELECT schema_version, config_fingerprint FROM events WHERE event_type='profile_updated'"
        ).fetchone()
        pv = conn.execute(
            "SELECT schema_version, config_fingerprint FROM profile_versions"
        ).fetchone()
    finally:
        conn.close()
    assert ev == ("1", CFG)
    assert pv == ("1", CFG)


def test_tampered_event_stream_detected(store_module, seeded):
    """逆向验收项：篡改事件流必须被发现——绕过写入层伪造一条 label_set
    （只进事件表、未进投影），verify_replay 必须报不一致。"""
    raw = sqlite3.connect(seeded.path)
    try:
        raw.execute(
            "INSERT INTO events(event_type, book_id, payload_json, occurred_at, recorded_at,"
            " actor, schema_version, config_fingerprint)"
            " VALUES('label_set', 2, '{\"tag_id\": \"t-urban\", \"source\": \"user\","
            " \"confidence\": 1.0, \"basis_json\": \"{}\"}', '2026-01-02T00:00:00',"
            " '2026-01-02T00:00:00', 'attacker', '1', ?)",
            (CFG,),
        )
        raw.commit()
    finally:
        raw.close()
    report = seeded.verify_replay()
    assert not report.ok
    assert any("book_tags" in d for d in report.diffs)


def test_tampered_projection_detected(store_module, seeded):
    """投影被直接改（绕过事件流）同样必须被发现（漂移双向检测）。"""
    raw = sqlite3.connect(seeded.path)
    try:
        raw.execute("UPDATE books SET display_name = '篡改'")
        raw.commit()
    finally:
        raw.close()
    report = seeded.verify_replay()
    assert not report.ok
    assert any("books" in d for d in report.diffs)


def test_effective_weight_not_a_column(store_module, seeded):
    """裁定：effective_weight 不存列——折叠时由 occurred_at + config_fingerprint
    重算（§7.2 events）。events 表结构里不允许出现该列。"""
    conn = sqlite3.connect(seeded.path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    finally:
        conn.close()
    assert "effective_weight" not in cols
