# reco-impl 测试基线（test-runner 常驻守护）

- 采集时间: 2026-11-21T00:42:56+0800 (UTC 偏移格式, Asia/Shanghai 本地)
- 采集人: test-runner (S1 / 任务 t3)
- git HEAD: 556af2d ("docs(tickets): 01 补 torch CRT 重放步骤（ADR-0004 前置）")
- 工作区状态: clean (0 dirty files)

## 命令与结果

```
./.venv/Scripts/python.exe -m pytest -q
```

结果: **7 passed in 0.07s**（exit 0）

## 环境

- Python: 3.12.14 (.venv)
- pytest: 9.2.0

## 测试清单（7 个占位测试，与 ARCHITECTURE 七包一一对应）

| 包 | 测试文件 |
|---|---|
| reco-core | packages/reco-core/tests/test_placeholder.py |
| reco-store | packages/reco-store/tests/test_placeholder.py |
| reco-labels | packages/reco-labels/tests/test_placeholder.py |
| reco-relations | packages/reco-relations/tests/test_placeholder.py |
| reco-io | packages/reco-io/tests/test_placeholder.py |
| reco-cli | packages/reco-cli/tests/test_placeholder.py |
| reco-ui | packages/reco-ui/tests/test_placeholder.py |

## pytest 配置

```
--- pytest cfg in pyproject ---
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["packages"]
addopts = "-ra --import-mode=importlib"
--- packages ---
reco-cli
reco-core
reco-io
reco-labels
reco-relations
reco-store
reco-ui
```

## 常驻守护职责（S1 持续生效）

每票 completed 报告到达时：
1. 立即跑全量基线命令 `./.venv/Scripts/python.exe -m pytest -q`，必须 ≥7 passed 且 0 failed/error；
2. 对该票改动做针对性逆向验证（改坏实现 → 对应测试必须变红；红→绿闭环成立才算过）；
3. 一致性冒烟：票间接缝（依赖方向、公共 API 签名）不回归；
4. 性能冒烟：涉及扫描/向量化/评分的热路径留意耗时无数量级回退；
5. 结果报队长；任何失败即阻断该票验收，走 PR-first 修复流（红测试先行 → 修复 → 逆向验证 → 独立评审 → merge --no-ff）。

> 本基线后续随票推进在下方追加记录（append-only），不删除历史条目。

---

## 基线追加记录

### 2026-11-21T00:43+0800 逆向验证冒烟（S1 自检）

- 操作：故意改坏 packages/reco-core/tests/test_placeholder.py（注入恒假断言）→ 跑 pytest → git checkout 恢复 → 复跑 pytest。
- 结果：破坏时 pytest 报 `ERROR ... 1 error during collection`（runner 正确变红）；恢复后 `7 passed in 0.05s`（回到基线）。
- 结论：测试 runner 对测试破坏敏感，无假绿风险；恢复后工作区 clean（0 dirty files）。
- 注：红相以收集期错误呈现（注入方式所致），已足以证明失败会被捕获；后续票的逆向验证将优先用断言级失败复核。
### 2026-11-21T01:05+0800 W1（工单01 依赖落地）正式验收记录 — PASS

独立复核证据（test-runner，非转述）：
- 绿域 pytest：`10 passed in 2.70s`（7 基线 + 3 个 W1 新增 test_dependencies.py；3 个 reco-io collection error 属 W2 在途红，按并行票口径排除）。
- imports：numpy 2.5.5 / scipy 1.18.1 / sklearn 1.9.1 / torch 2.15.0+cpu，全部满足声明下限。
- CRT 对齐：torch/lib 12 个 CRT DLL 全命中（concrt140/msvcp140×4/vcruntime140×3/vccorlib140/vcamp140/vcomp140，逐一名单核验）。
- 断网演练：本测试员独立复跑（临时 venv + --no-index --find-links wheels/，非采信留痕）→ DRILL-OK 2.5.5/1.18.1/1.9.1，exit 0；wheels/ 7 包 wheel 实存。
- 逆向验证：临时删除 pyproject numpy 声明 → test_dependencies 恰好 2 failed；git 恢复 → 3 passed。测试对声明缺失敏感，非 mock。
- 一致性：uv.lock 21 包锁定；DEPENDENCIES.md 只记版本/命令/清单无叙事正文。
- 边界：验收扰动零残留（git status 仅 W2 在途文件）。
### 2026-11-21T01:26+0800 W2（工单02 目录扫描与三专区同步）正式验收记录 — PASS（commit 5711f25 @ feat/02-scanner）

