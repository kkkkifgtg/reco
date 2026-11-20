"""工单 12 切片 4：画像页（轴权重/样本量/置信度/Top-N/漂移/版本）+ 生成篇数输入（默认 2）。

红相（TDD）：reco_ui.app.ProfilePage 尚不存在 → 导入失败转红。
C6：置信度诚实展示（n_pos<10 上限 0.3 + 「仅供参考」文案，不出现 AUC 字样）。
"""
from __future__ import annotations

import os

os_environ_set = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from reco_ui.glue import seed_books


@pytest.fixture()
def store(tmp_path):
    from reco_store import open_store

    s = open_store(tmp_path / "gui.db")
    seed_books(s, books=6, liked=3, candidate=2, now="2026-11-21T04:00:00+00:00")
    yield s
    s.close()


@pytest.fixture(scope="module")
def qapp():
    from reco_ui.qt import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_profile_page_fields_render(qapp, store):
    """画像页渲染轴权重/样本量/置信度/Top-N/漂移/版本；空库诚实降级文案。"""
    from reco_ui.app import ProfilePage

    page = ProfilePage(store)
    page.refresh()
    text = page.summary_text()
    # 空画像：诚实降级（「还不够准」文案，无伪造数值）
    assert "还不够准" in text
    for keyword in ("轴权重", "样本量", "置信度", "Top-N", "漂移", "版本"):
        assert keyword in text


def test_generate_spin_default_two(qapp, store):
    """生成篇数输入默认 2（§17 Q7 探索位口径一致）。"""
    from reco_ui.app import RecommendPage

    page = RecommendPage(store)
    assert page.generate_spin.value() == 2
    assert page.generate_spin.minimum() >= 1


def test_recommend_page_no_second_ranking_in_source(qapp, store):
    """A6 静态守护：reco_ui 源码无排序调用（rank 是唯一排序入口）。"""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "reco_ui"
    offenders = []
    for p in src.glob("*.py"):
        if re.search(r"\.\s*(sort|sorted)\s*\(", p.read_text(encoding="utf-8")):
            offenders.append(p.name)
    assert offenders == []
