"""工单 04——import 卫生（架构 §6.3/F5 红测试）。

import reco_core / reco_io / store / labels / relations 不得触发 sklearn / torch：
向量化与统计提取是纯数值实现；sklearn 仅允许在工单 08 的
train_pairwise / similarity_scores 内首次调用时延迟导入，
首屏与 import reco_core 都不得触发（硬约束）。
子进程探测，避免本测试进程已被其它测试模块污染；等待返回值（E5）。
"""
from __future__ import annotations

import subprocess
import sys

PROBE = (
    "import sys;"
    "import reco_core, reco_io, reco_store, reco_labels, reco_relations;"
    "bad = [m for m in ('sklearn', 'torch') if m in sys.modules];"
    "print('BANNED:' + ','.join(bad))"
)


def test_importing_public_packages_does_not_load_sklearn_or_torch():
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "BANNED:", proc.stdout + proc.stderr
