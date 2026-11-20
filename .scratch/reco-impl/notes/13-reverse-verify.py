"""工单 13 逆向验证脚本：给 GUI 塞第二条排序路径必须转红（issue 13 验收 2）。

变异：app.py RecommendPage.load_now 内对 rank 输出行序做一次反转
（= GUI 侧第二条排序/重排路径）→ tests/test_consistency.py::test_cli_gui_bitwise_consistency
必须转红；git checkout 恢复后必须回绿。
运行前提：干净树（W12 教训：RV 必须在干净树上跑）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "packages/reco-ui/src/reco_ui/app.py"
MUTATED_FROM = '        out = rank_candidates(self._store)\n'
MUTATED_TO = ('        out = rank_candidates(self._store)\n'
              '        out["rank_rows"] = list(out["rank_rows"])[::-1]  # 变异：GUI 第二条重排路径\n')
TEST = "tests/test_consistency.py::test_cli_gui_bitwise_consistency"


def run_pytest() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-q", "--no-header"],
        capture_output=True, text=True, timeout=600, cwd=str(ROOT),
    )
    out = (proc.stdout + proc.stderr)
    return ("failed" in out or "error" in out), out[-400:]


def main() -> int:
    status = subprocess.run(["git", "status", "--porcelain", "--", str(TARGET.relative_to(ROOT))],
                            capture_output=True, text=True, cwd=str(ROOT))
    assert status.stdout.strip() == "", "RV 必须在干净树上跑（目标文件有未提交改动）"

    original = TARGET.read_text(encoding="utf-8")
    assert MUTATED_FROM in original, "变异锚点缺失（load_now 实现形态变化，请更新脚本）"

    TARGET.write_text(original.replace(MUTATED_FROM, MUTATED_TO, 1), encoding="utf-8")
    try:
        red, red_out = run_pytest()
        print("RED:", red, red_out.replace("\r", ""))
        assert red, "变异未转红——一致性测试对 GUI 第二条排序路径不敏感（守护失效）"
    finally:
        TARGET.write_text(original, encoding="utf-8")  # 字节级恢复
    green, green_out = run_pytest()
    print("GREEN:", green, green_out.replace("\r", ""))
    assert not green, "恢复后未回绿"
    print("REVERSE-VERIFY-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
