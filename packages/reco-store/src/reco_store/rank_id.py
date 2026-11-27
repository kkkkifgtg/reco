"""推荐书号命名空间 "b<N>" 的唯一构造/解析处（#105）。

rank 输出（load_book_names / load_eval_samples / rank_candidates）构造书号，
UI/CLI 再解析回整数 book_id。只此一份实现，避免各入口各写 lstrip("b") 而漂移。
"""
from __future__ import annotations

__all__ = ["format_rank_book_id", "parse_rank_book_id"]

_PREFIX = "b"


def format_rank_book_id(book_id: int) -> str:
    """整数 book_id → rank 书号 "b<N>"。"""
    return f"{_PREFIX}{int(book_id)}"


def parse_rank_book_id(raw) -> int | None:
    """rank 书号 "b<N>"（也容忍裸 "N"）→ 整数 book_id；解析不了返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    digits = text[1:] if text[:1] in ("b", "B") else text
    try:
        return int(digits)
    except ValueError:
        return None