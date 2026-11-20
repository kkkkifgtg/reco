"""本地特征库与 append-only 事件流（reco-store 公开 API；工单 05）。"""

from __future__ import annotations

from reco_store.store import (
    APP_VERSION,
    COMPARE_TABLES,
    SCHEMA_VERSION,
    Event,
    PayloadViolationError,
    ReplayReport,
    Store,
    Synchronous,
    dedup_key,
    normalize_book_name,
    open_store,
    rebuild_projections,
)

__all__ = [
    "APP_VERSION",
    "COMPARE_TABLES",
    "SCHEMA_VERSION",
    "Event",
    "PayloadViolationError",
    "ReplayReport",
    "Store",
    "Synchronous",
    "dedup_key",
    "normalize_book_name",
    "open_store",
    "rebuild_projections",
]

__version__ = "0.1.0"