独立复核证据（test-runner）：
- 绿域 pytest：`32 passed in 3.91s`（排除 W3 在途红 test_placement.py；W2 三文件 26 测试全绿）。
- os.stat 身份判定：scanner.py:123 唯一身份来源 `provider(path, follow_symlinks=False)`；DirEntry 零字段回归测试实存（test_scan_zones.py:143-157，Windows 上 (st_dev,st_ino,st_nlink)==(0,0,0) 断言 + os.stat 非零对照）。
- 语义：同身份换路径→移动（book_id 继承），同路径 (size,mtime_ns) 变→内容变化，仅单侧→新增/删除。scanner.py:150-184 双指针归并实现与工单一致。
- 独立 e2e 演练（真实 os.stat）：缺失专区只上报不自动创建（`missing_zones=('推荐专区',)` 且目录未建）、跨专区移动、mtime 内容变化、删除，全部语义断言通过；非 .txt/.epub/.md 上报只到文件名（`ignored==('c.log',)`）。
- 逆向验证（自选变异，非采信自报）：把 classify_delta 移动判定变异为「仅同专区识别」→ `test_cross_zone_move_keeps_identity` 恰好转红（1 failed, 17 passed）；git 恢复→22 passed，scanner.py 零残留。测试对移动语义真实敏感。
- RECO_PERF：RECO_PERF=1 下 4 测试全绿；千文件门禁实跑（25×40 合成目录）<1s 断言通过；打点格式 `phase=scan.scan_zones ms=N.NNN` 到 stderr。
- 边界：5711f25 提交范围 6 文件全在 reco-io 工单 scope 内，无跨票改动。
### 2026-11-21T01:33+0800 W3（工单03 硬链接/复制落位）正式验收记录 — PASS（commit 856155c @ feat/03-placement）

独立复核证据（test-runner，git worktree 免打扰验证，主检出 feat/04-features 未受扰动）：
- targeted 基相：14 passed（0.17s / 复原后 0.11s 两轮确认，与队长复跑一致）。
- 提交范围：856155c 仅 3 文件（reco-io 的 __init__/placement.py/test_placement.py），无跨票改动。
- 契约要点逐条核验：同卷预判（st_dev）不制造失败 link；EXDEV/WinError 1/ENOSYS 退复制；EPERM/EACCES 上抛不静默降级；指纹 (size,mtime_ns) 判等 SKIPPED/不等改名 book(1).txt 确定性去冲突；PlacementResult 六字段无正文（B 类审计点有测试）。
- 逆向重放（变异 A，假硬链接：_default_link 的 os.link→shutil.copy2）：`test_same_volume_hardlink_end_to_end` 恰好转红（1 failed，st_ino 断言，票面指定检测点）；git 复原→14 passed。
- 逆向重放（变异 B，删除复制半成品清理行）：`test_copy_size_mismatch_raises` + `test_copy_mtime_mismatch_raises` 恰好 2 failed（清理断言 not dst.exists()）；复原→14 passed。mtime_ns 对偶用例（同 size 异 mtime_ns）检测与清理两面均实证。
- 证据差异注记（低危，透明记录）：implementer-3 自报假硬链接变异应致 6 failed；本测试员以标准 link→copy 单点变异重放为 1 failed（检测点本身）。检测性成立、无漏检风险，计数差异推测其变异位点更宽（如连 mode 上报一并破坏）；不影响验收结论，留队长与 implementer-3 对账。
- worktree 已移除（git worktree remove --force + prune），worktree 内变异零残留；主检出状态仅守护层文件改动。
### 2026-11-21T02:22+0800 W4（工单04 特征提取）正式验收记录 — PASS（commit 1dbc02d @ feat/04-features）

独立复核证据（test-runner，git worktree 免打扰验证；期间 implementer-2 切走主检出分支，验收全程未受扰动）：
- 全量基相：73 passed（与自报一致）；worktree 内包级作用域 io 34 + core 7 全绿。
- 提交范围：9 文件全在 reco-io/reco-core + notes 证据留痕（04-handcomputed-samples.md 期望值独立来源、04-freeze-literals.py stdlib 冻结字面量），无跨票改动。
- 工单验收映射：手算期望值断言 S1-S14（独立期望来源，不调用实现复算）；降级矩阵 empty/undecodable/corrupt_container/pending_timeout/oversized_streamed 全覆盖且不瞎填（None/真零语义）；读完即弃审计断言（tempfile boom + 目录快照前后一致）；EXTRACTOR_VERSION=reco-stats/1 钉死+传播断言。schema 不含正文列的表级断言归 W5（store 表落地时），W4 已备结果级 B2 金丝雀防线——W5 验收时核对该接缝。
- perf 口径（独立复测，非采信其脚本）：60 文件×60KB 合成语料 22.02 ms/篇，千篇外推 ~22s ≪ 架构 §11.2 整轮同步 120s 预算，单文件 ≪ 30s 预算；RECO_PERF 打点 phase=features.extract ms=0.742 与自报 0.74ms 一致。
- 逆向重放三项全红（隔离 worktree + 包级作用域）：①p95 nearest-rank→线性插值 → test_S5 恰红（assert 6.7==6，口径漂移被手算期望逮住）→复原 13 passed；②EXTRACTOR_VERSION 常量漂移 → test_extractor_version_pinned 红→复原绿；③结果字段硬编码断传播 → test_ok_utf8_below_threshold 红（传播断言兜住）→复原后 io 34+core 7 全绿。
- import 卫生：reco_core/reco_io/store/labels/relations 导入不触发 sklearn/torch（子进程探测测试实存且绿）。
- 方法论教训（守护层留存）：worktree 变异验证必须用包级作用域 pytest（包 pyproject 有 pythonpath=["src"]）；仓库根作用域会经 venv editable .pth 解析到主检出 src，变异不被 import（首轮 3 变异假绿即此因，已修正重做）。
- worktree 已移除，变异零残留；主检出 features.py 无 MUTATION 标记。
### 2026-11-21T02:50+0800 W6（工单06 标签体系）正式验收记录 — PASS（commit e9a23bc @ feat/06-labels）

