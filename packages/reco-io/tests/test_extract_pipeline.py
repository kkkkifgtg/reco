"""T1 红测试：特征提取 → 向量生成 事件管线（payload 契约 + 投影落表）。

集成票标注（CODING-STANDARDS §2 的显式例外）：本文件 import reco_store 做
「事件 → 投影落表」断言，属 T1 明确声明的集成测试；其余单测只依赖 reco_io。

E4 期望值独立：槽位/摘要/打包在测试内用 blake2b/struct/json 按契约直算，
不调用被测实现（防止「实现自证」）。
"""

from __future__ import annotations

import json
import math
import os
import struct
from hashlib import blake2b

import pytest

NUM_BUCKETS = 200_000  # features.DEFAULT_NUM_BUCKETS（§5.6 桶数口径）
VECTOR_DIM_CAP = 64
VECTOR_IDF_VERSION = "reco-ngram-counts/1"
EXTRACTOR_VERSION = "reco-stats/1"
NOW = "2026-11-21T00:00:00+00:00"
CFG = "vec-t1/1"

FEATURES_KEYS = {
    "extractor_version",
    "stats_json",
    "char_freq_hash_hex",
    "ngram_hash_digest_hex",
    "source_mtime_ns",
}
VECTOR_KEYS = {"vector_blob_hex", "dim", "idf_version", "norm"}


# ---------------------------------------------------------------- 契约直算（独立期望值）


def _slot(ch: str) -> int:
    return int.from_bytes(blake2b(ch.encode("utf-8"), digest_size=8).digest(), "big") % NUM_BUCKETS


def expected_char_freq_hash(char_freq) -> str:
    """单字频率哈希桶：slot 升序、(slot, count) 大端 >II 对，碰撞计数合并。"""
    merged: dict[int, int] = {}
    for ch, cnt in char_freq:
        slot = _slot(ch)
        merged[slot] = merged.get(slot, 0) + int(cnt)
    raw = b"".join(struct.pack(">II", s, c) for s, c in sorted(merged.items()))
    return raw.hex()


def expected_ngram_digest(buckets) -> str:
    """全量 2–3gram 桶的确定性摘要：slot 升序、(slot, count) 大端 >II 对。"""
    raw = b"".join(struct.pack(">II", int(s), int(c)) for s, c in sorted(buckets or ()))
    return blake2b(raw, digest_size=32).hexdigest()


def expected_vector(buckets) -> dict:
    """向量契约直算：非零桶 → 计数 Top-64（并列 slot 小者先）→ slot 升序 →
    权重 = count / sqrt(Σcount²)（单位向量）；blob = >{dim}i 索引 + >{dim}d 权重。"""
    nonzero = [(int(s), int(c)) for s, c in (buckets or ()) if c != 0]
    if len(nonzero) > VECTOR_DIM_CAP:
        nonzero = sorted(nonzero, key=lambda kv: (-kv[1], kv[0]))[:VECTOR_DIM_CAP]
    nonzero = sorted(nonzero)
    dim = len(nonzero)
    if dim == 0:
        return {"vector_blob_hex": "", "dim": 0, "idf_version": VECTOR_IDF_VERSION, "norm": 0.0}
    norm = math.sqrt(sum(float(c) ** 2 for _s, c in nonzero))
    weights = [float(c) / norm for _s, c in nonzero]
    blob = (
        struct.pack(f">{dim}i", *(s for s, _c in nonzero))
        + struct.pack(f">{dim}d", *weights)
    )
    return {
        "vector_blob_hex": blob.hex(),
        "dim": dim,
        "idf_version": VECTOR_IDF_VERSION,
        "norm": norm,
    }


def make_fr(*, stats=None, char_freq=(), ngram_buckets=None, quality_flag="ok",
            stats_complete=True):
    from reco_io.features import FeatureResult

    return FeatureResult(
        extractor_version=EXTRACTOR_VERSION,
        quality_flag=quality_flag,
        stats_complete=stats_complete,
        stats=stats,
        char_freq=char_freq,
        ngram_buckets=ngram_buckets,
        alias_counts={},
    )


# ---------------------------------------------------------------- 向量 payload

def test_vector_payload_keys_exactly_four():
    extract = _extract_module()
    payload = extract.vector_payload_from_features(make_fr(ngram_buckets=((5, 3), (9, 1))))
    assert set(payload) == VECTOR_KEYS


def test_vector_blob_roundtrips_with_store_decoder():
    """blob 必须能被 reco_store.recommend.decode_vector_blob 解码且逐值一致。"""
    from reco_store.recommend import decode_vector_blob

    extract = _extract_module()
    fr = make_fr(ngram_buckets=((9, 1), (5, 3), (7, 2)))
    payload = extract.vector_payload_from_features(fr)
    expected = expected_vector(fr.ngram_buckets)
    assert payload == expected

    blob = bytes.fromhex(payload["vector_blob_hex"])
    assert len(blob) == 12 * payload["dim"]
    assert struct.unpack(">3i", blob[:12]) == (5, 7, 9)  # 索引升序、大端
    decoded = decode_vector_blob(blob, payload["dim"])
    assert decoded == {5: 3 / math.sqrt(14), 7: 2 / math.sqrt(14), 9: 1 / math.sqrt(14)}


