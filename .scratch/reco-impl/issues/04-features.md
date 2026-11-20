# 04：特征提取（无分词器中文统计）

**What to build:** 新导入文件在本地内存算统计量并产出特征向量：字数/段落数/对话占比/标点比/感叹疑问比/平均段长/char n-gram 稀疏向量（n=(2,3), min_df=2, max_features=200k, sublinear_tf, L2）。只有数值与标签命中，正文零落盘。

**Blocked by:** 02
**Status:** ready-for-agent

- [ ] 每个统计量有手算期望值断言（合成短文本）
- [ ] extractor_version 版本化；schema 断言特征库任何表不含正文列
- [ ] 空/损坏/超大文件降级为部分特征+quality_flag，不崩不瞎填
- [ ] 读完即弃，无正文中间产物落盘（审计断言）
