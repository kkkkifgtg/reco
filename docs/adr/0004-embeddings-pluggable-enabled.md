# 0004 — 句向量路线可行：从「被否」改为「可插拔备选」，默认仍 TF-IDF

Status: accepted（supersedes ADR-0002）

D1 工具链修复（t9）翻转了 ADR-0002 的证据链：旧 Roaming torch 的「损坏」实为全机缺 VC 运行库组件（System32 缺 vcruntime140_1/vcruntime140_threads/msvcp140_atomic_wait）；项目 venv 全新安装 torch 2.15.0+cpu 走本机代理成功，CRT 12 DLL 对齐进 torch/lib 修复加载（LoadLibraryExW(0x1100) 不含应用目录）；HF 权重经 hf-mirror.com 可达，all-MiniLM-L6-v2 真实编码 dim=384。因此「句向量在目标环境不可行」的前提不再成立。

决定：相似度默认仍用 TF-IDF 字符 n-gram（ADR-0002 的其余理由继续成立：小样本可解释、可手算断言、无重依赖、冷启动友好——import torch 实测秒级）；句向量降级为可插拔备选，接口位置不变（similarity_scores 的实现侧适配器），由独立增强票按需启用。重建 venv 必须重放 CRT 对齐步骤（进环境搭建工单与 bootstrap）。

## Considered Options

- 全面切换句向量：语义质量更高，但引入秒级冷导入与 90MB 模型依赖，与首屏 <500ms 与「可手算断言」冲突。否决，保留为 pluggable。
- 维持「句向量不可行」旧结论：与实测矛盾，违反「补齐优先于绕开」。否决。
