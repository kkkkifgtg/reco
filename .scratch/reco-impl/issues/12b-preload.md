# 12b：自适应热加载（高频区域预载）

**What to build:** 程序监测用户高频点击的页面/功能区并逐步热加载：ui_usage 计数表（独立于口味事件流）→ 热集判定（uses≥3 或 Top-K，推荐专区恒热）→ 首帧后空闲档后台预热，随使用习惯动态收敛。

**Blocked by:** 12
**Status:** ready-for-agent

- [ ] 导航/功能点击使 ui_usage 计数递增（page_id, uses, last_used），落 SQLite 独立表
- [ ] 口味回放/打分/推荐完全忽略 ui_usage（回归测试：含 ui_usage 数据时 verify_replay 结果逐位不变）
- [ ] 热集判定：uses≥3（阈值可配）或 Top-K；推荐专区主面板恒热
- [ ] 预热在首帧骨架后空闲档执行（QThreadPool，低优先级），用户操作可打断、不抢首帧
- [ ] 「已加载」标记仅在预热成功后置位（逆向：预热失败后该区仍能按需加载且可重试）
- [ ] 冷区按需加载路径不回归；RECO_PERF 输出 preload span；首屏骨架 <500ms 不变
