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


def to_traditional(text: str) -> str:
    """简体→繁体（#92 别名保留用）；zhconv 缺失时原样返回。

    用途：从简体候选反推它可能被写成什么样——「催眠术」的繁体原形是
    「催眠術」。反推与分词结果无关，比回原文里找更可靠（分词器会把
    「催眠術」拆碎，字面回收不到）。
    """
    if not text:
        return text
    try:
        import zhconv

        return zhconv.convert(text, "zh-hant")
    except Exception:
        return text
