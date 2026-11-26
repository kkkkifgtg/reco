"""#108 残余半边：事件协议两层，名字必须不同。

reco_labels 的 `LabelsStore` 收的是标签词表（`EVENT_KINDS`），由 `StoreAdapter`
分派映射到 reco-store 的事件词表；`reco_store.Store.append_event` 才是写事件、
返回自增 id 的那一层。同名会让人以为是一次直通转发——并诱导把 store 的关键字
参数（`config_fingerprint`）按位置传进 labels 层。

本文件钉住三件事：名字分层、labels 层的 `occurred_at` 只能按关键字传、适配器
确实把 labels 事件分派成 store 事件（而不是静默丢弃或同名转发）。
"""
from __future__ import annotations

import inspect

import pytest

from reco_labels.auto_tag import StoreAdapter
from reco_labels.store import InMemoryLabelsStore, LabelsStore
from reco_store import open_store

AT = "2026-01-01T00:00:00+00:00"


def _last_event_kind(store) -> str | None:
    conn = store.read_conn()
    try:
        row = conn.execute(
            "SELECT event_type FROM events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else str(row[0])


def _event_count(store) -> int:
    conn = store.read_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        conn.close()


def test_labels_and_store_layers_do_not_share_the_name():
    """labels 层叫 record_label_event，store 层才叫 append_event。"""
    assert hasattr(LabelsStore, "record_label_event")
    assert not hasattr(LabelsStore, "append_event")
    assert hasattr(InMemoryLabelsStore, "record_label_event")
    assert not hasattr(InMemoryLabelsStore, "append_event")
    assert hasattr(StoreAdapter, "record_label_event")
    assert not hasattr(StoreAdapter, "append_event")


def test_store_layer_signature_keeps_event_type_first(tmp_path):
    """store 层仍是 (event_type, *, book_id, payload, occurred_at, ...)。"""
    store = open_store(tmp_path / "layers.db")
    try:
        params = inspect.signature(store.append_event).parameters
        assert list(params)[0] == "event_type"
        for name in ("book_id", "payload", "occurred_at", "config_fingerprint"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["occurred_at"].kind is inspect.Parameter.KEYWORD_ONLY
    finally:
        store.close()


def test_labels_layer_occurred_at_is_keyword_only():
    """位置传时间会在两层之间悄悄换语义——labels 层强制关键字。"""
    for impl in (LabelsStore, InMemoryLabelsStore, StoreAdapter):
        sig = inspect.signature(impl.record_label_event)
        assert sig.parameters["occurred_at"].kind is inspect.Parameter.KEYWORD_ONLY

    store = InMemoryLabelsStore()
    with pytest.raises(TypeError):
        store.record_label_event("tag_archived", {"tag_id": "t-1"}, AT)  # type: ignore[misc]


def test_adapter_dispatches_labels_event_to_store_event(tmp_path):
    """适配器把 labels 词表映射成 store 事件，而不是同名转发或静默丢弃。"""
    raw = open_store(tmp_path / "dispatch.db")
    try:
        adapter = StoreAdapter(raw)
        before = _event_count(raw)
        adapter.record_label_event("tag_archived", {"tag_id": "t-1"}, occurred_at=AT)
        assert _event_count(raw) == before + 1
        assert _last_event_kind(raw) == "tag_archived"
    finally:
        raw.close()


def test_folded_labels_event_writes_nothing_to_store(tmp_path):
    """tag_created/tag_proposed 已由写方法落库，事件面折叠为 no-op（不重复写）。"""
    raw = open_store(tmp_path / "folded.db")
    try:
        adapter = StoreAdapter(raw)
        before = _event_count(raw)
        adapter.record_label_event("tag_created", {"tag_id": "t-1"}, occurred_at=AT)
        assert _event_count(raw) == before
    finally:
        raw.close()


def test_unknown_labels_event_rejected_before_any_write(tmp_path):
    """未知 labels 词表在事件面之前拒绝（与 store 层各自的词表守卫不混用）。"""
    raw = open_store(tmp_path / "unknown.db")
    try:
        adapter = StoreAdapter(raw)
        before = _event_count(raw)
        with pytest.raises(ValueError):
            adapter.record_label_event("file_added", {"path": "x"}, occurred_at=AT)
        assert _event_count(raw) == before
    finally:
        raw.close()

