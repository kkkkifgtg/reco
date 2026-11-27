# #122 19 条 finding 依赖 / 冲突 / 排序研究

> 研究对象：[#119](https://github.com/kkkkifgtg/reco/issues/119) 的 19 条已核实 finding（A–S，对应 #108/#104/#106/#112/#107/#105/#100/#98/#109/#101/#99/#103/#96/#97/#102/#110/#111/#113/#127）。
> 目的：给修复顺序提供代码级证据；本票不改生产代码。

## 基线与证据范围

- 研究时工作区：`C:\Users\Administrator\AppData\Local\Temp\reco-w1`，分支 `research/122-dependency-order`，HEAD=`223e866`（`origin/master`，2026-11-26）。
- #122 写入时基线声明为 `ed87979`；当前 `origin/master` 已包含 PR #124/#125/#126/#127。按票面要求“若基线已移动，以实际代码为准并记录”：本笔记以 `223e866` 的实际代码与 `git show -s --format=%B 223e866` / `git show --stat 223e866` 为证据，并回看 #119 的 finding 描述与 fix commit 表。
- 证据命令：
  - `gh issue view 119 -R kkkkifgtg/reco --json number,title,body,url`
  - `git log --oneline --reverse ed87979..HEAD`
  - `git show --stat --oneline 223e866`
  - `git show -s --format=%B 223e866`
  - `rg -n "<symbol>" packages/...`（下文表格列出具体符号/路径）

## 19 条发现总表（入边 / 出边 / 依赖）

| ID | Finding | 主触点（文件 / 符号） | 入边（必须先于它） | 出边（它解锁/约束） | 可并行 | 证据 |
|---|---|---|---|---|---|---|
| A | #108 反馈写入/事件协议 | `packages/reco-store/src/reco_store/store.py` `Store.append_event`；`packages/reco-store/src/reco_store/feedback.py` `record_feedback`；`packages/reco-labels/src/reco_labels/events.py` `record_label_event`；`packages/reco-ui/src/reco_ui/glue.py` `_apply_label_change`/`_record_label_event` | 无 | I(#109 mtime 门读 labels 事件), F(#105 事件口径), Q(#111 重放比对面) | 可与 B/C/D/E/G/H/J/K/M/N/O/P/R/S 并行（不同 seam） | PR #124 msg: “persist_feedback 过去把 event_id 写成 int(now.timestamp())... Store.append_event 返回的 id 写回事件”；“events.py / labels 表成为真相源” |
| B | #104 循环导入 | `packages/reco-core/src/reco_core/features.py`；`packages/reco-core/src/reco_core/stats.py`；`packages/reco-core/src/reco_core/__init__.py` | 无 | 无硬下游；降低后续 core 改动导入风险 | 可与除同文件外并行 | PR #124 msg: “features.py 顶层 from reco_core.stats import pearsonr；stats.py 只在 TYPE_CHECKING 挂注解，运行期局部导入” |
| C | #106 sync_zone / 流水线失效 | `packages/reco-pipeline/src/reco_pipeline/stages.py` `sync_zone`；`packages/reco-pipeline/src/reco_pipeline/__init__.py` `process_zone`；`packages/reco-io/src/reco_io/scanner.py` `classify_delta` | F(#105 book_id/identity 口径，若用 store identity)；D(#112 若统一 textkit) | E(#107 前台重扫复用)；I(#109 mtime 依赖正确的 zone 同步) | 与 A/B/G/H/J/K/M/N/O/P/Q/R/S 可并行；与 D/F 有接触需协调 | `scanner.py` 定义 `classify_delta`；PR #124: “sync_zone 改成只认当前快照... book_id 由 identity 规则派生” |
| D | #112 阅读/解码路径统一 | `packages/reco-io/src/reco_io/textkit.py` `read_book_text`/`decode_text`/`detect_encoding`/`strip_gutenberg`/`split_spine`；`packages/reco-io/src/reco_io/readers/epub.py`；`packages/reco-io/src/reco_io/readers/txt.py` | 无 | C(#106 sync/scan 路径)；E(#107 ingest 共用 reader)；G(#100 分块/feature 读书路径)；M(#96 ingest 命名空间) | 可与 A/B/F/H/J/K/N/O/P/Q/R/S 并行；与 C/E/G/M 串行协调 | `textkit.py` 暴露 `read_book_text`；PR #124: “所有读书入口统一走 textkit.read_book_text” |
| E | #107 前台 ingest 重复重扫 | `packages/reco-ui/src/reco_ui/app.py` `_ingest_files`；`packages/reco-ui/src/reco_ui/workers.py` `PoolWorker`/`_Worker` | C(#106 sync/增量语义稳定)；D(#112 读路径统一) | 无直接下游；影响 UI ingest 体验与重复副作用 | 与 A/B/F/H/J/K/N/O/P/Q/R/S 可并行 | `workers.py` 定义 `PoolWorker`；PR #124: “前台 ingest 不再自己重扫落盘，由 pipeline 结果驱动” |
| F | #105 book_id / 身份口径 | `packages/reco-core/src/reco_core/identity.py` `book_id_for`/identity helpers；`packages/reco-store/src/reco_store/rank_id.py` `book_ref`/`parse_book_ref`；`packages/reco-store/src/reco_store/reads.py` `book_id_for_path`/`load_book_features` | 无（基础合同） | C(#106 sync 依赖稳定 book_id)；G(#100 评估样本按 book_id)；K(#99 跨包身份)；L(#103 eval 口径)；O(#102 正样本口径) | 可与 A/B/D/H/J/M/N/P/Q/R/S 并行；与 C/G/K/L/O 形成契约边 | `rank_id.py` 有 `book_ref`/`parse_book_ref`；`reads.py` 有 `book_id_for_path`；PR #124: “book_id 口径收成 identity 单一规则” |
| G | #100 L2 分块/评估可复现 | `packages/reco-workspace/src/reco_workspace/features.py`；`packages/reco-store/src/reco_store/reads.py` `load_eval_samples`；`packages/reco-core/src/reco_core/layer2.py` | D(#112 读路径)；F(#105 样本身份) | L(#103 eval 指标可信度) | 与 A/B/C/E/H/J/K/M/N/O/P/Q/R/S 可并行 | `reads.py` 定义 `load_eval_samples`；PR #124: “eval 样本/分块口径固定，重跑可比” |
| H | #98 workspace 软删除 | `packages/reco-workspace/src/reco_workspace/__init__.py`；`packages/reco-store/src/reco_store/reads.py`（查询过滤） | 无 | Q(#111 重放/reads 比对需覆盖 deleted 语义) | 可与大多数并行 | PR #124: “workspace 删除改软删除语义，reads 过滤 deleted” |
| I | #109 labels mtime 门 | `packages/reco-pipeline/src/reco_pipeline/stages.py` / pipeline stale 判定；labels/store 事件时间源 | A(#108 事件协议/append_event 身份稳定)；C(#106 sync/zone 状态正确) | 无 | 串行依赖 A,C | PR #124: “mtime 门只在 labels/store 事件推进后失效” |
| J | #101 反馈轴/写入口收敛 | `packages/reco-store/src/reco_store/feedback.py` `record_feedback`；`packages/reco-core/src/reco_core/feedback.py`；`packages/reco-core/src/reco_core/axes.py` `AXIS_PREFIX`/axis helpers；`packages/reco-cli/src/reco_cli/app.py` `_feedback` | 无（可先做接口收敛） | L(#103 eval 口径依赖轴定义稳定) | 可与 A/B/C/D/E/F/G/H/K/M/N/O/P/Q/R/S 并行 | `feedback.py` 有 `record_feedback`；`axes.py` 有 `AXIS_PREFIX`；PR #124: “UI 不再写死假轴，CLI --axis 显式覆盖；入口收成 record_feedback” |
| K | #99 跨包身份/doc 越界 | `packages/reco-store/src/reco_store/rank_id.py`；`packages/reco-store/src/reco_store/reads.py`；相关 CLI/UI 调用点 | F(#105 身份合同) | 无 | 可与多数并行；与 F 串行/同审 | `rank_id.py` `book_ref`/`parse_book_ref`；PR #124: “跨包不再拼接私有 id 格式” |
| L | #103 eval 口径 | `packages/reco-store/src/reco_store/reads.py` `load_eval_samples`；`packages/reco-workspace/src/reco_workspace/eval.py` / eval 相关；`packages/reco-core/src/reco_core/axes.py` | F(#105)；G(#100)；J(#101 轴定义)；O(#102 正样本) | 无 | 串行依赖 F/G/J/O | PR #124: “eval 只统计有效样本/轴一致口径” |
| M | #96 ingest 命名空间/隔离 | `packages/reco-cli/src/reco_cli/app.py` `_cmd_ingest`；`packages/reco-pipeline/src/reco_pipeline/stages.py`；`packages/reco-io/src/reco_io/textkit.py` | D(#112 路径/reader 统一后更稳) | 无 | 可与 A/B/F/H/J/K/N/O/P/Q/R/S 并行 | PR #124: “ingest 输出按命名空间隔离，避免互相覆盖” |
| N | #97 路径/根目录派生 | `packages/reco-cli/src/reco_cli/__init__.py` `default_data_root`；`packages/reco-ui/src/reco_ui/app.py`；`packages/reco-ui/src/reco_ui/workers.py` | 无 | R(#113 卫生收口) | 可与多数并行 | `reco_cli/__init__.py` 定义 `default_data_root`；PR #124: “默认数据根可派生，不写死 cwd 假设” |
| O | #102 正样本口径 | `packages/reco-store/src/reco_store/feedback.py` `record_feedback`/event payload；`packages/reco-store/src/reco_store/reads.py` `load_eval_samples`；`packages/reco-core/src/reco_core/feedback.py` | F(#105 身份)；J(#101 反馈入口/轴) | L(#103 eval) | 与 F/J 协调后再落 | PR #124: “正样本只认显式正反馈语义，负/中性不混入” |
| P | #110 coverage 缺口 | `packages/reco-cli/tests/test_profile_coverage.py`；`packages/reco-core/tests/test_profile_coverage.py`；`packages/reco-ui/tests/test_profile_coverage.py`（PR #125） | 无硬依赖；应跟随对应模块稳定后补 | 无 | 可与 W2/W3 并行，最好在对应修复合入后补测 | PR #125 `fd84059`: 给 reco-cli/core/ui 补 coverage |
| Q | #111 影子接缝/重放比对 | `packages/reco-store/src/reco_store/replay.py` `verify_replay`/`_compare_*`；`packages/reco-store/src/reco_store/reads.py`；`packages/reco-store/src/reco_store/store.py` | A(#108 append_event 身份/协议稳定)；H(#98 deleted 语义) | 无 | 与多数并行；与 A/H 串行 | `replay.py` 有 `verify_replay`；PR #124: “删死代码、补重放比对面、给静音解除接上出口” |
| R | #113 收尾卫生 | `.gitattributes`；`packages/reco-ui/src/reco_ui/workers.py`；`packages/reco-cli/src/reco_cli/__init__.py` `default_data_root`；`app.py` CRLF 规则 | N(#97 路径派生落地后收口) | 无 | 可最后收口 | PR #124: “默认数据根可派生、worker 模板收成一处”；`cfd127d` 另修 app.py CRLF |
| S | #127 append_event 落盘边界 | `packages/reco-store/src/reco_store/store.py` `Store.append_event`；`tests/test_c2_pipeline.py` | A(#108 同一 append_event seam，协议已改时必须一起审) | 无 | 与 A 同审/同批，不单独抢占 store 协议 | #119 表 S 描述：append_event 落盘后 `_events` 追加异常路径导致 DB/内存不一致；修复随 PR #124（`223e866` squash）事件协议收敛一并落地。#119 表中引用的 `5b9deca` 实为 PR #127/#121 测试提交，证据引用以实际提交内容为准。 |

说明：
- “无依赖”仅表示无硬前置代码合同；仍需避免与同文件改动并行合并。
- A/S 都落在 `Store.append_event` / 事件协议链，必须串行或同 PR 审，不能并行各自改同一协议。
- C/E/I 形成 pipeline/UI 运行链；D 是读路径公共 seam。

## 依赖 / 冲突矩阵（高冲突对）

| 对 | 关系 | 证据 | 处理 |
|---|---|---|---|
| A ↔ S | 同符号冲突 | `packages/reco-store/src/reco_store/store.py` `Store.append_event` | 串行，同批评审 |
| A ↔ I | 语义依赖 | A 定义事件/append 语义；I 依赖 labels/store 事件推进 | A 前，I 后 |
| C ↔ D | seam 依赖 | C `sync_zone`/scan 读书路径；D `textkit.read_book_text` 统一读入口 | D 先定读契约，C 复用 |
| C ↔ E | 行为依赖 | E 前台 ingest 复用 pipeline；C 修正 sync/增量语义 | C 前，E 后 |
| D ↔ G | 数据依赖 | G 分块/评估读书；D 统一解码/分块读路径 | D 前，G 后 |
| F ↔ C/G/K/L/O | 合同依赖 | F 收敛 book_id/identity；下游样本、sync、跨包引用、eval 都吃身份 | F 先定合同 |
| J ↔ O ↔ L | 评估语义链 | J 收敛反馈轴/入口；O 定正样本；L 定 eval 口径 | J→O→L |
| A ↔ Q | 比对面依赖 | Q `verify_replay` 比对事件/读模型；A 改事件协议 | A 前，Q 后 |
| N ↔ R | 收尾依赖 | N `default_data_root` 派生；R 收口 worker/路径卫生 | N 前，R 后 |

## 建议分批（按“解锁最多下游”排序，非按编号）

| 批次 | Findings | 为什么先做 | 解锁下游 | 合并风险 | 可并行性 |
|---|---|---|---|---|---|
| B0 合同/协议 | F(#105), A(#108), D(#112) | 三者分别锁定身份合同、事件协议、读路径公共 seam；是 C/E/G/I/K/L/O/Q 的前置证据面 | C,E,G,I,K,L,O,Q | 中高：触达 store/events/textkit/identity 多包 | F/A/D 可并行开发，但合并需按触点排队（A 与 S 同审） |
| B1 流水线与运行时 | C(#106), E(#107), I(#109), M(#96) | 在身份/读路径/事件协议稳定后修正同步、ingest、mtime 门、命名空间 | 收敛 pipeline/UI 运行时 | 中：pipeline+UI+scanner 接缝 | C/M 可并行；E 依赖 C；I 依赖 A+C |
| B2 反馈/评估语义 | J(#101), O(#102), G(#100), L(#103), K(#99) | 反馈轴、正样本、分块/评估样本、eval 口径共享 identity/反馈/读模型 seam | 让评估与反馈语义可复现 | 中：store.reads/feedback/core.axes/workspace.eval | J/K 可并行启动；O 等 J/F；G 等 D/F；L 最后收口 |
| B3 观测/清理 | Q(#111), H(#98), N(#97), R(#113), B(#104), S(#127), P(#110) | 在主合同稳定后补重放比对、删除语义、路径派生、卫生、循环导入、防线与 coverage | 降低回归与维护成本 | 低到中：多为局部/测试/卫生 | 大部分可并行；Q 跟 A/H；R 跟 N；S 跟 A/store 协议 |

首批建议（3–5 条，代码级）：**F, A, D, J, N**（5 条，分属不同 seam，可并行开发但分批合入）。

- **F #105 (`reco_core/identity.py`, `reco_store/rank_id.py:book_ref/parse_book_ref`, `reco_store/reads.py:book_id_for_path`)**：先定 book_id 合同，解锁 C/G/K/L/O；不先做会导致下游按旧 id 修完再迁移。
- **A #108 (`reco_store/store.py:Store.append_event`, `reco_store/feedback.py:record_feedback`, `reco_labels/events.py:record_label_event`)**：先定事件协议/落库 id 回传，解锁 I/Q，并约束 S；事件身份不稳时 mtime/replay 会假绿。
- **D #112 (`reco_io/textkit.py:read_book_text/decode_text/strip_gutenberg/split_spine`)**：统一读路径，解锁 C/E/G/M；否则各入口继续各自解码/分块。
- **J #101 (`reco_store/feedback.py:record_feedback`, `reco_core/axes.py:AXIS_PREFIX`, `reco_cli/app.py` feedback 入口)**：收敛反馈轴与入口，解锁 O/L；先做可避免 UI/CLI 再长出第二套轴映射。
- **N #97 (`reco_cli/__init__.py:default_data_root`, UI/workers 路径派生)**：独立但高价值，解锁 R；与 F/A/D/J 无同符号冲突，可并行。

## 文本依赖图（DAG，边表示“先于/解锁”）

```text
F(#105 identity/book_id) ──┬──> C(#106 sync_zone) ──┬──> E(#107 前台 ingest)
                          │                        └──> I(#109 mtime 门) <── A(#108 事件协议) <── S(#127 append_event 落盘)
                          ├──> G(#100 L2/eval 样本) ──┐
                          ├──> K(#99 跨包身份)          ├──> L(#103 eval 口径)
                          └──> O(#102 正样本) ─────────┘        ^
                                                              J(#101 反馈轴/入口) ──┘

D(#112 读路径统一) ──┬──> C(#106)
                    ├──> E(#107)
                    ├──> G(#100)
                    └──> M(#96 ingest 命名空间)

A(#108) ──> Q(#111 重放比对) <── H(#98 软删除)
N(#97 默认数据根) ──> R(#113 收尾卫生)
B(#104 循环导入) ──> (无下游)
P(#110 coverage) ──> (无硬下游，建议随对应修复合入后补)
```

## 并行 / 串行结论

- 可并行开发（不同 seam）：F / A / D / J / N / B / H / P；M 可与 B1 中非 C/E 任务并行。
- 必须串行或同审：A↔S（同 `Store.append_event`）；A→I；A→Q；D→C/E/G/M；F→C/G/K/O/L；J→O→L；C→E；N→R；H→Q。
- 合并顺序建议按批次 B0→B1→B2→B3；每批内先合合同/协议提交，再合行为提交，再补测试/卫生。

## 验收对照

- 19 条每条有入边/出边或明确无依赖：见“19 条发现总表”。
- 首批顺序有代码级证据：见“建议分批”首批 5 条的文件/符号。
- 指出并行/串行：见“并行 / 串行结论”和矩阵。
- 笔记落在 `research/`：本文件；从 #122 链接：需在 #122 评论补链接。