独立复核证据（test-runner，worktree@e9a23bc + 包级作用域）：
- 基相：labels 包级 57 passed；feat/06-labels 检出态全量 143 passed / 0 failed（master 87 + labels 净增 56，placeholder 移除对账吻合）。
- 提交范围：15 文件 1717 行全在 reco-labels scope。
- 种子锚定（防自洽）：程序化从 _SPEC 字面量独立重数——6 域/25 轴/133 值、逐轴与期望表全等（per-domain 22/31/33/21/16/10）、轴内无重复值、alias/override 逐字（mech.hypnosis ←「精神控制/洗脑/mind control」）；另目测抽算 3 轴复核（mechanism=10、archetype=11、tone=7）与期望表一致。注：原始规格包 tags.md 不在仓库内（三方核对第三源缺席），独立性以「红阶段期望表 ≠ 绿阶段实现」+ 字面量复算保障，留 auditor 裁量。
- 逆向重放（位点先行核验 MUTATION 标记落盘）：A) redirect_book_tag_rows 改删历史 → test_merge_redirects_rows_and_preserves_history 恰 1 failed（IndexError:51）→ 复原 10 passed；B) taxonomy.resolve_final 链不解析 → 4 failed（merge_archive: old_aliases_resolvable+chain_transitive；normalize_match: resolves_merged_chain+merged_alias_redirects）→ 复原 57 passed。
- RECO_PERF 与 test_labels_perf：3 测试全绿（200 书匹配<50ms、200 书落库<200ms、打点格式 phase=labels.load_taxonomy ms=N.NNN 契约成立）。低危观察：reco_labels 生产代码自身无 perf_span 打点（打点由测试包裹验证）——§11.3「可疑路径包住」在 labels 侧待 W5 SQLite 接入时补齐，不阻断本票。
- 全量口径归属：实测两检出态均 0 failed（feat/05-store 态 113/0、feat/06-labels 态 143/0）；队长 202+21 快照对应当时检出态下的 W5/W7 在途红（untracked 跨检出存续），无 W6 自有失败混入。计数差异属快照时点不同，透明记录。
- 协调观察：验证期间共享检出被切至 feat/05-store 又切回——我的 worktree 隔离未受影响；共享检出多票并发切换是常态风险，验收一律 worktree 化。
- worktree 已移除，变异零残留。
### 2026-11-21T02:58+0800 W7（工单07 人物关系模型）正式验收记录 — PASS（commit a9326ea @ feat/07-relations，附基点滞后注记）

独立复核证据（test-runner，worktree@a9326ea + 包级作用域）：
- 包级基相：relations 68 passed（三轮确认：基相/复原后/终态，与自报一致）。
- 提交范围：14 文件 1399 行全在 reco-relations scope。
- 八维度独立性（ADR-0001）：维度 1-8 各有独立测试函数（test_dimension_1..8+power/evolution/topology_fallback）；变异 I（清空 _CONSENT_MAP）→ 仅 dimension_7 测试 + test_golden_graph_hand_written 失败（后者是覆盖全维度的 E4 手写整图集成断言，属设计内多维修 assert）——其余维度测试零波及，独立性成立。
- manual 写入层保护（D2）：变异 II（_apply_updates 去保护）→ 恰 3 failed（refuses_to_overwrite / rebuild_never_rewrites_manual_to_auto_d2 / full_field_truth_survives_two_auto_rounds，refused 累计 4 次断言在内）→ 复原绿；拒绝发生在写入层而非调用方自觉，契约成立。
- validate 拒绝矩阵逐项断言实存：dangling_edge（含端点定位）、enum_out_of_range（role/age/type/consent/topology/power/evolution 各测）、intensity_out_of_range（±两向）、evidence_out_of_range、missing_id/duplicate_id、self_loop；B1 金丝雀（Finding 只有 code/path/message 无正文）实存。
- import 卫生：src 零上游包导入（grep 证实 ADR-0001）；test_surface.py:43 探针禁 numpy/sklearn/torch/sqlite3/reco_store/reco_io。
- 【基点滞后注记（低危，透明记录）】a9326ea 树内包级分和 core=4/io=37/labels=1 → 该树基点是 8c7587a（W3 后、W4/W6 merge 前），树内全量 113（46 基线+67 净增）；自报「全量 143」对应共享检出当时所处的 feat/06-labels 态（87+56），非本树状态。因 relations 零上游依赖、文件面与 W4/W6 不相交，merge 预计无冲突；merge 后 master 全量预计 143+67=210，全链绿统一核查归队长。
- worktree 已移除，变异零残留（主检出 src 无 MUTATION 标记）。
### 2026-11-21T03:38+0800 W8（工单08 相似度打分）正式验收记录 — PASS（commit 8543547 @ feat/08-scoring）

