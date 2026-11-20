"""目录扫描、增量判定、落位与推荐专区写盘（reco-io 公开 API）。

工单 02：ensure_zones / scan_zones / classify_delta / FileFingerprint。
"""
from __future__ import annotations

from reco_io.scanner import (
    DEFAULT_ZONES,
    TEXT_EXTS,
    Changed,
    Delta,
    FileFingerprint,
    Moved,
    ScanResult,
    classify_delta,
    ensure_zones,
    scan_zones,
)
from reco_io.placement import (
    PlacementError,
    PlacementMode,
    PlacementPermissionError,
    PlacementPolicy,
    PlacementResult,
    PlacementVerificationError,
    StatInfo,
    place_by_hardlink,
)

__all__ = [
    "DEFAULT_ZONES",
    "TEXT_EXTS",
    "Changed",
    "Delta",
    "FileFingerprint",
    "Moved",
    "PlacementError",
    "PlacementMode",
    "PlacementPermissionError",
    "PlacementPolicy",
    "PlacementResult",
    "PlacementVerificationError",
    "ScanResult",
    "StatInfo",
    "classify_delta",
    "ensure_zones",
    "place_by_hardlink",
    "scan_zones",
]

__version__ = "0.1.0"
