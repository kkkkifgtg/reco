# 02：目录扫描与三专区同步

**What to build:** 数据根三专区单趟 scandir 扫描，产出 delta（新增/移动/删除/内容变化）；移动文件即等于标注。演示：临时目录放/移/删，scan 返回正确 delta。

**Blocked by:** None（与 01 并行）
**Status:** ready-for-agent

- [ ] 身份判定用 os.stat(entry.path, follow_symlinks=False)（DirEntry.stat 三字段为 0 有回归测试）
- [ ] (st_dev,st_ino) 识别同卷移动/改名保留标注；(size,mtime_ns) 识别内容变化
- [ ] 递归扫描；.txt/.epub/.md 之外忽略并上报（只文件名）
- [ ] 合成数据：空目录/同名不同内容/损坏/超大/无候选
- [ ] RECO_PERF 打点：千文件合成目录 < 1s
