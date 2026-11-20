# 01：依赖落地——科学计算栈进项目 venv

**What to build:** `uv sync` 一步把 numpy/scipy/scikit-learn 装进 reco/.venv 并锁版本；「首次安装需网络 + wheels/ 缓存可重装」落地。PyQt6 不在本票（归 12）。

**Blocked by:** None (can start immediately)
**Status:** ready-for-agent

- [ ] reco-core pyproject 声明 numpy/scipy/scikit-learn（版本下限按架构 §12）
- [ ] `uv sync` 后 .venv 内 import numpy/scipy/sklearn 全部成功
- [ ] wheels/ 缓存三包 wheel，断网重装演练成功
- [ ] torch 2.15.0+cpu 可选装（代理 127.0.0.1:7897；ADR-0004 句向量备选的前置）：装后必须重放 CRT 对齐——pip install msvc-runtime 后把全套 12 个 CRT DLL 复制进 .venv/Lib/site-packages/torch/lib（System32 缺 vcruntime140_1/vcruntime140_threads/msvcp140_atomic_wait，实测根因）
- [ ] 全量 pytest 仍绿；docs/DEPENDENCIES.md 记版本清单（无正文）
