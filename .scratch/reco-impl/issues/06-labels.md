# 06：标签体系（数据驱动）

**What to build:** 域→轴→值种子六域；aliases 命中识别；新增（user 创建暂不自动识别）/合并（历史改指向保留）/归档（不删）；自动发现只提议。演示：加标签→补 alias→重扫命中。

**Blocked by:** 04
**Status:** ready-for-agent

- [ ] merge 后旧 id 重定向且历史不丢（逆向：删历史转红）
- [ ] archive 后不参与打分但数据完整
- [ ] 某轴无标注→该轴权重 0；覆盖样本数可查
- [ ] propose_tags 只产提案，落库需显式确认
