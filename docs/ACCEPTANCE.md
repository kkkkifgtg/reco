# reco 验收记录

## 当前回归快照（2026-11-25）

- 环境：Windows、CPython 3.12、`QT_QPA_PLATFORM=offscreen`。
- 命令：`.venv/Scripts/python.exe -m pytest packages tests -q`。
- 结果：**737 passed / 2 failed / 4 skipped**。跳过项为可选 torch/CRT 对齐与 POSIX 专属权限/符号链接测试；两处失败均在 `tests/test_extract_closed_loop.py`（既有缺陷，本次文档统一不涉及代码改动）。
- 依赖：`uv.lock` 已由 uv 0.13.10 重新解析，共 29 个包；新增锁定 `zhconv 1.4.3`。
- 下方 W14 清单是 2026-11-21 的历史收官证据，其 384/379 数字不代表当前基线。

## W14 历史验收清单（2026-11-21）

> 历史执行环境：分支 feat/14-swap-drill（基线 master 65a07b2）；Python 3.12.14 / pytest 9.2.0；
> 该收官时全仓 pytest **384 passed**（当时修正了根 testpaths 与 vector blob 键名收集盲点）。
> 每条证据保留当时实际执行的命令与输出摘要；§ = docs/ARCHITECTURE.md 当时的 v1.2。

## 1. 代码与 git 基线

| # | 项目 | 结论 | 证据 |
|---|---|---|---|
| 1 | 代码全在 reco/ 内，git 基线可复现 | ✅ | 仓库根 F:\date\adult-reco；git log 线性可读（master 65a07b2 → 收官分支）；所有工作票走 PR-first 分支 + merge --no-ff |
| 2 | 7 包独立可安装、独立可测 | ✅ | 隔离演练 v7（.scratch/reco-impl/notes/w14-isolated-install.sh）：7 包全部 deps_install=0 test=0 —— core 81 / labels 62 / relations 68 / store 36 / io 71 / cli 18 / ui 41 passed（独立 venv + editable 传递闭包）；演练暴露并修正 5 项契约缺陷（commit 998ebcb，披露记录在提交信息） |
| 3 | 依赖方向单向无环（§4.1/L98-110） | ✅ | 源码 grep 实测：io→store/labels/relations；labels/relations/store→core；cli→core/io/store；ui→core/io/store；core 零内部依赖；ADR-0001 接缝（duck-typed/零跨包 import）由各包源码断言测试守护 |

## 2. 数据层（工单 02/03/05）

| # | 项目 | 结论 | 证据 |
|---|---|---|---|
| 4 | 三专区同步 + 增量判定 | ✅ | test_scan_zones/test_scan_delta 36 passed；DirEntry 身份/跨卷 st_dev 配对两注入缺陷曾转红修复（W2 逆向验证记录） |
| 5 | 硬链接/复制落位 | ✅ | test_placement 全绿；EXDEV 退复制路径有独立测试（W3 交付） |
| 6 | SQLite 特征库 + append-only 事件流 | ✅ | test_store_core/test_store_concurrency/test_store_replay/test_store_findings_fix 36 passed；15 投影表 + events 唯一真相；W5 逆向双变异闭环；W5-N/W5-F 修复已 merge |
| 7 | 唯一硬过滤=去重（同名同大小） | ✅ | normalize_book_name/dedup_key 契约测试（casefold 移除曾转红）；负样本静音层零过滤语义（test_feedback S5） |

## 3. 四模型隔离（工单 04/06/07/08/09；§6.5）

