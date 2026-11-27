"""#105：rank 书号 "b<N>" 的构造与解析各只有一处（reco_store.rank_id）。"""
from __future__ import annotations

import pytest

from reco_store.rank_id import format_rank_book_id, parse_rank_book_id


@pytest.mark.parametrize("book_id", [0, 1, 7, 203])
def test_format_round_trips_through_parse(book_id):
    raw = format_rank_book_id(book_id)
    assert raw == f"b{book_id}"
    assert parse_rank_book_id(raw) == book_id


@pytest.mark.parametrize("raw,expected", [
    ("b7", 7), ("B7", 7), ("  b7  ", 7), ("7", 7), (7, 7), (None, None),
    ("b", None), ("bx", None), ("", None), ("b3.5", None),
])
def test_parse_accepts_rank_namespace_and_bare_digits(raw, expected):
    assert parse_rank_book_id(raw) == expected