独立复核证据（test-runner，worktree@8543547 + 包级作用域 + PYTHONPATH 解析修正）：
- 全量核对：270 passed（与自报一致；master 8c01afe 树 + score 模块 44 测试）。
- 提交范围：2 文件 914 行（score.py + test_score.py）全在 reco-core scope。
- RankRow 契约：NamedTuple 五字段（book_id/score/rank/reasons/is_exploration）测试断言钉死；rank 唯一排序入口（_synthesize 共享核，CLI/GUI 无第二路径）；同分 book_id 升序稳定次序（sorted key (-score, book_id)）。
- 逆向重放（位点核验 MUTATION 落盘后执行）：M1 去同分决胜 → test_S1_tied_scores_break_by_book_id_ascending 恰 1 failed（assert:120）→ 复原绿；M2 去权重归一断言 → test_S3a_non_normalized_weights_rejected 恰 1 failed（pytest.raises 落空:220）→ 复原 40 passed（score+vectorize+import_hygiene 三文件）。
- weight_schedule 单调平滑：sigmoid(quality) × clip(ramp/60) 实现，测试逐 5 样本步进断言 w_model 单调不减且相邻差 ≤0.05；CV AUC<0.5 拒绝赋权（ValueError）；n_pos<10 门 w_model=0。
- 探索位：默认 2 条（_DEFAULT_EXPLORATION=2，§17 Q7）；指纹确定性（blake2b(排序后 book_id 序列) 决定旋转起点 + 固定次序行走）；同输入同输出断言（again == rows）；舒适区顶部排除、池≤配额不探索。
- sklearn 延迟导入（F5）：import reco_core.score 不触发 sklearn + train_pairwise 低于阈值返回 None 且 sklearn 未加载——双子进程探针在 PYTHONPATH 修正后双 GREEN。
- §6.5 反例 1-4 测试化：TAGS/RELATIONS/EVENTS 键拒绝、常数打分拒绝、权重变化必变输出。
- 方法论注记（守护层留存）：F5 子进程探针经 venv editable .pth 解析到主检出——worktree 验证时若主检出分支缺该模块，探针测试报 ModuleNotFoundError（本次 2 failed 伪影即此因）；修正=PYTHONPATH 指向 worktree src。共享检出自分支含代码时无此问题。
- worktree 已移除，master 检出 score.py 无 MUTATION 残留。
### 2026-11-21T04:10+0800 W9（工单09 画像与置信度）正式验收记录 — PASS（commit af55dfd @ feat/09-profile）

独立复核证据（test-runner，worktree@af55dfd + 包级作用域 + PYTHONPATH）：
- 包级基相：reco-core 60 passed（score 44 + profile 16）；全量对账 270+16=286 与自报吻合。提交范围 6 文件全在 reco-core + notes scope。
- 逆向 7 项抽 3 重放（位点核验 MUTATION 落盘）：V1 半衰期非法回退 reason 失效 → test_S6 恰 1 failed（回退默认断言在、reason「回退」缺失）→ 复原绿；V2 去单事件 clip 上限 → test_S4 恰 1 failed（virgin 0.55/0.45 顶格断言）→ 复原绿；V3 重放容差 1e-9→1e-3 → test_S9 恰 1 failed（1e-6 篡改被容差捕获）→ 复原 16 passed。容差语义实证：逐位一致 + 1e-9 容差内可过 + 1e-6 偏移必检出。
- C7 语义：test_S10 8 连 dislike 严格递减冻结序列 + all(x>0) 趋零不归零（永不硬排除）；公式层 decay_weight(10_000, 90)>0 根基断言；负样本带权衰减（dislike 折叠进轴负计数）。
- 置信度文案：「还不够准」两处断言（3 篇样本形态 + n_pos=9 cap=0.3 形态）+「暂无正样本，画像仅供参考」空态；C6 硬门 n_pos<10 → cap=0.3（(9,100)→0.237… 冻结）与 cap 释放边界 n_pos=10 全测。
- perf 双轨独立复核：①工单原脚本（worktree 内补拷，未跟踪文件不随提交）replay 4.6ms/单事件 195µs/PERF-SMOKE-OK；②我自制探针（绝对路径 sys.path+模块解析断言防伪影）replay 3.3ms/172µs/decay 0.29µs/call——与自报 3.5ms/177µs 一致，远低于 §11.2 预算（单事件更新秒级承诺余量 250 倍+）。脚本自带断言 <2000ms/<50ms。
- profile_versions 版本化：PROFILE_VERSION=reco-profile/1 进 generated payload（白名单断言）+ store 侧 profile_versions.schema_version/config_fingerprint 往返（W5 test_store_replay 交叉印证在 master 树）+ build→v1/update→+1 单调。
- 附带核验：eval 持久化门 ≥10 双类（不足不写返回 None）、事件缺口 completeness=partial + high_water、B6 payload 白名单断言（_GENERATED_PAYLOAD_KEYS assert in 实现层）、Laplace 折叠手算黄金值（0.8908987181/0.7937005259/0.55 冻结）。
- 方法论补注：未跟踪 notes 脚本不随提交进 worktree——worktree 内跑工单自报脚本前需先拷入；脚本内相对 sys.path 在主检出缺模块时报 ModuleNotFoundError，统一用绝对路径探针防伪影。
- worktree 已移除，主检出 profile.py 无 MUTATION 残留。
### 2026-11-21T04:52+0800 W10（工单10 不合口味反馈闭环）正式验收记录 — PASS（两提交 858e1aa + b5cfa96 @ feat/10-feedback）

