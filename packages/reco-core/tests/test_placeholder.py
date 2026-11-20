"""T0 占位测试：确认包可导入、公开出口声明就位。"""

from __future__ import annotations

import reco_core


def test_package_importable() -> None:
    assert reco_core.__version__ == "0.1.0"
    assert isinstance(reco_core.__all__, list)
