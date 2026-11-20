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

PROBE = """
import sys
import importlib
loaded = []
for name in ('reco_core', 'reco_io', 'reco_store', 'reco_labels', 'reco_relations'):
    try:
        importlib.import_module(name)
        loaded.append(name)
    except ImportError:
        pass  # 【W14】独立 venv 演练：兄弟包缺位时跳过（reco-core 独立安装合法）
bad = [m for m in ('sklearn', 'torch') if m in sys.modules]
print('LOADED:' + ','.join(loaded) + '|BANNED:' + ','.join(bad))
"""


def test_importing_public_packages_does_not_load_sklearn_or_torch():
    proc = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    # 【W14】自适应探针：reco_core 本体必须已加载；F5 断言对所有可导入者成立；
    # 全仓安装时 reco_io/store/labels/relations 全部参与探测（覆盖与原版一致）。
    assert "LOADED:reco_core" in out, out
    assert out.endswith("|BANNED:"), out + proc.stderr
