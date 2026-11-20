# reco-impl 测试基线（test-runner 常驻守护）

- 采集时间: 2026-11-21T00:42:56+0800 (UTC 偏移格式, Asia/Shanghai 本地)
- 采集人: test-runner (S1 / 任务 t3)
- git HEAD: 556af2d ("docs(tickets): 01 补 torch CRT 重放步骤（ADR-0004 前置）")
- 工作区状态: clean (0 dirty files)

## 命令与结果

```
./.venv/Scripts/python.exe -m pytest -q
```

结果: **7 passed in 0.07s**（exit 0）

## 环境

- Python: 3.12.14 (.venv)
- pytest: 9.2.0

## 测试清单（7 个占位测试，与 ARCHITECTURE 七包一一对应）

| 包 | 测试文件 |
|---|---|
| reco-core | packages/reco-core/tests/test_placeholder.py |
| reco-store | packages/reco-store/tests/test_placeholder.py |
| reco-labels | packages/reco-labels/tests/test_placeholder.py |
| reco-relations | packages/reco-relations/tests/test_placeholder.py |
| reco-io | packages/reco-io/tests/test_placeholder.py |
| reco-cli | packages/reco-cli/tests/test_placeholder.py |
| reco-ui | packages/reco-ui/tests/test_placeholder.py |

## pytest 配置

```
--- pytest cfg in pyproject ---
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["packages"]
addopts = "-ra --import-mode=importlib"
--- packages ---
reco-cli
reco-core
reco-io
reco-labels
reco-relations
reco-store
reco-ui
```

## 常驻守护职责（S1 持续生效）

每票 completed 报告到达时：
1. 立即跑全量基线命令 `./.venv/Scripts/python.exe -m pytest -q`，必须 ≥7 passed 且 0 failed/error；
2. 对该票改动做针对性逆向验证（改坏实现 → 对应测试必须变红；红→绿闭环成立才算过）；
3. 一致性冒烟：票间接缝（依赖方向、公共 API 签名）不回归；
4. 性能冒烟：涉及扫描/向量化/评分的热路径留意耗时无数量级回退；
5. 结果报队长；任何失败即阻断该票验收，走 PR-first 修复流（红测试先行 → 修复 → 逆向验证 → 独立评审 → merge --no-ff）。

> 本基线后续随票推进在下方追加记录（append-only），不删除历史条目。

---

## 基线追加记录

### 2026-11-21T00:43+0800 逆向验证冒烟（S1 自检）

- 操作：故意改坏 packages/reco-core/tests/test_placeholder.py（注入恒假断言）→ 跑 pytest → git checkout 恢复 → 复跑 pytest。
- 结果：破坏时 pytest 报 `ERROR ... 1 error during collection`（runner 正确变红）；恢复后 `7 passed in 0.05s`（回到基线）。
- 结论：测试 runner 对测试破坏敏感，无假绿风险；恢复后工作区 clean（0 dirty files）。
- 注：红相以收集期错误呈现（注入方式所致），已足以证明失败会被捕获；后续票的逆向验证将优先用断言级失败复核。
