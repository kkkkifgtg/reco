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

__all__ = ["mine_alias_counts"]


def mine_alias_counts(path, *, limit: int = 512) -> dict[str, int]:
    """单本正文 → 候选标签词计数（词频 Top limit；不可读/不支持 → 诚实空字典）。

    #92：词条先做繁体→简体归一再计数——同一词的繁/简形态合并为同一候选，
    避免繁体小说把同一标签拆成两个提案。
    """
    text = _decode_text(Path(path))
    if not text:
        return {}
    from reco_io.tokenizer import top_words  # 延迟导入：本模块 import 零成本

    return merge_simplified_counts(dict(top_words(text, limit=limit)))


def merge_simplified_counts(counts: dict[str, int]) -> dict[str, int]:
    """繁/简同形合并：键 t2s 归一为简体，计数相加；单字丢弃（#92）。"""
    from reco_io.t2s import to_simplified

    merged: dict[str, int] = {}
    for word, count in (counts or {}).items():
        key = to_simplified(word)
        if len(key) < 2:
            continue
        merged[key] = merged.get(key, 0) + count
    return merged


def _decode_text(path: Path) -> str:
    """格式解码：容器格式复用 features._READERS；txt/md 走 utf-8→gb18030 链。

    与 reco-ui.reader 同一先例（跨包复用 features 的读取器接缝）；任何解码
    失败都诚实返回空串（调用方得到 {}，与提取降级口径一致），不猜内容。
    """
    from reco_io.features import _READERS  # noqa: SLF001——既有跨包先例（reader.py）

    try:
        reader = _READERS.get(path.suffix.lower())
        if reader is not None:
            return str(reader(path) or "")
        raw = path.read_bytes()
    except (OSError, ValueError, UnicodeDecodeError, RuntimeError):
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError:
            return ""