独立复核证据（test-runner，worktree@feat/10-feedback + 包级作用域 + PYTHONPATH）：
- 基相对账：core 包级 82 passed（W8 44 + W9 16 + feedback 22）；全量 308 passed 与自报吻合（270+16+22）。提交范围：858e1aa feedback.py+test_feedback.py、b5cfa96 score.py 接缝扩展，全在 reco-core scope。
- ①探索位不捞回（核心断言）：b5cfa96 给 rank/_pick_exploration 增 exploration_exclude=frozenset() 缺省参数（向后兼容零行为变化，W8 44 测试仍绿）；feedback 层传 frozenset(muted_ids)；test_S1_muted_never_returned 用冻结指纹转向夹具断言排除态 explorers==[C2,E7] 且 BM 恒不进探索位。逆向 MV-A（去掉 exclude 传递）→ 该测试恰 1 failed → 复原绿。
- ②95/5 数值+过期解除：FEEDBACK_MUTE_RATIO=0.05 钉死；尾档 TAIL=cos10°×0.05=0.0492403876506104 精确断言；静音窗分档 30/90d 过期时刻字面冻结（2025-12-21 / 2026-02-19）；test_S2_expired_mute_lifts_penalty：91 天后 B1 回到第一且分数恢复 COS10（解除零残留）。逆向 MV-B（去惩罚信号）→ test_S1_dislike_mutes_candidate 恰 1 failed（沉底+TAIL 断言）→ 复原 22 绿。
- ③负样本仍在候选池/特征库：非硬排除断言（B1 仍在 ids、S7 全饱和 3 候选全输出且 min score>0）；候选向量原样透传（身份诚实）；静音走行为信号通道不改向量；S5 去重键身份规则（同 dedup_key=X1/X2 同静音）。
- ④回流入口：like → axis +0.05 上调文案 + B2 仍在排序首位；add_to_pool → pool_added 事件不进口味事件流（rehydrate 空 v1 证明画像折叠忽略，§7 隔离）。
- ⑤画像秒级更新复用 W9：handle_feedback 直调 update_profile（version 1→2、clip -0.05 冻结 0.5→0.45），零重实现；S6 源码级红线测试：禁第二条排序路径（def _rank/sorted(rows)）+ 零跨包引用 + 禁 sklearn。
- 附带核验：append-only（FakeAppendOnlyStore 拒追溯插入 InvalidFeedback）+ 重放复现（rehydrate v1 基线恒定 + verify_replay ok/diffs==() + 增量链与同流重建内容逐位一致）；多静音 ≥2 退路文案「口味可能已变」；重复反馈=替换窗口（remaining 90.0 不累积）。
- 透明注记（极低危）：b5cfa96 提交信息「33 个 W8 测试」为措辞计数（实际 test_score.py 44 测试，全绿无碍）；不影响验收。
- worktree 已移除，主检出 feedback.py 无 MUTATION 残留。
### 2026-11-21T05:20+0800 W11（工单11 CLI 入口 + 强制接缝①②）正式验收记录 — PASS（两提交 8c01a7b + 120574b @ feat/11-cli；验证期间已合入 master 3320259）

