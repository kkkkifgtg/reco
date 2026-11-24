"""#85——GUI 画像接线测试：单本处理/导入/扫描后 book_tags 与 tag_hit 正确落库。"""
from __future__ import annotations

import pytest

from reco_labels.auto_tag import StoreAdapter
from reco_labels.seed import build_seed_rows
from reco_store import open_store
from reco_ui.tagging import book_id_by_path, process_book_for_profile

TEXT = ("他催眠了她。催眠的力量强大。调教与驯化交织。"
        "摄心术无处不在。摄心术再次出现。摄心术第三次。")


@pytest.fixture()
def store(tmp_path):
    s = open_store(tmp_path / "ui.db")
    yield s
    s.close()


def _seed_taxonomy(store) -> None:
    adapter = StoreAdapter(store)
    for row in build_seed_rows():
        adapter.upsert_tag(row)


def _seed_book_file(store, path: str, zone: str) -> None:
    name = path.replace("\\", "/").split("/")[-1]
    store.append_event(
        "file_added",
        payload={"path": path, "zone": zone, "ext": ".txt",
                 "size_bytes": 123, "mtime_ns": 1, "st_dev": 1, "st_ino": 1,
                 "display_name": name, "normalized_name": name,
                 "dedup_key": "k-" + name},
        occurred_at="2026-11-21T00:00:00+00:00", config_fingerprint="t/1")


def test_process_liked_book_tags_and_folds_profile(store, tmp_path):
    _seed_taxonomy(store)
    p = tmp_path / "liked-book.txt"
    p.write_text(TEXT, encoding="utf-8")
    _seed_book_file(store, str(p), "liked")

    res = process_book_for_profile(store, str(p), "liked",
                                   now="2026-11-21T00:00:00+00:00")
    assert res["tagged"] is True and res["folded"] is True
    tags = {r.tag_id for r in StoreAdapter(store).iter_book_tags()}
    assert "mech.hypnosis" in tags
    kinds = [e.event_type for e in store.read_events()]
    assert "tag_hit" in kinds
    assert "features_computed" in kinds


def test_process_candidate_book_tags_without_folding(store, tmp_path):
    _seed_taxonomy(store)
    p = tmp_path / "cand-book.txt"
    p.write_text(TEXT, encoding="utf-8")
    _seed_book_file(store, str(p), "candidate")

    res = process_book_for_profile(store, str(p), "candidate",
                                   now="2026-11-21T00:00:00+00:00")
    assert res["tagged"] is True and res["folded"] is False
    assert "tag_hit" not in [e.event_type for e in store.read_events()]


def test_process_skips_when_file_row_missing(store, tmp_path):
    res = process_book_for_profile(store, str(tmp_path / "ghost.txt"), "liked",
                                   now="2026-11-21T00:00:00+00:00")
    assert res.get("skipped") is True


def test_book_id_by_path(store, tmp_path):
    p = tmp_path / "b.txt"
    p.write_text("x", encoding="utf-8")
    _seed_book_file(store, str(p), "liked")
    assert book_id_by_path(store, str(p)) is not None
    assert book_id_by_path(store, str(tmp_path / "nope.txt")) is None


def test_import_worker_wires_tagging(store, tmp_path):
    """导入 worker 落库后接通打标与画像折叠（#85）。"""
    from reco_ui.pages import ImportWorker

    _seed_taxonomy(store)
    src = tmp_path / "src.txt"
    src.write_text(TEXT, encoding="utf-8")
    worker = ImportWorker(store, "liked", tmp_path / "data", [str(src)])
    worker.run()

    kinds = [e.event_type for e in store.read_events()]
    assert "tag_hit" in kinds
    tags = {r.tag_id for r in StoreAdapter(store).iter_book_tags()}
    assert "mech.hypnosis" in tags


def test_scan_worker_wires_tagging_for_touched_books(store, tmp_path):
    """再扫描触及的书接通画像（#85）：文件就位 → 扫描 → 打标+折叠。"""
    from reco_ui.pages import ScanWorker

    _seed_taxonomy(store)
    data_root = tmp_path / "data"
    (data_root / "liked").mkdir(parents=True)
    book = data_root / "liked" / "scan-novel.txt"
    book.write_text(TEXT, encoding="utf-8")
    worker = ScanWorker(store, "liked", str(data_root))
    worker.run()

    kinds = [e.event_type for e in store.read_events()]
    assert "file_added" in kinds and "tag_hit" in kinds
    tags = {r.tag_id for r in StoreAdapter(store).iter_book_tags()}
    assert "mech.hypnosis" in tags
