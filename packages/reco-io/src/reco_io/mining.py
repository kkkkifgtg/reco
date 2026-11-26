"""正文 → 标签候选词挖掘（#83；报告人指令：分词→剔虚词/常用词→高频词即候选标签）。

- 分词复用 reco_io.tokenizer（jieba 优先、ngram 兜底；虚词过滤内建，单字不取）；
- 产出 词→计数，与 W4 接缝 stats.alias_counts 同形，调用方直接喂 auto_tag_book；
- 已知标签词由 match_tags 命中落 book_tags；未知高频词走 proposals（双层边界不变）；
  与既有值标签显示名仅差一字的谐音/形近变体 → alias 提案（挂到原标签，不拆分类）；
- 正文与片段绝不落库/落日志（§5.6/B2）：本模块只返回计数字典；
- jieba 依赖纪律：经 tokenizer 延迟导入（可选依赖，venv 已装、不入锁——同 torch 先例）。
"""
from __future__ import annotations

from pathlib import Path

__all__ = [
    "mine_alias_counts", "mine_alias_counts_with_variants",
    "merge_simplified_counts", "merge_simplified_counts_with_variants",
]


def mine_alias_counts(path, *, limit: int = 512) -> dict[str, int]:
    """单本正文 → 候选标签词计数（词频 Top limit；不可读/不支持 → 诚实空字典）。

    #92：正文先做繁体→简体归一再分词——同一词的繁/简形态合并为同一候选，
    避免繁体小说把同一标签拆成两个提案。
    """
    return mine_alias_counts_with_variants(path, limit=limit)[0]


def mine_alias_counts_with_variants(
    path, *, limit: int = 512,
) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    """同上，额外返回 {简体键: 繁体原形}（#92 别名保留用）。"""
    text = _decode_text(Path(path))
    if not text:
        return {}, {}
    from reco_io.tokenizer import top_words  # 延迟导入：本模块 import 零成本
    from reco_io.t2s import to_simplified

    # #92：先归一再分词。繁体原文直接分词会被拆成「催眠」+「術」，简体原文
    # 却是「催眠术」——同一个词在线路两头成了两个候选，计数永远合不到一起。
    return merge_simplified_counts_with_variants(
        dict(top_words(to_simplified(text), limit=limit))
    )


def merge_simplified_counts(counts: dict[str, int]) -> dict[str, int]:
    """繁/简同形合并：键 t2s 归一为简体，计数相加；单字丢弃（#92）。"""
    return merge_simplified_counts_with_variants(counts)[0]


def merge_simplified_counts_with_variants(
    counts: dict[str, int],
) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    """#92：合并繁/简计数，并给出每个简体键的繁体原形（别名保留的原料）。

    只归一不记原形，「催眠術」这个写法就被系统遗忘了——用户嘴里的词进不了
    标签体系（搜不到、也挂不回）。原形从简体键反推（zhconv 繁化），而不是回
    原文里找：分词器会把「催眠術」拆碎，字面回收不到。
    """
    from reco_io.t2s import to_simplified, to_traditional

    merged: dict[str, int] = {}
    for word, count in (counts or {}).items():
        key = to_simplified(str(word))
        if len(key) < 2:
            continue
        merged[key] = merged.get(key, 0) + count
    variants: dict[str, tuple[str, ...]] = {}
    for key in merged:
        trad = to_traditional(key)
        if trad != key:
            variants[key] = (trad,)
    return merged, variants


def _decode_text(path: Path) -> str:
    """格式解码：容器格式复用 features._READERS，纯文本走 textkit 的唯一解码链。

    与阅读器、提取层同一条链（BOM → utf-8 → gb18030，统一 LF）；任何解码失败都
    诚实返回空串（调用方得到 {}，与提取降级口径一致），不猜内容。
    """
    from reco_io.features import _READERS  # noqa: SLF001——既有跨包先例（reader.py）
    from reco_io.textkit import decode_bytes

    try:
        reader = _READERS.get(path.suffix.lower())
        if reader is not None:
            return str(reader(path) or "")
        raw = path.read_bytes()
    except (OSError, ValueError, UnicodeDecodeError, RuntimeError):
        return ""
    try:
        return decode_bytes(raw)
    except UnicodeDecodeError:
        return ""