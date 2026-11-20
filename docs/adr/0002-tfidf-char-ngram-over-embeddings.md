# 0002 — 相似度用 TF-IDF 字符 n-gram，不用句向量

Status: proposed

本机 torch 运行时损坏（WinError 126），模型权重源不可达，句向量路线在目标环境不可行；TF-IDF（字符 2–3 gram）可离线安装与缓存、可手算断言、对 <100 篇的小样本足够。因此相似度采用字符 n-gram TF-IDF，句向量仅作为可插拔备选保留在接口之后。

## Considered Options

- sentence-transformers 句向量：语义质量更高，但依赖链（torch + 模型权重）在目标环境无法安装或下载。否决，保留为未来可插拔项。
