# RETRO.md —— reco-impl 团队会话复盘（W14 历史快照，2026-11-21）

> 本文保留 W14 当时的环境与数字；当前测试基线见 `docs/ACCEPTANCE.md`。目标：改环境不改代码。三收编：机械性错误→确定性脚本/检查；判断性取舍→编码规范；
> 工具链缺口→bootstrap 补齐项。规范正文见 docs/CODING-STANDARDS.md（8 条）。

## 1. 机械性错误 → 确定性检查（已收编 scripts/pre-commit-guard.sh）

| # | 事故 | 防线 |
|---|---|---|
| 1 | 共享检出分支漂移（W6 误把 master ff 落到队友检出分支；W7 断言两度拦截漂移） | guard#1 分支断言；git 写操作前断言分支（全队已实践） |
| 2 | 逆向验证在脏树上跑（W4 未跟踪文件不可 checkout 恢复，变异叠加） | RV 前置：干净树（git status --porcelain 仅允许本票文件） |
| 3 | 变异「不红」被当通过（W8 M1、W10 MV4 三轮） | 变异核必须断言行为位（转红+指定测试），不是「退出码非零就行」；NOT-RED 必须出追查报告 |
| 4 | 空表/空候选探针缺失（W8 探索位池规则三轮暴露） | 边界测试显式列出：空集/单元素/等于配额/全并列（guard#4 提醒裸 except 同源） |
| 5 | 字节级回写未验证（W7 行尾翻转 434/434 事故） | RV 尾部必须输出 BYTE-IDENTICAL: True（sha256 对比），不再用肉眼看 diff |
| 6 | 测试文件语法坏导致整个套件不可收集（W14 PROBE 字符串拼接 SyntaxError） | guard#5 py_compile staged 文件；PROBE 用三引号真换行 |
| 7 | editable .pth 生效误解（W14 v2/v3 演练误判安装成功） | 安装后必须跑 pytest 断言（install=0 test=0 分开记录）；venv 内测试才是真安装 |

## 2. 判断性取舍 → 编码规范（docs/CODING-STANDARDS.md）

1. 数值契约/结构契约分层（换核演练核心发现）
2. 跨包测试即集成测试（labels 独立 venv 实锤）
3. 守卫必须纯函数（W8 config.pop 副作用）
4. 余弦类度量夹具二维+（S1 三轮失败同根因）
5. 机制选型先做不变量快检（W10 正交混合机制性无效）
6. 显式状态优于隐式全局（rank._last_status 审计遗留）
7. 依赖声明=架构表机械镜像（cli/ui 漏声明）
8. add 清单=触碰文件全集（W10 score.py 漏 add）

## 3. 工具链缺口 → bootstrap 补齐项

| # | 缺口 | 补齐建议 |
|---|---|---|
| 1 | 无独立安装验收脚本 | ✅ 已入库：.scratch/reco-impl/notes/w14-isolated-install.sh（v7 全传递闭包，可升格 scripts/isolated-install.sh） |
| 2 | 无提交前机械守卫 | ✅ 已入库：scripts/pre-commit-guard.sh（7 项确定性检查） |
| 3 | 无 CI 一致性入口 | ✅ 已有（W13）：scripts/ci-consistency.sh（快照比对+逐位一致+eval 门） |
| 4 | uv 不在 PATH（bootstrap 用裸 pip 走 PyPI 网络慢） | 建议 bootstrap.ps1 增 uv 安装与镜像配置（离线 wheels 复用） |
| 5 | hatchling 不在 wheels/（离线 pip install -e 不可行） | 建议 wheels/ 增补 hatchling/setuptools wheel 或 ADR 记录「独立安装走 PyPI」口径 |
| 6 | 限流频发导致回合中断（本会话多次 429） | 团队级：派票描述预载全部依赖结果（已实践有效，零上下文损失） |

## 4. 会话级数字

- 全量测试：379 passed（收官分支），从 W1 起点 7 passed 一路红→绿推进
- 逆向验证变异总计：6+2+2+3+4+7+5+1+1+4（换核红转绿明细）≈ 35 项全部转红闭环
- 交付票：W1-W13+12b 共 14 张 + W14 收官；披露的跨写域改动 2 次（b5cfa96/998ebcb），均自查发现
- 审计：audit-log 逐票 PASS；「仅 auto 被拒」接管语义等裁定已固化进实现

## 5. 未竟事项（移交）

- _last_status 多线程复核（归票 12）
- isolated-install.sh 升格 scripts/ 并入 CI（建议下一迭代）
- uv/hatchling 离线补齐（bootstrap）