独立复核证据（test-runner，worktree@120574b + 多包 PYTHONPATH + 新口径 --ignore=packages/reco-ui）：
- 基相对账：store 包级 28 passed（含 pool_mute_projection 6）；cli 包级 18 passed；全量 --ignore=reco-ui 330 passed（与队长 W11 报告一致）。初跑 cli 8 failed 为跨包解析伪影（PYTHONPATH 只设 cli/src，reco_store 经 editable .pth 解析到 merge 前的 master），四包路径修正后全绿——伪影非缺陷，透明记录。
- ①pool_added 投影器扩展（真库先红后绿证明）：MV-A 把 store.py checkout 回接缝前父提交 0bd43e3 → test_pool_mute_projection 恰 6 failed（S1 真库投影「未知事件类型」+白名单收编+S2 重启往返/替换释放/重放逐位）→ 恢复 120574b → 6 passed。「记账收编」实现核验：_apply_event 尾部 pass-through 列表 (scan_started, scan_completed, rec_feedback, perf_sample, pool_added)（store.py:1001-1002），verify_replay 重放零漂移断言在真库成立。
- ②mutes 表持久化+双事件投影+COMPARE_TABLES 收编：mutes 表 schema 实存（book_id PK FK books/dedup_key/axis/kind/expired_at/remaining_days/schema_version/config_fingerprint，store.py:642-652）；mute_upserted（ON CONFLICT 替换窗口）/mute_released（DELETE 零残留）双投影；COMPARE_TABLES 已收编 mutes（store.py:665）；重启（close→重新 open_store）后静音窗/到期时刻逐字段恢复；CLI 胶水 mute_map_from_store 过期行过滤+dedup_key 优先（形态转换不做判定，判定留 feedback 层）。
- ③UTF-8 cp1252 管道回归真实性：修复=__init__.py main() 入口 win32 且 stdout 非 utf-8 时 reconfigure(encoding=utf-8, errors=replace, line_buffering=True)——原地改编码不替换 wrapper（UTF-8 教训落地，注释明示 GC 关底层缓冲风险）；测试以 PYTHONIOENCODING=cp1252 子进程真实模拟西文管道，解码断言无 \ufffd。MV-C（实现行禁用）→ test_C2 恰 1 failed → 复原 18 passed。首轮 MV-C 误锚注释行变异未生效（1 passed 假红）——标记核验流程补上精确锚点后重做，方法论注记：变异后必须核实现行为位而非注释位。
- ④A6 rank 唯一入口消费+只读入口不写库：recommend→feedback.rank_only、feedback→persist_feedback/feedback_effects（W10 层复用，零重实现）；C6 源码级断言（CLI 无 sorted(/无 reco_ui）；C7 真库事件计数前后不变（report/recommend/explain/replay/eval 五入口）；MV-B（report 注入 append_event）→ test_C7 恰 1 failed → 复原；C9 replay 漂移检出（直改投影→exit 非 0）。
- ⑤changelog 首行：docs/algorithm-changelog.md「2026-11-21 ｜ 工单 11 ｜ 首次算法上线…reco-cli 0.2.0」实存。
- Qt 崩溃面口径确认：全量 --ignore=packages/reco-ui 330 passed exit 0；reco-ui 3 失败归属 W12 在途红（implementer-6），不混入 W11；W12 merge 前 Qt 崩溃面核查（QT_QPA_PLATFORM=offscreen/窗口实例化跳过）列入我的守护清单。
- 时序透明注记：验证过程中 feat/11-cli 已被合入 master（3320259 merge）且分支删除——前四项证据在 merge 前 120574b 树实测，MV-C 用提交哈希 detached worktree 补做；主检出现处 feat/12-gui worktree（.wt/12-gui，f2dfaea 切片1）与 reco-ui/__init__.py 未提交改动，属 W12 在途，未受我扰动（全部验证在独立 worktree）。
### 2026-11-21T06:00+0800 W12（工单12 GUI 三板块，7 commits @ feat/12-gui）正式验收记录 — PASS（附 1 条低危 finding）

独立复核证据（test-runner，detached worktree@feat/12-gui b3603bf + offscreen + 多包 PYTHONPATH）：
- ①Qt 崩溃面核查（守护清单首要项）：全量【含 UI、无 --ignore】QT_QPA_PLATFORM=offscreen → 354 passed / EXIT=0，连续复跑无 0xC0000409；UI 包单独 24 passed ×2 轮。三重防线源码实锤：conftest 铁律（session 持 QApplication 引用防 GC + 每测试 waitForDone 泄洪泵事件）+ loader 模块级注册表持活（17eceaf）+ 每测试泄洪扩展（d4025b3）。→ **--ignore=packages/reco-ui 守护口径自 W12 起可解除**（保留 offscreen 条件）。354 = master 330 + UI 24 对账吻合（自报连续 3 轮 354 与本轮一致）。
- ②性能门槛复测：150 行批更新 <50ms PASSED、首帧骨架 <500ms PASSED（MainWindow 构造实测）、RECO_PERF_TABS=1 遍历四页（phase=ui.tab.liked/candidate/recommend/profile 打点 + 快照 JSON 落盘）PASSED。
- ③逆向重放（自设计 2 变异全红闭环）：Y 生成篇数默认 2→3 → test_generate_spin_default_two 恰 1 failed → 复原 24 绿；X2 set_rows 去 begin/endResetModel 重置路径 → test_rank_table_model_reset_is_batched 恰 1 failed（assert:158 _reset_calls）→ 复原 24 绿。方法论注记：X 首两轮假红（保留 _reset_calls 递增绕过断言面）——读测试实体后定向变异才闭环，与 W11 MV-C 同款教训（先读断言面再设计变异）。
- ④生成篇数语义：app.py:242 generate_spin.setValue(2)（§17 Q7）+ QSpinBox 用户增量语义；变异 Y 实证测试钉死默认值。
- ⑤QSS 截图美观验收：4 张（recommend/liked/candidate/profile-dark.png，1080×720，be9bcf3 提交）已逐张查验——【finding F-12a，低危】offscreen 平台无 CJK 字体（Qt 警告 not shipping fonts），全部文字渲染为 tofu（□），排版/字面维度不可验；布局骨架维度合格（深色底/左导航四页/选中高亮/面板层次/表格 5 列结构/状态栏）。建议：桌面有字体环境重截真实字形截图，或 Qt 平台装字体重截——留队长裁定是否要求补。
- ⑥降级退路：test_launch_reports_cli_fallback_when_pyqt6_missing（F4/R14）实存；qt.py 全仓唯一 PyQt6 import 点（§11.5 许可隔离）；DEPENDENCIES.md 记 PyQt6 6.12.1 三件套 + 离线重建（zipfile 解压绕杀软 DLL 锁，2656 文件）。
- 透明注记：b3603bf 提交信息诚实记录了其逆向脚本在未提交树上误吞 app.py 改动的流程事故（教训：RV 必须在干净树上跑）——与本测试员 W11 MV-C 注释行假红同族，方法论已入基线。W12 未 merge（master 3320259）；主检出与 .wt/12-gui worktree 未受我扰动（验证全在 detached worktree，变异零残留）。
### 2026-11-21T06:20+0800 W12b（工单12b 自适应热加载）正式验收记录 — PASS（commit de017e4 @ feat/12b-preload）

独立复核证据（test-runner，detached worktree@de017e4 + offscreen + 多包 PYTHONPATH）：
- 对账：全量含 UI offscreen 377 passed / EXIT=0（354+23 吻合：policy 8 + preload_ui 9 + ui_usage store 侧 6）；store 包级 34 passed。6 文件 674 行全在声明写域（store.py/ui_usage 测试/preload.py/app.py/两测试）。
- ①ui_usage 计数独立表：bump/get 走单写者队列与 read_conn；test_events_table_never_contains_ui_usage_rows 硬边界（events 表零 ui_usage 行）；COMPARE_TABLES 排除断言；ui_usage 有数据 vs 空两库 verify_replay 逐位不变（compare_rows 全等）+ 50 连 bump 口味侧零变化——C12 隔离三层实证。
- ②热集判定（纯函数确定性）：threshold=3 可配、Top-K=2 补位（uses 降序、并列 page_id 升序）、恒热 frozenset({recommend}) 不占 K 名额不可关闭；8 测试含确定性复调用断言。
- ③首帧后空闲档预热可打断：QTimer singleShot 1500ms 声明式持有（防 GC 悬垂延续 W12 防线）；user_interrupt 可变槽 + 调度层早退 + worker 运行前二道检查；nav 点击 bump 后立即 interrupt（app.py:224-226 接线）。
- ④「已加载」成功才置位（坑 3）：失败页按需路径照常且可重试；已加载页零重复预热。
- ⑤RECO_PERF：phase=ui.preload.<page_id> ms=N.NNN span 实测断言；首屏 <500ms 双口径（perf span + wall）不回归复测通过。
- 逆向重放（自设计 3 变异）：A' 调度层打断失效 → test_user_interrupt_skips_queued_jobs 恰 1 failed → 复原 41 绿；B 失败也置位 → test_preloader_success_marks_loaded_failure_does_not 恰 1 failed（assert:93）→ 复原绿；A 首轮变异 worker 层检查假红——打断语义被测在调度层，worker 层检查属未测防御纵深（观察项，非缺陷）。
- 方法论注记：假红根因与 W11 MV-C/W12 X 同族（变异前先读断言面与语义层级）——已三度验证该纪律，入基线供 W13/W14 参照。
- worktree 已移除，主检出与 .wt/* worktree 零扰动。
### 2026-11-21T06:55+0800 W13（工单13 CLI/GUI 排序一致性）正式验收记录 — PASS（三提交 859ae38+84331f5+8662e73 @ feat/13-consistency，附键名协调项）

独立复核证据（test-runner，detached worktree@8662e73 + offscreen + 多包 PYTHONPATH）：
- 对账：全量 377 passed / EXIT=0（与自报一致；一致性 5 测试计入、377 总数与 W12b 持平=新测试替换了 profile 占位测试）。
- 一致性测试真实性：30 书合成池（liked 12/candidate 18）+ 真实 vector_computed 事件（struct 大端 pack 逐书异质权重）+ like 12/dislike 11 走 glue.record_feedback 真实静音语义；CLI 子进程探针（cmd_recommend JSON）vs GUI 子进程探针（MainWindow→page.load_now 生产装配路径 _model._rows）——book_id 顺序/score/rank/is_exploration/reasons 全字段逐位相等断言 + 探索位两入口一致出现 + 静音 source=muted 透传断言。逐位不是宽松形态比对。
- GUI 第二排序路径变异重放（亲手重放，与 RV 脚本同锚点不同实现方式）：load_now 内反转行序 → test_cli_gui_bitwise_consistency 恰 1 failed（assert:157）→ git 复原 → consistency 5 测试全绿。RV 脚本质量核验：bytes 级恢复（write_bytes 零 newline 翻译——行尾翻转教训已吸收）+ 干净树前置断言（W12 教训吸收）。
- 共享装配层（reco_store/recommend.py 159 行）：零排序（纯装配）+ A6 遵守（rank 调用在入口层）+ W11 遗留向量真实接线闭合（decode_vector_blob 大端契约、损坏行跳过不静默 0 分、n_skipped 如实计数）+ 只读零写库 + 依赖方向 reco-store→reco-core 合规。承接②profile_rows 真实画像（n_pos=12/n_neg=11 逐字段断言）、承接④eval 真实样本（12/18 口径+evaluation_runs 恰 1 行/不足零写入）——两承接接线全部实证。
- 性能回归：150 行<50ms、首帧<500ms（含 Preloader 接线态）、RECO_PERF_TABS 快照两跑逐位一致+recommend rows=30 真实渲染、8 测试 targeted 复测全 PASSED（RECO_PERF=1 可见 span）。CI 脚本三段式（快照生成/基线逐位比对/一致性 pytest）齐备。
- 【键名协调项（透明注记，非缺陷）】feat/13 store.py 仍为 vector_blob_b64 键（store.py:114,493），test_consistency fixture 注释「临时对齐 W5-N 裁定统一 hex——merge 后切 vector_blob_hex 并复跑全量（captain 协调项）」。当前树内 B→B 自洽（b64 进投影、b64 出装配）成立；但 W5-N（t19）merge 后必须：fixture 键名切 hex + 全量复跑——已列为我 W14 前置守备点，等队长协调指令。
- 提交范围注记：tests/test_consistency.py+golden 快照+make_tabs_snapshot.py+ci-consistency.sh 属验收产物面（工单验收 3 明示 CI 快照），与实现文件分离清晰；.scratch RV 脚本证据面。
- worktree 已移除，主检出（master 3320259）与各 worktree 零扰动。
### 2026-11-21T07:50+0800 W14（工单14 换核演练+全验收清单）终验记录 — PASS（8 提交 @ feat/14-swap-drill；交付前最后验收获准点）

六项重点独立复核（test-runner，detached worktree@6652129 + offscreen + 多包 PYTHONPATH + 隔离 venv）：
- ①换核态证据：docs/swap-drill-evidence.md 完整（余弦→欧氏核 1/(1+d²)、金标 0.9494…→0.8333… 变体重冻结、零向量诚实 0.3787…、保序性证明）；本测试员亲手核实 git：1f463a8 变体提交 cli/ui 文件数=0（仅 reco-core）；revert f36a820 复原。红转绿明细（score 5 failed→重冻结 33 passed；feedback 4 failed→22 passed；**结构契约零失败**）=数值契约/结构契约解耦的 E2 级证明。379=384-5（tests/ 集成测试系 121e6b6 修正 testpaths 后入收集，口径差透明）。
- ②384 基线独立复跑：384 passed EXIT=0（含 UI、offscreen、多包 PYTHONPATH）——与自报一致。121e6b6 两处收集盲点修正（根 testpaths 增 tests/ + consistency fixture 切回 vector_blob_hex 正式键名）=我 W13 键名协调项正式收口（store.py:488 hex 契约+fixture:124 hex 实核）。
- ③pre-commit-guard.sh 实跑：ALL GUARDS PASSED / exit 0（含 RV 脚本 RESTORED+BYTE-IDENTICAL 提醒检查）。
- ④7 包独立安装抽验（隔离 venv+pip install -e+包级 pytest，本测试员亲手三包）：core 81 passed+1 skipped（CRT skip）、store 36 passed、labels 62 passed——与声明计数 81/62/36 逐一吻合；其余四包（relations 68/io 71/cli 18/ui 41）采信 ACCEPTANCE.md v7 演练记录（披露五项契约修正 998ebcb：cli/ui pyproject 补 reco-store 依赖声明等）。7 包合计 384 与全量对账吻合（含跨包集成 3 测试入收集）。
- ⑤性能门槛终测：150 行<50ms / 首帧<500ms（Preloader 接线态）/ RECO_PERF_TABS——4 测试 targeted 全 PASSED；TABS 快照逐位稳定性由 384 全量内 test_perf_tabs_snapshot_stable 实证（W13 已亲验基线比对）。
- ⑥RV 总扫：ACCEPTANCE.md #23 逆向记录全列（W4×6/W5×2/W6×2/W7×3/W8×4/W9×7/W10×5/W12b×打断重试/W13×1+快照/W14 换核红转绿明细）；13-reverse-verify.py 在位（bytes 级恢复+干净树前置）。本测试员十二票验收累计亲手重放变异 20+ 全部红→绿闭环。
- 全验收清单 24 项逐条打勾（docs/ACCEPTANCE.md）+ CODING-STANDARDS 首版 + RETRO 收官 + pre-commit-guard 入库。
- 终验结论：六项全过，W14 放行——reco 项目 15 张实现票（00-14+12b+12）全部验收 PASS，交付前获准点成立。
