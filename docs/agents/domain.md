# Domain Docs

reco 采用 **single-context** 布局：全仓共用根目录的领域术语与 ADR。

## 探索前阅读

- **`CONTEXT.md`**：领域术语的唯一来源。修改代码、测试、issue 或用户可见文案时，沿用其中的正式名称与避用词。
- **`docs/adr/`**：读取与当前工作区域相关的已接受决策。
- **`docs/ARCHITECTURE.md`**：需要接口、数据流或当前实现口径时阅读；文首「当前实现口径」优先于下文保留的历史论证。

## 文件结构

```text
/
├── CONTEXT.md
└── docs/
    ├── ARCHITECTURE.md
    └── adr/
```

## 消费规则

- 输出中的领域概念使用 `CONTEXT.md` 的术语；发现术语缺口时交给 `/domain-modeling`，不要自行创造同义别名。
- 实现与 ADR 冲突时显式说明冲突及原因，不要静默覆盖决策。
- 术语或架构决策只写入权威文件；其他文档通过指针引用，不复制定义。
