# G2r 审计基线 — 阅读器边界 + 自动标签边界 + 双闸门现状核对

维护人：auditor ｜ 团队：reco-reader ｜ 任务：t5 (G2r) ｜ 日期：2026-11-21T15:35+0800 ｜ 状态：**就位（两组边界基线 + 双闸门现状核对完成）**

## 0. 就位结论

- 承接 reco-next G2 基线（audit-log 第 19 条，PR #1）：真 PR 双闸门流程、隔离副本审计、PR-first、分支写操作断言、`.audit-` 前缀禁用全部**继续有效**，本基线不重述、只增补。
- 本基线自身按同一流程闭环：分支 chore/g2r-audit-baseline → cherry-pick 悬空提交 a436be5（第 21 条落库）→ 第 22 条 + 清单 §R/§L → gh pr create → 双闸门 → merge。

## 1. 边界基线（本团队新增的两张票域）

### 1.1 阅读器「界面例外」边界清单（TXT/EPUB/DOCX/PDF）

事实前提：ARCHITECTURE Q12（L951）裁定当前 **GUI 不内建阅读器**（详情只显示元数据与统计量）。阅读器是将来新增模块；落地票强制执行清单 §R（audit-checklist）：

| # | 边界 | 一句话契约 |
|---|---|---|
| R1 | 界面例外 | 正文唯一被允许出现的地方 = 阅读器渲染视口；例外仅限界面，永不持久化 |
| R2 | 零落盘 | 不写磁盘缓存/临时文件/DB 表/导出物；打开即渲染，关闭不留痕 |
| R3 | 日志/诊断零正文 | 解析失败只报文件名/偏移/枚举原因，无片段 |
| R4 | 事件流零正文 | 阅读事件 payload 仅数值/枚举/路径/进度键，白名单外拒绝 |
| R5 | 渲染层无驻留 | 无持久正文缓存；关闭即释放内存 |
| R6 | 详情默认元数据 | 正文视图显式进入；统计量不含原文摘句 |
| R7 | 实验开关默认关 | 任何显示正文的实验路径默认 off |
| R8 | §B 常驻全查 | B1-B7 对阅读器票全查；fixture 全合成 |

### 1.2 自动标签（auto/user 来源纪律）

事实前提（master 7489aaa 实证）：写入层双保护已在位——book_tags source=manual/user 由 `ManualTagProtected`（reco_labels/store.py:155）拒绝，record.py:48-49 跳过路径在先；测试覆盖拒绝路径（test_merge_archive.py、test_takeover_semantics.py）。清单 §L 落 audit-checklist，要点：

- auto 是自动路径唯一合法来源；auto→auto 更新允许（刷 confidence/basis/updated_at）；manual/user 永不被 auto 覆盖（写入层拒绝，非调用方自觉）。
- 识别只靠「域→轴→值」taxonomy + aliases 命中；不从正文推断新标签值；新标签走 proposals。
- 接真数据验证只读数值/特征/标签表，不读正文；证据引用 basis 偏移，非摘句。
- 事件 payload 白名单：数值/标签 id/confidence/basis/时间戳；§B1/B6 同口径。

## 2. 双闸门现状核对（基于真 PR 页面与 git 史，2026-11-21T15:11+0800 取证）

| 对象 | 事实 | 判定 |
|---|---|---|
| PR #15（RN1 刷新+定时扫描） | 双结论在案（auditor 第 20 条 + test-runner），05:32Z squash merge | ✓ 流程符合 |
| PR #26（W19 N2 真移动） | 双结论在案（auditor 引第 21 条 + test-runner），05:49Z squash merge；分支已删 | ✓ 流程符合；但第 21 条提交 a436be5 落在陈旧基线未进 master（本票 cherry-pick 落库） |
| PR #25（Codacy workflow） | **Audit Trail 两行均空**、正文仅模板占位、audit-log 零条目，05:06Z merge | ✗ 双闸门缺位（CI-only 工具票；medium 记录项，见第 22 条） |
| 0ccfa12 / 4e794d5 / 7489aaa | 三笔 chore 直推 master（.scratch+scripts +124/-0），无 PR | ✗ 跳过 PR-first（脚本收编类历史先例；自本条起 scripting-only 亦走 PR-first） |
| master...origin/master | fetch 后 0/0；共享检出净树（0 脏项，仅 4 个未跟踪 .scratch 探针） | ✓ 同步健康 |

## 3. 常驻守护规则（增量）

- 阅读器票（TXT/EPUB/DOCX/PDF）：必查 §R1-R8 + §B 全查；任何正文落盘/日志/事件 payload 键超白名单 = blocker。
- 自动标签票：必查 §L1-L7 + §D1/D3 + §B1/B6；auto 覆盖 manual/user 或绕过写入层 = blocker。
- 双闸门硬条件沿袭 G2 基线 §4：PR 页 Audit Trail 双行非空且 pass + 全量绿；**CI/workflow-only 票与脚本收编票同样适用**（第 22 条偏差的整改）。
- 审计记录随 PR 落 master（PR #1/#15 先例）：每票 auditor 条目随该票分支入库，merge 前完成。

## 4. 严重度范围声明

第 2 节 ②③ 流程偏差为 **medium 记录项**（流程闸缺位，无边界互穿/正文泄漏/append-only 破坏），不构成 blocker——下游 t6+ 与实现票可派发；整改以「自本条起」口径执行，不追溯重开已 merge PR。
