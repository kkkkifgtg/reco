# 11：CLI 入口（无界面依赖）

**What to build:** python -m reco_cli：report/scan/recommend/feedback/explain/replay 子命令，stdout/JSON 纯净输出；UTF-8 输出断言（修 T0 占位版乱码问题）；算法 CLI 四入口（report/eval/explain/replay）。

**Blocked by:** 10
**Status:** ready-for-agent

- [ ] 无 UI/文件扫描 import（只经公开 API）；sys.exit 0/非 0 语义正确
- [ ] 输出 UTF-8 且英文 locale 控制台不乱码（回归测试）
- [ ] reports/reco-<date>.json 诊断文件：样本量/轴覆盖/权重/CV 指标，无正文无书名（book_id 代替）
- [ ] docs/algorithm-changelog.md 记一行首次算法上线
