"""工单 11 后的入口契约测试（原 T0 占位测试按新契约重写）。

- 无参数 = 帮助 + exit 0（T0 契约延续）
- 输出不再是占位文案（工单 11 替换占位版）
- 版本随 CLI 实装升 0.2.0
"""

from __future__ import annotations

import pytest

import reco_cli


def test_main_no_args_shows_help_and_returns_zero(capsys):
    assert reco_cli.main([]) == 0
    out = capsys.readouterr().out
    assert "usage:" in out
    assert "placeholder" not in out  # 占位文案已随工单 11 退役


def test_main_unknown_command_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as exc:
        reco_cli.main(["nope"])
    assert exc.value.code != 0


def test_package_version_bumped():
    assert reco_cli.__version__ == "0.2.0"
