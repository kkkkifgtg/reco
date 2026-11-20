# 03：硬链接/复制落位

**What to build:** 提交到专区的落位：同卷硬链接、跨卷复制（st_dev 预判 EXDEV）、失败退回复制并记录 placement=copy。演示：同卷提交后删源仍可读。

**Blocked by:** 02
**Status:** ready-for-agent

- [ ] EXDEV/非 NTFS 自动复制不抛用户
- [ ] st_nlink>=2、st_ino 相等、删源可读（逆向：假硬链接测试转红）
- [ ] 推荐专区只存索引与分数
- [ ] 指纹提供者注入模拟跨卷（不依赖真第二块盘）
