# 0003 — SQLite 以 append-only 事件表为唯一真相，投影可重建

Status: accepted

口味画像必须可回放、可版本化、能回答「为什么现在这么推荐」；WAL + 单写者 + 短事务已满足本地单机并发。因此特征库以 append-only 事件表为唯一真相，books/features/profile 等投影表可随时由事件重建，画像版本从事件流重放可逐位复现。

## Considered Options

- 直接 mutable 行存储：写入简单，但丢失回放能力，「算法持续成长」与「可复现」两条硬约束无法验收。否决。
