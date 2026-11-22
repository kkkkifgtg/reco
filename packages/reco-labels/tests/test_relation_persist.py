"""#69 回归：有标签的书必须产生关系图并落库。

修复前：extract_relation_graph 无任何生产调用点，relation_graphs 表永远 0 行。
修复后：auto_tag_book 命中的标签 → 权重 → 关系图 → relation_updated 事件 → 投影落表。
"""
from __future__ import annotations

import json

import pytest

NOW = "2026-11-22T09:00:00+00:00"


def _seed_taxonomy(store):
    """灌入种子标签（含 mech.hypnosis 的别名索引），match_tags 才有词表可用。"""
    from reco_labels.auto_tag import StoreAdapter
    from reco_labels.seed import build_seed_rows

    adapter = StoreAdapter(store)
    for row in build_seed_rows():
        adapter.upsert_tag(row)


def _seed_with_tags(store, tags=("mech.hypnosis",), zone="liked"):
    """落一本书并挂上标签（先灌 taxonomy，再用真实标签 id）。"""
    _seed_taxonomy(store)
    from reco_store import dedup_key

    name = "关系测试书.txt"
    store.append_event(
        "file_added",
        payload={
            "path": "F:/fake/" + name, "zone": zone, "ext": ".txt",
            "size_bytes": 100, "mtime_ns": 1, "st_dev": 1, "st_ino": 1,
            "display_name": name, "normalized_name": name, "dedup_key": "k1",
        },
        occurred_at=NOW, config_fingerprint="t/1", actor="user",
    )
    conn = store.read_conn()
    try:
        book_id = int(conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
    finally:
        conn.close()
    for i, tag_id in enumerate(tags):
        store.append_event(
            "tag_added",
            payload={"tag_id": tag_id, "axis": tag_id.split(".")[0], "label": tag_id},
            occurred_at=NOW, config_fingerprint="t/1",
        )
        store.append_event(
            "label_set",
            payload={"tag_id": tag_id, "source": "auto", "confidence": 1.0, "basis_json": "{}"},
            book_id=book_id, occurred_at=NOW, config_fingerprint="t/1", actor="system",
        )
    return book_id


def _stats(alias_counts=None):
    """构造 match_tags 的输入：必须有 alias_counts 才会命中标签。"""
    return {
        "character_count": 2,
        "alias_counts": alias_counts or {"精神控制": 3},
    }


def _relation_row(store, book_id):
    conn = store.read_conn()
    try:
        return conn.execute(
            "SELECT graph_json, extractor_version, confidence FROM relation_graphs WHERE book_id=?",
            (book_id,),
        ).fetchone()
    finally:
        conn.close()


def test_relation_graph_persisted_after_autotag(store):
    """自动打标后关系图应落库。"""
    from reco_labels.auto_tag import StoreAdapter, auto_tag_book

    book_id = _seed_with_tags(store)
    adapter = StoreAdapter(store)
    # stats 里给 character_count，让角色维度可填充
    outcome = auto_tag_book(adapter, book_id, "liked", _stats(), now=NOW)
    assert outcome.hits, "前置条件：应有标签命中"

    row = _relation_row(store, book_id)
    assert row is not None, "有标签的书必须产生关系图行"
    graph = json.loads(row[0])
    assert graph["edges"], "两人时应生成边"
    assert "mech.hypnosis" in graph["edges"][0]["mechanism"], "机制应含 mech.hypnosis"


def test_relation_graph_idempotent(store):
    """重复提取幂等：行数不变、内容一致。"""
    from reco_labels.auto_tag import StoreAdapter, auto_tag_book

    book_id = _seed_with_tags(store)
    adapter = StoreAdapter(store)
    auto_tag_book(adapter, book_id, "liked", _stats(), now=NOW)
    first = _relation_row(store, book_id)

    auto_tag_book(adapter, book_id, "liked", _stats(), now=NOW)
    second = _relation_row(store, book_id)

    conn = store.read_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM relation_graphs WHERE book_id=?", (book_id,)).fetchone()[0]
    finally:
        conn.close()
    assert count == 1, "重复提取不得产生重复行"
    assert first[0] == second[0], "同样输入应得到同样关系图"


def test_no_tags_no_relation_graph(store):
    """无标签的书不产生关系图（诚实留空，不造假图）。"""
    from reco_labels.auto_tag import StoreAdapter, auto_tag_book

    from reco_store import dedup_key
    name = "无标签书.txt"
    store.append_event(
        "file_added",
        payload={"path": "F:/fake/" + name, "zone": "liked", "ext": ".txt",
                 "size_bytes": 100, "mtime_ns": 1, "st_dev": 1, "st_ino": 1,
                 "display_name": name, "normalized_name": name, "dedup_key": "k2"},
        occurred_at=NOW, config_fingerprint="t/1", actor="user",
    )
    conn = store.read_conn()
    try:
        book_id = int(conn.execute("SELECT book_id FROM books ORDER BY book_id DESC LIMIT 1").fetchone()[0])
    finally:
        conn.close()

    adapter = StoreAdapter(store)
    auto_tag_book(adapter, book_id, "liked", {"character_count": 1, "alias_counts": {}}, now=NOW)
    assert _relation_row(store, book_id) is None, "无标签不应产生关系图"


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store
    s = open_store(tmp_path / "rel.db")
    yield s
    s.close()