def test_vector_dim_cap_64_keeps_highest_counts_tie_by_slot():
    extract = _extract_module()
    buckets = tuple((100 + i, 1 + (i % 4)) for i in range(80))
    payload = extract.vector_payload_from_features(make_fr(ngram_buckets=buckets))
    expected = expected_vector(buckets)
    assert payload["dim"] == VECTOR_DIM_CAP
    assert payload == expected
    kept = set(struct.unpack(">64i", bytes.fromhex(payload["vector_blob_hex"])[: 4 * 64]))
    # 被截掉的必是计数最小的 16 个桶（并列按 slot 大者先出局）
    dropped = {s for s, _c in buckets} - kept
    assert len(dropped) == 16
    counts = dict(buckets)
    assert min(counts[s] for s in kept) >= max(counts[s] for s in dropped)


def test_vector_payload_is_deterministic():
    extract = _extract_module()
    fr = make_fr(ngram_buckets=((3, 1), (7, 4), (11, 2)))
    assert extract.vector_payload_from_features(fr) == extract.vector_payload_from_features(fr)


def test_vector_payload_empty_has_zero_dim_and_decodes_rejects():
    """无 n-gram（空/降级）→ dim=0、空 blob、norm=0.0；该形态不是可解码向量。"""
    from reco_store.recommend import decode_vector_blob

    extract = _extract_module()
    payload = extract.vector_payload_from_features(make_fr(ngram_buckets=()))
    assert payload == {"vector_blob_hex": "", "dim": 0,
                       "idf_version": VECTOR_IDF_VERSION, "norm": 0.0}
    with pytest.raises(ValueError):
        decode_vector_blob(b"", 0)


# ---------------------------------------------------------------- 特征 payload

def test_features_payload_keys_and_hashes_exact():
    extract = _extract_module()
    stats = {
        "char_count": 7,
        "cjk_char_ratio": 4 / 7,
        "char_freq": (("中", 3), ("a", 2)),
        "alias_counts": {},
    }
    fr = make_fr(stats=stats, char_freq=(("中", 3), ("a", 2)),
                 ngram_buckets=((5, 3), (9, 1)))
    payload = extract.features_payload_from_features(fr, source_mtime_ns=123)
    assert set(payload) == FEATURES_KEYS
    assert payload["extractor_version"] == EXTRACTOR_VERSION
    assert payload["source_mtime_ns"] == 123
    # 正文边界：单字原文不进 stats_json，只进哈希桶
    stored = json.loads(payload["stats_json"])
    assert "char_freq" not in stored
    assert stored["char_count"] == 7
    assert stored["cjk_char_ratio"] == 4 / 7
    assert payload["char_freq_hash_hex"] == expected_char_freq_hash((("中", 3), ("a", 2)))
    assert payload["ngram_hash_digest_hex"] == expected_ngram_digest(((5, 3), (9, 1)))


def test_features_payload_degraded_is_honest_empty():
    extract = _extract_module()
    fr = make_fr(stats=None, char_freq=(), ngram_buckets=None,
                 quality_flag="undecodable", stats_complete=False)
    payload = extract.features_payload_from_features(fr, source_mtime_ns=None)
    assert json.loads(payload["stats_json"]) == {}
    assert payload["char_freq_hash_hex"] == ""
    assert payload["ngram_hash_digest_hex"] == expected_ngram_digest(())
    assert payload["source_mtime_ns"] is None


# ---------------------------------------------------------------- 临时 Store 投影落表

@pytest.fixture
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "t1.db")
    yield s
    s.close()


def _seed_book(store) -> int:
    from reco_store import dedup_key

    store.append_event(
        "file_added",
        payload={
            "path": "F:/liked/a.txt", "zone": "liked", "ext": ".txt",
            "size_bytes": 100, "mtime_ns": 111, "st_dev": 2, "st_ino": 10,
            "display_name": "a.txt", "normalized_name": "a.txt",
            "dedup_key": dedup_key(100, "a.txt"), "mount_hint": "F:",
        },
        occurred_at=NOW, config_fingerprint=CFG,
    )
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT book_id FROM books LIMIT 1").fetchone()[0])
    finally:
        conn.close()


def _rows(store, table: str, book_id: int):
    conn = store.read_conn()
    try:
        return conn.execute(f"SELECT * FROM {table} WHERE book_id = ?", (book_id,)).fetchall()
    finally:
        conn.close()


