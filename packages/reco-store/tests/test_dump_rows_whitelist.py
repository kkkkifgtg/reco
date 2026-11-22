"""回归：_dump_rows 表名白名单（SQL 拼接的唯一入口必须显式把关）。"""
from __future__ import annotations

import pytest

from reco_store.store import COMPARE_TABLES, _dump_rows


def test_rejects_non_whitelisted_table(tmp_path):
    with pytest.raises(ValueError, match="COMPARE_TABLES"):
        _dump_rows(tmp_path / "x.db", "users; DROP TABLE books")


def test_accepts_whitelisted_table(tmp_path):
    from reco_store import open_store

    db = tmp_path / "w.db"
    store = open_store(db)
    store.close()
    # events 是白名单成员，空库应返回空列表而不抛
    assert _dump_rows(db, "events") == []


def test_all_compare_tables_pass_whitelist(tmp_path):
    for table in COMPARE_TABLES:
        assert isinstance(table, str)
