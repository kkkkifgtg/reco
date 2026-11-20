# reco-impl 审计清单（auditor 常驻守护）

- 维护人：auditor ｜ 建立日期：2026-11-21 ｜ 版本：v1.0
- 依据（只读真相源）：
  - CONTEXT.md（术语表 77 行：不合口味/负样本/探索位/去重/落位/manual 永不覆盖等语义）
  - docs/adr/0001-four-independent-models.md（四模型独立接口+存储）
  - docs/adr/0002-tfidf-char-ngram-over-embeddings.md（superseded by 0004）
  - docs/adr/0003-appendonly-event-store.md（事件表唯一真相、投影可重建）
  - docs/adr/0004-embeddings-pluggable-enabled.md（句向量可插拔备选，默认仍 TF-IDF）
  - docs/ARCHITECTURE.md §6.5（L513-526 禁止形态清单，七条一律 reject）+ §7 数据模型（L529-694）
  - .scratch/reco-impl/issues/00-index.md（执行规则：小切片/TDD 红→绿→重构/逆向验证转红/PR-first/全合成数据/不读正文/不写 xiaos/每票完成即 commit）

## 用法

1. 触发时机：每张工单 status=completed 时，逐类过一遍本清单。
2. 产出：审计结论 = **pass** 或 **findings 列表**（id / severity / problem / requiredFix / file:line）。
3. 发现问题**强制走 PR-first**：开独立分支（fix/<票号>-<finding-id>）→ 提交**修复前红测试**（先证红）→ 修复 → 逆向验证（把修复 revert 必须转红）→ 他人评审 → merge --no-ff → 删分支。**禁止直接改主分支。**
4. auditor 不写实现代码：红测试、审计清单与审计记录属审计产物，修复代码由原 implementer 或队长指定成员完成。
5. 审计结论逐票追加到 .scratch/reco-impl/audit-log.md（日期 / 票号 / commit / 结论 / findings 摘要）。
6. 严重度：**blocker**（边界互穿、正文泄漏、append-only 被破坏、manual 被覆盖——下游不得推进）｜ high ｜ medium ｜ low。§6.5 七条默认 blocker。

## A. 边界互穿（四模型隔离；§6.5 反例 1-6、ADR-0001）

- [ ] A1 score() 内部不得读事件流、算时间衰减、改权重（§6.5-1：profile 塞进 score）。
- [ ] A2 score() 内部不得解析标签词典、匹配机制词（§6.5-2：tag 塞进 score）。
- [ ] A3 score() 内部不得推断人物关系（拓扑/权力/自愿性）（§6.5-3：relation 塞进 score）。
- [ ] A4 打分不得是「关系相似度+标签相似度+TF-IDF」单一 hardcode 常数和；权重必须有来源与版本（§6.5-4；profile_versions.weights_json 的 w_sim/w_model/w_behavior）。
- [ ] A5 画像模型只读已落库的数值化结果，不读 RelationshipGraph / 标签匹配中间态（§6.5-5）。
- [ ] A6 CLI 与 GUI 不得各自实现排序或各持一套权重；rank 是唯一打分入口（§6.5-6；工单 08/13）。
- [ ] A7 换实现演练可用：任一模型换桩实现（返回固定值），其余三个模型与两个入口零改动仍能跑（§6.5 评审动作；工单 14 全量验收）。
- [ ] A8 四组模型各有独立接口与存储：关系图逐字段 JSON、标签表、无状态打分纯函数、画像版本表（ADR-0001）。
- [ ] A9 句向量只能位于可插拔接口之后（similarity_scores 实现侧适配器），默认路径仍 TF-IDF 字符 n-gram；无独立增强票不得切换默认（ADR-0004）。

## B. 正文泄漏（正文与片段绝不离开计算内部；§6.5-7、§7）

- [ ] B1 日志、事件、报告、异常信息不得含原文片段（§6.5-7，任何模型违反均 reject）。
- [ ] B2 数据库不存正文：全部表字段只允许数值/枚举/哈希桶/标签；features.char_freq_hash 与 ngram_hash_digest 必须不可还原原文（§7.2）。
- [ ] B3 books.display_name 只允许文件名/书名，不允许正文（§7.2 books，F3 裁定）。
- [ ] B4 recommendations.reasons_json 只含标签 id/统计量/分数理由，不含原文摘句。
- [ ] B5 测试夹具一律全合成数据（00-index 硬约束）；禁止用真实作品正文做 fixture 或示例。
- [ ] B6 events.payload_json 字段白名单核对：仅数值/标签/路径/原因；**不得含 effective_weight**（由 occurred_at+config_fingerprint 折叠时重算，§7.2 events）。
- [ ] B7 导出物（画像导出/报告/推荐专区索引）不得夹带正文；推荐专区只存索引与分数（CONTEXT.md 落位）。

## C. 契约违背（架构与 ADR 契约）