| # | 项目 | 结论 | 证据 |
|---|---|---|---|
| 8 | 特征提取（无分词器中文统计） | ✅ | features/vectorize 78 测试；金标 0.74ms/篇；零 sqlite/numpy/sklearn/torch import（子进程卫生测试） |
| 9 | 标签体系（数据驱动 域→轴→值） | ✅ | labels 62 passed；种子六域 25 轴 133 值逐字转录；manual/user 写入层拒绝；merge/archive 逆向验证闭环 |
| 10 | 人物关系模型（结构化可编辑） | ✅ | relations 68 passed；§6.1 全签名；D2 manual 保护变异 3 测试转红→字节级恢复 |
| 11 | 相似度打分（rank 唯一入口） | ✅ | score 33 passed；金标 0.9494740323391176 独立冻结；§6.5 反例 1-4 测试化（TAGS/RELATIONS/EVENTS 传入即 ValueError）；sklearn 延迟导入双探针 CLEAN；M1-M4 逆向全红 |
| 12 | 画像与置信度（时间衰减） | ✅ | profile 28 测试；w(half)=0.5 金标；clip 0.05；回放容差 1e-9；千事件重放 3.5ms；W9 逆向 7 变异全红 |
| 13 | 换核演练（modules.md 唯一硬证据） | ✅ | docs/swap-drill-evidence.md：余弦→欧氏核 1/(1+d²)，变体快照 1f463a8 永久存档，cli/ui 零改动（git diff --stat 为空），全量 379 passed，revert f36a820 复原；数值契约/结构契约解耦证明 |

## 4. 反馈闭环与一致性（工单 10/13）

| # | 项目 | 结论 | 证据 |
|---|---|---|---|
| 14 | 不合口味反馈闭环 | ✅ | feedback 22 passed；静音 95/5 惩罚通道（score_muted=0.05·w_sim·sim 恒>0 沉底）；30/90/180d 过期自动解除；画像秒级更新复用 W9；pool_added 不进口味流；「已调整：X 维度权重已下调」显示契约；MV1-M5 逆向全红 |
| 15 | CLI/GUI 排序一致性 | ✅ | test_consistency 3 passed（真实向量接线后逐位全字段相等）；RECO_PERF_TABS 快照基线逐位比对（scripts/ci-consistency.sh）；GUI 第二条排序路径变异曾转红（W13 RV 记录） |

## 5. 入口与热加载（工单 11/12/12b）

| # | 项目 | 结论 | 证据 | 备注 |
|---|---|---|---|---|
| 16 | CLI 入口 | ✅ | reco-cli 18 passed（report/eval/explain/replay/recommend/feedback 子命令）；python -m reco_cli UTF-8 输出 | feat/11-cli 已 merge |
| 17 | GUI 三板块（PyQt6+QSS 深色） | ✅ | reco-ui 41 passed；PyQt6 许可隔离薄适配层（§11.5，全仓唯一 import 点）；离屏测试 QT_QPA_PLATFORM=offscreen | feat/12-gui 已 merge |
| 18 | 自适应热加载（§11.7） | ✅ | ui_usage 6 + preload 17 = 23 passed：ui_usage 独立口味语义（§7 隔离，signals 严格分离）；预热可打断；失败可重试；<500ms 预算 | feat/12b-preload 已 merge |

## 6. 全局纪律

| # | 项目 | 结论 | 证据 |
|---|---|---|---|
| 19 | 合成数据零正文 | ✅ | fixtures 全合成；B 类金丝雀测试在位（results 只含数值/枚举/计数）；§7「只存数值与标签」由 payload 白名单（PayloadViolationError）强制 |
| 20 | RECO_PERF 打点契约 | ✅ | phase=<name> ms=<float> → stderr；io/labels/perf 测试 7 passed（RECO_PERF=1 冒烟实跑取证） |
| 21 | 探索位默认 2 篇可调 | ✅ | test_S1 探索位契约 + exploration_count 配置测试；确定性=候选集指纹旋转（同输入必同位） |
| 22 | 双轴评审已跑 | ✅ | docs/review/A2-findings.md（Standards×Spec 双轴产出，findings 全部关闭或归票） |
| 23 | 逆向验证记录 | ✅ | 每关键测试改回缺陷形态转红：W4×6、W5×2、W6×2、W7×3、W8×4、W9×7、W10×5、W13×1+快照、W12b×（打断/重试）、W14 换核红转绿明细（evidence 文档） |
| 24 | retro | ✅ | docs/RETRO.md（同分支提交；机械教训→pre-commit-guard.sh+checklist；判断性取舍→CODING-STANDARDS.md） |

## 遗留与移交

- W8 遗留 _last_status 进程内状态多线程复核 → 归票 12（captain 裁定）。
- 「不合口味」闭环的 GUI 展示文案接线（feedback.display → 状态栏）属 W12 契约，已由 reco-ui 消费面覆盖。
- 全部 24 条证据可重放：命令与期望值均已入库（测试文件/脚本/文档）。
