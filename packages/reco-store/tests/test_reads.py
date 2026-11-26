"""#105：UI/CLI 的 files 读面统一走 reco_store.reads（投影口径只此一份）。"""
from __future__ import annotations

import pytest

from reco_store import open_store
from reco_store.reads import (
    book_id_by_path,
    count_present_in_zone,
    first_present_file,
    first_present_path,
    present_book_zones,
    present_files,
    present_files_in_zone,
    source_row,
    zone_file_identities,
    zone_page,
)

pytestmark = pytest.mark.integration

T0 = "2026-11-21T00:00:00+00:00"


def _add(store, *, path, zone, name, ino, size=100, dev=1):
    store.append_event("file_added", payload={
        "path": path, "zone": zone, "ext": "epub", "size_bytes": size,
        "mtime_ns": 1000 + ino, "st_dev": dev, "st_ino": ino,
        "display_name": name, "normalized_name": name,
        "dedup_key": f"{name}:{size}",
    }, occurred_at=T0, config_fingerprint="t105/1")


@pytest.fixture()
def seeded(tmp_path):
    store = open_store(tmp_path / "reads.db")
    try:
        _add(store, path="F:/candidate/a.epub", zone="candidate", name="a.epub", ino=1)
        _add(store, path="F:/liked/a.epub", zone="liked", name="a.epub", ino=2)
        _add(store, path="F:/candidate/b.epub", zone="candidate", name="b.epub", ino=3)
        yield store
    finally:
        store.close()


def test_present_files_and_zones(seeded):
    paths = {(b, p, s) for b, p, s in present_files(seeded)}
    assert "F:/candidate/b.epub" in {p for _b, p, _s in paths}
    assert {b for b, _p, _s in paths} == {1, 2}
    # 同书双区：a.epub 在 candidate 与 liked 各一行，两行都是 book_id 1
    assert set(present_book_zones(seeded)) == {(1, "candidate"), (1, "liked"), (2, "candidate")}


def test_book_id_by_path_and_zone_scoping(seeded):
    assert book_id_by_path(seeded, "F:/candidate/a.epub") == 1
    assert book_id_by_path(seeded, "F:/candidate/a.epub", zone="liked") is None
    assert book_id_by_path(seeded, "F:/nope.epub") is None


def test_first_present_file_prefers_zone_then_file_id(seeded):
    assert first_present_file(seeded, 1, zone="candidate") == ("F:/candidate/a.epub", "candidate")
    assert first_present_path(seeded, 1) == "F:/candidate/a.epub"
    assert first_present_file(seeded, 1, zone="推荐专区") is None


def test_source_row_carries_display_name(seeded):
    assert source_row(seeded, 1, "candidate") == ("F:/candidate/a.epub", "a.epub")
    assert source_row(seeded, 2, "liked") is None


def test_zone_counts_and_identities(seeded):
    assert count_present_in_zone(seeded, "candidate") == 2
    ids = zone_file_identities(seeded, "liked")
    assert ids == [("F:/liked/a.epub", 1, 2, 100, 1002)]


def test_present_files_in_zone_respects_since(seeded):
    assert {(b, z) for b, _p, z in present_files_in_zone(seeded, "candidate")} == {(1, "candidate"), (2, "candidate")}
    assert present_files_in_zone(seeded, "candidate", since="2999-01-01T00:00:00+00:00") == []


def test_zone_page_reports_other_zones(seeded):
    rows = zone_page(seeded, "candidate")
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][3] == frozenset({"liked"})
    assert rows[1][3] == frozenset()
    assert [r[0] for r in zone_page(seeded, "candidate", keyword="b.epub")] == [2]