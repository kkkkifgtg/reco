# 12：GUI 三板块（PyQt6 + QSS 深色主题）

**What to build:** 喜欢专区/候选池专区/推荐专区三板块；推荐列表带理由与探索标记；「不合口味/加入候选池/标为喜欢」按钮；画像页（轴权重/样本量/置信度/Top-N/漂移/版本）；生成篇数输入（默认 2 篇）。性能 7 坑按 PyQt6 语境落实。

**Blocked by:** 10, 03（落位回流），依赖票 01（PyQt6 由本票声明并安装，装不上走 CLI 降级退路）
**Status:** ready-for-agent

- [ ] QSS 深色主题规范：统一间距/圆角/层次/图标（美观验收：截图入 docs/ui-screenshots/）
- [ ] UI 线程只发起+渲染：扫描/打分/DB 全在 QThread/QThreadPool；全局搜无 .GetAwaiter().GetResult() 等价阻塞
- [ ] 按需加载+后台预热；「已加载」标记成功后才置位
- [ ] model/view + setUpdatesEnabled 批更新；≥100 行刷新 <50ms；冷启动首屏骨架 <500ms（RECO_PERF=1 实测）
- [ ] RECO_PERF=1 各阶段毫秒入 stderr（关闭近零开销）；RECO_PERF_TABS=1 + QT_QPA_PLATFORM=offscreen 自动遍历退出
- [ ] in-flight 去重；QMenu 销毁幂等；PyQt6 装不上→CLI 全功能可用降级路径演练
