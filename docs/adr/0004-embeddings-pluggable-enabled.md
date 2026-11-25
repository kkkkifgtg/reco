# 0004 — 句向量路线可行：从「被否」改为「可插拔备选」

Status: accepted（supersedes ADR-0002）

D1 工具链修复（t9）翻转了 ADR-0002 的证据链：旧 Roaming torch 的「损坏」实为全机缺 VC 运行库组件（System32 缺 vcruntime140_1/vcruntime140_threads/msvcp140_atomic_wait）；项目 venv 全新安装 torch 2.15.0+cpu 走本机代理成功，CRT 12 DLL 对齐进 torch/lib 修复加载（LoadLibraryExW(0x1100) 不含应用目录）；HF 权重经 hf-mirror.com 可达，all-MiniLM-L6-v2 真实编码 dim=384。因此「句向量在目标环境不可行」的前提不再成立。

决定：句向量保持为可插拔备选，接口位置不变（`similarity_scores` 的实现侧适配器），由独立增强票按需启用。重建 venv 必须重放 CRT 对齐步骤（进环境搭建工单与 bootstrap）。

## 2026-11-25 实现校正

生产提取入口 `reco_io.extract` 当前使用字符 2–3gram 哈希桶计数、Top-64、L2 归一，不计算 IDF；`feature_vectors.idf_version` 是历史列名，当前值为 `reco-ngram-counts/1`。`reco_core.vectorize.char_ngram_bucket_tfidf` 保留独立 TF-IDF 实现，可供后续替换生产向量的实现使用。因此本 ADR 继续约束的是「句向量为可选备选、默认不引入重依赖」，不再声称当前生产默认调用 TF-IDF。

## Considered Options

- 全面切换句向量：语义质量更高，但引入秒级冷导入与 90MB 模型依赖，与首屏 <500ms 与「可手算断言」冲突。否决，保留为 pluggable。
- 维持「句向量不可行」旧结论：与实测矛盾，违反「补齐优先于绕开」。否决。
