"""CWE-732 回归：新建 SQLite 库收紧权限到 0600（POSIX）。"""
from __future__ import annotations

import os

import pytest

from reco_store import open_store


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限位")
def test_new_store_mode_0600(tmp_path):
    db = tmp_path / "reco.db"
    store = open_store(db)
    try:
        assert (db.stat().st_mode & 0o777) == 0o600
    finally:
        store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限位")
def test_existing_store_mode_untouched(tmp_path):
    db = tmp_path / "reco.db"
    store = open_store(db)
    store.close()
    os.chmod(db, 0o644)
    store = open_store(db)
    try:
        assert (db.stat().st_mode & 0o777) == 0o644
    finally:
        store.close()
