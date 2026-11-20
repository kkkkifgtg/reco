# 09：画像与置信度（时间衰减）

**What to build:** build/update_profile、decay_weight(w=0.5^(age/half))、profile_confidence、axis_trends、export_profile；半衰期分档 90d(标注)/30d(行为)/180d(稳定偏好)。画像可见可编辑可导出。

**Blocked by:** 08
**Status:** ready-for-agent

- [ ] w(half)=0.5 数值断言；旧反馈权重更低；单次反馈影响有上限
- [ ] 样本少→confidence 如实报「还不够准」，不给假指标
- [ ] 「不合口味」后画像秒级更新且显示「XX 维度权重已下调」
- [ ] profile 版本化；同事件流重放版本逐位一致
