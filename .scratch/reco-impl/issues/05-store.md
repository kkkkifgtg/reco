# 05：SQLite 特征库 + append-only 事件流

**What to build:** WAL + 单写者队列 + 短事务 + busy_timeout：books/files/features/tags/book_tags/relations/feedback/profile_versions/recommendations 投影表 + events 事件表（唯一真相）。演示：写入→投影→回放重建逐位一致。

**Blocked by:** 04
**Status:** ready-for-agent

- [ ] verify_replay 重放重建与存量逐位一致（逆向：篡改事件必须被发现）
- [ ] 双连接并发读不阻塞写；写由单写者队列串行化
- [ ] schema_version + config_fingerprint；dedup_key=(size,规范化名) 部分唯一索引，规范化规则表进文档
- [ ] effective_weight 折叠时由 occurred_at 重算（裁定：不存列）
