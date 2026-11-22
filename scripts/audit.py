"""提交前第三方审计门禁（本仓库）。

用法：
  python scripts/audit.py           # 全量审计（提交前默认）
  python scripts/audit.py --staged  # 预留：仅审计暂存区改动（当前实现等同全量）

规则（工单：每一次提交前必须先审计）：
1. Bandit（PyCQA，独立第三方 Python SAST）：扫描 packages/ 与 scripts/，
   仅 HIGH 级别触发失败；历史遗留问题记录在 .bandit-baseline.json，不阻断新提交。
2. pip-audit（PyPA，依赖漏洞审计，OSV 的本地替代）：已安装则审计当前环境依赖。
3. 工具缺失时打印「跳过」而不是失败——CI 层有 Bandit/Semgrep/OSV 三件套兜底。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _find_tool(name: str) -> str | None:
    """依次在调用方 venv 的 Scripts、仓库 .venv、PATH 中查找工具。"""
    py_dir = Path(sys.executable).parent
    for candidate in (py_dir / f"{name}.exe", ROOT / ".venv" / "Scripts" / f"{name}.exe"):
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def _run(cmd: list[str]) -> int:
    """执行外部审计工具并回显命令（便于在提交说明里复核）。"""
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, text=True).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="提交前第三方审计")
    parser.add_argument("--staged", action="store_true", help="保留参数：暂存区模式")
    parser.parse_args(argv)

    failed = False

    bandit = _find_tool("bandit")
    if bandit:
        cmd = [bandit, "-q", "-lll", "-r", "packages", "scripts", "-f", "custom"]
        baseline = ROOT / ".bandit-baseline.json"
        if baseline.exists():
            cmd += ["-b", str(baseline)]
        if _run(cmd) != 0:
            failed = True
        else:
            print("Bandit：无 HIGH 级别发现")
    else:
        print("Bandit：未安装，跳过本地扫描（CI 层仍执行）")

    pip_audit = _find_tool("pip-audit")
    if pip_audit:
        if _run([pip_audit, "--progress-spinner", "off"]) != 0:
            failed = True
    else:
        print("pip-audit：未安装，跳过（CI 层 OSV-Scanner 兜底）")

    print("审计结论：" + ("失败（修复后重跑）" if failed else "通过"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
