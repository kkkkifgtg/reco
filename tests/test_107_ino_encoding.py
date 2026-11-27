"""#107 —— 事件里的文件身份必须过 int64 编码层（Windows 无符号 64 位）。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

T0 = "2026-11-21T00:00:00+00:00"


def test_store_rejects_out_of_int64_identity_with_readable_error(tmp_path):
    from reco_store import open_store
    from reco_store.store import PayloadViolationError

    store = open_store(tmp_path / "ino.db")
    try:
        with pytest.raises(PayloadViolationError) as err:
            store.append_event("file_added", payload={
                "path": "F:/liked/a.epub", "st_dev": 1, "st_ino": 2**63 + 5,
                "size_bytes": 1, "mtime_ns": 1, "ext": "epub", "zone": "liked",
                "display_name": "a.epub", "normalized_name": "a.epub",
                "dedup_key": "a.epub:1",
            }, occurred_at=T0, config_fingerprint="t107/1")
        assert "to_int64" in str(err.value)
        # 拒绝发生在写事件之前：库里不应留下半条事件。
        assert store.read_events() == []
    finally:
        store.close()


def test_ui_identity_encoder_wraps_unsigned_ino():
    from reco_io.int64 import to_int64
    from reco_ui.pages import _encoded_identity

    class _S:
        st_dev, st_ino = 3, 2**63 + 5

    assert _encoded_identity(_S()) == (3, to_int64(2**63 + 5))