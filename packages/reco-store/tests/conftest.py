"""reco-store 测试共享 fixture：fixture 惰性 import（collection-safe 在途红形态）。"""

from __future__ import annotations

import pytest


@pytest.fixture
def store_module():
    from reco_store import store

    return store


@pytest.fixture
def store(store_module, tmp_path):
    s = store_module.open_store(tmp_path / "store.db")
    yield s
    s.close()
