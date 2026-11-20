"""PyQt6 桌面 GUI 入口（T0 占位，T11 前不引入 PyQt6）。"""

from __future__ import annotations

__all__ = ["main"]

__version__ = "0.1.0"


def main(argv: list[str] | None = None) -> int:
    """T0 占位入口：打印占位信息后正常退出（返回码 0）。"""
    _ = argv  # T0 不解析参数，真实 GUI 见 T11。
    print(
        f"reco-ui {__version__} (placeholder) —— 尚未实现，"
        "后续工单补齐；当前仅用于验证安装与入口可用。"
    )
    return 0
