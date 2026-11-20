# 01：依赖落地——科学计算栈进项目 venv

**What to build:** `uv sync` 一步把 numpy/scipy/scikit-learn 装进 reco/.venv 并锁版本；「首次安装需网络 + wheels/ 缓存可重装」落地。PyQt6 不在本票（归 12）。

**Blocked by:** None (can start immediately)
**Status:** ready-for-agent

- [ ] reco-core pyproject 声明 numpy/scipy/scikit-learn（版本下限按架构 §12）
- [ ] `uv sync` 后 .venv 内 import numpy/scipy/sklearn 全部成功
- [ ] wheels/ 缓存三包 wheel，断网重装演练成功
- [ ] 全量 pytest 仍绿；docs/DEPENDENCIES.md 记版本清单（无正文）
