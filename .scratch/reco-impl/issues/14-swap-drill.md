# 14：换核演练 + 全验收清单

**What to build:** modules.md 唯一硬证据：复制 reco-core 打分实现改成另一套（分类器变体），只改 reco-core 内部，reco-cli/reco-ui 一行不改跑通；然后逐条打勾最终验收清单（计划 §11）。

**Blocked by:** 13
**Status:** ready-for-agent

- [ ] 换核演练通过（diff 证明 cli/ui 零改动）
- [ ] 7 包各自独立 pip install -e 与 pytest 通过
- [ ] 全验收清单逐条打勾并归档 docs/ACCEPTANCE.md
- [ ] 逆向验证记录：每个关键测试改回缺陷形态都转红
