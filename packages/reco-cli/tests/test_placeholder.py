"""T0 占位测试：入口可导入、可执行且返回码为 0。"""

from __future__ import annotations

import reco_cli


def test_main_returns_zero(capsys) -> None:
    assert reco_cli.main([]) == 0
    out = capsys.readouterr().out
    assert "placeholder" in out
    assert isinstance(reco_cli.__all__, list)