- [ ] C1 事件表 append-only：只允许 INSERT；UPDATE/DELETE 必须被拒（写入层拒绝；触发器硬保险为加分项）（ADR-0003、§7.1）。
- [ ] C2 投影表（books/features/profile 等）可由事件重建；画像版本从事件流重放**逐位复现**（ADR-0003；对应测试必须存在且断言一致）。
- [ ] C3 events 与 profile_versions 必须带 schema_version 与 config_fingerprint（回放可复现，§7.2）。
- [ ] C4 半衰期 w = 0.5^(age/half)；配置非法（≤0）时拒绝该配置、回退默认并记事件（§6.4/§6.5 上文 L506）。
- [ ] C5 权重用相对归一，避免极高样本量下 0/1 饱和（L507）。
- [ ] C6 n_pos<10 时 confidence 上限 0.3 且展示「样本 n 篇，仅供参考」；不展示 AUC；AUC 仅在正负样本各 ≥10 且交叉验证后才出现（§6.4 L509、CONTEXT.md 置信度）。
- [ ] C7 不合口味：进负样本、立即从推荐列表消失；带权且时间衰减，**永不构成硬排除**（CONTEXT.md）。
- [ ] C8 探索位默认 2 篇，选取规则确定性（不得引入不可复现的随机性）（CONTEXT.md）。
- [ ] C9 去重唯一硬过滤 = 同名同大小；dedup_key = (size_bytes, 规范化文件名) 哈希；跨名同内容进人工合并清单；**内容哈希已被否**（CONTEXT.md 去重、§7.2 books，工单 02）。
- [ ] C10 落位：同分区硬链接、跨分区复制；推荐专区不复制实体（CONTEXT.md 落位，工单 03）。
- [ ] C11 SQLite 设置：WAL / synchronous=NORMAL / busy_timeout=5000 / foreign_keys=ON；写连接唯一、事务短小、特征提取在事务外（§7.1，工单 05）。
- [ ] C12 ui_usage 与 signals 严格分离：ui_usage 不进口味事件流，画像回放/打分/推荐语义完全忽略它（§7.2 L686-694、§11.7，工单 12b）。
- [ ] C13 schema_meta 存 schema_version/created_at/app_version（§7.1）。
- [ ] C14 每票完成即 commit；提交前全量 pytest 绿（00-index）。
- [ ] C15 依赖变更只进 reco/.venv 与 pip/HF 缓存目录；venv 重建必须重放 CRT 对齐 12 DLL 进 torch/lib（ADR-0004、工单 01）。
- [ ] C16 不写 xiaos 仓库、不读真实正文目录做开发输入（00-index）。

## D. manual 覆盖（人工优先；§7.2、CONTEXT.md 关系图/标签）

- [ ] D1 book_tags 中 source=manual 的行不被自动提取覆盖；拒绝发生在**写入层**，非调用方自觉（L611）；必须有覆盖「拒绝路径」的测试。
- [ ] D2 relation_fields 是逐字段真相：source=manual 字段不被 relation_updated/auto 提取覆盖；graph_json 只是可重建快照，重建不得把 manual 字段改写成 auto 值（L618-620、CONTEXT.md 关系图「人工修改永不被自动提取覆盖」）。
- [ ] D3 status=merged/archived 的标签不参与打分但保留标注；标签合并/归档不得丢失 manual 标注（L611、CONTEXT.md 标签）。
- [ ] D4 关系图置信度低时留空，不填默认值伪装（CONTEXT.md 关系图）。
- [ ] D5 GUI 逐字段编辑写回 relation_fields 时 source 必须记 manual；重新提取后不静默重置（工单 12/07）。
- [ ] D6 手动「标为喜欢」产生的正样本身份稳定，不被扫描/导入流程清除（CONTEXT.md 正样本，工单 10）。

## E. 自洽断言（测试真实性；00-index、历史 gotcha）

- [ ] E1 红测试先行：每切片有先红后绿证据；修复票必须有**修复前红**（00-index TDD 硬约束）。
- [ ] E2 逆向验证：把实现改坏（revert 修复/变异一行）必须转红；不能只靠「曾经红过」的口头记录。
- [ ] E3 数值断言可手算：TF-IDF/相似度/衰减/归一小样本给出手算期望值（ADR-0002/0004 的可解释性理由）；不得只断言「不抛异常」。
- [ ] E4 期望值来源独立：测试不得 import 实现内部同一函数来算期望（复制实现 = 假自洽）；用独立手算/固定桩/契约值。
- [ ] E5 异步与超时：测试必须等待返回值（历史 gotcha：丢弃 async 返回值假绿）；单测试有超时上限。
- [ ] E6 全量 pytest 绿才允许标 completed；不留 skip/xfail 静默通过。
- [ ] E7 跨模块契约（13 排序一致性、14 换核演练）不得只用桩测试冒充集成验证。
- [ ] E8 时间相关测试注入时钟（occurred_at 注入时钟写入，§7.2 signals）；不 sleep 等真实时间。
- [ ] E9 合成数据生成器确定性（固定种子），测试可重复运行且幂等。

## 各票重点速查

| 票 | 必查项 |
|---|---|
| 01 | C15 |
| 02 | B5、C9、C14、C16 |
| 03 | B7、C10、C14 |
| 04 | B2、B3、B5、C11（事务外提取）、E3 |
| 05 | B2、B6、C1、C2、C3、C11、C13 |
| 06 | D1、D3、C3 |
| 07 | D2、D4、B1、A8 |
| 08 | A1-A5、A8、A9、E3 |
| 09 | A4、A5、C2、C4、C5、C6、E3、E8 |
| 10 | A1、C7、D6、E8 |
| 11 | A6、C6、C7 |
| 12 | A6、B4、D5、C6、C7 |
| 12b | C12、E8 |
| 13 | A6、E7 |
| 14 | A7 全量演练 + 以上全部抽样复查 |

## 常驻守护规则（每票 completed 时执行）

1. 读该票 changedPaths 的 diff，对照上表必查项 + 五类全查。
2. 结论写入 .scratch/reco-impl/audit-log.md。
3. 有 findings → 立即通知队长 + 该票 implementer，强制 PR-first；blocker 未修复前，依赖它的下一批票不派发。
4. 审计中发现清单本身缺项 → 先补清单（本文件，追加式修订并升版本号），再审计。
