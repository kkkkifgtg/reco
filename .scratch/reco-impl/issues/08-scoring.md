# 08：相似度打分（rank 唯一排序入口）

**What to build:** score/rank 纯函数：小样本（正<10）只相似度（TF-IDF char n-gram 质心）；正负各≥10 才启用成对分类器并交叉验证；w_sim/w_model/w_behavior 按 weight_schedule 平滑过渡；sklearn 延迟导入；探索位确定性选取（候选集指纹序）。

**Blocked by:** 05,06,07
**Status:** ready-for-agent

- [ ] rank 是 CLI/GUI 唯一排序入口（文档明示；第二条排序路径出现即 blocker）
- [ ] 手算相似度期望断言；禁自洽期望；单类别不返回 0.5 假指标（如实「不足以判定」）
- [ ] 首次调用才 import sklearn；冷启动首屏骨架不触发打分
- [ ] weight_schedule 单调平滑无跳变；每次权重变化入画像（事件）
- [ ] ADR-0001：score 只读四模型输出合成；relation/tag 模型语义不进 score 内部（§6.5 反例清单为测试用例）
