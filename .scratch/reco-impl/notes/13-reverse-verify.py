"""工单 13 逆向验证脚本：给 GUI 塞第二条排序路径必须转红（issue 13 验收 2）。

变异：app.py RecommendPage.load_now 内对 rank 输出行序做一次反转
（= GUI 侧第二条排序/重排路径）→ tests/test_consistency.py::test_cli_gui_bitwise_consistency
必须转红；**bytes 级恢复**（write_text 的 newline 翻译会整文件 LF→CRLF——教训）后必须回绿。
运行前提：干净树（W12 教训：RV 必须在干净树上跑）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[3]  # .scratch/reco-impl/notes/ → 仓库根
TARGET = ROOT / "packages/reco-ui/src/reco_ui/app.py"
ANCHOR = b'        out = rank_candidates(self._store) if self._store is not None else {"rank_rows": (), "names": {}}'
MUTATION = b'        out["rank_rows"] = list(out["rank_rows"])[::-1]  # RV: GUI second ranking path'
TEST = "tests/test_consistency.py::test_cli_gui_bitwise_consistency"


def run_pytest() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", TEST, "-q", "--no-header"],
        capture_output=True, timeout=600, cwd=str(ROOT),
    )
    out = (proc.stdout or b"") + (proc.stderr or b"")
    text = out.decode("utf-8", errors="replace")
    return ("failed" in text or "error" in text), text[-400:]


def main() -> int:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "packages/reco-ui/src/reco_ui/app.py"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert status.stdout.strip() == "", "RV 必须在干净树上跑（目标文件有未提交改动）"

    original = TARGET.read_bytes()
    assert ANCHOR in original, "变异锚点缺失（load_now 实现形态变化，请更新脚本）"
    mutated = original.replace(ANCHOR, ANCHOR + b"\n" + MUTATION, 1)
    assert mutated != original

    TARGET.write_bytes(mutated)
    try:
        red, red_out = run_pytest()
        print("RED:", red, red_out.replace("\r", ""))
        assert red, "变异未转红——一致性测试对 GUI 第二条排序路径不敏感（守护失效）"
    finally:
        TARGET.write_bytes(original)  # bytes 级恢复（零 newline 翻译）
    green, green_out = run_pytest()
    print("GREEN:", green, green_out.replace("\r", ""))
    assert not green, "恢复后未回绿"
    print("REVERSE-VERIFY-OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
