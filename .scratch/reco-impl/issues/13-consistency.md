# 13：CLI/GUI 排序一致性

**What to build:** 同一合成候选池，CLI 输出排序与 GUI 展示排序逐位一致的自动化比对；探索位选取也一致（确定性序）。

**Blocked by:** 11, 12
**Status:** ready-for-agent

- [ ] 一致性测试：两入口 rank 输出逐位相等（含 reason 字段）
- [ ] 逆向验证：给 GUI 塞第二条排序路径测试必须转红
- [ ] RECO_PERF_TABS 快照比对入 CI 脚本
