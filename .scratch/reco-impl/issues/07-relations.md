# 07：人物关系模型（结构化可编辑）

**What to build:** 从统计量产出 relationship_graph：characters/edges/topology/power_balance/evidence(confidence,source)；八维度识别。JSON 落盘、逐字段可编辑。

**Blocked by:** 04
**Status:** ready-for-agent

- [ ] source=manual 字段永不被自动覆盖（逆向：去保护转红）
- [ ] 低置信留空；每字段带识别依据
- [ ] merge_manual_overrides 只合并用户改过字段；validate 拒绝悬空边
- [ ] 八维度各自独立可测（ADR-0001 四模型独立接缝）
