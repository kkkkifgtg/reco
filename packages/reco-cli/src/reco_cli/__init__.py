"""无界面命令行入口（T0 占位，真实命令待 T10）。"""

from __future__ import annotations

__all__ = ["main"]

__version__ = "0.1.0"


def main(argv: list[str] | None = None) -> int:
    """T0 占位入口：打印占位信息后正常退出（返回码 0）。"""
    _ = argv  # T0 不解析参数，真实命令见 T10。
    print(
        f"reco-cli {__version__} (placeholder) —— 尚未实现，"
        "后续工单补齐；当前仅用于验证安装与入口可用。"
    )
    return 0
