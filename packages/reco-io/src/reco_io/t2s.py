"""繁体→简体转换（#92）：优先 zhconv（自带词库），缺失时诚实降级原样返回。

用途：标签挖掘的词条规范化——同一词的繁/简两种形态归一为简体计数，
避免拆成两个候选。转换纯函数化；缺失依赖时不中断挖掘。
"""
from __future__ import annotations


def to_simplified(text: str) -> str:
    """繁体→简体；zhconv 缺失时原样返回（不猜、不中断）。"""
    if not text:
        return text
    try:
        import zhconv

        return zhconv.convert(text, "zh-hans")
    except Exception:
        return text
