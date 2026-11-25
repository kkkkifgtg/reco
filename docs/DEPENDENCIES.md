# DEPENDENCIES — 依赖与锁文件

> 采集日期：2026-11-25。版本以根 `uv.lock` 为准；本文件只解释归属与用途，不复制完整求解结果。
> 环境：CPython 3.12、uv 0.13.10（安装在 `.venv`，不属于锁文件）、29 个锁定包。

## 安装

```bash
scripts/bootstrap.ps1                 # Windows
scripts/bootstrap.sh                  # Git Bash / POSIX
# 等价核心命令：.venv/Scripts/uv.exe sync --frozen --inexact
```

`--frozen` 保证使用 `uv.lock`；`--inexact` 保留环境里由用户额外安装、未写入锁文件的包。首次安装需要访问包索引；锁文件本身不提供离线 wheel 缓存。

## 直接运行依赖

| 声明包 | 依赖 | 锁定版本 | 声明 | 用途 |
|---|---|---:|---|---|
| reco-core | numpy | 2.5.5 | `>=2.5` | 向量与评估数值运算 |
| reco-core | scipy | 1.18.1 | `>=1.18` | scikit-learn 稀疏矩阵载体 |
| reco-core | scikit-learn | 1.9.1 | `>=1.9` | 可选的成对分类与评估；生产提取管线不调用它 |
| reco-io | pypdf | 7.1.1 | `>=7.1` | PDF 文本层读取 |
| reco-io | zhconv | 1.4.3 | `>=1.4.3` | 标签候选词繁简归一 |
| reco-ui | PyQt6 | 6.12.1 | `>=6.12` | 桌面 GUI；Qt 导入集中在 `reco_ui.qt` |
| reco-ui | PyQt6-Qt6 | 6.12.0 | PyQt6 元数据依赖 | Qt6 运行库 |
| reco-ui | PyQt6-sip | 13.13.1 | PyQt6 元数据依赖 | Python/Qt 绑定胶水 |

### 工作区内部依赖

- `reco-cli` → `reco-core`、`reco-io`、`reco-store`
- `reco-io` → `reco-store`、`reco-labels`、`reco-relations`
- `reco-labels` → `reco-core`、`reco-relations`
- `reco-relations`、`reco-store` → `reco-core`
- `reco-ui` → `reco-core`、`reco-io`、`reco-labels`、`reco-store`

包描述、脚本入口与 pytest marker 声明位于各包 `pyproject.toml`；修改 import 结构时同步修改这些声明。

## 开发依赖

根 `pyproject.toml` 的 `dependency-groups.dev` 声明：

| 包 | 锁定版本 | 声明 | 用途 |
|---|---:|---|---|
| pytest | 9.2.0 | `>=8.0` | 全仓测试 |
| python-docx | 1.2.0 | `>=1.2` | 程序化生成 DOCX 合成测试样例 |

## 可选、未入锁的依赖

| 包 | 当前状态 | 说明 |
|---|---|---|
| jieba | 0.42.1（本机已装，未入锁） | 标签挖掘首选分词器；缺失时 `tokenizer` 诚实降级到内置字符 n-gram |
| pkuseg | 环境可选安装 | 可选分词器；缺失时同样降级 |
| torch / sentence-transformers | 默认不安装 | ADR-0004 的句向量备选；当前生产推荐未启用 |

可选分词器只影响标签候选切分，不影响 `reco-core` 的生产向量格式。任何新增第三方依赖都必须更新对应包声明、`uv.lock`、本文件与架构依赖说明。

## 向量口径

生产链路 `reco_io.extract.vector_payload_from_features` 使用字符 2–3gram 哈希桶计数，保留 Top-64 个桶并做 L2 归一；`idf_version` 是既有列名，当前值为 `reco-ngram-counts/1`，明确表示没有 IDF。`reco_core.vectorize.char_ngram_bucket_tfidf` 另保留语料级 TF-IDF 实现，但当前提取入口没有调用它。
