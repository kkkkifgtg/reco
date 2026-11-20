# 工单 06（W6/t8）标签体系——预读笔记（prep：implementer-2，2026-11-21）

> 状态：t8 阻塞于 t6（W4 特征提取），未认领。本文件沉淀预读结论，认领后按此落 TDD 切片。
> 依据：原始规格包 F:/date/xiaos/docs/proposals/adult-reco/tags.md（只读）+ ARCHITECTURE §6.2/§7.2/Q11 + audit-checklist D 类 + issue 06 四条验收。

## 0. 接缝（已与 implementer-4 达成一致，t6/attempt 7c348e3d）
1. **match_tags 输入契约（W4 已落红测试与口径 §5）**：reco_io.features 的 extract_statistics(text, ..., alias_probe: Sequence[str] = ()) → dict 含 key "alias_counts"；extract_features(..., alias_probe=()) → FeatureResult.alias_counts（降级结果一律 {} → 零命中、不抛异常）。词表纯注入、零硬编码（§5.6）；流式路径携带跨块尾巴，别名跨界不漏计。match_tags 鸭子读 .alias_counts，签名零冲突；t8 红测试可直接 import reco_io.features 这两个签名写集成期望。
2. **计数语义（W4 口径）**：双方 casefold 后非重叠子串计数（str.count 语义，含标点上下文），空 alias 忽略。
3. **store 协议**：reco-labels 依赖图只挂 reco-core（§4.2），绝不 import reco-store 实现。定义窄 Protocol LabelsStore（iter_tags/iter_aliases/iter_book_tags/update_tag/redirect_book_tag_rows/append_event/insert_proposal），测试用内存 fake；W5（t7）落地后其连接按协议满足。方法名在 t7 开工时与 implementer-3 对齐。
4. **四模型隔离（A2/§6.5-2）**：reco_labels 不读事件流语义、不碰 score/weights；axis_vector 的权重由调用方（profile 侧）传入。

## 0.1 种子内 alias 冲突（实测规格文本发现）
- 「双性」同时是 D2-role（攻/受/互攻/**双性**）与 D3-body_part（…/**双性**/…）的值名——同 alias 文本命中两个 active 标签，两别名等长，「最长者胜」平局。
- **裁定**：等长平局 → tag_id 字典序最小者胜，同时写 tag_conflict 事件（payload 只含 alias/tag_id 列表/计数，无正文）待人工裁定；explain_tag_hit 必须披露该命中经冲突裁定（evidence.kind="alias_conflict_resolved"）。
- 对应红测试：test_normalize_match.py 双性冲突用例断言确定性与事件落库。

## 1. 种子数据（tags.md 权威，不臆造）
- 六域：D1 关系结构（topology/power/consent/evolution）｜D2 人物属性（role/body/archetype/age/species）｜D3 核心机制（mechanism/coercion/transformation/body_part/setting）｜D4 呈现方式（explicitness/pov/length/style/pace）｜D5 情绪与结局（ending/tone/emotion_arc）｜D6 元信息（language/completion/quality_flag）——共 **25 轴**。
- tags 表三层层级：域行（id=d1..d6, parent=NULL）、轴行（id=轴缩写如 mech, parent=dN）、值行（id=mech.hypnosis 形态, parent=轴 id, axis=全名）。§6.2 说 parent 是 D1…D6、规格 JSON 示例 parent="mech"——取 JSON 数据样例为准（值行 parent=轴行；§6.2 括号描述的是域级父节点）。
- aliases：规格只给出 mech.hypnosis 一例（催眠 ← 精神控制/洗脑/mind control），逐字种子；其余值行 alias 空 → **暂不参与自动识别**（规格「新增」规则），补 alias 才升级为可识别。created_by=user、created_at 固定种子日期（确定性）。

## 2. v1 口径裁定（Q11：只做 alias 精确命中）
- normalize_tag(raw, taxonomy)：精确命中（strip 后全等），只在 status=active 的标签及其 alias 里找；merged/archived 不命中；命中 merged 行按 merged_into 链解析到目标（旧 id 重定向）；冲突（多 active 标签同 alias）→ 别名最长者胜 + 记冲突（写 conflict 事件待人工裁定）；未命中 → None，绝不模糊猜。
- match_tags(stats, taxonomy)：读 stats.alias_counts（Protocol：鸭子类型 .alias_counts 映射）；alias→tag 经 normalize 语义映射；同标签多 alias 命中合并为一个 TagHit；TagHit = {tag_id, confidence, basis}。confidence：精确命中即事实 → **1.0**（不臆造分级）；basis = {"matches":[{"alias","count"}]}——只含标签/别名/数值，无正文（B1/B4）。
- explain_tag_hit(hit)：把 basis 展开为 Evidence 列表（alias、count、来源=alias_exact）。

## 3. 生命周期
- merge_tags(store, source, target)：①book_tags 中 source 行改指向 target（**逐行保留 source/manual/confidence/basis**——D3 合并不丢 manual）；②source 行 status=merged + merged_into=target；③source 的 aliases 原地保留（经 merged_into 链可解析）；④append merge 事件。MergeResult 返回重定向行数。逆向验收：删历史转红（issue 06 第 1 条）。
- archive_tag(store, tag_id)：status=archived，一切数据保留；normalize/match 跳过 archived（不参与打分）；book_tags 原样（issue 06 第 2 条）。
- propose_tags(store, cooccurrence, threshold)：cooccurrence 是外部传入的共现统计（Mapping[词对/词, count]——来源 W4/后续统计，本票不计算共现）；超阈值候选写 proposals 表 status=pending，**绝不创建 tags 行**（issue 06 第 4 条：落库需显式确认）；确认动作（accept_proposal）才建标签，created_by=user。
- record_tag_hits(store, book_id, hits)（自动命中写库）：PK(book_id, tag_id) 冲突时，existing.source=manual/user → **写入层拒绝覆盖**（D1，拒绝在写入层非调用方自觉），返回 skipped 明细；existing.source=auto → 更新 confidence/basis/updated_at。

## 4. 轴覆盖与向量（手算断言，audit E3/E4）
- axis_coverage(tag_hits_by_book, axis) -> Coverage(covered, total)：covered=该轴有命中的书数，total=书总数；0/N 照实返回（小样本「1/N 信息不足」不放大）。
- axis_vector(tag_hits, axis_weights) -> dict[str, float]：只为**有命中的轴**产条目，值 = axis_weights[axis] × mean(confidence)；未覆盖轴**缺席**（下游读缺席=权重 0，不猜默认值——规格「稀疏数据」节）。手算样例随红测试落盘。

## 5. 测试切片计划（TDD 红→绿→逆向）
| 文件 | 覆盖 |
|---|---|
| test_taxonomy_seed.py | 六域 25 轴完整性、三层层级、种子 alias 逐字、确定性 created_at |
| test_normalize_match.py | 精确命中/未命中 None/merged 链解析/冲突最长者胜/补 alias 才识别/explain_tag_hit |
| test_merge_archive.py | merge 重定向+manual 保留+旧 id 可解析（逆向：删历史转红）；archive 不参与打分数据完整 |
| test_axis_coverage.py | axis_coverage/axis_vector 手算期望；未覆盖轴缺席语义 |
| test_proposals.py | propose 只产 pending、tags 零改动、accept 显式确认才建标签 |
| test_book_tags_write.py | D1 写入层拒绝覆盖 manual（拒绝路径测试）；auto 可更新 |
