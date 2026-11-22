"""回归：GUI 默认运行库必须解析到仓库根 reco.db。

背景：#58 之后用户反馈「点开始推荐显示『库中缺少特征向量』」。根因是 B2 目录上移后，
_default_db 用固定 parents[3] 解析到仓库的 packages/ 目录，打开了一个空的 packages/reco.db。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_default_db_is_repo_root():
    from pathlib import Path

    from reco_ui.app import _default_db_path

    db = _default_db_path()
    assert db.name == "reco.db"
    # 仓库根判定：同目录应有 pyproject.toml 与 packages/
    root = db.parent
    assert (root / "pyproject.toml").is_file(), "默认库应位于含 pyproject.toml 的仓库根"
    assert (root / "packages").is_dir(), "默认库应位于含 packages/ 的仓库根"
    # 明确排除误解析到 packages/ 子目录的旧 bug
    assert db.parent.name != "packages", "默认库不得落在 packages/ 目录"


def test_default_db_not_inside_package_tree():
    from reco_ui.app import _default_db_path

    db = _default_db_path()
    here = __import__("pathlib").Path(__file__).resolve()
    pkg_src = here.parents[2] / "src"  # packages/reco-ui/src
    assert pkg_src not in db.parents, "默认库不能位于包源码树内"
