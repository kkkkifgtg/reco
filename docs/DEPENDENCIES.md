# DEPENDENCIES — 版本清单（工单 01）

> 只记实装版本与声明下限；背景与决策见 docs/ARCHITECTURE.md §12 与 docs/adr/0004。

## 采集

- 日期: 2026-11-21
- 解释器: CPython 3.12.14（reco/.venv）
- 锁定: uv 0.13.9（.venv/Scripts/uv.exe）+ uv.lock（Resolved 21 packages）
- 安装: uv sync --inexact（不 prune torch 等票外包）

## 运行依赖（reco-core 声明，架构 §12/F1）

| 包 | 实装版本 | 声明下限 | 说明 |
|---|---|---|---|
| numpy | 2.5.5 | numpy>=2.5 | 向量运算 |
| scipy | 1.18.1 | scipy>=1.18 | sklearn 稀疏矩阵载体 |
| scikit-learn | 1.9.1 | scikit-learn>=1.9 | TF-IDF 相似度/评估（延迟导入，F5） |

## GUI 依赖（reco-ui 声明，架构 §12/F1，工单 12）

| 包 | 实装版本 | 声明下限 | 说明 |
|---|---|---|---|
| PyQt6 | 6.12.1 | PyQt6>=6.12 | Qt 绑定（GPLv3/商业，本地自用不分发 §17 Q2）；Qt6 运行库 6.12.0 随 wheel |
| PyQt6-Qt6 | 6.12.0 | （PyQt6 元数据依赖） | Qt 运行库 DLL（Qt6/bin 103 DLL） |
| PyQt6-sip | 13.13.1 | （PyQt6 元数据依赖） | 绑定胶水 |

## 可选依赖（ADR-0004 可插拔备选；不入 uv.lock/依赖声明）

| 包 | 实装版本 | 说明 |
|---|---|---|
| torch | 2.15.0+cpu | 句向量备选前置；import 无 WinError 126/1114 |
| sentence-transformers | 6.3.0 | 随 torch 线安装 |
| msvc-runtime | 14.51.36231 | CRT DLL 来源；pip install 后复制 12 个 CRT DLL 进 torch/lib（重建 venv 必须重放） |

## 开发依赖（根 pyproject [dependency-groups]）

| 包 | 实装版本 | 声明 |
|---|---|---|
| pytest | 9.2.0 | pytest>=8.0 |
| uv | 0.13.9 | .venv 内安装，不进锁文件 |

## 离线 wheel 缓存（wheels/）

| wheel | 大小 |
|---|---|
| numpy-2.5.5-cp312-cp312-win_amd64.whl | 12.6 MB |
| scipy-1.18.1-cp312-cp312-win_amd64.whl | 36.7 MB |
| scikit_learn-1.9.1-cp312-cp312-win_amd64.whl | 8.3 MB |
| joblib-1.6.0-py3-none-any.whl | 0.3 MB |
| narwhals-2.29.0-py3-none-any.whl | 0.5 MB |
| threadpoolctl-3.8.0-py3-none-any.whl | 0.03 MB |
| cloudpickle-3.1.3-py3-none-any.whl | 0.02 MB |
| pyqt6-6.12.1-cp310-abi3-win_amd64.whl | 7.1 MB |
| pyqt6_qt6-6.12.0-py3-none-win_amd64.whl | 79.7 MB |
| pyqt6_sip-13.13.1-cp312-cp312-win_amd64.whl | 0.05 MB |

- 离线重建命令（架构 §12）：`pip install --no-index --find-links wheels/ "numpy==2.5.5" "scipy==1.18.1" "scikit-learn==1.9.1"`
- 断网演练: 2026-11-21 于临时 venv 以 `--no-index --find-links wheels/` 离线装齐 7 包并 import 验证通过（exit 0）。
- PyQt6 离线重建（工单 12 实测）：pip 撞杀软 DLL 锁时，可 `.venv/Scripts/python.exe -m zipfile -e wheels/pyqt6_qt6-*.whl .venv/Lib/site-packages/` 直接解压恢复（2656 文件）。
- pip 缓存: %LOCALAPPDATA%\\pip\\Cache（交叉印证用）。

## torch CRT 对齐硬验收（ADR-0004）

torch/lib 必须齐备 12 个 CRT DLL（System32 缺 vcruntime140_1 / vcruntime140_threads / msvcp140_atomic_wait，实测根因）：
concrt140 / msvcp140 / msvcp140_1 / msvcp140_2 / msvcp140_atomic_wait / msvcp140_codecvt_ids / vcruntime140 / vcruntime140_1 / vcruntime140_threads / vccorlib140 / vcamp140 / vcomp140（均 .dll）。

回归测试: packages/reco-core/tests/test_dependencies.py::test_torch_optional_crt_alignment（torch 缺席时 skip，装上未对齐时 fail）。