def _event_types(store) -> list[str]:
    """全事件流类型（file_added 的 book_id 为 NULL，按事件序号取全流）。"""
    conn = store.read_conn()
    try:
        rows = conn.execute("SELECT event_type FROM events ORDER BY event_id").fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def test_extract_and_record_projects_features_and_vector(store, tmp_path):
    from reco_io.features import extract_features
    from reco_io.extract import extract_and_record

    path = tmp_path / "book.txt"
    text = "窗外的风穿过长长的走廊，带来远处潮水的声音。" * 20
    path.write_text(text, encoding="utf-8")
    fr = extract_features(path)
    book_id = _seed_book(store)

    result = extract_and_record(store, book_id, path, now=NOW, config_fingerprint=CFG)

    assert result.book_id == book_id
    assert result.quality_flag == "ok"
    assert result.stats_complete is True
    assert result.vector_written is True
    assert result.source_mtime_ns == os.stat(path).st_mtime_ns

    (features_row,) = _rows(store, "features", book_id)
    (_bid, extractor_version, stats_json, char_freq_hash, ngram_digest,
     computed_at, source_mtime_ns) = features_row
    assert extractor_version == EXTRACTOR_VERSION
    assert computed_at == NOW
    assert source_mtime_ns == os.stat(path).st_mtime_ns
    stored_stats = json.loads(stats_json)
    assert stored_stats["char_count"] == fr.stats["char_count"]
    assert "char_freq" not in stored_stats
    assert char_freq_hash.hex() == expected_char_freq_hash(fr.char_freq)
    assert ngram_digest.hex() == expected_ngram_digest(fr.ngram_buckets)

    (vector_row,) = _rows(store, "feature_vectors", book_id)
    _vbid, blob, dim, idf_version, norm = vector_row
    expected = expected_vector(fr.ngram_buckets)
    assert blob.hex() == expected["vector_blob_hex"]
    assert (dim, idf_version, norm) == (expected["dim"], VECTOR_IDF_VERSION, expected["norm"])
    from reco_store.recommend import decode_vector_blob

    decoded = decode_vector_blob(bytes(blob), int(dim))
    assert decoded == {s: c / math.sqrt(sum(int(c2) ** 2 for _s2, c2 in fr.ngram_buckets if c2))
                       for s, c in fr.ngram_buckets if c}

    assert _event_types(store) == ["file_added", "features_computed", "vector_computed"]
    # 事件 payload 键集必须恰为契约键（白名单内不多不少）
    conn = store.read_conn()
    try:
        payloads = dict(conn.execute(
            "SELECT event_type, payload_json FROM events WHERE book_id = ? ORDER BY event_id",
            (book_id,),
        ).fetchall())
    finally:
        conn.close()
    assert set(json.loads(payloads["features_computed"])) == FEATURES_KEYS
    assert set(json.loads(payloads["vector_computed"])) == VECTOR_KEYS


def test_extract_and_record_projection_is_idempotent(store, tmp_path):
    from reco_io.extract import extract_and_record

    path = tmp_path / "book.txt"
    path.write_text("重复提取必须得到同一投影行。" * 30, encoding="utf-8")
    book_id = _seed_book(store)

    extract_and_record(store, book_id, path, now=NOW, config_fingerprint=CFG)
    first_features = _rows(store, "features", book_id)
    first_vectors = _rows(store, "feature_vectors", book_id)

    extract_and_record(store, book_id, path, now=NOW, config_fingerprint=CFG)
    assert _rows(store, "features", book_id) == first_features
    assert _rows(store, "feature_vectors", book_id) == first_vectors
    assert len(first_features) == len(first_vectors) == 1


def test_extract_and_record_bad_file_degrades_without_raising(store, tmp_path):
    from reco_io.extract import extract_and_record

    path = tmp_path / "bad.txt"
    path.write_bytes(b"\x80\x80\x80")  # utf-8 与 gb18030 双败（notes/04 已实测）
    book_id = _seed_book(store)

    result = extract_and_record(store, book_id, path, now=NOW, config_fingerprint=CFG)

    assert result.quality_flag == "undecodable"
    assert result.stats_complete is False
    assert result.vector_written is False
    (row,) = _rows(store, "features", book_id)
    assert json.loads(row[2]) == {}
    assert row[3] == b"" and row[4] == bytes.fromhex(expected_ngram_digest(()))
    assert _rows(store, "feature_vectors", book_id) == []
    assert _event_types(store) == ["file_added", "features_computed"]


def test_extract_and_record_empty_file_has_no_vector(store, tmp_path):
    from reco_io.extract import extract_and_record

    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    book_id = _seed_book(store)

    result = extract_and_record(store, book_id, path, now=NOW, config_fingerprint=CFG)

    assert result.quality_flag == "empty"
    assert result.stats_complete is True
    assert result.vector_written is False
    assert result.dim == 0
    assert _rows(store, "features", book_id) != []
    assert _rows(store, "feature_vectors", book_id) == []


def test_extract_and_record_missing_path_degrades_without_raising(store, tmp_path):
    from reco_io.extract import extract_and_record

    book_id = _seed_book(store)

    result = extract_and_record(store, book_id, tmp_path / "gone.txt",
                                now=NOW, config_fingerprint=CFG)

    assert result.quality_flag == "unreadable"
    assert result.stats_complete is False
    assert result.source_mtime_ns is None
    (row,) = _rows(store, "features", book_id)
    assert json.loads(row[2]) == {}
    assert row[6] is None
    assert _rows(store, "feature_vectors", book_id) == []


def _extract_module():
    from reco_io import extract

    return extract
