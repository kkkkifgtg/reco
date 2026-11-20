"""PyQt6 桌面 GUI 入口（工单 12）。"""
from __future__ import annotations

import sys

__all__ = ["main"]
__version__ = "0.1.0"


def _pyqt6_available() -> bool:
    try:
        import reco_ui.qt  # noqa: F401  薄适配层可导入 ⇔ PyQt6 可用（§11.5）
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    """GUI 启动入口；Qt 不可用时明确停用并指向 CLI 全功能退路（F4/R14）。"""
    if not _pyqt6_available():
        print("GUI 停用：PyQt6 不可用（导入失败或未安装）。CLI 全功能可用：", file=sys.stderr)
        print("python -m reco_cli scan / recommend / feedback / report / explain / replay", file=sys.stderr)
        return 2
    from reco_ui.app import run

    return run(argv)

