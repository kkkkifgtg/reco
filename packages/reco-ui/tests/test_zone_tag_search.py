"""#91——专区搜索命中标签：按标签筛选书籍（书名 OR 标签名 OR tag_id）。"""
from __future__ import annotations

from reco_labels.auto_tag import StoreAdapter
from reco_labels.seed import build_seed_rows
from reco_store import open_store
from reco_ui.glue import zone_rows


def _seed_book_with_tag(tmp_path, name: str, *, tag_label: str | None) -> int:
    s = open_store(tmp_path / "zone.db")
    adapter = StoreAdapter(s)
    for row in build_seed_rows():
        adapter.upsert_tag(row)
    s.append_event(
        "file_added",
        payload={"path": f"F:/date/adult-reco/data/liked/{name}", "zone": "liked",
                 "ext": ".txt", "size_bytes": 100, "mtime_ns": 1, "st_dev": 1,
                 "st_ino": abs(hash(name)) % 100000, "display_name": name,
                 "normalized_name": name, "dedup_key": "k-" + name},
        occurred_at="2026-11-21T00:00:00+00:00", config_fingerprint="t/1")
    conn = s.read_conn()
    try:
        bid = int(conn.execute("SELECT book_id FROM files WHERE path = ?",
                               (f"F:/date/adult-reco/data/liked/{name}",)).fetchone()[0])
    finally:
        conn.close()
    if tag_label:
        target = next(r.tag_id for r in adapter.iter_tags() if r.label == tag_label)
        adapter.record_book_tag(bid, target, source="auto", confidence=1.0,
                                basis_json="{}", updated_at="2026-11-21T00:00:00")
    s.close()
    return bid


def test_zone_search_matches_tag_label(tmp_path):
    bid = _seed_book_with_tag(tmp_path, "tagged-novel.txt", tag_label="催眠")
    s = open_store(tmp_path / "zone.db")
    try:
        hits = zone_rows(s, "liked", keyword="催眠")
        assert any(int(r[0]) == bid for r in hits)
        misses = zone_rows(s, "liked", keyword="绝不存在的标签xyz")
        assert not any(int(r[0]) == bid for r in misses)
    finally:
        s.close()


def test_zone_search_still_matches_book_name(tmp_path):
    bid = _seed_book_with_tag(tmp_path, "tagged-novel.txt", tag_label="催眠")
    s = open_store(tmp_path / "zone.db")
    try:
        hits = zone_rows(s, "liked", keyword="tagged-novel")
        assert any(int(r[0]) == bid for r in hits)
    finally:
        s.close()
