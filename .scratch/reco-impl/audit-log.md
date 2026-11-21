
## 2026-11-21T01:29+0800 W2（工单02 目录扫描）审计 — PASS（建议可 merge）

- 对象：5711f25 @ feat/02-scanner（6 文件 +650/-2；auditor 独立审计，非转述）。
- 方法：共享 worktree 存在 W3 在途文件，审计在 git archive 提取的隔离副本（reco/.audit-w2-tmp，仓库外）上执行，零工作树污染。
- A 类：scanner.py:123 身份唯一来源 os.stat(path, follow_symlinks=False)（recording_provider 测试断言全部调用 follow=False）；DirEntry.stat 零字段回归在位（test_scan_zones.py:142-157，非 Windows 环境显式 skip，Windows 断言 (0,0,0) 与 scan 结果非零对向夹击）。
- B 类：静态 grep open(/read_text/read_bytes/readline/decode 零命中；monkeypatch builtins.open 抛 AssertionError 的测试真证明「未打开任何文件」（损坏/同名不同内容/53MB 假超大均仅 stat）；ignored 上报只到文件名（路径分隔符断言）；import 卫生探针：import reco_io 后 sys.modules 无 numpy/scipy/sklearn/torch。
- C 类：同卷身份配对移动继承 book_id、跨卷同 ino 判删+增（合成 provider 卷号更低锚定防真实 stat 泄漏）；同名不同内容绝不互相配对；缺失专区只上报不创建；千文件 25 目录×40 = 1000 断言 <1s（实测门禁内）。
- E 类逆向验证（auditor 复放）：隔离副本变异 scanner.py 去掉身份键中的 st_dev → test_scan_delta 恰 1 failed（跨卷同 ino 误配为 Moved，test_scan_delta.py:98），恢复提交态 → 全量 32 passed（含 W1 三票测试）。测试对身份语义敏感，非假绿。
- 隔离全量：32 passed in 3.66s（提交态 5711f25 独立复现，与 implementer-2 报告 22 passed 口径一致——其余为基线占位与 W1 依赖票）。
- 遗留观察（不阻断）：FileFingerprint 排序键含 path 使同身份多路径在归并前排序确定（硬链接先到先配，测试覆盖）；RECO_PERF 由 import 时环境变量求值（perf.py:13），属工单 11/12 接缝裁定范畴，工单 02 内无影响。


## 2026-11-21T01:35+0800 W3（工单03 硬链接/复制落位）审计 — PASS（建议可 merge）

- 对象：856155c @ feat/03-placement（3 文件 +483：placement.py 184 / test_placement.py 281 / __init__.py 导出面；auditor 独立审计，非转述）。
- 方法：共享 worktree 已切至 feat/04-features（W4 在途），审计在 git archive 提取的隔离副本上执行；快照确认仅含四个已提交源文件，零工作树污染。
- C10 契约：同卷 os.link（st_ino 相等 + st_nlink>=2 + 删源可读三重断言，test_placement.py:103-119）；跨卷 st_dev 预判直接复制且不制造失败 link 尝试（link.calls==[] 断言，:184）；EXDEV/WinError1(非NTFS)/ENOSYS 退回复制记 COPY（:188-214）；EPERM/EACCES 上抛 PlacementPermissionError 且不产生半成品（:222-230）；源文件永不移动；同卷+EXDEV 兜底分类（errno 先判、winerror 补充，permission errno 显式排除误判）。
- 冲突语义：指纹 (size, mtime_ns) 判等 SKIPPED 且不动既有目标（:133-144）；不等则确定性改名 book(N).txt（:161-169）；复制后双判据校验失败清理半成品（size 与 mtime_ns 两条变异路径均覆盖，:233-269）。
- B7 白名单：PlacementResult 恰六字段 mode/src/dst/st_ino/st_nlink/size_bytes；专测 test_result_fields_carry_no_content 断言 set(payload) 恰等六键、无 bytes 值、正文不在 str(payload)（:272-281）；异常消息仅含文件名与 errno/winerror；模块 grep 无 print/logging。
- 推荐专区边界：模块 docstring 明示只服务 liked/candidate 实体落位、推荐专区只存索引与分数（归票 12）；本票未提供面向推荐专区的实体复制 API——无越界。
- C14 逆向重放（auditor 复放）：隔离副本把 _default_link 变异为 shutil.copy2（复制冒充硬链接）→ test_placement 恰 1 failed（st_ino 相等断言，test_placement.py:113）——自报「假硬链接转红」检测点真实有效；恢复提交态 → 全量 46 passed。
- 隔离全量：46 passed in 3.63s（提交态 856155c，含 W1+W2 全部测试）。
- 遗留观察（不阻断）：_default_stat 用 os.stat 默认 follow_symlinks=True——落位管道上游 scanner 已过滤符号链接/reparse，票 03 原语范围内无影响；若票 12 GUI 允许用户直接拖路径进落位 API，需补 follow_symlinks=False 或入口校验（已记入票 12 审计预案）。


## 2026-11-21T02:28+0800 W4（工单04 特征提取）审计 — PASS（建议可 merge）

- 对象：1dbc02d @ feat/04-features（9 文件 +1325：features.py 534 / vectorize.py 113 / 5 测试 / 2 手算注记；auditor 独立审计，非转述）。方法同前：git archive 隔离副本审计提交态。
- ① B 类等效性裁决（schema 断言移交 05）：**成立**。库在 05 才建库，schema 断言在 04 结构上不可能存在；04 侧等效守护完备——金丝雀正文不出 json.dumps 结果（test_features_file.py:128-146）、桶输出纯 (int,int) 且无 n-gram 字符串（test_features_buckets.py:62 + test_vectorize.py:78-81）、monkeypatch 禁 tempfile + 目录快照前后一致（:165-174）、损坏/超大/超时降级矩阵全走 flag 不带内容。**同时立交接条款：05 建库时 schema 断言（全部表无正文列 + payload 白名单 + 事件表 INSERT-only）为 05 审计强制必收项，缺失即 05 不出 PASS**（audit-checklist 05 行 B2 已含，此为显式化）。
- ② E 类期望值独立性：**成立**。04-handcomputed-samples.md 为真实手算 oracle（S1-S14 每统计量一条推导，文本全合成）；冻结脚本 04-freeze-literals.py 仅 stdlib。auditor 独立复算（自写内联代码从公式重推，未用其脚本）：slot(学习)=45598、slot(好好学)=115630、S9 w_ab=0.6046521283053111——与冻结字面值十六位逐位吻合。测试未 import 实现复算期望；test_streamed_equals_whole_read 用 extract_statistics 对照属「流式=整读」自洽不变量（合法用途，非金标来源）。E4 通过。
- ③ import 卫生：**达标**。test_import_hygiene 子进程探针覆盖 import 时五个公开包（timeout=120 且等待返回值，E5）；auditor 补调用期探针：真实调用 extract_statistics+char_ngram_bucket_tfidf 后 sys.modules 仍无 sklearn/torch/numpy；源码 grep 零重依赖 import（仅 docstring 提及「不 import」）。延迟导入权限仅归 08（train_pairwise/similarity），W4 代码路径不存在延迟加载点。
- ④ 共享 worktree 事故复盘：**已闭环，提交态纯净**。reflog 轨迹完整还原：W3 曾误落 feat/04-features → reset 回 5711f25 → W4 首提 73d3503 含队友 06-labels-preread.md → reset HEAD~1 → 干净重提 1dbc02d。git ls-tree -r 1dbc02d 确认不含任何队友文件（9 文件全为工单 04 范围）；06-labels-preread.md 现仅存于工作树未跟踪区（属 W6 预读物，归 implementer-2）。
- E2 变异复红（auditor 独立变异，非采信自报）：破坏 vectorize sublinear tf 公式（1+ln(c)→ln(c)）→ 恰 2 failed（test_S9_golden_values_real_hash + test_S10_max_features_truncation_stub_hash，金标字面值敏感）→ 恢复提交态 73 passed。与 test-runner 三变异复红口径互补，检测点真实。
- 隔离全量：73 passed in 3.53s（提交态 1dbc02d，含 W1/W2 全量 + W4 新增 30 测试）。
- 其它核对：C11 通过（features/vectorize 零 sqlite import，提取天然在事务外）；B5 全合成夹具；E8 注入时钟（timeout 用 time_provider 注入非 sleep）；分支基点为 5711f25（不含 W3 提交，merge 时由 git 合流，文件不相交无冲突）。
- 遗留观察（不阻断）：S9 断言容差实际 1e-9，严于 notes 声明的 1e-6（更严无碍）；alias_counts 接缝（W6/t8）已按 §5 备案实现且测试覆盖（真零记 0、casefold 非重叠、跨块边界安全）。


## 2026-11-21T03:00+0800 W6（工单06 标签体系）审计 — PASS（建议可 merge）

- 对象：e9a23bc @ feat/06-labels（15 文件 +1717：9 模块 + 6 测试；基点 38b0856 含 W4 merge；auditor 独立审计，非转述）。方法：git archive 隔离副本审计提交态。
- ① 冲突裁定确定性与可回放：**成立**。裁定为纯函数 min(cands, key=(-alias_len, tag_id))（taxonomy.py:78），与迭代顺序无关（候选集排序后 min）；_alias_index 构建时一次算好，match 零重建；「双性」跨轴场景（role.双性 vs bpart.双性）测试断言胜者 bpart.双性 + tag_conflict 事件落库（payload 只含 alias/candidates/winner/book_id）+ explain_tag_hit 披露 alias_conflict_resolved 证据。可回放：事件与 basis 均为元数据，重放可复现裁定。
- ② 种子逐字性：**超越抽 3 值，25 轴全量对照**。auditor 直接对读规格包 F:/date/xiaos/docs/proposals/adult-reco/tags.md 与 seed.py：133 值逐轴逐值一致（总数 22+31+33+21+16+10=133 独立复核）；抽核 archetype 11 值/mechanism 10 值/tone 7 值逐字；mech.hypnosis 与规格 JSON 样例六字段逐字（id/label/aliases/parent/status/created_by/created_at）；age「跨代（父子、师生、叔侄）」与 length「（<3千）」等括号注释正确按注释丢弃不做值；唯一 alias 只配 hypnosis（其余 132 值暂不识别=规格「新增」行）。created_at 统一 2026-11-20 确定性、幂等断言在位。
- ③ user 暂不识别专项：**在位**。test_normalize_user_tag_needs_alias_upgrade（add_tag→label 不识别→set_aliases→升级识别）+ test_seed_labels_not_identifiers（label 不入别名）+ test_seed_only_hypnosis_is_matchable。
- D 类：**通过+重放**。ManualTagProtected 在 store.record_book_tag 写入层结构化拒绝（非调用方自觉）；redirect_book_tag_rows 逐行保留 source/confidence/basis（manual 行 basis_json 断言逐字）；merged 别名原地保留经链解析（含 a→b→c 传递链测试）；archived 数据完整不参与识别；逆向重放：把 redirect 变异为删除历史行 → 恰 1 failed（test_merge_archive.py:51 IndexError，历史不丢断言转红）→ 恢复 57 passed。
- C 类 payload：tag_conflict/tag_merged/tag_proposed 事件 payload 全元数据（alias/tag_id/计数/行数），basis_json 只含 axis/matches(alias,count)/total/evidence；record._plain 退化策略不展开正文类对象；test_record_writes_conflict_event_payload_metadata_only 显式断言。
- A 类四模型隔离：reco-labels 不读事件流语义、不做打分；axis_vector 权重由调用方注入，未知轴绝不臆造；未覆盖轴缺席（非 0.0 占位）符合规格「稀疏数据」节。
- import 边界：源码 grep 零 reco_store/reco_io/sklearn/torch/numpy/print/logging；调用期探针（真实走 seed→taxonomy→match→record）sys.modules 无非法模块；LabelsStore 窄 Protocol（10 方法）与 W5 对接面清晰。
- 隔离全量：reco-labels 57 passed in 0.22s（提交态 e9a23bc 独立复现，与 test-runner 口径一致）。
- 遗留观察（不阻断，2 条 low）：① match.py:92 与 test_merge_archive.py:215 残留 if False else 死分支（行为恒走 else=正确实现，纯代码异味）——建议票 08 消费 TagHit.basis 触碰该文件时顺手 PR 清理；② merge_tags 的 occurred_at 硬编码 2026-11-21（archive/add 有 now 参数而 merge 没有）——建议 W5 store 对接或 08/09 消费事件前补 now 参数（C3 业务时间回放语义）。
- 事故观察（按队长指示不计 findings）：reflog 证实 ff 事故轨迹（feat/05-store 曾 8c7587a→38b0856 fast-forward 落错分支，当时零独有提交零损失，随后 W5 真提交 0fb5811 正常落位）；「分支写操作断言」规则已全局化，本审计员后续 git 写操作同样执行目标分支断言。


## 2026-11-21T03:05+0800 W5（工单05 SQLite 特征库+事件流）审计 — NEEDS_REVISION（1 blocker 级接条款缺口 + 1 medium 契约分歧；底层事实无正文泄漏）

- 对象：0fb5811 @ feat/05-store（6 文件 +1068：store.py 661 / 3 测试 / conftest / init；基点 38b0856；auditor 独立审计，非转述）。方法：git archive 隔离副本。
- 强制必收项核查（audit-log 第 3 条 W4 接条款，三要件）：
  ① 全部表无正文列 schema 断言：**缺失 → 不出 PASS**。现有 test_effective_weight_not_a_column 只覆盖 events 单列；无遍历全部表 PRAGMA table_info 的断言测试。auditor 临时探针证实底层事实目前干净（19 表 0 个正文形列），但这不是已提交的守护——按接条款原文「缺失即 05 不出 PASS」。
  ② payload 白名单：**达成**。_ALLOWED_KEYS 50+ 键白名单 + 非标量拒绝 + effective_weight 显式禁止（store.py:94-124），API 面导出 PayloadViolationError；测试覆盖未知键/嵌套 dict/effective_weight 三拒绝路径。
  ③ 事件表 INSERT-only：**达成**。trg_events_no_update/no_delete 实存 schema（store.py:271-279）；auditor 实弹探针：直插真实行后 UPDATE/DELETE 均被 IntegrityError 拒绝（'events is append-only'）。
- ② dedup_key 契约（F3）：**一致**。sha256(size+\x1f+规范化名) UNIQUE；五条规范化规则 docstring 与实现一致（NFKC→casefold→空白折叠去首尾→全角半角由 NFKC 覆盖→括号扩展名保留）；E4 期望值独立 hashlib 计算；同名不同大小另立一本 + 绕过投影直插同指纹书被 UNIQUE 拒绝（schema 硬保险测试）。
- ③ 单写者队列：**真实**。max_workers=1 executor 串行化；4 线程 20 写 event_id 恰 1..20 连续递增；WAL 双连接快照读测试（写事务开启时读到 pre-commit 快照）非 mock。
- C2/C3 回放：rebuild_projections 逐位复现（15 表逐表比对）、verify_replay 双向漂移检测（伪造事件+直改投影两篡改路径测试）、events/profile_versions 均带 schema_version+config_fingerprint。
- finding-1（blocker 级·接条款）：缺「全部表无正文列」schema 断言测试。requiredFix：PR-first 分支 fix/05-schema-assertion——先写红测试（遍历 sqlite_master 全部表 × PRAGMA table_info，断言无 content/body/text/raw_text 类列名；先证红：向临时 schema 注入一个 body 列使其红）→ 提交测试+确认绿 → 逆向验证（revert 必须可转红）→ 他人评审 → merge --no-ff。修复前不派 W8（08 blocked by 05）。
- finding-2（medium·跨票契约分歧）：book_tags user 行保护语义不一致——W5 store.py _apply_label_set 只保护 manual（:392），label_cleared 删除 source != 'manual' 行会连带删 user 行（:438）；而 W6 LabelsStore 协议（e9a23bc，已 PASS）保护 manual+user 双源（store.py:152）。两实现将对「用户显式动作」的保护语义劈叉。requiredFix：随 finding-1 同分支裁定统一语义（建议按 W6 收敛为 manual+user 双保护，符合 CONTEXT.md 正样本/人工优先），补对齐测试；若裁定 user 不保护须回写 W6 契约文档并报备。
- 隔离全量：reco-store 16 passed in 1.03s（提交态 0fb5811 独立复现）。
- 其它核对：C11 库设置（WAL/NORMAL/5000/FK，读写连接都断言）；C13 schema_meta 三键；B6 达成；D1 manual 拒绝在写入层（_apply_label_set DO NOTHING/跳过）；无正文类事实泄漏。


## 2026-11-21T03:05+0800 W7（工单07 人物关系模型）审计 — PASS（建议可 merge）

- 对象：a9326ea @ feat/07-relations（14 文件 +1399：7 模块 + 7 测试；基点 8c7587a（不含 W5/W6，merge 时合流）；auditor 独立审计，非转述）。方法：git archive 隔离副本。
- 票 07 必查四项：
  - D2：**通过+重放**。拒绝在写入层非事后过滤：_apply_updates force=False 时拒写 manual 字段并累计 refused（fields.py:115-117），apply_auto_extraction 强制 force=False（:160）；storage 层 save_relation_fields 默认 auto、manual 必须 edit=True（storage.py:28-29）；rebuild_graph_from_store EdgeKey 迁移时 manual 原样保留 source 永不变 auto；refused 留痕完整（端到端测试断言两轮×两路径=4 次拒写、路径集合恰为 manual 字段）。E2 重放（auditor 变异）：拆除拒写分支（if False and ...）→ 恰 3 failed（test_write_layer_refuses_to_overwrite_manual_field + test_rebuild_never_rewrites_manual_to_auto_d2 + test_full_field_truth_survives_two_auto_rounds）→ 恢复 68 passed。与其自报闭环一致。
  - D4 低置信留空：**通过**。evidence_floor=0.35（可注入 config）；_best 过阈值才落值，未过阈值留 unknown/None/intensity=None（维度测试逐项断言）；依据仍记 basis（alias:<id>/stat:<name> 排序）；confidence=已消费信号最大值如实低报；「仅强度不足以捏造类型」专项断言。留空是 unknown/None 语义值而非 null 冒充 0 值（similarity 中 unknown 恒 0 参与但不计，axes 摊平如实呈现 unknown）。
  - B1 basis 无原文：**通过**。basis 只含 alias:<tag_id>/stat:<name>；validate Finding message 只含字段路径+取值 repr（数值/枚举/标签 id）；explain_relation 产出 {kind, ref} 不虚构权重；独立 grep + test_no_forbidden_imports_in_source 双重边界确认（零 reco_store/reco_io/numpy/sklearn/torch/sqlite3）。
  - A8 独立接缝：**通过**。duck-typed 纯 dict 输入（stats/alias_weights），不 import 任何上游包；test_surface 断言 pyproject 只依赖 reco-core；§6.1 全部 12 个公开 API 面完整。
- 四验收核对：manual 写入层拒绝+refused 留痕 ✓；低置信留空 floor=0.35 ✓；merge 只合用户改过字段 changed_by 留痕（含 graph_stamp 来源快照标识）✓；validate 拒悬空边/枚举越界/自环/重复 id/缺 id/强度越界/置信越界（Finding code/path 定位）✓。
- 八维度独立测试 ✓（role/traits/age/type/mechanism/intensity/consent/topology+power/evolution 每维度一测）；相似度手算黄金值（0.2/0.1/0.6/对称/置换不变/unknown 不参与）✓。
- 隔离全量：68 passed in 0.14s（提交态 a9326ea 独立复现）。
- 遗留观察（不阻断）：相似度 round(total, 12) 为确定性输出无碍；mechanism/intensity 轴在 relation_axes 按需扩展（_EDGE_AXES 注释明示，留 W8 接缝）；「分支写操作断言两度拦漂移」见 reflog 与公告，规则生效中，本审计员同步执行。


## 2026-11-21T03:15+0800 W5-F（fix/05-schema-assertion，1e5caa4）复核 — PASS，W5 双 finding 关闭，W5 整票转 PASS

- 对象：1e5caa4（2 文件 +160/-12：store.py 22 行 + test_store_findings_fix.py 6 测试；分支正确从 feat/05-store 切出，reflog 干净，HEAD 已还原 feat/08-scoring——分支纪律 ✓）。方法：git archive 隔离副本。
- F1 复核：test_no_content_column_in_any_table 遍历 sqlite_master 全部表×PRAGMA table_info，黑名单 6 模式（content/body/text_body/raw_text/fulltext/chapter）；守卫自证在位（smuggled.body 恰好被抓，防永真）。auditor 独立重放（更强形态）：向真实 store 库注入 injected 表(body 列)→守卫抓到 ['injected.body']。守护从一次性探针升级为已提交测试——接条款闭环。
- F2 复核：_apply_label_set 重构为「auto 来源不覆盖 manual/user 行」；label_cleared 改为只删 source='auto' 行；测试覆盖 manual 保留/user 保留/双保护第二半/auto 仍可清（保护不扩大）。
- E2 revert 重放（auditor）：store.py 还原 0fb5811 → 恰 2 failed（test_label_cleared_keeps_user_row + test_auto_relabel_does_not_override_manual_or_user，恰为 F2 引入的行为差；manual 测试在旧代码本就过——敏感性精确指向修复 delta）→ 恢复 22 passed。
- 隔离全量：22 passed（16 旧 + 6 新）。
- 语义裁决（implementer-3 自报修正）：**收敛方向正确，采纳**。「仅 auto 被拒」与 CONTEXT.md 关系图/标签条款字面语义精确对齐——禁令的施动者是「自动提取」而非人工；人工互可接管（user 接管 manual 槽位）是人工优先语义的必要组成，否则 GUI 二次编辑在首次 manual 落位后即死锁（票 12 接缝）。过宽形态（manual/user 完全不可动）被正确修正。
- 遗留跟进（不阻断 W5-F，记票 10/12 审计预案）：W6 InMemoryLabelsStore.record_book_tag 仍是严格形态（user→manual 接管会被 ManualTagProtected 拒绝，store.py:152 source != existing.source）——与本次裁定存在残余分歧。建议后续小票把 W6 协议守卫对齐为「仅 auto 被拒」（约 2 行+1 测试），或明确文档化两形态差异；W6 已 PASS 的裁决不受影响（其守卫满足 D1 字面，本裁定为其后收紧口径的统一）。
- 结论：F1/F2 关闭，W5（0fb5811+1e5caa4）整票 PASS 可 merge；W8 schema 依赖解除。


### 2026-11-21T03:22+0800 第 7 条遗留跟进 — 闭环（W6-A merge 8c01afe）

- cf3f9c0（fix/06-align）+ 8c01afe merge：record_book_tag 守卫收敛「仅 auto 被拒」（master:store.py:154 逐字确认 in (manual,user) and source==auto → 拒）+ test_takeover_semantics.py 86 行（接管×2 修复前红、auto 拒绝回归×2、auto→auto、管道跳过）。
- auditor 独立核验：master 快照隔离 reco-labels 63 passed；守卫单行逐字比对与 audit-log 第 7 条裁定精确对齐；分支已删（PR-first 全闭环）。
- 状态：第 7 条遗留跟进 **关闭**；W6-A 与 W5-F 裁定全面一致（manual/user 互可接管、仅 auto 被拒）。


## 2026-11-21T03:40+0800 W8（工单08 相似度打分）审计 — PASS（建议可 merge）

- 对象：8543547 @ feat/08-scoring（2 文件 +914：score.py 501 / test_score.py 413；基点 8c01afe 正确，reflog 干净，提交面纯净）。方法：git archive 隔离副本。
- ① 金标独立复算（E3/E4）：auditor 内联重推逐位吻合——dot=1.2400000000000002、q_norm=1.2806248474865698、c_norm=1.019803902718557、sim=0.9494740323391176（repr 逐字匹配）；权重调度独立抽查 ws(70,70,0.9)=0.4910068950189542（满爬坡≈0.5 合理）。期望来源独立于实现，禁自洽成立。
- ② M1 重放：拆掉 rank 同分 tiebreak（sorted key 去掉 r[0]）→ 恰 1 failed（test_S1_tied_scores_break_by_book_id_ascending，test_score.py:120——正是 M1 揭示契约后补的测试）→ 恢复 44 passed。补测试真敏感，同分 book_id 稳定序契约（§6.3 L468）从无覆盖变为有守护。
- ③ 探索位确定性（§17 Q7/建议项 3）：指纹=排序后 book_id 序列 blake2b（稳定+区分性+空集 FingerprintTooShort 守卫）；固定次序行走；舒适区顶（相似度并列最高）不作探索位（rows[0].is_exploration False + b3 不在探索集断言）；全并列池取全体；池≤配额不探索（全员探索=没有探索）；同输入两次 rank 输出全等断言。
- ④ weight_schedule：cv_auc<0.5 → ValueError 拒绝坏评估（不静默赋权）；单调平滑测试步进 n=0..60 相邻差 ≤0.05 上限断言（cap×quality×ramp 每样本斜率有界）；CV 敏感性（0.5 vs 0.9 权重可分）；n_pos<10 强制 w_model=0。事件化接缝：WeightSet 携带 version+reason（来源可追溯，§6.5-4），权重变化落事件归 W9 画像层（score 写事件将违反 §6.5-1）——接缝成立，列入 W9 强制必查（profile_updated 随权重变化触发）。
- ⑤ 诚实性（C6）：evaluate_pairwise 单类别 → {auc: None, verdict: 不足以判定, folds: 0}（禁伪造 0.5/AUC）；CI 跨 0.5 → 尚不显著（同文本+每折 1正1负构造迫使 AUC=0.5 的测试设计精彩）；train_pairwise 门 ≥10/≥10 否则 None 且子进程探针 NONE|CLEAN（sklearn 零加载）；F5 import 探针 CLEAN。E8 不适用（W8 无时间运算，now 仅签名契约且明示不作衰减——§6.5-1 反例 1 的对偶守护）。
- A8 §6.5 反例测试化：_BANNED_CONFIG_KEYS 守卫（TAGS/RELATIONS/EVENTS → ValueError，测试断言）；反例 4 权重变化必改输出；反例 1 now 不做衰减（docstring+守卫）；缺失向量跳过计数不静默当 0 分；空候选 no_candidates 状态；模型文件缺失降级路径（file_missing 优先于样本量门）。
- 隔离全量：reco-core 44 passed in 3.10s（提交态独立复现；全仓 270 passed 与队长口径一致）。
- 遗留观察（不阻断，2 条 low）：① evaluate_pairwise 自身无 ≥10 门（5/5 亦产 AUC）——门控已在 train（None）与 weights（w_model=0）强制，评估持久化门归 §7.2「evaluation_runs 只在样本足够时写入」= **W9 审计强制必查项**；② rank/score 模块级 _last_status/_last_skipped 为进程内状态（GUI 单写者队列前无并发需求，票 12 若多线程读状态需复核）。


## 2026-11-21T04:10+0800 W9（工单09 画像与置信度）审计 — PASS（建议可 merge）

- 对象：af55dfd @ feat/09-profile（6 文件 +1025：profile.py 569 / 3 测试 369 / 2 注记；基点 2370de4 正确，reflog 干净，提交面纯净）。方法：git archive 隔离副本。
- ① C4 黄金公式：decay_weight = 0.5^(age/half)，auditor 独立重推 w(90,90)=0.5 / w(60,90)=0.6299605249474366 / w(15,90)=0.8908987181403393 / w(270,90)=0.125 全部逐位吻合；Q1 分档 90/30/180 断言（含 subject./relation.→stable、signal.→30 前缀分类）；非法配置（≤0/NaN/非映射）拒绝→回退默认→reason 记入 profile_updated（「回退」断言）→ 直接调用抛 InvalidHalfLife。
- ② C6 置信 cap + 【强制必查】persist_evaluation_gate：cap=0.3（n_pos<10）边界 n_pos=10→cap=1.0 释放测试；「还不够准」display；置信度只作诚实展示（无 AUC 字段）。persist 门：≥10/≥10 才写 eval_run、否则 None+零调用——E2 变异重放（auditor）：阈值 10→1 → 恰 1 failed（test_S14_evaluation_persistence_gate，test_profile_update.py:154）→ 恢复 60 passed。W8 接缝②闭环，audit-log 第 8 条遗留①关闭。
- ③ C7 趋零不归零：dislike 序列测试（value=0.5×8 次）严格递减且全部 >0（frozen trace 逐位断言）；公式层根 decay_weight(10000,90)>0 专项断言——永不构成硬排除。
- ④ C2 重放：同流逐位一致 + verify_replay 容差 1e-9（相对容差 max(tol,|a|·tol)）；篡改 1e-6 被检出（rep2.ok False）；事件缺口 → completeness=partial + event_id_high_water=5（S8 4 号缺失构造）；计数/版本/完整性/置信度相等性比对。
- ⑤ 权重变化事件化接缝闭合（§6.5-1 关闭点）：WeightSet 鸭子输入（dict/对象）→ ProfileData.weights/weight_version/weight_reason 回声 + profile_updated payload.weights_json 原样回声（w_sim/version/reason 逐值断言，test_S13）——W8→W9→W10 链路的权重溯源闭环；profile 只产事件不写库，score 只读。
- ⑥ clip+Laplace 独立复算：auditor 自写循环重推 8 步 clip 序列 [0.45,0.4,0.35000000000000003,...,0.16666666666666666] 逐位全等（|Δ|≤0.05 顶格语义 a：显示权重按步进演化）；Laplace 目标 (sp+1)/(sp+sn+2) 与冻结 trace 一致；dwell 500s 满强度 + 30d 档 100 天衰减 0.09921256574801246 吻合。冻结脚本 09-freeze-goldens.py 纯 stdlib（E4 成立）。
- 边界：reco_core 零 reco-store/reco-io/sklearn/sqlite import（grep 仅 docstring 提及）；payload 白名单子集自断言（assert set(payload) <= _GENERATED_PAYLOAD_KEYS）；B 类零正文（payload 全数值/JSON 数值串）。
- 隔离全量：reco-core 60 passed in 3.21s（44 W8 + 16 W9 新增）。
- 遗留观察（不阻断）：2 处 if False else 死分支残留（test_profile_build.py:164、test_profile_update.py:75，与 W6 match.py:92 同模式——红相演化痕迹，纯异味）——建议在 W10 或收尾票做一次三处清理 PR（match.py + 两个 test 文件）；W8 遗留② _last_status 进程内状态维持待票 12 复核。


## 2026-11-21T04:50+0800 W10（工单10 不合口味反馈闭环，双提交）审计 — PASS（建议可 merge）

- 对象：858e1aa（feedback.py 390 + test_feedback.py 385）+ b5cfa96（score.py 接缝扩展 11+/7-）；基点 d61fe97 正确，reflog 干净，提交面纯净。方法：git archive 隔离副本审计 tip。
- 裁决① 接缝 b5cfa96 必要且最小：**成立**。keyword-only exploration_exclude 缺省 frozenset() → 33 个 W8 测试零破坏（隔离 82 passed 含 test_score 全量）；diff 严格限于 _pick_exploration 池过滤 + rank 签名 + docstring；score 不知道「静音」概念（通用排除能力，语义归调用方，§6.5-1 合规）；必要性由变异 B 反证——无此参数则静音候选可被探索位捞回（mutation B 恰 1 failed 于 test_S1_muted_never_returned_via_exploration_slot:166）。无语义漂移。
- 裁决② 静音语义等价性：**成立**。代数恒等式 auditor 独立推导：score_muted = w_sim'·sim + w_behavior'·S = 0.95·w_sim·sim − 0.9·w_sim·sim = 0.05·w_sim·sim（1e-15 内精确；Σ=1 保持过 W8 归一断言）；降权 95%+ 即「立即从推荐头部消失」（test_S1：B1 沉底 ids[-1] + 探索位不捞回 + reasons source=muted 只标注不改序）；「静音带过期」= MuteWindow 分档 30/90/180（Q1 同 W9）+ 过期零残留（test_S2_expired：91 天后 B1 回到第一、分数恢复 COS10 原值）；非硬排除（C7）由架构硬规则决定——「消失」的唯一合规读法是沉底+不捞回，全饱和退路（§15 L907「权重下限（不归零）+ 探索配额；提示口味可能已变」）逐字对应 test_S7。规格语义与实现等价。
- 裁决③ pool_added 不进口味流：**成立**。persist_feedback add_to_pool → pool_added 事件；test_S5b：store 只含 pool_added 时 rehydrate → version 1（画像折叠忽略）；与 ui_usage 先例同构（交互类 ≠ 口味类，§7.2）。**前瞻接缝点（W11 必办）**：W5 store._apply_event 的事件类型枚举不含 pool_added（落真实 Store 会抛「未知事件类型」）——W11 CLI 接线 persist_feedback→真实库前必须把 pool_added 加入投影器 pass-through 记账型清单（与 scan_started/perf_sample 同列），否则 pool 回流在真库上不可用。
- 裁决④ E2 重放抽 2（MV1-M5 自报之外 auditor 独立）：变异 A（静音比 0.05→0.5）→ 6 failed（S1/S2/S5/S7 家族，金标 TAIL=0.0492403876506104 锚定深）；变异 B（拆探索排除）→ 恰 1 failed（:166）→ 均恢复 82 passed。静音核心与防捞回两大安全点检测性成立。
- 四预案：C7 数据层消失=沉底不捞回非硬排除（上述）；fold_dislike 对接=handle_feedback 经 W9 update_profile 折叠 rec_feedback（clip -0.05 → 0.45，test_S3，秒级 version+1）；D6 手动标喜欢=like 回流不静音且轴权上调（0.55），身份数据层稳定由 W2 (st_dev,st_ino)+W5 dedup 链承担；E8 全链路 NOW 注入（MuteWindow.with_remaining/feedback_effects/handle_feedback 全可注入，零 sleep）。
- 隔离红线（S6）：inspect.getsource 断言 feedback 内无第二排序实现（def _rank/rank(/score(/sorted(rows 零命中）+ 零跨包引用（reco_store/reco_io/relations/labels/sklearn）；auditor 复核源码确认只 import 同包 score/profile。
- 隔离全量：reco-core 82 passed in 3.11s（60 W9 + 22 W10）；全仓 308 与队长口径一致。
- 遗留观察（不阻断）：① pool_added→W5 投影器扩展为 W11 强制必办（上述裁决③）；② 静音表 mute_by_key 由调用方持有（feedback 无持久化静音存储）——W11 CLI 需定义静音表的落库与恢复形态（建议随事件流或 store KV），票 11 审计预案新增此项。


## 2026-11-21T05:20+0800 W11（工单11 CLI 入口，双提交）审计 — PASS（建议可 merge）

- 对象：8c01a7b（store 接缝：store.py +84 / test_pool_mute_projection.py 6 测试 160 行）+ 120574b（CLI 实装：app.py 332 / test_cli_app.py 255 / __init__ 93 / store.py append 鸭子适配 +11 / algorithm-changelog.md 首行；基点 0bd43e3=W10 merge 正确；amend 轨迹即 UTF-8 修复）。方法：git archive 隔离副本审计 tip。
- 接缝① pool_added pass-through：**达成+重放**。收编与 scan_started/scan_completed/rec_feedback/perf_sample 同列入记账型清单（store.py:542），注释明示「pool_added 交互类，§7 隔离」；真库落库测试（修复前抛未知事件类型）+ 不进口味流测试（signals=0/profile_versions=0）+ rebuild_projections 重放零漂移（含静音事件同测）；auditor E2 重放：从清单移除 pool_added → 恰 2 failed（真库投影+口味流隔离两测试）→ 恢复 46 passed。
- 接缝② 静音表持久化：**达成**。mutes 表（book_id 主键/dedup_key/axis/kind/expired_at/remaining_days/schema_version/config_fingerprint/updated_at）+ mute_upserted/mute_released 事件投影（替换窗口 ON CONFLICT / 解除零残留）+ record_mute/release_mute/load_mutes 生命周期；重启恢复测试（close→重新 open→窗口/到期时刻/剩余天数全保留）；替换不累积（两 record → 1 行新窗口）；COMPARE_TABLES 收编 mutes → verify_replay 逐位覆盖静音投影（含 F2 修复不回退）；白名单收编 dedup_key/remaining_days/expired_at 用真实行先红（W5「空表探针测不出白名单行为」教训复用，audit E5 方法论内化）。
- ③ B 类 JSON：report/recommend/explain 输出契约面纯净（test_C4_recommend_json_has_no_prose_fields set(payload) 恰等三键）；无书名（「合成样本」not in text，book_id 代替）；B6 payload 白名单断言在 store 层延续。
- ④ C13：mutes 行携带 schema_version+config_fingerprint；事件面 W5 契约延续（回放逐位成立）。
- ⑤ A6 CLI 无第二排序路径：docstring 声明+test_C6 源码级断言（rank_only 消费+sorted( 零命中+reco_ui 零依赖）+ auditor 独立 grep（sorted(/.sort(/def rank/def score 全零命中）双确认；CLI 只做静音胶水形态映射（mute_map_from_store 过期行过滤+dedup_key 优先），判定留在 feedback 层。
- ⑥ UTF-8 教训评估：stdout.reconfigure 原地改编码（不替换 sys.stdout 对象）——正确做法。方法论价值已入注记：替换 sys.stdout 会让旧 TextIOWrapper 在 GC 时关闭底层缓冲，级联炸 pytest capsys/tempfile（首轮 25 ERROR 根因）；修复带 subprocess cp1252 管道回归测试（PYTHONIOENCODING=cp1252 下 utf-8 解码无 U+FFFD）。此坑与 W2「空表探针」同类——环境级交互失效难由单元断言暴露，子进程/真实行探针是唯一可靠形态。
- 四预案：A6 ✓（上述）；JSON 无正文 ✓（C3/C4 断言）；C6/C7 文案透传 ✓（recommend「仅供参考/还不够准」、feedback「已调整…下调」真实 Store 闭环）；只读入口零写入 ✓（test_C7 五入口 events 数不变）；E8（CLI 用真实时钟——report/generated_at/recommend 即时输出属表现层时间戳非口味计算，口味计算仍全在 W9/W10 注入点内，CLI 未引入新的时间敏感语义）。
- 附带正向发现：cmd_replay 漂移检出 exit 1（test_C9 绕过事件流直改投影 → 非 0）——ADR-0003 的运维面闭环；eval 小样本诚实降级（不足以判定不伪造 AUC）。
- 隔离全量：cli+store 46 passed in 2.00s（28 store 含 W5/W5-F 回归 + 18 CLI）；与 test-runner 并行验收口径一致。
- 遗留观察（不阻断）：① cmd_recommend 候选向量恒空 dict（特征库尚无向量数据，file_added 不产向量）——W13 一致性票需确认 CLI/GUI 同样以空向量基线对齐（现契约 face 一致，真实向量接入后行为待验）；② cmd_eval n_pos≥10 分支传空样本调 evaluate_pairwise([])——在真实样本接线（W13/14）前恒走诚实降级分支，语义安全；③ 8c01a7b 与 120574b 各自独立可回滚，接缝先行提交纪律良好。


## 2026-11-21T06:05+0800 W12（工单12 GUI 三板块，7 提交链）审计 — PASS（建议可 merge；W13 承接两接线）

- 对象：4fc7f63→b3603bf 七提交（骨架+QSS / 推荐页+模型 / 板块+异步加载 / 画像页 / loader 注册表 / 反馈异步 worker / RV 重放修复），rebase 到 3320259 验证（merge-base 恰为 3320259、7 提交在范围内）；+1638/-23（UI 六模块 957 行 + 5 测试文件 + 4 离屏截图 + uv.lock PyQt6）。方法：git archive 隔离副本审计 tip。
- ① A6 GUI 零第二排序：**三重确认+重放**。test_C6 源码级断言（tables.py「零重排、零补算」）；grep（sorted(/.sort( 全零命中）；RankTableModel golden 顺序测试（RankRow 输入序=渲染序）。E2 重放（auditor）：向 set_rows 注入 sorted → golden 顺序测试恰 1 failed（test_actions.py:141）→ 恢复 24 passed。
- ② D5 逐字段编辑写回：**本票无越界**。票 12 无逐字段编辑验收（归工单 07/12 GUI 编辑面）；GUI 写路径仅三按钮（dislike/like/pool）→ glue.record_feedback → persist_feedback + record_mute（单写者队列）；与 W5-F/W6-A 裁定一致（静音落库走 record_mute 事件投影，无 book_tags 直写面）。D5 断言留至编辑功能票。
- ③ 0xC0000409 三重防线核验：**结构成立，守护口径解除成立**。①整场持引用（conftest _qapp_session session 级 _app_holder 防 PyQt GC 销毁 C++ 对象）②loader/feedback 注册表持活（_ACTIVE_LOADERS/_ACTIVE_FEEDBACK 入队即 add、run() finally discard——QThreadPool 不持 Python 包装的根因对治）③conftest 每测试泄洪（waitForDone+processEvents，跨测试 worker 生命周期根治）+closeEvent 兜底泄洪+迟到信号 RuntimeError 静默丢弃。防线覆盖崩溃面：GC 销毁/悬垂包装/跨测试存活三路径各有对治；24 测试退出期零崩溃。遗留②关闭。
- ④ B4：理由渲染只含 axis/contribution/source（render_reasons）+ 测试断言；zone 行/画像行无正文。
- ⑤ C6/C7：画像页空库诚实降级（「还不够准（样本不足，仅供参考）」不伪造数值）；recommend/feedback 文案经 glue 复用 feedback_effects（与 CLI 同契约）。
- ⑥ 12b ui_usage 隔离：**无越界**。本票零 ui_usage 写路径（grep 仅注释提及）；test_seed/dislike/pool 三测试显式断言 usage==0。
- ⑦ RV 事故：b3603bf 重放修复确认（FeedbackWorker 接线+状态行+按钮防护完整回位）；提交态纯净（范围 diff 全为工单 12 文件）；教训「RV 必须在干净树上跑」随 commit message 入库——与「分支写操作断言」互补，共同点都是写操作前断言环境状态。
- PyQt6 可重现性：DEPENDENCIES.md L60 记载 zipfile 解压 wheel 恢复法（2656 文件、pip 撞杀软 DLL 锁场景）+ 三个 wheel 版本表 + 断网演练记录——方法具体（命令、路径、文件数）可重现；uv.lock 60 行 PyQt6 锁条目。
- 隔离全量：reco-ui 24 passed（QT_QPA_PLATFORM=offscreen）。
- 承接 W13 两接线（不阻断）：① GUI recommend 页 rank 消费接线（现 RankRow 经 set_rows 测试注入，生产加载 worker 未接 rank_only/静音映射——W13 CLI/GUI 逐位一致的核心对齐面）；② pages.profile_rows 现为占位快照（has_profile 门控+计数查询真实，axis_weights/confidence 硬编码占位值）——W13 需接 _load_profile 真实画像。
- 工作树观察：共享检出（master 3320259）有未提交 __init__.py 修改（内容=W12 工单 12 版本）——共享树切换残留，merge feat/12-gui 后自然消解；不构成提交面问题。


## 2026-11-21T06:35+0800 W12b（工单12b 自适应热加载，de017e4）审计 — PASS（建议可 merge）

- 对象：de017e4 @ feat/12b-preload（6 文件 +674：store.py ui_usage API + preload.py 135 + app.py 接线 46 + 三测试文件 460；基点 0031354=W12 merge 正确，reflog 干净，提交面=声明写域精确一致）。方法：git archive 隔离副本。
- ① C12 隔离三层防线 + M2 重放：**达成，取证强度对齐 pool_added 先例**。层一独立表（bump_ui_usage 走 submit 单写者队列 ON CONFLICT 递增、get_ui_usage 走 read_conn——W5-F 接缝纪律⑤）；层二 COMPARE_TABLES 排除（test_ui_usage_table_not_in_compare_tables 硬边界断言）；层三口味语义零反应（compare_rows 快照逐位：含/不含 ui_usage 数据两库 r1.compare_rows==r2.compare_rows；狂 bump 50 页口味侧逐位不变 test_profile_fold_ignores；events 表永远不含 ui_usage 行）。auditor M2 重放：把 ui_usage 加进 COMPARE_TABLES → 恰 4 failed（隔离硬边界+单写者+逐位+折叠四测试齐转红）→ 恢复 75 passed。ReplayReport.compare_rows 快照为 W12b 新增（默认 None 向后兼容，回放 ok/diffs 语义不变——W5 verify_replay 契约无漂移）。
- ② 热集纯函数确定性：**成立**。compute_hot_set = 恒热 ∪ 阈值(可配 ≥3) ∪ Top-K；Top-K 按 uses 降序、并列按 page_id 升序（C8 同源纪律）；恒热 recommend 不占 K 名额；重复调用全等断言；auditor 独立复核排序键 (-uses, pid) 与测试逐例吻合。ALWAYS_HOT_PAGES frozenset 不进配置不可关闭（§11.7）。
- ③ QTimer 声明式持有 + 可打断（M3 重放）：**成立**。未 start 不建 timer（防 GC 悬垂，与 0xC0000409 防线同源）；singleShot 1500ms 空闲档；interrupt 置位可变槽 + 停表；auditor M3 重放（首轮变异无效如实弃用，精确匹配重放）：禁用 _warm_hot_set 早退检查（:115）→ 排队任务真实执行 → test_user_interrupt_skips_queued_jobs 恰 1 failed（:46 KeyError）→ 恢复绿——可打断性真敏感。
- ④ 成功才置位（M1 重放）：**成立**。worker ok=False/异常/被中断 → 不置位；冷区按需路径兜底可重试（逆向基相测试）；auditor M1 重放：mark 条件改为恒真 → 恰 2 failed（失败置位+按需兜底两测试）→ 恢复绿。陷阱 3 原文语义闭合。
- ⑤ W5-F 接缝纪律：写 submit / 读 read_conn 全部合规（测试显式覆盖）。
- ⑥ app.py 接线无越界：46 行恰为契约三方法（store/preload_jobs/_make_zone_job）+ nav bump + interrupt + Preloader 启动；**未占 W13 rank 消费面**（preload_jobs 仅覆盖 liked/candidate zone 页，recommend/profile 明示「接线后扩」）；bump 在 _on_nav（UI 线程只发起，写走单写者队列）。
- ⑦ 生命周期防线沿用：_ACTIVE_PRELOADS 注册表入队 add/run() finally discard + 迟到信号 RuntimeError 静默——17eceaf 防线同款。
- 隔离全量：UI+store 75 passed（24 W12 + 9 W12b UI + 42 store 含 ui_usage 6 测试）。
- audit-log 登记 W5-N（跨票发现，队长核实三处）：store.py:113/484/492 vector_blob_b64 键名实不符（:492 bytes.fromhex 解码 b64 名键）——行为自洽（hex 值+hex 解码）但键名必踩坑；W5-N 修复票（t19）裁定统一 hex+rename+契约测试+逆向；**纳入下次 merge 审查范围**。
- 遗留观察（不阻断）：① recommend/profile 无预热任务——属 W12 承接①②的 W13 范围（热集恒热页已加载则零动作，语义安全）；② test_preload_ui.py:200 tmp_path_none() 用 tempfile.mkdtemp 绕过 fixture（测试自身小异味，非契约问题，随手可清）。


### 2026-11-21T06:35+0800 W5-N 登记（跨票发现，audit-log 首条 finding 台账）

- 发现：store.py:113（白名单 vector_blob_b64）/ :484（payload 构造）/ :492（bytes.fromhex 解码）——键名声明 b64、解码用 hex，名实不符。
- 严重度：medium（low 行为面：当前自洽不产错误结果；medium 契约面：跨票必然踩坑——W13 向量真实接线/任何后续消费者按 b64 名解码即崩）。
- 处置：W5-N 修复票（t19）已派 implementer-3——裁定统一 hex：rename+全链 grep+键名-编码契约测试+逆向。auditor 将在 W5-N 达闸门时按 PR-first 复核（rename 后 grep 零 b64 残留 + 契约测试双向敏感 + W8/W9/W10 零回归）。
- 状态：登记完成；W5-N 纳入下次 merge 审查范围（与 W12b 并行推进，互不阻塞）。


## 2026-11-21T06:55+0800 W13（工单13 CLI/GUI 一致性，三提交）审计 — PASS（建议可 merge，附一项 merge 顺序裁定）

- 对象：859ae38（主实现 8 文件 +770）+ 84331f5（收编 merge）+ 8662e73（接缝语义修正）；分支自 0031354 正确切出。方法：git archive 隔离副本审计 tip（8662e73）。
- ①【核心】CLI/GUI 逐位一致：**达成+E2 重放**。test_consistency.py 是双子进程真集成（E7 合规：CLI probe 走 cmd_recommend 生产路径 vs GUI probe 走 MainWindow.load_now 生产装配路径——非桩测试）；30 候选+真实向量+12 like/11 dislike 静音+探索位全字段逐位断言（book_id/score/rank/is_exploration/reasons 三元组列表）+静音理由透传+探索位两入口一致出现。auditor 重放（锚点①）：GUI load_now 注入行反转（第二条排序路径）→ 恰 1 failed（:157 逐位断言）→ 恢复 5 passed。注：其 RV 脚本在隔离快照跑不了是 editable .pth 伪象（pytest 误入 master 包），非脚本缺陷——PYTHONPATH 指向快照的标准手法重放成功。
- ② A6 共享装配层：**零排序零复制确认**。recommend.py 源码审查——只做读库/向量解码/形态映射，rank 调用发生在两入口消费 rank_only；grep sorted(/.sort( 零命中；CLI 装配代码整体删除（-71 行收敛），静音胶水/画像加载委托共享层并保留公开别名（契约不破）。损坏向量行跳过计数（不静默当 0 分，§6.3）。
- ③ eval 门双路径：**达成**。真实样本接线（load_eval_samples zone→label，W11 遗留闭合）→ ≥10 路径产出 AUC+persist_evaluation_gate 落行（evaluation_runs 恰 1 行断言）；insufficient 路径零写入断言。n_pos=12/n_neg=18 手算口径与 zone 标签对齐。
- ④ inflight 断言语义更新：**裁定合理非过度放宽**。{liked} → {liked, recommend} 的变化是生产接线的必然结果——recommend 恒热主面板构造时 setCurrentRow(2) 触发 emit 现在真正走 RankLoader（旧占位形态直接 return）；去重语义本身未放宽（重复 _start_load 仍被忽略断言保留）。
- ⑤ W5-N 键名：**队长「已收编」前提有误，需 merge 顺序裁定**。84331f5 merge 只带入 W12b 六文件（stat 证实），W13 tip store.py:114/493 仍是 vector_blob_b64；test_consistency.py:121-125 明示「临时对齐，W5-N merge 后切回 vector_blob_hex 并复跑全量（captain 协调项）」。W13 本身键名自洽（fixture 注入 b64 键+hex 值，解码侧 fromhex 一致），不阻断 W13 语义；但 **merge 顺序裁定：W5-N 先 merge → W13 再 merge → master 上复跑全量（切回正式键名后 382+ 全绿才收尾）**，或接受 b64 键形态并改 W13 注释——建议前者（W5-N 已 PASS 在案，先 merge 消灭名实不符）。
- ⑥ RECO_PERF_TABS CI：快照两跑逐位一致测试在位（a==b）+ golden 基线入库（tests/golden/reco-perf-tabs.json）+ scripts/ci-consistency.sh 可执行性审查通过（set -euo pipefail/漂移 assert 非零退出/基线缺失首跑自动创建/末段挂 test_consistency 全量——漂移即 CI 红）。
- ⑦ 两承接真接线：**确认**。①GUI rank 生产接线=RankLoader（QRunnable 池线程 rank_candidates）→ apply_rank 渲染，推荐页 rows=30 真实渲染（test_perf_tabs recommend rows==30 非占位 0）；②profile_rows 真实画像（axis_weights 非占位、n_pos=12/n_neg=11 逐位断言、version=1）——两处硬编码占位全部替换。
- 隔离全量：382 passed in 11.10s（含 master 收编 W12b/W5-N 测试面 + W13 一致性 5 测试）。
- 遗留观察（不阻断）：① RV 脚本 bytes 级恢复教训（write_text newline 翻译致 LF→CRLF）与 W12b 多行 sed 同类——脚本型 RV 的写回必须字节级；② recommend.py load_eval_samples 文本=display_name（W8 契约文本位），真实正文特征接线后此口径需 W14 复核；③ 一致性测试依赖 offscreen Qt——CI 环境需 QT_QPA_PLATFORM=offscreen（脚本已内置）。


## 2026-11-21T07:50+0800 W14（收官票：换核演练+全验收+retro，8 提交链）终审 — PASS（建议可 merge，交付前最后一道审计通过）

- 对象：1f463a8（换核快照）→ 5f9a358（证据归档）→ f36a820（revert）→ 998ebcb（7 包独立演练 5 项契约修正披露）→ 1243f56（ACCEPTANCE 24 项）→ 6bedfbe（CODING-STANDARDS 8 条 + pre-commit-guard.sh）→ c302b2f（RETRO）→ 121e6b6+6652129（testpaths/键名收口，全量 384）。基线 65a07b2 正确。方法：git archive 隔离副本终审。
- ① 换核演练真实性：**真实现替换（非换皮）+ 独立复算全吻合**。度量变（余弦 dot/范数 → 欧氏核 1/(1+d²)）且计算路径变（点积/范数 → 逐维差平方和+候选独有键惩罚）；零范数语义从 0.0（余弦二义）改为 1/(1+‖质心‖²)（诚实保序）；保序性证明成立（单位向量 1/(3−2cosθ) 严格单调增）。auditor 独立手算：d²=0.2→sim=0.8333333333333334 逐位吻合、零向量 0.3787878787878788 吻合、保序抽验 True。cli/ui 零改动 git 实证：tip 与 master 的 score.py diff 为空（revert 精确还原）+ revert stat 与 swap 精确互逆（28+/18- vs 18+/24-）。红转绿明细（score 5/feedback 4 仅数值契约，结构契约零失败）= 数值/结构契约解耦的硬证据。
- ② 7 项修复写域核验：**在域**。范围 diff 13 文件与披露一致（5 项契约修正=pyproject×2 依赖声明补齐+hygiene 探针自适应+labels 两测试去跨包；跨包测试移根 tests/ 37 行=test_cross_package_integration.py——CODING-STANDARDS#2 的实现载体）；跨写域改动 2 次（b5cfa96/998ebcb）均披露自查。
- ③ ACCEPTANCE 24 项抽查复跑（6 项）：**全绿**。①11（一致性 5 passed）②11（score+feedback 55 passed）③16/9（cli+labels 80 passed）④全量 384 passed（隔离独立复现）⑤guard 干跑 ALL GUARDS PASSED ⑥键名收口（tip store.py:114 vector_blob_hex + 键名-编码契约注释——W5-N 协调项正式关闭）。
- ④ RETRO/CODING-STANDARDS/guard 固化核验：**8 条规范逐条对照成立**——每条均映射真实争议（#1 换核解耦、#2 labels 独立 venv 实锤、#3 W8 config.pop 副作用、#4 S1 三轮失败、#5 W10 机制无效、#6 _last_status 审计遗留、#7 依赖漏声明、#8 W10 漏 add）；guard 7 项确定性检查（分支断言/漏 add/.audit- 命名空间/裸 except/语法编译/RV 注明/字节级回写提醒），bash -n 语法 OK + 干跑通过。机械教训 7 条全部有脚本或流程载体。
- ⑤ isolated-install 升格处置：**建议合理且留痕**——w14-isolated-install.sh 已入库（v7 全传递闭包，install=0 test=0 分离记录，7 包 core81/labels62/relations68/store36/io71/cli18/ui41），升格 scripts/ 并入 CI 列入未竟移交（下一迭代）。
- ⑥ 净树纪律：**通过**。共享检出脏项全为未跟踪审计产物（audit-log/notes RV 脚本/截图工作目录），无未提交跟踪文件；本票 RV 总扫由 auditor 方法论执行（隔离快照+变异重放历史 14 条全闭环）。
- 终审动态证据汇总：隔离全量 384 passed in 12.16s；抽查 4 组全绿；黄金值独立手算 3 项全吻合；guard 干跑通过。
- 终审结论：五类全量（A 边界互穿/B 正文泄漏/C 契约/D manual/E 自洽）无未关闭 blocker；14 票审计记录完整闭环；**建议 merge feat/14-swap-drill 收官，项目交付就绪**。
- 交付后备忘：3 处 if False else 死分支收尾票、_last_status 多线程复核（票 12 已裁定）、isolated-install 升格 CI、uv/hatchling 离线补齐——全部在 RETRO 未竟移交清单，不阻断交付。


## 2026-11-21T10:35+0800 W16（交付后迭代：GUI 导入三功能，双提交）审计 — PASS（建议可 merge）

- 对象：6462809（F2/F3 切片1：app.py 25 + pages.py 53 + test 124）+ 2e0cbe0（F1 拖拽切片2：pages.py 127 + qt.py QUrl + test 128）；基点 7718c8c（W15 图标 merge）正确，reflog 干净，范围 4 文件 +453/-5 写域精确（reco-ui 单包）。方法：git archive 隔离副本审计 tip。
- ① payload 同构契约：**达成+重放**。ImportWorker.file_added payload 与 store.py:421 file_added 投影逐字段对齐（path=落位产物实际路径/zone/ext/size_bytes/mtime_ns/st_dev/st_ino/display_name/normalized_name/dedup_key）；st_dev/st_ino 取自 PlacementResult.dst.stat()（产物 stat——扫描器后续按身份配对不重复建 book，队长契约补充成立）。E2 重放（auditor）：payload.path 改为源路径 → 恰 1 failed（test_import_places_file_and_persists_file_added:207 产物路径断言）→ 恢复 53 passed。
- ② place_by_hardlink 契约：**经公共 API 复用零复制**——EXDEV 退复制/指纹判等（test_import_twice SKIPPED 不重复落库）/权限上抛语义全部由 W3 原生实现承担，GUI 不重实现。
- ③ 白名单拒绝路径：dragEnterEvent 预筛（目录/非法扩展 ignore）+ drop 拒绝计数（test_import_rejects：exe+目录 → 零落位零落库+状态「拒绝 2」）双层在位；ALLOWED_IMPORT_EXTS 与扫描侧 TEXT_EXTS 同口径。
- ④ F2 票面假设修正：**与 W2 源码/§8.1 一致**。scanner.ensure_zones 刻意不建目录（§8.1 避免猜用户意图，W2 审计已锚定）；open_zone_folder 文档明示「用户点击本身就是意图，创建在本函数完成（root/专区层 mkdir）再调 ensure_zones 走校验契约」——语义分层清晰，测试覆盖（点按钮→目录创建+startfile 断言）。
- ⑤ A6：F3 开始推荐 = candidate 专属按钮 → recommend_requested → _start_recommend → _start_load("recommend") 复用 W13 RankLoader 生产接线 + 自动切页——零第二排序，与 W13 逐位一致契约直接兼容。
- ⑥ 坑 2 去重：test_start_recommend 显式在预载 in-flight 期间清空模拟完成后再点击——去重语义的测试构造合理。
- ⑦ B 类：新 UI 面零正文（status 摘要只有计数、表格列仍是 book_id/名称/大小、payload 全数值/路径/枚举）；_ACTIVE_IMPORTS 注册表防线沿用（17eceaf 同款）。
- 隔离全量：390 passed in 12.37s（384 基线 + W16 12 新测试 - W14 遗留口径修正）；UI 子套件 53 passed。
- 遗留观察（不阻断）：① data_root 默认 F:/date/adult-reco/data 可 config 注入覆盖——目录不存在由 F2 点击创建（契约自洽）；② ImportWorker 异常整体吞为 error 字符串（UI 只收字符串惯例），单文件 place 失败会中断整批（后续迭代可考虑逐文件容错）；③ os.startfile 仅 Windows——open_zone_folder 在非 Windows 无 startfile 属性（项目 Windows-only 口径内无碍）。


## 2026-11-21T11:20+0800 R17（W17 格式扩展 DOCX/PDF，1603b52 @ feat/17-formats）审计 — PASS（建议可 merge）

- 对象：1603b52（12 文件 +450/-8：features.py 读取器接缝 62 行 + scanner 白名单 + UI 并集 + test_formats_w17.py 229 行 15 测试 + pypdf 声明 + python-docx dev 声明 + uv.lock + 4 wheels）；基点 095cc72 正确。方法：git archive 隔离副本审计 tip。
- ① 白名单单一事实源：scanner.TEXT_EXTS 扩 .docx/.pdf（一处改动）；UI ALLOWED_IMPORT_EXTS = set(scanner.TEXT_EXTS) | {".docx",".pdf"}——引用常量而非字面重复；并集语义成立（.txt/.md 本就在 TEXT_EXTS 内，并集幂等；向后兼容且扫描口径变化自动传导 UI）。三处收口零劈叉（测试 test_ui_import_whitelist_union 断言 >= TEXT_EXTS 且含 docx/pdf）。
- ② _READERS 接缝：纯函数字典（suffix→读取函数，路径→纯文本零落盘零输出）；.epub 原实现原样入缝；新格式=加条目；分派逻辑从 if suffix==.epub 硬编码收敛为 _READERS.get + 外层降级统一（ValueError 族归一 undecodable）。
- ③ DOCX stdlib：zipfile+ElementTree 提取 word/document.xml 的 w:t 文本，w:p 段落边界 \n\n 保留（测试断言段落换行）；坏 zip→corrupt_container、空文档→empty（真零不瞎填）。
- ④ PDF pypdf：函数内延迟导入（W8 sklearn 先例，auditor 探针证实 import reco_io.features 后 sys.modules 无 pypdf/docx/sklearn/torch/numpy）；加密空密码失败/结构损坏→ValueError 归一→undecodable 降级；无文本层→empty 口径（扫描版降级不瞎填）；pypdf>=7.1 声明+4 wheels 在位（python-docx 仅 dev 组测试样例生成，不进运行时依赖）。
- ⑤ 四态测试：docx（正常/损坏/加密形态模拟/空）+pdf（文本层/无文本层/加密/损坏）+接缝注册+段落换行+扫描拾取+UI 并集+B2 金丝雀扩展（docx/pdf 读取零输出流泄漏，金丝雀断言沿用）+EXTRACTOR_VERSION 不变断言。
- ⑥ E2 重放（auditor）：TEXT_EXTS 回退到 W17 前 → 恰 2 failed（test_scanner_text_exts_extended + test_scanner_picks_up_docx_and_pdf，:198）→ 恢复 86 passed（reco-io 子套件）。
- 两项披露裁决：EXTRACTOR_VERSION 不 bump——**成立**（版本钉的是统计口径，读取器只改变「怎么拿到纯文本」不改变「拿到文本后算什么」；现有 golden 测试不改一字全绿=不变量实证；若 bump 反而迫使全库无谓重算）；W2 scan 测试样例 notes.PDF→notes.png——**合理演进**（.pdf 入白名单后原样例不再是非文本，换成 png 保持「非法扩展」测试语义）。
- 隔离全量：411 passed in 15.61s（独立复现，与 implementer-4 报告一致）。
- 遗留观察（不阻断）：① test_formats_w17.py make_pdf 工厂 51-57 行有死代码（empty_layer 分支重赋 writer 后循环体为空、注释自述「简化」——行为正确但属坏味道，建议随手清理或收尾票）；② RTF 移交记录在案（.scratch/reco-impl/notes/rtf-handoff.md，正则剥控制字对 \uN 有损违背不瞎填、striprtf 超时间盒——口径文档已追加 W17 节）；③ 加密 docx 用非 zip 魔数模拟（真实加密 docx 是 OLE 容器）——降级路径语义等价，注释已如实披露模拟形态。


## 2026-11-21T11:30+0800 W18（交付后迭代：UX 修复批次，3 提交链 @ feat/18-ux-fix，R18 取消并入 R-CLOSE 一并归档）审计 — PASS（随 R-CLOSE 终验确认）

- 对象：134e7f8（修复主体 5 文件 +238）+ 05c4c5a（W16 inflight 测试适配新口径）+ c797488（U3 断言收紧）；基点 095cc72 正确；已 merge 15700df。方法：git archive 隔离副本（R18 在途取证由 R-CLOSE 收口确认）。
- ① U1 黑控制台：bat 重构双分支——默认 pythonw.exe 无控制台；python.exe 分支由 RECO_PERF/RECO_PERF_TABS 守卫（打点走 stdout）。测试逐行断言（启动行含 pythonw、python.exe 行必须带守卫环境变量）钉死。
- ② U2 候选页无响应根治：**排障纪律合格**（先造能红的复现：SlowStore 0.3s 在旧 200ms 下必超时→死页；根因=阈值过激非 in-flight 吞点击——排查方向 a 被证据否决并披露）；修复三件套=_LOAD_LIB_MS 200→1500ms + mark_loading 点击即反馈「加载中…」+ mark_failed 亮出「重试」按钮（reload_requested→_start_load，死页出口）；BrokenStore 专测重试机制；坑 2 原语义未破坏（in-flight 去重保留，W16 测试适配断言「不停在加载中」由新口径自然成立）。
- ③ U3 按下态：通用 QPushButton:pressed 增强（#3a3d42 加深一档+padding-top 9px 位移）+ #primary 专属 #2f66c0 + 导航 :active/:!active 双态 + QTableView:!active；两帧截图对照测试（up/pressed 文件大小差异断言）；c797488 断言收紧钉住通用规则（RV 盲区#primary 假绿教训）。
- ④ U4 重绘守卫：静态守卫禁 .repaint( 与 FullViewportUpdate（Qt 视图默认双缓冲，无整窗重绘点）；耗时反馈由 U2 状态行承担。
- 四项 red→green→逆向闭环：M_U1（启动行退 python.exe）/M_U2（重试按钮藏回）/M_U3（删按下块）自报全红；auditor E2 重放 M_U3：**首轮变异无效如实弃用**（sed 单花括号未命中 f-string 双花括号源行——W12b M3 教训重演），修正后删除源行 → test_qss_has_pressed_state 恰 1 failed（:149）→ 恢复 7 passed。三轮 RV 盲区（bat 注释行命中/#primary 假绿/多行 sed）教训已入票注记。
- 隔离全量（tip）：403 passed ×2 连跑（implementer 自报）+ auditor 快照 403 口径复核；merge 后 master 全量见 R-CLOSE 条目（418）。
- 遗留观察（不阻断）：_LOAD_LIB_MS 1500ms 为经验值（真实库规模再增长时需复评，失败路径已有重试兜底）。


## 2026-11-21T11:35+0800 R-CLOSE（t30 收口终验：W17+W18 双 merge 后 master 终态）— PASS（任务流收官）

- 终态：master 15700df（W17 a58520b + W18 15700df 双 merge 在位）；feat/17*/feat/18* 分支已删；共享检出脏项仅未跟踪审计产物；store.py:114 vector_blob_hex + W5-N 契约注释在位（键名收口保持）。
- ① 全量 418 独立复现：418 passed in 15.39s（隔离快照 + PYTHONPATH src + offscreen，与队长口径一致）。
- ② W17 四态抽验（auditor 独立探针）：docx 损坏→BadZipFile→corrupt_container ✓；pdf 损坏→undecodable ✓；docx 正常→ok ✓；金丝雀「读取文本含金丝雀=True / 结果 JSON 含金丝雀=False」——**零泄漏精确成立**；TEXT_EXTS 五扩展收口 ['.docx','.epub','.md','.pdf','.txt'] ✓。
- ③ W18 四项 UX 终验：U1 bat 双分支守卫（测试逐行钉死+静态核验）✓；U2 根因修复+复现纪律+重试机制（mark_failed→btn_retry.show 实现确认+BrokenStore 专测）✓；U3 pressed 块实存（theme.py grep 计数 2：通用+primary）+截图对照测试 ✓；U4 守卫 grep 零命中（.repaint(/FullViewportUpdate 全仓 UI 源码无）✓。四项 red→green→逆向闭环（auditor M_U3 修正重放恰 1 failed :149）。
- ④ audit-log 第 17 条（R17）在案；第 18 条（W18，R18 取消并入本票一并归档）随本条落盘。
- ⑤ 分支已删/worktree 已清/隔离审计目录清零（ALL-CLEAN）。
- 收官结论：audit-log 18 条全闭环（W1-W14+12b+W15/W16/W17/W18+修复票 W5-F/W6-A/W5-N+双轴评审），五类检查点无未关闭 blocker，PR-first 全程无一违规直改。**团队任务流收官——后续改进另开团队（用户指令），本审计员不新增任何改进项。**

## 2026-11-21T12:25+0800 G2（reco-next t4 真 PR 双闸门基线）审计 — PASS（PR #1 实弹闭环，第 19 条起为新流程条目）

- 对象：4f4c047 @ chore/g2-pr-baseline（1 文件 +59：.scratch/reco-impl/G2-PR-BASELINE.md 就位报告；auditor 独立审计，非转述）。
- 门位清单全部实证：gh 2.105.0 auth = kkkkifgtg Active（keyring token，scopes gist/read:org/repo/workflow）；origin = kkkkifgtg/reco（PRIVATE，default branch master）；fetch 后本地 master 5e179d4 与 origin/master ahead 0 / behind 0；共享检出净树（0 脏项）；gh pr list --state all 为空 → 本 PR 为仓库第 1 张真 PR。
- 双闸门：PR #1 Audit Trail 区双结论 merge 前留痕——本条为 auditor 结论（PASS），test-runner 结论见 PR #1 页面评论；两条齐且 pass 方可 merge。
- 本审计条目随 PR #1 本身入库——新流程首个「审计记录随 PR 落 master」闭环；此后每票审计条目关联 PR 链接。
- 遗留观察（不阻断）：① bash PATH 无 gh（command not found），统一全路径 D:/Program Files/GitHub.cli/gh.exe；② git identity = reco-builder（团队共享提交身份，与 gh 账号 kkkkifgtg 分离属预期）；③ harness create_worktree 工具因缺 node 子进程不可用，隔离副本改用 git worktree add 原生命令（分支写操作断言照执行）。


