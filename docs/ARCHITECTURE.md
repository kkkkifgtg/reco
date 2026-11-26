# ARCHITECTURE — reco 本地小说推荐程序

| 项 | 值 |
|---|---|
| 版本 | v1.3（实现口径统稿；2026-11-25） |
| 日期 | 2026-11-25 |
| 作者 | 架构师（architect） |
| 状态 | **已生效**。本文记录架构决策；与当前实现不一致处以「当前实现口径」为准 |
| 范围 | 技术选型、7 包划分、四组模型、数据模型、接口签名、风险与退路 |

**本文件不含实现代码**：只出现函数名、参数名与返回形状，不出现函数体；确有歧义处的伪代码一律标注「仅示意」。文中 Windows 路径统一用 / 作分隔符（Windows API 等价接受）。

---

## 当前实现口径（2026-11-25）

1. **生产向量**：`reco_io.extract` 使用字符 2–3gram 哈希桶计数、Top-64、L2 归一；没有调用 IDF。`feature_vectors.idf_version` 保留旧列名，当前写 `reco-ngram-counts/1`。`reco_core.vectorize` 的语料级 TF-IDF 是独立可用实现，不是当前生产入口。
2. **正样本口径**：相似度质心 = liked 专区中已产出特征向量的作品（按 book_id 去重），与 CONTEXT.md「正样本 = liked/ 中的作品」一致；candidate 与推荐专区不进质心，因此候选区扩容不会静默改动已有书的分数（#102 已收敛）。`rank_candidates` 额外返回 `n_positive`（质心成员数）与 `n_vectors`（库级有向量书数，空态文案依据）。
3. **阅读器**：GUI 已内建 txt/md、EPUB、DOCX、PDF 阅读页；位置写入独立 `reading_progress` 表，行为信号写 `signal_recorded` 事件。§17 Q12 的「不内建」是历史裁定，已被后续实现取代。
4. **阅读信号**：`dwell`、`completion`、`reopen` 参与画像行为折叠；`read_time` 当前只采集，不产生画像效应。
5. **依赖**：以 `uv.lock` 与 `docs/DEPENDENCIES.md` 为准。jieba/pkuseg 是标签挖掘的可选分词器，不再适用早期「不引入 jieba」的依赖结论。
6. **测试**：当前全仓基线为 `737 passed / 2 failed / 4 skipped`（2026-11-25，离屏模式）；两处失败均位于 `tests/test_extract_closed_loop.py`，为本次统稿之前既有的缺陷（与文档变更无关，另行跟踪）；历史验收与复盘中的 379/384 均是其当时快照。

下文保留决策形成时的调研与论证；涉及上述六项的旧结论不覆盖本清单。

---

## 0. 阅读方式

1. 先读 §1 结论摘要与 §17 已裁定项 —— 5 分钟判断是否放行。
2. 实现前必读 §4（包与依赖）、§6（四组模型）、§7（数据模型）、§11（GUI 契约）。
3. §5 是 9 项联网调研的结论与来源；每项都附「被否备选 + 否决原因」，便于复核。
4. 规格来源：F:/date/xiaos/docs/proposals/adult-reco（只读参考，本文件不修改其中任何内容）。

---

## 1. 结论摘要

1. **扫描用 os.scandir 单趟 + os.stat 补齐身份**：Windows 上 DirEntry.stat() 的 st_dev/st_ino/st_nlink 恒为 0（本机实测），身份判定必须落到 os.stat(entry.path, follow_symlinks=False)；指纹用 (st_dev, st_ino) 识别「同卷移动/改名」，用 (size, mtime_ns) 识别「内容变化」。
2. **硬链接只在同卷、NTFS、文件（非目录）可用**：跨卷 os.link 抛 OSError errno=18 EXDEV（本机实测 C:→F:，WinError 17）；st_dev 判同卷是本机实测的有效手段（C: 与 F: 的 st_dev 不同）。硬链接删源后仍可读（实测）。落位策略必须有「复制」退路。
3. **存储用 SQLite WAL + append-only 事件表**：读者不阻塞写者、写者不阻塞读者，但同时只有一个写者、不支持网络文件系统、超大事务表现差（SQLite 官方 WAL 文档）。因此本项目采用「单写者队列 + 短事务 + 事件表为唯一真相 + 投影表可重建」。
4. **相似度检索选 TF-IDF（字符 2–3gram）而非句向量**：本机 torch 导入即失败（WinError 126 shm.dll，实测），sentence-transformers 因此不可用；句向量还需要额外下载模型权重（本机对 HuggingFace 不可达，实测）。TF-IDF 依赖的 numpy/scipy/scikit-learn 由 **reco-core 声明并安装**（§12，F1 修订）；F-8 的「已在本机」指 DSH 自带运行时，**不是**本项目 .venv——项目 venv 内这些包均未预装（评审实测），首次安装需网络（pypi 可达），wheel 缓存后可离线重建；运行期离线、可解释、可手算断言。
5. **四组模型必须各自独立成模型与接缝**：关系模型（结构化 relationship_graph）、标签模型（数据驱动的域→轴→值）、打分模型（相似度/分类器/行为信号三路加权合成）、画像模型（权重、样本量、置信度、漂移、版本）。**严禁把四者揉进一个 score() 内部**：score() 只做「读取四组模型的输出并合成」，不允许反向定义任何一组模型的语义（§6.5 给出反例清单）。
6. **CLI 与 GUI 共用一个 rank()**：排序结果逐位一致，且用合成数据的自动化比对守住；GUI 不得有任何第二条打分路径。
7. **GUI 采用 PyQt6 桌面（已裁定，§17 Q2）**：本地自用不分发，GPLv3 无实际合规风险；PyQt6/PySide6 为同一 Qt C++ 内核的绑定、运行时性能等价；GUI 与 Qt 之间只保留一个薄适配层，将来若需闭源分发可只改该层切 PySide6（LGPLv3 可动态链接）。性能 7 坑按 PyQt6 语境逐条落成约束（§11）。
8. **依赖管理用 uv workspace（根 pyproject.toml 已声明 [tool.uv.workspace]）**，同时保留「每包 pip install -e」的独立可安装性（T0 已在 .venv 中验证 7 包全部 editable 安装成功）；uv 目前只存在于 .venv 内（0.13.9），不是系统命令。
9. **测试全部用合成数据 + 注入式时钟/设备号**：跨卷、损坏文件、超大文件、同名不同内容、空目录、无候选都由生成器覆盖；跨卷用「指纹提供者注入」模拟，不依赖真实第二块盘。
10. **性能必须可测量**：RECO_PERF=1 输出各阶段毫秒，RECO_PERF_TABS=1 遍历页面后退出（离屏运行），用于 CI 回归。
11. **GUI 深色主题**：QSS 统一规范（间距/圆角/层次/图标，§11.6，夜间裁定）；美观不得引入启动期阻塞，首屏骨架 <500ms 口径不变（F5）。

---

## 2. 事实基线（本机实测，2026-11-20）

下表全部来自本机探针与仓库现状，**不是推测**。探针临时文件在系统 TEMP 与 reco/_probe*.py，用后即删。

| # | 事实 | 值 / 结论 |
|---|---|---|
| F-1 | 解释器 | C:/Users/Administrator/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe，CPython 3.12.14 |
| F-2 | 项目 venv | reco/.venv，pip 25.0.1、pytest 9.2.0、**uv 0.13.9（装在 venv 内）**、7 包全部 editable |
| F-3 | 系统无 Python / 无系统 uv | 成立；不依赖系统 PATH |
| F-4 | os.stat（Windows, NTFS） | st_dev = 卷序列号（C: 5916619507170670637，F: 13457648007697939719），st_ino = 文件索引（2533274790566880），st_nlink 有效，st_mtime_ns 精度 100 ns（末两位为 0） |
| F-5 | os.DirEntry.stat()（Windows） | st_dev/st_ino/st_nlink **恒为 0**（与 Python 文档一致）→ 身份判定必须 os.stat |
| F-6 | os.link 同卷 | 成功；st_nlink 变 2；两路径 st_ino 相等；删除源路径后读目标正常 |
| F-7 | os.link 跨卷 | 失败：OSError errno=18 (EXDEV)，winerror=17（C:→F:） |
| F-8 | 依赖体量（**DSH 自带运行时**，非本项目 .venv） | numpy 2.5.4 = 32.6 MB；scipy 1.18.1 = 114.5 MB；scikit-learn 1.9.1 = 42.5 MB；jieba 0.42.1 = 43.0 MB。注意：项目 .venv（include-system-site-packages=false）内这些包与 PyQt6 均未预装（评审实测），需按 §12 安装 |
| F-9 | torch | **导入失败**：OSError WinError 126 ... torch/lib/shm.dll；sentence_transformers 随之不可用 |
| F-10 | 网络可达性 | 可达：docs.python.org、peps.python.org、sqlite.org、docs.astral.sh、pypi.org（含 pip index versions）、arxiv.org、scikit-learn.org、learn.microsoft.com、hypothesis/readthedocs；**不可达/被拦**：huggingface.co、github.com、doc.qt.io(403)、qt.io、pythonguis.com |
| F-11 | cncrawler 浏览器兜底 | 本会话内以 Edge 兜底的抓取会以 0xC0000005 退出；直连模式可用。已如实记录，未降级为其它抓取方式 |
| F-12 | 数据根 | F:/date/adult-reco/data 下已有 liked/、candidate/、推荐专区/ 三个空目录 |
| F-13 | T0 现状 | 根 pyproject.toml 含 [tool.uv.workspace] 与 [tool.uv.sources]；7 包各自 pyproject.toml + src/ 布局 + 独立 pytest 配置；scripts/bootstrap.ps1|sh 已就位；.gitignore 已排除 .venv/、data/、*.db、*.db-wal、events.jsonl |
| F-14 | 无正文样本 | 数据根为空，无正文可读；本文件全部结论来自文档、实测与合成推理 |

> 说明：F-6/F-7 的实测探针在系统 TEMP 建临时文件并在结束前删除；仓库内只短暂存在过 _probe*.py 且已删除，不构成项目文件。

---

## 3. 技术栈选型表

| 领域 | 选择 | 理由 | 被否备选 | 否决原因 |
|---|---|---|---|---|
| 语言 / 运行时 | CPython 3.12（DSH 自带解释器）+ 项目 .venv | 唯一可用解释器；3.12 提供 scandir 上下文管理、st_birthtime、pathlib 完善 | 系统 Python / 便携版 | 系统无 Python（商店占位符）；便携版需额外下载且不可控 |
| 目录扫描 | os.scandir + os.stat | 单趟读取目录即可拿到 is_file/is_dir 与 stat；PEP 471 记录 Windows 上约 8–9 倍于 os.walk（后者每项多一次 stat） | os.walk；pathlib.rglob；watchdog 文件监听；全文哈希 | os.walk 每文件 2 次系统调用；rglob 同样逐项 stat；watchdog 需常驻进程且漏掉离线改动，还要新增依赖；全文哈希要读整本正文，成本与隐私边界都不接受 |
| 文件身份 / 移动识别 | st_dev+st_ino（os.stat） | Windows 上 st_ino = 文件索引、st_dev = 卷序列号（实测 F-4）；改名/移动不变 | 仅路径；仅 (size, mtime) | 路径变化即误判为删+增；(size,mtime) 在「同大小同时间改名」时仍误判，且无法区分「移动」与「复制后删源」 |
| 内容变化识别 | (size_bytes, st_mtime_ns) | 100 ns 精度（F-4）；改动必然变 mtime；不读正文 | 内容哈希；逐字节比较 | 需读正文/整文件；大文件代价高 |
| 落位/去重手段 | 硬链接优先，EXDEV 退复制 | 同卷零拷贝、两份目录项共享同一文件；删源仍可读（F-6） | 一律复制；一律移动 | 一律复制浪费磁盘且破坏「同一本书只有一份」；一律移动会让用户原目录失去文件，且「加入候选池」语义应保留来源 |
| 元数据/特征存储 | SQLite（WAL） | 单文件、零服务、事务与索引齐备、Python 标准库内建；WAL 支持读写并发（单写者） | DuckDB；JSON 文件；LMDB；Postgres | DuckDB 写放大与并发模型不适合「小写入 + 频繁读」；JSON 文件无事务/索引/回放；LMDB 需额外依赖且不便 SQL 查询；Postgres 要常驻服务 |
| 事件流 | 同库 append-only 表 + 投影表 | 与特征同库同事务，回放成本低；事件即唯一真相 | 单独 events.jsonl；消息队列 | 跨文件无事务、易与库不一致；MQ 引入服务依赖 |
| 相似度 | TF-IDF + 余弦（scikit-learn） | 依赖声明在 reco-core、运行期离线（首次安装需网络一次，见 §12/F1 修订）；确定性、可解释、可手算断言；<100 篇时无需训练 | 句向量（sentence-transformers）；BM25；字符 Jaccard | 句向量需 torch（本机坏，F-9）+ 模型权重下载（HF 不可达，F-10）；BM25 需分词/词表；Jaccard 丢失权重信息且对长度敏感 |
| 中文特征 | 字符 2–3gram（analyzer=char，ngram_range=(2,3)） | 无需分词器；arXiv 1708.02657 结论：字符级 n-gram 表示在 CJK 上与词级可比，且不依赖完美分词 | jieba 分词 + 词袋；单字；字节 n-gram | 分词需词表与消歧，专有名词/拟声词易切错；单字丢失搭配信息；字节 n-gram 对中文等价于字符但维度更碎 |
| 权重/在线学习 | 规则化平滑权重（样本量 + 交叉验证共同决定） | 小样本下唯一诚实做法；权重连续变化、可回放、可解释 | 端到端训练排序模型；固定常数权重 | 端到端在小样本上过拟合且不可解释；固定常数违背「权重随证据变化」的规格 |
| 时间衰减 | 幂衰减 w = 0.5^(age_days / half_life_days)（= exp(-ln2 · age_days / half_life_days)，F2 统一公式） | 半衰期语义直观、单参数、可逆推；业界 recency 实现采用等价的指数遗忘形式（数值表与验算见 §5.5） | 线性衰减；分段阶梯；无衰减 | 线性在半衰期处仍 50%、窗口外 0，与平滑要求冲突；阶梯会在边界跳变；无衰减违背「旧反馈弱化」 |
| GUI 框架 | **PyQt6**（Qt6 绑定，GPLv3/商业） | 已裁定维持（§17 Q2）；API 与 Qt 文档一致；QThreadPool/QRunnable/model-view 完备 | PySide6（LGPLv3/GPLv2/商业）；PyQt5；Tkinter；Web UI | PySide6 许可更宽松但指令未选；PyQt5 已过维护期；Tkinter 列表性能与高 DPI 不足；Web UI 需额外服务与前端栈，启动开销与「桌面程序」定位不符（详见 §17 Q2，已裁定；许可隔离层见 §11.5） |
| 打包/依赖管理 | uv workspace（根声明）+ 每包 pip install -e 兜底 | 根配置已存在（F-13）；uv 0.13.9 在 venv 内（F-2）；editable 安装已验证 | poetry；hatch env；纯 pip + requirements | poetry 的 workspace 语义与锁文件绑定更强、迁移成本高；hatch 强在构建/环境而非依赖求解；纯 pip 无锁、无统一约束 |
| 测试 | pytest + 自研合成数据生成器（可选 hypothesis 做性质测试） | 生成器可精确构造边界；hypothesis 适合「衰减单调/权重归一」这类性质 | 真实样本夹具；无种子 fuzz | 真实样本明令禁止；无种子 fuzz 不可复现 |
| 性能观测 | RECO_PERF / RECO_PERF_TABS 环境变量 + stderr 毫秒打点 | 规格硬要求；关闭时近零开销 | cProfile 常开；日志框架 | 常开会污染性能；日志框架引入配置与格式化开销 |

---

## 4. 包划分与依赖图

### 4.1 七包与四模型映射

| 包 | 职责 | 承载的模型 | 允许依赖 |
|---|---|---|---|
| reco-core | 共享数据类型、特征规整、**打分模型**、**画像模型** | score model、profile model | 无内部依赖 |
| reco-store | SQLite schema、事件追加、投影重建、迁移 | 四模型的**持久化契约**（不含模型语义） | reco-core |
| reco-labels | 标签体系（数据驱动）、alias 命中、合并/归档/提议 | **tag model** | reco-core、reco-relations |
| reco-relations | 关系图提取、逐字段人工覆盖、关系相似度 | **relation model** | reco-core |
| reco-io | 目录扫描、增量判定、硬链接/复制落位、统计量提取 | 无（纯 IO 与统计） | reco-store、reco-labels、reco-relations |
| reco-cli | 无界面入口、JSON 输出 | 无 | reco-core、reco-store、reco-io（仅公开 API） |
| reco-ui | PyQt6 桌面入口、阅读页 | 无 | reco-core、reco-store、reco-io、reco-labels（仅公开 API） |

### 4.2 依赖方向（单向，无环）

```
                 reco-core   （types / score model / profile model）
                  ↑  ↑  ↑  ↑
     reco-store ──┘  │  │  └── reco-labels    （tag model）
        ↑            │  └───── reco-relations （relation model）
        │            │
     reco-io ────────┘   （依赖 store + labels + relations）
        ↑
   reco-cli   reco-ui     （平级，互不依赖；只接公开 API）
```

> 上图仅示意依赖方向，不是代码。

规则：
- 箭头只向上。reco-core 不知道目录、文件、SQLite、界面的存在。
- reco-cli 与 reco-ui 平级；**二者都必须只调用 reco_core 的 rank()**，不得各自排序。
- 新增依赖前先问：这一层真的需要知道下一层吗？能通过参数传入就不 import。

### 4.3 四模型独立性（本轮新增硬要求）

| 模型 | 独立包/模块 | 输入 | 输出 | 失败模式 | 小样本行为 | 如何被替换 |
|---|---|---|---|---|---|---|
| relation model | reco_relations（独立包） | 文本统计量 + 词典 + 配置 | RelationshipGraph（JSON 可落盘） | 低置信度 → 留空；字段冲突 → 按来源优先级合并 | 无人物/无互动 → topology=unknown，权重 0 | 换实现只改包内公开函数，reco-core/reco-ui 零改动 |
| tag model | reco_labels（独立包） | 文本统计量 + Taxonomy（来自 store） | list[TagHit] + 轴覆盖度 | alias 冲突 → 取最长别名并记 evidence；未知词 → 不臆造 | 未覆盖轴 → 该轴权重 0（不猜默认值） | 同上；标签数据在库里，换匹配器不影响数据 |
| score model | reco_core.score（模块级接缝） | 特征向量 + Profile + 候选集 | 排序 list[(book_id, score)] + 理由 | 单类别/样本不足 → 只走相似度并明示；空候选 → 返回空并报「无候选」 | n_pos<10 → w_sim≈1，w_model=0 | 替换打分实现不改签名；reco-cli/reco-ui 零改动 |
| profile model | reco_core.profile（模块级接缝） | 事件流 + 配置 + 注入时钟 | Profile + 版本 + 置信度 + 漂移 | 事件缺失 → 用已有事件重建并标 partial；半衰期非法 → 回退默认并记事件 | 样本少 → 置信度低、明示「还不够准」 | 同上；版本化 Schema 允许并行新旧画像 |

**额外约束**：四个模型**互不 import 对方实现**，只通过 reco-core 里的不可变数据类型（FeatureVector、Profile、TagHit、RelationshipGraph）通信。关系模型不得读 Profile，标签模型不得读 RelationshipGraph，打分模型不得自己调整画像权重（画像权重由 profile model 产出，score 只读）。

> 若人审认为「模块级接缝」不够硬，可选方案 B：拆成 9 包（新增 reco-score、reco-profile）。**已裁定：保持 7 包（§17 Q4，2026-11-20）**，与 modules.md 一致；拆包作为将来可选项保留。

---

## 5. 九项联网调研

> 工具纪律：搜索一律 searchcli（引擎：bing/sogou/360 + anysearch；**tavily 429 不可用**，如实标注），正文一律 cncrawler grab；未用内置搜索/抓取。每项标注来源的抓取状态：**[抓取]** = 已抓正文并核对；**[搜索]** = 仅得候选与摘要（站点不可达或反爬，如实标注）。Edge 兜底 0xC0000005 已入险 R16（建议项 5）。

### 5.1 目录扫描与增量检测（新增/移动/删除）

**结论**
- 扫描用 os.scandir 单趟递归；entry.is_file()/is_dir() 命中 dirent 类型时零系统调用，仅在 DT_UNKNOWN（部分网络盘）才回退 stat。
- 身份 = (st_dev, st_ino)：改名/移动后不变，因此「老指纹出现在新路径」判为**移动**（保留 book_id 与历史标注），不是「删+增」。
- 内容变化 = (size_bytes, st_mtime_ns) 变化且身份不变；st_mtime_ns 在 NTFS 上 100 ns 精度（F-4）。
- 删除 = 本次快照缺失且上次快照存在；**软删除**（present=0）保留历史，不做物理清理。
- 扫描结果不落「正文」，只落路径、大小、时间、设备号、inode。

**依据**
- [抓取] PEP 471 — os.scandir()：目录迭代系统调用已带回类型信息，os.walk 从约 2N 次系统调用降到 N 次，Windows 上约快 8–9 倍。 <https://peps.python.org/pep-0471/>
- [抓取] Python os 文档：scandir() 与 DirEntry；DirEntry.stat() 在 Windows 上 st_ino/st_dev/st_nlink 恒为 0，需 os.stat()；st_ino 在 Windows 是文件索引。 <https://docs.python.org/3/library/os.html#os.scandir>
- [搜索] 用 inode 识别移动/改名的工程实践（站点不可达，仅摘要） <https://www.pkrc.net/detect-inode-moves.html>
- [抓取·A3' 升级 2026-11-20] watchdog DirectorySnapshot 源码（官方文档站）以 inode 做快照差分，作为「业界怎么判移动」的旁证——cncrawler 直连抓取并核对（st_ino/inode 命中：true），由[搜索]升级为[抓取]。 <https://python-watchdog.readthedocs.io/en/latest/_modules/watchdog/utils/dirsnapshot.html>
- A3' 复核（2026-11-20）：pkrc.net 站点仍不可达，维持[搜索·二手]；「移动判定」的一手支撑已足——Python os 官方文档（st_ino=文件索引，已[抓取]）+ watchdog 源码页（升级[抓取]），结论不变。

**被否备选与原因**
- os.walk / pathlib.rglob：每文件多一次 stat；PEP 471 已量化差距。
- watchdog 常驻监听：跨会话漏事件、需常驻进程、新增依赖；本项目是「启动/刷新时同步」模型，不需要监听。
- 全文哈希做变化判定：要读整本正文，成本与隐私边界都不接受；且收益不抵成本。
- 纯路径集合差分：改名即判删+增，历史标注会丢。

### 5.2 Windows 硬链接

**结论**
- os.link(src, dst) 在 Windows 上自 Python 3.2 起可用；默认 follow_symlinks=False（Windows 默认值）。
- 限制：**仅 NTFS**、**仅文件**（目录不行）、单文件最多 1023 个硬链接、SMB 3.0 持续可用共享不支持创建硬链接、跨卷必然失败。
- 跨卷失败形态在本机实测为 OSError errno=18 EXDEV（winerror=17），可直接按 errno.EXDEV 分支退回复制；也可先用 st_dev 预判同卷（C:/F: 的 st_dev 不同，实测 F-4）避免一次失败。
- 硬链接后删除源路径，目标路径仍可读、st_nlink 回落（实测 F-6）；因此「放进 liked/ 后删掉 candidate/ 里的原件」是安全操作。
- 身份判定注意：DirEntry.stat() 在 Windows 上 st_nlink=0，不能用它判断「是否已有硬链接」。

**依据**
- [抓取] Python os.link 文档：Unix/Windows 可用，3.2 起支持 Windows，Windows 默认不跟随符号链接。 <https://docs.python.org/3/library/os.html#os.link>
- [抓取] Microsoft CreateHardLinkW：仅 NTFS、仅文件、最多 1023 链接、SMB 3.0 持续可用共享不支持、SMB 上目录项属性刷新时机。 <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createhardlinkw>
- [搜索] CPython 对 Windows os.link 的实现与历史讨论（github.com 本机不可达，仅摘要） <https://github.com/python/cpython/issues/53125>
- [抓取·A3' 升级 2026-11-20] bpo-10646：ntpath.samefile 对硬链接判定的缺陷（官方 Python tracker，说明「用路径判同文件」不可靠）——cncrawler 直连抓取并核对（samefile/硬链接命中：true），由[搜索]升级为[抓取]。 <https://bugs.python.org/issue10646>
- A3' 复核（2026-11-20）：CPython issue 53125（github.com 本机不可达）维持[搜索·二手]；硬链接限制一手支撑已足（Microsoft CreateHardLinkW 官方页），结论不变。
- [实测] F-4/F-6/F-7。

**被否备选与原因**
- 一律 shutil.copy2：跨卷可用但双份占用、失去「同一文件」语义。
- 一律 os.rename / shutil.move：保留单一副本但破坏来源目录；若来源是网盘同步目录，移动会触发删除同步。
- os.symlink：Windows 需开发者模式或管理员权限，且目标移动即断链。
- 目录联接（junction）：只能指向目录，不能替代文件级落位。

### 5.3 SQLite schema 与增量更新

**结论**
- **WAL**：读者不阻塞写者、写者不阻塞读者；但**所有进程必须在同一主机**（WAL 不能用在网络文件系统），且**同时只有一个写者**；大事务（>约 100 MB）表现差；默认 WAL 达约 1000 页自动 checkpoint。→ 我们用「单写者连接 + 短事务」，长任务分块提交。
- **索引设计**：按查询路径建索引，避免全表扫描；rowid（INTEGER PRIMARY KEY）天然是聚簇键，热表用它做主键；高频过滤列建二级索引；不做「每列都建索引」。
- **append-only 事件表 + 回放**：事件不可改、不可删；状态 = 事件折叠（fold）；投影（读模型）可整表重建，重建后与线上读模型比对以验一致性。事件表按时间建索引以支持「从某时刻起增量重建」。
- **单写者**：GUI 常驻与 CLI 批处理可能并发。策略：busy_timeout + 写操作串行队列；长读用独立连接（WAL 下读不阻塞写）；禁止在事务里做 IO/特征提取。
- **迁移**：schema_meta.version + 幂等迁移；每版本迁移必须在合成库上跑「升级→校验→回滚演练」。

**依据**
- [抓取] SQLite WAL：同主机限制、网络盘禁用、单写者、1000 页自动 checkpoint、大事务不友好、checkpoint 饥饿。 <https://sqlite.org/wal.html>
- [抓取] SQLite 查询规划：全表扫描 vs rowid 二分 vs 索引查找。 <https://sqlite.org/queryplanner.html>
- [抓取] Event Sourcing with SQLite：append-only 表、不可变历史、投影重建、为回放建索引。 <https://www.sqliteforum.com/p/event-sourcing-with-sqlite>
- [抓取·A3' 升级 2026-11-20] SQLite 官方《Isolation In SQLite》（读快照语义、BEGIN 并发语义）——cncrawler 直连抓取核对，由[搜索]升级为[抓取]。 <https://sqlite.org/isolation.html>
- [抓取·A3' 升级 2026-11-20] SQLite 官方《Wal-Mode Blocking Locks》（WAL 模式下的阻塞锁与超时语义，单写者约束细节）——cncrawler 直连抓取核对，由[搜索]升级为[抓取]。 <https://sqlite.org/src/doc/tip/doc/wal-lock.md>
- A3' 复核（2026-11-20）：WAL 并发/单写者/checkpoint 约束经 sqlite.org 三页（wal.html / isolation.html / wal-lock.md）交叉一致，全部[抓取]；结论不变。

**被否备选与原因**
- 单文件 JSON / events.jsonl：无事务、无索引、崩溃易半写；回放要全量解析。
- DuckDB：分析强但并发写模型与「秒级增量写入」不匹配，且是小样本场景的过度设计。
- 每个模型一个库文件：跨模型事务与回放一致性难保证；单库 + 表隔离已足够。
- 事件表可更新（updated_at 覆盖）：破坏可回放与可追溯，直接否掉。

### 5.4 小样本（<100 篇）相似度检索：TF-IDF vs 句向量

**结论**
- **默认 TF-IDF（字符 2–3gram）+ 余弦**：零训练、确定性、可解释（可列出命中的 n-gram/标签）、可手算断言；<100 篇时 IDF 仍有意义（min_df 要调低，max_features 控维度）。依赖（numpy/scipy/scikit-learn）由 **reco-core 声明**；运行期离线，首次安装需网络（§12，F1 修订）。
- **句向量不是本机可行路线**：torch 导入失败（F-9）、HuggingFace 不可达（F-10），模型权重无法获取；即使能装上，torch 体积与启动开销对「冷启动 <500 ms」也是硬伤。
- 若将来要用句向量：只能作为**可选插件**（reco_core.score 的相似度提供者），并先解决 torch 依赖与权重离线分发；接口不变、默认关闭。
- 精度口径：小样本**不给准确率**；只给排序与「像哪几篇」的理由。分类器仅在正负各 ≥10 且交叉验证后报 AUC 与置信区间。

**依据**
- [抓取] scikit-learn TfidfVectorizer：analyzer={word,char,char_wb}、ngram_range、min_df、max_features、sublinear_tf、norm。 <https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html>
- [搜索] all-MiniLM-L6-v2 模型卡（384 维、约 22.7M 参数、约 90 MB 权重）：HuggingFace 本机不可达，仅取搜索摘要，未当作已核实事实使用。 <https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2>
- [抓取·A3' 升级 2026-11-20] 词嵌入 vs TF-IDF 在文档级任务上的对比（7000 万学术文献语料上 word2vec 与 TF-IDF 表示的质量/效率对比；支撑「换向量不等于更准」，结论不一致）——cncrawler 直连抓取并核对，由[搜索]升级为[抓取]。 <https://arxiv.org/abs/2107.05151>
- [搜索] TF-IDF + 余弦的相关推荐工程实现 <https://osher.com.au/blog/automatically-relate-blogs-tf-idf-cosine-similarity/>
- [搜索] sentence-transformers 效率文档（CPU 推理成本） <https://github.com/huggingface/sentence-transformers/blob/main/docs/sentence_transformer/usage/efficiency.rst>
- [实测] F-8/F-9/F-10。

**被否备选与原因**
- 句向量默认路线：依赖不可得 + 体积 + 不可解释。
- BM25：需要分词与词表；中文无分词器时退化。
- 字符 Jaccard / 编辑距离：丢失词权重，长文敏感，无法解释「哪些片段像」。
- 训练小型分类器（<10 正样本）：必然过拟合，且违背「样本不足只做相似度」的规格。

### 5.5 在线学习与时间衰减

**结论**
- 衰减形式（F2 修订，唯一公式）：**w(age) = 0.5^(age_days / half_life_days) = exp(-ln2 · age_days / half_life_days)**。半衰期处 w=0.5；两个等价写法数值恒等。Aether 来源页的公式行（exp(-age/half)）与其自身数值表相互矛盾——其表在半衰期处为 0.50（本机重抓核实），本文采用与数值表一致且数学自洽的 0.5^(age/half)，不照抄其公式行。
- 半衰期候选与适用场景（数值可直接验算；全部按 w = 0.5^(age/half) 计算，2 位小数为取整显示，F2 修订）：
  - 30 天：7 天 0.85 / 30 天 0.50 / 90 天 0.13 —— 适合「最近在追什么」的行为信号（阅读时长、完读率）。（0.5^(7/30)=0.849、0.5^(90/30)=0.125，表中显示取整值）
  - 90 天：7 天 0.95 / 30 天 0.79 / 90 天 0.50 / 180 天 0.25 —— 适合「显式标注（喜欢/不合口味）」的默认档。（0.5^(7/90)=0.947、0.5^(30/90)=0.794、0.5^(180/90)=0.25）
  - 180 天：90 天 0.71 / 180 天 0.50 / 365 天 0.25 —— 适合「稳定偏好」（题材、关系结构），避免被一两季波动带偏。（0.5^(90/180)=0.707、0.5^(365/180)=0.246）
- 在线更新：事件到达即增量更新画像（不是全量重训）；每条事件带 occurred_at；**effective_weight 不落库（建议项 2 裁定，二选一定死）**：折叠时由 occurred_at + config_fingerprint 对应的当时配置重算，避免同一权重两处真相——回放解释「当时为何是这个权重」由 config_fingerprint 提供。
- 可回放：画像 = 事件流折叠；每次画像变更写 profile_versions，回放可逐版本对比；浮点比较用相对容差 1e-9。
- 防退化：单条反馈影响上限（clip）+ 探索配额（默认 10 留 1–2）；这两条在 profile model 实现，不在 score 里。

**依据**
- [抓取] Aether 文档：recency 公式行（exp(-age/half)）与其 7/30/90 天数值对照表**相互矛盾**——公式在半衰期处应为 e⁻¹≈0.368，其表在半衰期处为 0.50（本机重抓核实，2026-11-20）。本文取与数值表一致且数学自洽的幂形式 w = 0.5^(age/half)（F2 修订），不照抄其公式行。 <https://docs.aetherdb.ai/docs/guides/recency-ranking>
- [抓取·A3' 补强 2026-11-20] arXiv 2412.11127（IEEE TSMC: Systems 2018）：隐半马尔可夫模型刻画用户兴趣状态的**异构持续时长**——不同类型的兴趣自然衰减速度不同（实验显示异构时长建模显著优于固定衰减基线），直接支撑「按信号类型分档半衰期」（行为快变 30 天 / 显式标注 90 天 / 稳定偏好 180 天）。cncrawler 直连抓取并核对。 <https://arxiv.org/abs/2412.11127>
- [搜索] 矩阵分解 + 半衰期衰减模型（指数衰减建模兴趣漂移） <https://ceur-ws.org/Vol-2038/paper1.pdf>
- [搜索] 指数衰减的时间感知推荐（电商场景） <https://thesai.org/Downloads/Volume13No10/Paper_71-Exponential_Decay_Function_Based_Time_Aware_Recommender_System.pdf>
- [搜索] 社交标注中的近期效应研究（说明「近期偏好主导」是真实风险） <https://christophtrattner.com/pubs/recsys2015b.pdf>
- [搜索] 时间衰减权重的通俗定义 <https://inferensys.com/glossary/dynamic-retail-hyper-personalization/sequential-user-behavior-modeling/time-decay-weighting>
- [搜索·A3' 补强 2026-11-20] 非线性遗忘的用户偏好动态建模（MDPI Systems 2025；mdpi.com 本机两次抓取超时，仅摘要） <https://doi.org/10.3390/systems13111034>
- A3' 交叉核验记录（anysearch/bing --save 留痕，2026-11-20 23:41–23:45）：检索命中的推荐系统衰减研究均围绕**衰减形状与参数选择**展开、无不衰减的对照结论，未见与 F2 幂公式冲突的一手来源；IEEE 5582899 命中为 Retraction 通知，弃用不引用。

**被否备选与原因**
- 线性衰减 max(0, 1 - age/window)：半衰期处仍 50%，窗口外直接 0，边界与「平滑」要求冲突。
- 分段阶梯：会在边界跳变，规格明令权重变化要平滑。
- 无衰减：与「旧口味会漂移」矛盾，早期几条标注会永久主导。
- 全量重训：违背「增量更新、不设训练步骤」。

### 5.6 中文文本特征提取（无分词器）

**结论**
- 字符 2–3gram 足够做「相似度 + 风格/机制相关轴」的第一版：arXiv 1708.02657 在 14 个中英日韩数据集、473 个模型上得出「词级 n-gram 线性模型即使没有完美分词也具竞争力」「字符级 n-gram 的 fastText 表现最好（但特征过密会过拟合）」。
- 明确不做：不落任何原文片段、不落 n-gram 的字符串本身（只落数值向量/哈希桶计数），避免特征库成为正文的间接副本。
- 统计口径（全部数值，入库只存数值）：
  - char_count（全文字符数，不含空白）；cjk_char_ratio；
  - paragraph_count：按空行分段；avg_paragraph_chars；
  - dialogue_char_ratio：引号内字符数 / 全文字符数，引号集合覆盖中英文双引号、直角引号，以及行首破折号对白行；
  - dialogue_turn_count：连续对白段切换次数；
  - exclamation_ratio / question_ratio / ellipsis_ratio：标点占比；
  - punct_density、line_len_mean、line_len_p95；
  - char_freq_topk：单字频率 Top-K（只存 hash_slot -> count，K≤512）；
  - repeat_phrase_ratio：高频 2–3gram 的覆盖占比（识别复读/口水文）。
  - 以上全部可由合成数据构造期望值，手算断言。
- 关系与标签需要的「机制词」命中走标签模型 alias（数据驱动），不走硬编码词表。

**依据**
- [抓取] arXiv 1708.02657（Zhang & LeCun）摘要：字符级/字节级编码在 CJK 分类上可比，词级 n-gram 线性模型不依赖完美分词；字符 n-gram 特征过密会过拟合。 <https://arxiv.org/abs/1708.02657>
- [搜索] 无分词亚洲语言文本分类 <https://aclanthology.org/W03-1106.pdf>
- [搜索] 中文 n-gram 表示与特征选择 <https://link.springer.com/article/10.2991/ijcis.2009.2.4.5>
- [搜索] 中文 n-gram 表示对比（JADT 2008，TanCorpV1.0 语料） <http://lexicometrica.univ-paris3.fr/jadt/jadt2008/pdf/wei-chauchat-miao.pdf>
- [抓取] scikit-learn char_wb 与 ngram_range 语义（词边界内字符 n-gram） <https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html>
- [搜索·A3' 补强 2026-11-20] 《Impact of Word Segmentation Errors on Automatic Chinese Text Classification》（IEEE DAS 2012，仅摘要：分词误差显著影响词级模型，字符级特征受影响较小——与 arXiv 1708.02657 方向一致） <https://doi.org/10.1109/das.2012.43>
- [搜索·A3' 补强 2026-11-20] 《Automatic Chinese Text Classification Using Character-Based and Word-Based Features》（IEEE ICDAR 2013，仅摘要；同上方向） <https://doi.org/10.1109/icdar.2013.73>
- A3' 核验说明（2026-11-20）：searchcli find arxiv 垂直源已停用（返回 Atom XML、enabled=false），改走 anysearch 检索 + cncrawler 直连抓取 arxiv.org 页面（照实标注）。

**被否备选与原因**
- jieba 分词：词典与消歧引入不可控误差，专名/拟声/新词切错会污染相似度；且词典 43 MB（F-8），收益不抵。
- 纯单字词袋：丢失搭配（机制词的关键在二字组合）。
- 字节 n-gram：对中文等价于字符，却让维度与哈希更碎，没有额外信息。
- 直接存 n-gram 字符串：会让特征库可还原正文片段，触碰 F2 边界。

### 5.7 桌面 GUI：PyQt6 / PySide6

**结论**
- 许可（**已在 PyPI 页面核实文本**）：
  - PyQt6：「released under the GPL v3 license and under a commercial license」——闭源分发必须买商业许可。
  - PySide6：「Open Source (LGPLv3 or GPLv2 or GPLv3) and commercial license」——LGPL 允许闭源动态链接使用。
  - 本项目维持 PyQt6（已裁定 §17 Q2）：本地自用不分发，GPLv3 无实际合规风险；将来若需闭源分发，经 §11.5 薄适配层切 PySide6。
- 性能：两者都是 Qt6 C++ 的薄绑定，运行时性能差异不是主因；真正的问题是「在 UI 线程做阻塞事」与「错误控件用法」（见 §11）。
- 线程模型：UI 线程只做发起 + 渲染；耗时任务用 QThreadPool + QRunnable（全局池 QThreadPool.globalInstance()，线程自动回收），长生命周期服务用 QThread + moveToThread 的 worker 对象；跨线程只用信号槽（自动排队连接），不共享可变状态。
- 离屏：QT_QPA_PLATFORM=offscreen 必须在**导入 QtWidgets 之前**设置；用于 pytest 与 RECO_PERF_TABS 的全页面遍历；离屏抓 QPixmap 可做截图冒烟。
- 体积：PyQt6 win_amd64 wheel 约 7 MB（运行库 PyQt6-Qt6 另计，PyPI 页面显示整套 release 约 44 MB）；PySide6 主 wheel 约 579 kB（Qt 库在 PySide6-Essentials/Addons）。首次安装都需要网络（本机 pypi 可达，F-10）。

**依据**
- [抓取] PyPI PyQt6：GPL v3 / 商业双授权；wheel 与 release 体积；版本 6.12.1。 <https://pypi.org/project/PyQt6/>
- [抓取] PyPI PySide6：LGPLv3 / GPLv2 / GPLv3 / 商业；版本 6.12.0。 <https://pypi.org/project/PySide6/>
- [抓取·A3' 补强 2026-11-20] Riverbank License FAQ（PyQt 版权方一手）：发行许可与 GPL 不兼容即需商业许可；商业版按开发者数计费、允许二进制/扩展模块形式分发。cncrawler 直连抓取并核对。 <https://riverbankcomputing.com/commercial/license-faq>
- A3' 复核（2026-11-20）：两包许可经 PyPI 页面 + Riverbank 一手 FAQ 交叉一致；searchcli find pypi 垂直源复核包元数据留痕（PyQt6 wheel 系列）。PySide6（Qt 公司）为 LGPLv3，闭源动态链接合规——支撑 §17 Q2 维持 PyQt6（本地自用不分发）的裁定。
- [抓取] QThreadPool/QRunnable 用法：全局实例、任务对象、线程自动回收。 <https://www.pythontutorial.net/pyqt/qthreadpool/>
- [抓取] 离屏渲染与截图冒烟：QT_QPA_PLATFORM=offscreen 必须在导入 Qt 前设置、缺平台插件时的报错形态。 <https://relay.sparkrouter.com/development--and--tooling/headless-pyside6-screenshot-testing-for-qt-apps>
- [搜索] Qt 平台抽象（offscreen 插件属 QPA 平台插件）：doc.qt.io 本机 403，仅取搜索摘要。 <https://doc.qt.io/qt-6/qpa.html>
- [搜索] PyQt6 vs PySide6 许可对比（站点不可达，仅摘要；结论与 PyPI 页面一致） <https://www.pythonguis.com/faq/licensing-differences-between-pyqt6-and-pyside6/>

**被否备选与原因**
- PySide6 直接采用：许可更优，但与用户显式选择的 PyQt6 冲突 → 当初列为确认项上报、未擅自改；现已裁定维持 PyQt6（§17 Q2），PySide6 仅作将来分发场景的切换备选。
- Tkinter/ttk：大数据表性能与高 DPI 支持不足，无法满足「910 行 <50 ms」。
- Electron/Web 前端：多一层运行时与 IPC，冷启动难达 500 ms，且引入 Node 工具链。
- 无 GUI（纯 CLI）：违背用户「日常使用」需求。

### 5.8 monorepo 依赖管理

**结论**
- 采用 **uv workspace**：根 pyproject.toml 的 [tool.uv.workspace] members + [tool.uv.sources] workspace=true 已存在（F-13），包间依赖被解析为工作区内的本地包，uv sync 一次装齐；uv lock 产出跨包统一锁文件。
- 同时保留 **每包独立 pip install -e .** 的可行性：T0 已在 .venv 里验证 7 包全部 editable 安装（reco-core/cli/io/labels/relations/store/ui 均显示 editable location）。这是 modules.md「每包可单独安装/单独测试」的硬证据。
- 环境事实：uv **只在 .venv 内**（0.13.9，由 pip 装进去的），系统 PATH 无 uv。因此命令统一写成 .venv/Scripts/uv.exe（Windows）或 .venv/bin/uv（POSIX），由 scripts/bootstrap.* 负责初始化。
- 离线：uv sync 需要网络取依赖；依赖已在本机缓存或 wheel 已下载时才能离线。项目自身 7 包永远可离线 editable 安装（不依赖网络）。
- poetry / hatch 对比结论：两者都能做「单包构建」，但跨包依赖求解与锁文件的统一性弱于 uv workspace；poetry 的 workspace 支持较新且与自身锁模型耦合；hatch 强项是构建与环境矩阵，不是依赖求解器。

**依据**
- [抓取] uv workspaces 文档：members、workspace sources、单锁文件、uv sync。 <https://docs.astral.sh/uv/concepts/projects/workspaces/>
- [抓取] pip 本地项目安装：pip install -e 的语义与「元信息变化才需重装」。 <https://pip.pypa.io/en/stable/topics/local-project-installs/>
- [搜索] uv workspace 与 editable dependencies 的差异讨论（github.com 本机不可达，二手旁证） <https://github.com/astral-sh/uv/issues/8195>
- A3' 复核（2026-11-20）：uv workspace 语义一手依据 = astral 官方文档（已[抓取]）+ PyPI 包元数据留痕（searchcli find pypi）；打包工具对比类文章全部标二手、仅旁证。
- [搜索] Python 打包工具横评（uv/poetry/hatch/pip） <https://articlevil.substack.com/p/battle-of-python-package-managers>
- [搜索] hatch/poetry/rye 对比 <https://github.com/davidkun/hatch-vs-poetry-vs-rye>
- [实测] F-2/F-13。

**被否备选与原因**
- 纯 pip + requirements.txt：无锁、跨包约束不统一、装 7 个包要手工排序。
- poetry：锁文件与 workspace 语义强绑定，迁移成本高；且系统无 poetry。
- hatch 作为依赖管理：它解决的是构建与环境，不是依赖求解；仍要 pip/uv 兜底。
- 单一 umbrella 包：违背「每包独立安装/独立测试」的硬要求。

### 5.9 合成数据测试策略

**结论**
- 生成器是**唯一**测试数据来源：make_corpus(spec) -> SyntheticCorpus，spec 覆盖目录结构、文件数、大小分布、重复模式、损坏模式、卷/设备号注入、时钟注入、随机种子。固定 seed 保证可复现。
- 必须覆盖的边界（每条都要有独立测试）：空目录；只有 candidate/ 没有 liked/（无正样本）；同名不同内容；同内容不同名（重复书）；跨卷（注入不同 st_dev）；损坏文件（截断 epub/非法编码）；超大文件（>50 MB，走流式统计与超时降级）；无候选（候选池为空 → 明示）；全部被标「不合口味」（负样本饱和）；移动/改名（保留 book_id）；删除后重现。
- 断言纪律：**禁止自洽期望**（不许用实现算出的值反过来断言实现）。相似度、衰减、权重归一、覆盖度都要手算期望值或使用固定黄金值。
- 性质测试（可选 hypothesis）：衰减严格单调递减且 w(0)=1、**w(half_life)=0.5（公式 w = 0.5^(age/half)，F2 修订后自洽）**；权重和为 1 且随样本量变化无跳变（相邻样本量的差 < 阈值）；rank() 对候选顺序置换不变；同一事件流两次回放结果一致（容差内）；人工覆盖永不被自动提取覆盖。
- 逆向验证：每条修复都要「把实现改回缺陷形态 → 测试必须转红」，否则测试无效。
- GUI 测试：QT_QPA_PLATFORM=offscreen + RECO_PERF_TABS=1 全页面遍历不崩；排序一致性测试对比 CLI JSON 与 GUI model 的行序。

**依据**
- [抓取] Hypothesis 快速入门（性质测试、策略、可复现失败用例）。 <https://hypothesis.readthedocs.io/en/latest/quickstart.html>
- [抓取·A3' 升级 2026-11-20] pytest tmp_path 官方文档（每个测试独立临时目录，天然适合生成器落盘）——cncrawler 直连抓取核对，由[搜索]升级为[抓取]。 <https://doc.pytest.org/en/stable/how-to/tmp_path.html>
- [搜索] 用 tmp_path 驱动 hypothesis 的实践 <https://github.com/HypothesisWorks/hypothesis/pull/4050>
- 规格：algorithm.md「禁止用自洽期望当测试」「每条结论必须能在数据里复现」；modules.md「不得使用任何真实作品」。

**被否备选与原因**
- 真实作品夹具：明令禁止（F5）。
- 无种子随机 fuzz：失败不可复现，回归无从下手。
- 只测 happy path：本项目历史教训是「修复前也红」的测试没有区分度，必须覆盖失败路径。
- Mock 掉文件系统：会让扫描/移动/跨卷这些最易错的路径失去覆盖。

---

## 6. 四组模型（各自独立）

> 本轮硬要求：**关系、标签、打分、画像必须分别建模型，各自可独立识别、独立测试、独立替换；严禁揉成一个打分函数。** 本节逐组给出接口签名、输入输出、失败模式、小样本行为、替换方式；§6.5 给出禁止形态。

### 6.1 relation model（reco_relations）

**产出契约**（JSON 落盘，字段自解释）

| 字段 | 类型 | 说明 | 缺失时 |
|---|---|---|---|
| characters[].id | string | 稳定 id（c1…），人工可改 | 必须存在 |
| characters[].role | enum：攻/受/互攻/双性/无明确分工/unknown | 角色分工 | unknown，不猜 |
| characters[].traits | list[string] | 原型/体型/身份（走标签模型别名命中） | 空列表 |
| characters[].age_group | enum：同龄/年上/年下/跨代/unknown | 年龄结构 | unknown |
| edges[].from / edges[].to | string | 人物 id | 必须存在 |
| edges[].type | enum：支配-服从/对等/保护/利用/… | 关系类型 | 空边集 |
| edges[].mechanism | list[string] | 催眠/调教/强迫/交易/自愿…（来自标签 alias） | 空列表 |
| edges[].intensity | float 0–1 | 亲密/支配强度 | null（低置信留空） |
| edges[].consent | enum：自愿/半自愿/非自愿/被迫转化/unknown | 自愿性 | unknown |
| topology | enum：1v1/1vN/NvN/群像/三角/unknown | 关系拓扑 | unknown |
| power_balance | enum：对等/强攻弱受/弱攻强受/双向支配/unknown | 权力结构 | unknown |
| evolution | enum：稳定/单向恶化/双向沉沦/救赎/反转/unknown | 关系演化 | unknown |
| evidence | {confidence: 0–1, source: auto/manual, basis: list[string]} | 识别依据（关键词类名/统计量名，不含原文） | confidence=0 |

**接口签名**
- extract_relation_graph(stats, taxonomy, config) -> RelationshipGraph
- merge_manual_overrides(auto_graph, manual_fields) -> RelationshipGraph（manual 优先，逐字段）
- relation_axes(graph) -> dict[str, float|str]（把图谱摊平成可比对的轴值）
- relation_similarity(graph_a, graph_b, axis_weights) -> float
- validate_relation_graph(graph) -> list[Finding]（枚举越界、悬空边、强度越界）
- explain_relation(graph, axis) -> list[Evidence]

**失败模式与行为**
- 置信度低于阈值（默认 0.35）：该字段**留空**，不填猜测值（「空值可补，错值会误导」）。
- 自动与人工冲突：逐字段比较 source，manual 永不覆盖；合并结果记 changed_by。
- 无人物/无互动：topology=unknown，且该轴权重 0（不参与打分）。
- 人物重名/指代不清：保留多个候选为 unknown，不合并。

**小样本行为**：liked/ 只有 1–2 篇时，只输出「这篇自己」的图谱并标 confidence；不做跨篇统计。

**替换方式**：替换 extract_relation_graph 的实现（规则引擎/LLM/外部服务）即可，输出契约不变；reco-io 与 reco-ui 零改动。

### 6.2 tag model（reco_labels）

**数据契约**（标签是数据不是代码常量；表是种子不是封闭清单）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | domain.axis.value（如 mech.hypnosis） |
| axis | string | 轴名（topology/power/consent/evolution/role/body/archetype/age/species/mechanism/coercion/transformation/body_part/setting/explicitness/pov/length/style/pace/ending/tone/emotion_arc/language/completion/quality_flag） |
| label | string | 展示名 |
| aliases | list[string] | 机器识别用；用户改 label 不影响历史 |
| parent | string/null | 域/轴父节点（D1…D6） |
| status | enum：active/merged/archived | merged/archived 保留历史标注 |
| created_by | enum：user/auto | 自动发现只提议，须用户确认 |
| created_at | date | 审计 |

**接口签名**
- load_taxonomy(store) -> Taxonomy
- normalize_tag(raw, taxonomy) -> TagId | None（只认 alias 命中，不模糊猜）
- match_tags(stats, taxonomy) -> list[TagHit]（TagHit = {tag_id, confidence, basis}）
- axis_vector(tag_hits, axis_weights) -> dict[str, float]
- axis_coverage(tag_hits_by_book, axis) -> Coverage（已覆盖样本数/总数）
- merge_tags(store, source_tag, target_tag) -> MergeResult（历史标注改指向，source 标 merged）
- archive_tag(store, tag_id) -> None（不硬删）
- propose_tags(store, cooccurrence, threshold) -> list[TagProposal]（只提议，不启用）
- explain_tag_hit(hit) -> list[Evidence]

**失败模式与行为**
- alias 冲突（两个标签同别名）：取 status=active 且别名最长者，写冲突事件待人工裁定。
- 未知词/新机制：不创建新标签（避免噪音），进 proposals 队列。
- 某轴无任何标注：该轴权重 **0**，不猜默认值；画像页显示「已覆盖样本数」。
- 合并后旧 id 仍可解析（merged_into 映射），历史标注不断链。

**小样本行为**：每轴覆盖 1 篇也照实显示「1/N，信息不足」，不放大权重。

**替换方式**：换成词典命中 / 正则 / 相近度匹配，只需满足 match_tags 的出参契约；标签数据不动。

### 6.3 score model（reco_core.score）

**合成公式**（权重由 profile model 给，score 不自己调）

score = w_sim × 相似度 + w_model × 分类器输出 + w_behavior × 行为信号

**接口签名**
- similarity_scores(candidate_vectors, positive_vectors, config) -> list[tuple[book_id, sim]]（positive_vectors 取 liked 正样本的向量，见上文「当前实现口径」第 2 条）
- weight_schedule(n_pos, n_neg, cv_auc, config) -> WeightSet（平滑连续，相邻样本量差有上限）
- score(candidate_features, profile, config, now) -> list[tuple[book_id, score]]（**唯一**打分入口，纯函数）
- rank(candidate_features, profile, config, now) -> list[RankRow]（RankRow = {book_id, score, rank, reasons, is_exploration}；CLI/GUI 唯一排序来源）
- explain(candidate_features, profile, book_id) -> list[Reason]（命中轴/相似样本 id/权重贡献）
- train_pairwise(labeled_features, config) -> ModelHandle | None（仅 n_pos≥10 且 n_neg≥10 时返回句柄，否则 None）
- evaluate_pairwise(samples, config) -> EvalResult（auc, ci_low, ci_high, folds, n_pos, n_neg）
- similarity_provider（可注入：默认 TF-IDF；句向量只能作为外部插件）

**硬性行为**
- n_pos < 10：w_model = 0；**不声称准确率**，只输出「像哪几篇 → 所以推这篇」。
- n_pos ≥ 10 且 n_neg ≥ 10：允许分类器；必须成对比较（正 vs 负）、交叉验证、报 AUC 与 95% 置信区间；置信区间跨 0.5 时明示「尚不显著」。
- 单类别（只有正或只有负）：**禁止**返回 0.5、禁止伪造 AUC；返回「不足以判定」并降级到相似度。
- 空候选：返回空列表 + 明确状态，不返回「成功但空」的假结果。
- 延迟导入（F5）：score 模块顶层禁止 import sklearn；similarity_scores/train_pairwise 首次调用时才加载，GUI 首屏骨架不触发打分（支撑 §11.3 冷启动口径）。
- 排序确定性：同一输入同一配置，输出顺序稳定（同分用 book_id 作稳定次序）。
- 探索位（已裁定 §17 Q7）：默认 **2 条** is_exploration=true（跨出舒适区），探索结果同样进训练。**选取必须确定性（建议项 3）**：按候选集指纹（排序后 book_id 序列哈希）+ 固定次序（同分按 book_id 升序）选位，同输入必得同探索位——否则 §10 的 CLI/GUI 逐位一致不成立。

**失败模式**
- 特征缺失（某书无向量）：跳过并计入 skipped，不静默当 0 分。
- 模型文件缺失/损坏：降级到相似度，日志只记版本号与原因。
- 权重不归一：每次合成前断言 |w_sim+w_model+w_behavior - 1| < 1e-6，否则报错而非继续。

**替换方式**：替换 similarity_scores / train_pairwise 的实现，rank 与 score 的签名不变；reco-cli/reco-ui 不感知。

### 6.4 profile model（reco_core.profile）

**产出契约**

| 字段 | 说明 |
|---|---|
| axis_weights | 每轴权重（0–1） |
| sample_counts | 正/负/行为信号各自样本量与每轴覆盖数 |
| confidence | 0–1；样本少时低，并在 UI 明说「还不够准」 |
| top_tags | Top-N 标签（标签 id + 权重 + 覆盖样本数） |
| trends | 各轴趋势（上升/下降/稳定 + 幅度） |
| updated_at / version | 版本号递增，可对比历史 |
| weights | w_sim/w_model/w_behavior 与变化原因（样本量/cv_auc/时间衰减） |
| half_life_days | 生效的半衰期（按信号类型分档时给出映射） |

**接口签名**
- build_profile(events, config, now) -> Profile
- update_profile(profile, event, config, now) -> Profile（增量；单事件影响上限 clip）
- decay_weight(age_days, half_life_days) -> float（实现契约：w = 0.5^(age_days/half_life_days) = exp(-ln2·age_days/half_life_days)，与 §5.5 同一公式，F2 修订；黄金值按此手算）
- profile_confidence(profile) -> Confidence（样本量 + 覆盖度 + 一致性）
- axis_trends(profile_history) -> list[AxisTrend]
- export_profile(profile) -> dict（JSON；可导出给用户看「依据什么在推荐」）
- verify_replay(events, stored_profile_version, config) -> ReplayReport（容差内一致）
- merge_signal(event, config) -> list[SignalEffect]（行为信号折算软标签，显式标注优先）

**失败模式与行为**
- 事件流缺口（如手工删表）：只用现存事件重建，Profile.completeness 标 partial 并在画像页提示。
- 旧事件缺 effective_weight（迁移前）：按当前配置补算并标注「重算」。
- 半衰期配置非法（≤0）：拒绝该配置，回退默认并记事件。
- 数值爆炸（极高样本量）：权重用相对归一，避免 0/1 饱和。

**小样本行为**：n_pos < 10 时 confidence 上限压到 0.3，并显示「样本 n 篇，仅供参考」；不展示 AUC。

**替换方式**：画像实现（如贝叶斯平滑/分层先验）只改 build_profile/update_profile，Profile 契约与版本化 Schema 不变；score 只读契约。

### 6.5 四模型隔离的反例（禁止形态）

以下写法一律 **reject**（评审时逐条检查）：

1. 在 score() 内部读事件流、算衰减、改权重 —— 这是把 profile model 塞进 score。
2. 在 score() 内部解析标签词典、匹配机制词 —— 这是把 tag model 塞进 score。
3. 在 score() 内部推断人物关系（拓扑/权力/自愿性） —— 这是把 relation model 塞进 score。
4. 打分函数直接返回「关系相似度 + 标签相似度 + TF-IDF」的单一 hardcode 常数和，且无权重来源与版本 —— 违反「权重随证据变化、可回放」。
5. 画像模型读取 RelationshipGraph 或标签匹配中间态（应只读已落库的数值化结果）。
6. CLI 与 GUI 各自实现一份排序或各自调一套权重 —— 违反排序一致性。
7. 关系/标签/打分/画像任一模型把原文片段写进日志、事件、报告或异常信息。

**评审动作**：对每个模型做「换实现演练」——把该模型换成桩实现（返回固定值），其它三个模型与两个入口必须零改动仍能跑；做不到即说明有隐式耦合。

---

## 7. 数据模型（SQLite；只存数值与标签，绝不存正文）

### 7.1 库级设置

- journal_mode=WAL；synchronous=NORMAL；busy_timeout=5000；foreign_keys=ON；默认 autocheckpoint（1000 页）。
- 写连接**唯一**（单写者队列）；读可用独立连接；事务短小，特征提取在事务外。
- 事件表只允许 INSERT；可选「触发器拒绝 UPDATE/DELETE」作为硬保险。
- schema_meta(key, value) 存 schema_version、created_at、app_version。

### 7.2 表与字段

**volumes**（卷注册）

| 字段 | 类型 | 约束/索引 | 说明 |
|---|---|---|---|
| volume_id | INTEGER PK | — | 自增 |
| st_dev | INTEGER | UNIQUE | 卷序列号（C:/F: 不同，实测） |
| mount_hint | TEXT | — | 仅提示（如 F:），不参与判定 |
| first_seen_at | TEXT(ISO8601) | — | — |

**files**（文件快照；每路径一行当前态）

| 字段 | 类型 | 约束/索引 | 说明 |
|---|---|---|---|
| file_id | INTEGER PK | — | — |
| volume_id | INTEGER | FK→volumes | — |
| st_dev | INTEGER | — | 冗余便于查询 |
| st_ino | INTEGER | — | 文件索引 |
| path | TEXT | UNIQUE | 绝对路径 |
| zone | TEXT | CHECK(liked/candidate/推荐专区/quarantine/unknown) | 专区 |
| size_bytes | INTEGER | INDEX | — |
| mtime_ns | INTEGER | — | 内容变化判定 |
| ext | TEXT | — | .txt/.epub/.md |
| book_id | INTEGER | FK→books, INDEX | 同一本书的多个文件共享 |
| present | INTEGER | INDEX(zone, present) | 1 在盘 / 0 已消失（软删除） |
| first_seen_at / last_seen_at | TEXT | — | 审计 |
| moved_from_path | TEXT NULL | — | 最近一次移动来源（审计） |

索引：UNIQUE(volume_id, st_ino) WHERE present=1（部分唯一索引，允许历史行保留）、INDEX(zone, present)、INDEX(book_id)。

**books**（书级身份；去重单位）

| 字段 | 类型 | 约束/索引 | 说明 |
|---|---|---|---|
| book_id | INTEGER PK | — | — |
| display_name | TEXT | — | 文件名（书名可出现，正文不行，F3） |
| size_bytes | INTEGER | INDEX | 去重线索之一 |
| ext | TEXT | — | — |
| dedup_key | TEXT | UNIQUE | (size_bytes, 规范化文件名) 的哈希；**同名且同大小才判重**（F3 裁定）；规范化规则见 §9；不读正文 |
| status | TEXT | — | active/duplicate/quarantined |
| first_seen_at | TEXT | — | — |

**features**（统计量与标签命中；全部数值/枚举）

| 字段 | 类型 | 约束/索引 | 说明 |
|---|---|---|---|
| book_id | INTEGER PK/FK | — | 一本书一份当前特征 |
| extractor_version | TEXT | — | 规则版本，升级可重算 |
| stats_json | TEXT | — | §5.6 全部统计口径（数值） |
| char_freq_hash | BLOB | — | 单字频率哈希桶（K≤512） |
| ngram_hash_digest | BLOB | — | 2–3gram 哈希桶（数值向量，不可还原原文） |
| computed_at | TEXT | INDEX | — |
| source_mtime_ns | INTEGER | — | 与 files 对齐，判断是否需重算 |

**feature_vectors**（生产为字符 n-gram 哈希桶计数向量；core 另有可选 TF-IDF 实现）

| 字段 | 类型 | 说明 |
|---|---|---|
| book_id | INTEGER PK | — |
| vector_blob | BLOB | 稀疏 (index, weight) 序列，数值 |
| dim | INTEGER | 维度 |
| idf_version | TEXT | 历史列名；当前生产值 `reco-ngram-counts/1` 表示无 IDF |
| norm | REAL | L2 范数（余弦用） |

**tags / tag_aliases / book_tags**

| 表 | 字段 | 约束/索引 |
|---|---|---|
| tags | tag_id TEXT PK, axis TEXT, label TEXT, parent TEXT NULL, status TEXT, merged_into TEXT NULL, created_by TEXT, created_at TEXT | INDEX(axis, status)、INDEX(parent) |
| tag_aliases | alias TEXT PK, tag_id TEXT FK, source TEXT | INDEX(tag_id) |
| book_tags | book_id INT, tag_id TEXT, source TEXT(auto/manual/user), confidence REAL, basis_json TEXT, updated_at TEXT, PRIMARY KEY(book_id, tag_id) | INDEX(tag_id)、INDEX(book_id, source) |

规则：source=manual 的行自动提取永不覆盖（写入层拒绝，非仅靠调用方自觉）；status=merged/archived 的标签不参与打分但仍保留标注。

**relation_graphs / relation_fields**

| 表 | 字段 | 约束/索引 |
|---|---|---|
| relation_graphs | book_id PK, graph_json TEXT, extractor_version TEXT, confidence REAL, updated_at TEXT | INDEX(extractor_version) |
| relation_fields | book_id INT, field_path TEXT, value_json TEXT, source TEXT(auto/manual), confidence REAL, basis_json TEXT, updated_at TEXT, PK(book_id, field_path) | INDEX(book_id, source) |

规则：relation_fields 是逐字段覆盖的真相；graph_json 是合并后的可读快照（可重建）。

**signals**（行为信号，数值）

| 字段 | 类型 | 说明 |
|---|---|---|
| signal_id | INTEGER PK | — |
| book_id | INTEGER FK | — |
| kind | TEXT | dwell/read_time/completion/reopen/abandon/favorite/dislike/search |
| value | REAL | 秒数/比例/次数 |
| occurred_at | TEXT | 注入时钟写入 |
| source | TEXT | cli/gui/import |
| session_id | TEXT | 匿名会话 id（不存设备信息） |

索引：INDEX(book_id, occurred_at)、INDEX(kind, occurred_at)。

**events**（append-only 唯一真相）

| 字段 | 类型 | 说明 |
|---|---|---|
| event_id | INTEGER PK AUTOINCREMENT | 顺序即因果顺序 |
| event_type | TEXT | scan_started/completed、file_added/moved/removed/fingerprint_changed、tag_added/merged/archived、tag_hit、relation_updated/manual_set、label_set/label_cleared/label_moved、signal_recorded、profile_updated、rec_generated、rec_feedback、eval_run、perf_sample |
| book_id | INTEGER NULL | 无书的系统事件为空 |
| payload_json | TEXT | 事件私有字段（数值/标签/路径/原因；**不含 effective_weight**——由 occurred_at + config_fingerprint 折叠时重算，建议项 2 裁定） |
| occurred_at | TEXT | 业务时间（回放基准） |
| recorded_at | TEXT | 落库时间（审计） |
| actor | TEXT | cli/gui/user/system |
| schema_version | TEXT | 事件载荷版本 |
| config_fingerprint | TEXT | 当时的半衰期/阈值配置指纹（回放可复现） |

索引：INDEX(occurred_at)、INDEX(event_type, occurred_at)、INDEX(book_id, occurred_at)。

**profile_versions**（画像版本；回放校验基准）

| 字段 | 类型 | 说明 |
|---|---|---|
| version_id | INTEGER PK | 递增 |
| created_at | TEXT | — |
| axis_weights_json | TEXT | 数值 |
| sample_counts_json | TEXT | 数值 |
| confidence | REAL | — |
| top_tags_json | TEXT | 标签 id + 数值 |
| weights_json | TEXT | w_sim/w_model/w_behavior + 原因 |
| half_life_days_json | TEXT | 分档配置 |
| reason | TEXT | feedback/like/dislike/import/scheduled/eval |
| event_id_high_water | INTEGER | 折叠到的事件上界（回放锚点） |

**rec_runs / recommendations**

| 表 | 字段 |
|---|---|
| rec_runs | run_id PK, created_at, actor, profile_version, candidate_count, exploration_count, config_fingerprint |
| recommendations | run_id, book_id, rank, score, reasons_json, is_exploration, PRIMARY KEY(run_id, book_id)；INDEX(run_id, rank) |

**evaluation_runs**（只在样本足够时写入）

| 字段 | 类型 |
|---|---|
| eval_id PK, created_at, method, n_pos, n_neg, folds, auc, ci_low, ci_high, notes |

**proposals**（自动发现只提议）

| 字段 | 说明 |
|---|---|
| proposal_id PK, kind(tag/alias/relation), payload_json, evidence_json, status(pending/accepted/rejected), created_at |

**ui_usage**（GUI 用量统计；**性能数据，不进口味事件流**，§11.7）

| 字段 | 类型 | 说明 |
|---|---|---|
| page_id | TEXT PK | 页面/功能区 id |
| uses | INTEGER | 累计使用次数 |
| last_used | TEXT | 最近使用时间（ISO8601） |

规则：该表不属于口味事件流——画像回放、打分、推荐语义完全忽略它；仅用于热集判定与预热，与 signals 表严格分离（防「点得多=喜欢」语义污染）。

### 7.3 回放与一致性

- 回放路径：read_events(since=0) → build_profile → 与 profile_versions 最新版比对（axis_weights 相对误差 < 1e-9；集合类逐项相等）。
- 投影重建：files/books/book_tags/relation_fields 全部可由事件重建；重建后与线上投影做行级比对，差异即 bug。
- 高水位线：profile_versions.event_id_high_water 让增量更新可断点续算。
- 只读入口（CLI report/eval/explain/replay）绝不写库。

---

## 8. 扫描与增量同步（实现路径）

> 本节编号流程为契约描述、仅示意（建议项 6）：表达先后关系与不变量，不是代码；实现细节（函数名/参数）以 §6 接口签名为准。

1. ensure_zones(root)：确认 liked/、candidate/、推荐专区/ 存在且可读；缺则报告不自动创建（避免猜用户意图）。
2. scan_zones(root, zones, config)：递归 os.scandir，跳过隐藏/系统项；只收 .txt/.epub/.md；每个文件取 os.stat（不用 DirEntry.stat 的身份字段）→ FileFingerprint = {st_dev, st_ino, size_bytes, mtime_ns, path, zone}。
3. classify_delta(previous, current)：
   - 身份命中且路径变 → **移动**（保留 book_id、标注、特征，只更新路径与 moved_from_path）；
   - 身份命中且 (size,mtime_ns) 变 → **内容变化**（重算特征，保留标注）；
   - 仅当前有 → **新增**；
   - 仅上次有 → **删除**（present=0，软删）。
4. append_event：每类变化写一条事件（file_added/moved/removed/fingerprint_changed），事务内批量写、短事务。
5. 特征提取：对「新增/变化」的书在**事务外**计算统计量与标签命中，再一次性 upsert；失败（损坏/超大超时）标 quarantined 并记事件，不阻塞整轮。
6. 画像更新：update_profile 折叠新事件（含 signal_recorded），写 profile_versions。
7. 推荐生成：rank(...) → 写 rec_runs/recommendations；GUI/CLI 均读同一 run_id。

**超大文件**：>50 MB 走流式分块统计（按字节窗口解码），单文件预算 30 s；超时标 pending 并在下次扫描重试；不阻塞 UI（后台任务）。

**损坏文件**：解压/解码失败只记 quarantined + 错误类别（不记正文、不记异常栈里的内容片段）。

---

## 9. 落位与硬链接策略

- place_by_hardlink(src, dst_dir, policy) -> PlacementResult（以下编号为契约步骤、仅示意，建议项 6；实现以签名契约与 §5.2 依据为准）：
  1. 预判同卷：os.stat(src).st_dev == os.stat(dst_dir).st_dev（本机 F-4 实测有效）；不同卷直接走复制，**不制造一次失败**。
  2. 同卷：os.link(src, dst)；捕获 OSError：
     - errno.EXDEV（跨卷，实测 F-7）→ 复制；
     - errno.EPERM/EACCES → 报告权限问题，不静默降级；
     - 目标已存在 → 用指纹判等：等则跳过，不等则改名去冲突。
  3. 复制路径：shutil.copy2 + 复制后校验 (size, mtime_ns)；结果标 placement_mode=copy。
- 「标为喜欢」/「加入候选池」的语义：**默认硬链接落位，源文件保留**（用户可随时清理来源目录）；界面明示「已硬链接（同卷）/已复制（跨卷）」。
- st_nlink 可作为「是否已落位」的旁证，但**不能**作为唯一判据（Windows DirEntry.stat 为 0，实测 F-5；复制模式下 nlink 恒 1）。
- 去重只认 books.dedup_key，判定口径（F3 裁定，全文唯一口径）：**同名（规范化后相等）且同大小（字节级相等）才判重**；「同名不同大小」不算重（同名不同内容用例守住）；「同内容不同名」不可能被 dedup_key 命中 → 进入**人工合并清单**（proposals 表，只提示不自动合并）；内容哈希维持被否（不读正文）；跨格式同书（.txt 与 .epub）视为两本，留待人工合并（§17 Q6）。
- normalized_name 规范化规则（建议项 4，大小写与空白三类是判重必需，其余只影响显示名）：① Unicode NFKC 归一；② 大小写折叠（casefold）；③ 连续空白折叠为单空格、去首尾空白；④ 全角/半角统一到半角；⑤ **不改**括号、扩展名——二者保留参与比较；规则版本写入 extractor_version 一并升级。

---

## 10. CLI / GUI 双入口与排序一致性

- 唯一排序入口：reco_core.score.rank(candidate_features, profile, config, now) -> list[RankRow]。
- CLI：reco recommend --limit N --json 输出 RankRow 序列（含 reasons、is_exploration）。
- GUI：表格模型逐行填 RankRow，**不得重排、不得补算分**；is_exploration 行加视觉标记。
- 一致性契约（自动化测试）：
  1. 同一合成数据 + 同一 now + 同一配置 → CLI JSON 的 (book_id, rank, score, reasons) 与 GUI 表格逐位相等（score 比较用 1e-9 容差）。
  2. 置换候选输入顺序 → 排序不变。
  3. RECO_PERF_TABS=1 遍历所有页面后导出的表格快照与 CLI 快照比对。
- 禁止：GUI 侧「为了方便」做二次排序、二次过滤或本地缓存旧 run_id 结果而不标注。

---

## 11. GUI 架构（PyQt6）

### 11.1 线程与进程模型

- 主线程：只做「发起任务 + 渲染」。**无同步 IO、无同步 DB、无 processEvents 轮询等待**。
- 短任务：QThreadPool.globalInstance().start(worker)，worker 为 QRunnable 子类，结果通过信号回主线程（队列连接）。
- 长服务（扫描器/特征提取器）：worker QObject + moveToThread(QThread)；线程生命周期与窗口一致；退出时 quit + wait 有上限。
- 跨线程只传不可变数据（RankRow、统计量 dict）；不共享可变模型实例。
- 取消：每个任务带 cancel_token，在**循环体内**检查（不是入口检查一次）；UI 侧用「超时 + 直接丢弃结果」兜住不可中断的同步调用。
- 单写者：GUI 的 DB 写操作走独立写线程队列（与 CLI 的写锁互斥），读走独立连接。

### 11.2 性能 7 坑（PyQt6 语境逐条落地）

| # | 原坑 | PyQt6 语境约束 |
|---|---|---|
| 1 | 超时设成「宁可等很久」 | 本地 IO 也要分级预算：扫描探测 ≤2 s、单文件特征 ≤30 s、整轮同步 ≤120 s；超时即报「暂不可用」并允许重试；UI 路径**永不**无限等待 |
| 2 | UI 线程做阻塞操作 | 扫描/特征/DB 写入全部进 QThreadPool/worker 线程；主线程禁止批量 os.stat 循环、禁止同步 sqlite3 查询、禁止 sleep 等待 |
| 3 | 启动一次性加载所有页面 | 只加载当前页；QTimer.singleShot(150, warm) 后台预热其余页；预热可被用户操作打断；**预热成功后才置 loaded 标志**（失败不永久空白） |
| 4 | 列表控件隐藏开销 | 用 QTableView + 自定义 QAbstractTableModel（不用 QTableWidget）；批量 dataChanged 一次发；固定行高（verticalHeader 默认尺寸）；列宽按前 100 行采样后设定，**不用 resizeColumnsToContents() 全量测量**；关闭逐单元格 setStyleSheet |
| 5 | 按字符数切分富文本 | 阅读/预览用整章交给 QTextBrowser 或 QTextDocument 原生滚动，不手工算断点；进度用滚动条 value()/maximum()；禁止对 HTML 做前缀切片 |
| 6 | 重复请求同一份数据 | 维护「在途任务表」（key = 资源 id + 参数指纹），并发请求共享同一 worker 与结果；完成后从表移除 |
| 7 | 菜单/对话框双重释放 | deleteLater() + 幂等 flag；closed 与 catch 分支只释放一次；QMenu 用完即弃；QTimer.stop() 后再 deleteLater |

### 11.3 性能打点（必做）

- RECO_PERF=1：每个可疑路径用 perf_span(name) 包住，作用域结束向 stderr 打毫秒；关闭时仅一次布尔判断。
- RECO_PERF_TABS=1：自动遍历所有页面（每页渲染一次、记录首帧与刷新耗时）后退出，供 CI 与人工对比；与 QT_QPA_PLATFORM=offscreen 组合可在无显示器环境跑。
- 输出格式固定（phase=<name> ms=<float>），便于脚本化 diff；不打路径以外的隐私信息。
- 自检门槛（完工前跑）：**冷启动口径 = 首屏骨架可交互 <500 ms**（F5 修订：骨架可交互指窗口/空表/导航可用，不含后台 worker 填充数据的时间）；切页命中预热 <100 ms；≥100 行列表刷新 <50 ms；UI 线程无同步 IO。
- 延迟导入约束（F5 新增）：sklearn（连带 numpy/scipy）实测模块导入墙钟约 1432 ms（评审实测），**score/rank 相关路径首次使用才 import sklearn**（GUI 首屏不触发打分——列表先空、后台 worker 完成后填充；CLI recommend 首用即导入）；禁止模块顶层 import sklearn。

### 11.4 离屏与测试

- 测试入口**在导入 Qt 前**设置 QT_QPA_PLATFORM=offscreen；缺平台插件时明确报错（不静默跳过）。
- 离屏冒烟：构造主窗口 → grab() 到 QPixmap → 存图与基线比对（尺寸/关键像素），不必逐像素。
- GUI 测试不得依赖真实显示器；CI 与本地同一路径。

### 11.5 许可隔离（为 Q2 留退路）

- 所有 PyQt6 导入集中在 reco_ui.qt 一个薄模块（QtWidgets/QtCore/QtGui 的再导出 + 少量适配），其余代码不直接 import PyQt6。
- 若人审改判 PySide6：只改该模块（pyproject 依赖与 import 名），业务/模型代码零改动；信号槽与枚举差异在该模块内吸收。
- 安装失败退路（F4 修订）：PyQt6 或其 Qt6 运行库装不上时，**GUI 停用、CLI 全功能可用**（CLI 覆盖扫描/推荐/反馈/画像/回放/评估，正式验收判据写入 §16 T10/T11）；PySide6 备选同样需要联网安装、不构成离线兜底。

### 11.6 UI 美观约束（2026-11-20 夜间裁定，用户新要求）

- **QSS 统一深色主题**：全部控件走一份集中式 QSS（深色底、统一前景/强调色阶），禁止散落 setStyleSheet（与 §11.2 坑 4 一致）。
- **间距规范**：统一 4/8/12/16 px 栅格；卡片内边距 12 px，区块间距 16 px。
- **圆角**：按钮/卡片/输入框统一 6 px（QSS border-radius），表格容器 8 px。
- **层次**：背景（最底）→ 卡片 → 悬停/选中 → 强调色，四层色阶固定，禁止临时取色。
- **图标**：统一来源与尺寸（单色 16/20 px 两档，与主题色同步），不用系统随机图标。
- **美观不阻塞启动**：主题 QSS 随首屏骨架一次应用；首屏骨架 <500ms 口径不变（F5），不得为样式引入启动期同步 IO 或字体/图标网络加载。
- **验收**：进 §16 T9 验收——offscreen 截图冒烟 + 「无散落 setStyleSheet」检查 + 深色主题色板抽查。

### 11.7 自适应热加载（2026-11-20 夜间裁定并入 t7，与 §11.3 按需加载衔接）

- **用量监测（性能数据，非口味数据）**：GUI 记录每个页面/功能区的使用计数（page_id, uses, last_used），存 SQLite 独立表 `ui_usage`（schema 见 §7 附注）。
- **严格分离**：`ui_usage` **不属于口味事件流**——画像回放、打分、推荐语义完全忽略它；「点得多」只说明「用得勤」，不等于「喜欢」。口味只来自 signals/显式标注，防止行为信号被性能数据语义污染。
- **热集判定**：uses ≥ 3（阈值可配）或 Top-K 高频区进入热集；**推荐专区主面板恒热**（不进配置，不可关闭）。
- **执行时机**：首帧骨架渲染完成后的空闲档（约 T+1.5s）由后台线程预热热集区域；用户操作可打断、不抢首帧；「已加载」标记必须在预热**成功之后**置位（§11.2 坑 3 原文约束：失败不永久空白，可重试）。
- **冷区**：照旧按需加载（§11.3）；预热失败自动回退按需路径，不影响功能可用性。
- **配置**：阈值 / Top-K 进 config；冷区清单由热集判定动态得出，不手工维护。
- **打点**：RECO_PERF 增加 preload span（phase=preload.<page_id> ms=<float>）；**冷启动首屏骨架 <500ms 口径不变（F5），预热时间不计入首屏**。
- **验收**：见 §16 T12b（工单已建）。

---

## 12. 依赖管理与构建

- 根 `pyproject.toml` 声明 7 包 workspace、workspace sources 与 dev 依赖；根 `uv.lock` 是安装版本的唯一锁定来源。
- 命令（Windows）：`.venv/Scripts/uv.exe sync --frozen --inexact` 安装；`.venv/Scripts/uv.exe lock` 在依赖声明变化后更新锁文件。`scripts/bootstrap.ps1|sh` 使用同一同步命令。
- 直接依赖归属：numpy/scipy/scikit-learn 在 reco-core；pypdf/zhconv 在 reco-io；PyQt6 在 reco-ui；pytest/python-docx 在根 dev group。jieba/pkuseg 是 reco-io 标签挖掘的可选分词器，未入锁，缺失时降级到内置字符 n-gram。
- 当前锁定版本与 transitive dependencies 见 `docs/DEPENDENCIES.md`。不依赖 torch/sentence-transformers；句向量仍是 ADR-0004 的可选备选。
- 首次安装需要访问包索引；`uv.lock` 不包含 wheel 文件，仓库不承诺离线重建。换机时使用锁文件重新下载，或由使用者另行准备完整离线缓存。
- 新增第三方依赖时同步修改声明包、`uv.lock`、依赖清单与本节；不得把可选运行时依赖偷偷变成所有安装的必需项。
- 解释器链（无系统 Python 时的解析顺序，建议项 1）：**RECO_PYTHON 环境变量 → DSH 自带运行时（F-1 路径）→ Windows py launcher → PATH 上的 python**；逐级探测、第一个可用者生效，全部失败时明确报错并指向 bootstrap；对应风险 R15。
- `scripts/bootstrap.ps1|sh` 负责：创建/复用 `.venv`、安装 uv、按锁文件同步 workspace 与 dev 依赖、跑一次 `pytest -q` 冒烟；首次同步需要访问包索引。

---

## 13. 合成数据测试策略（落地形态）

**生成器契约**
- make_corpus(spec) -> SyntheticCorpus：spec = {seed, zones, file_count, size_dist, duplicate_mode, corrupt_mode, volumes, clock, names}。
- SyntheticCorpus 提供：临时根目录、每本书的期望指纹、期望统计量（可选）、事件脚本（把文件移动/删除/改名写成动作序列）。
- FakeStat 注入：在 scan_zones 的 stat_provider 参数上注入「不同 st_dev/固定 st_ino」，用于跨卷与移动测试，**不需要真实第二块盘**。
- FakeClock：注入 now，让衰减、漂移、版本时间可断言。

**边界用例矩阵（每行一个测试文件）**

| 用例 | 构造 | 期望 |
|---|---|---|
| 空目录 | 三个专区都空 | 扫描成功、0 文件、推荐返回「无候选」而非空成功 |
| 只有候选无正样本 | candidate 5 篇、liked 空 | 降级为「无依据」，不调用分类器、不报 AUC |
| 同名不同内容 | 两目录同文件名、内容不同大小 | 两个 book_id；不误判为移动 |
| 同内容不同名 | 复制同一文件两个名字（不同文件名） | **不判重**（裁定：dedup_key 只认同名同大小）；两书均保留，进入人工合并清单（proposals，只提示不自动合并），内容哈希不启用（§17 Q6） |
| 移动/改名 | 同一 (dev, ino) 换路径 | 判移动、book_id 与标注保留 |
| 跨卷 | 注入不同 st_dev | 落位走复制、placement_mode=copy、不抛 EXDEV 到 UI |
| 损坏文件 | 截断的 .epub、非法 UTF-8 .txt | quarantined、其余文件正常、整轮不失败 |
| 超大文件 | 51 MB 合成文本 | 流式统计、超时标 pending、UI 不冻结 |
| 负样本饱和 | 所有候选被标「不合口味」 | 仍按衰减权重排序（不硬排除），最低权重不为 0 |
| 单类别 | 只有正样本 | 禁用分类器、w_model=0、明示「不足以判定」 |
| 回放一致 | 事件脚本 → 两次 build | 画像逐字段一致（容差内） |
| 人工覆盖 | 先 auto 后 manual 改同字段 | manual 值保留，auto 不覆盖 |
| 排序一致 | 同数据跑 CLI 与 GUI | 逐位一致 |

**纪律**：全部用例只用合成数据；固定 seed；黄金值手算；每条修复做逆向验证（改回缺陷必转红）。

---

## 14. 风险清单

| # | 风险 | 触发条件 | 影响 | 验证方式 | 缓解 |
|---|---|---|---|---|---|
| R1 | 用 DirEntry.stat() 判身份 | Windows | 所有文件 st_ino=0 → 全部判「新增/删除」，标注与画像被清零 | 单测断言「stat_provider 返回 0 时必须回退 os.stat」 | 代码层强制：身份字段只从 os.stat 取 |
| R2 | 跨卷硬链接失败未捕获 | 用户数据根在另一块盘 | 落位失败、UI 报错 | 注入不同 st_dev 的合成用例 + 实测 C:→F: | st_dev 预判 + EXDEV 捕获 → 复制 |
| R3 | 句向量路线不可用 | 有人提议换向量 | 依赖装不上、启动变慢 | 本机 import torch 实测（F-9） | 默认 TF-IDF；句向量只作可选插件 |
| R4 | 小样本假指标 | 实现者「补个 AUC」 | 用户被误导，违背诚实性要求 | 单测：n_pos<10 时 evaluate_pairwise 必须返回「不足以判定」 | 门禁写在 score model，不在 UI |
| R5 | CLI/GUI 排序漂移 | 两条路径各自排序 | 用户看到两套结果，信任崩塌 | 一致性测试 + RECO_PERF_TABS 快照比对 | 唯一 rank() + 评审禁止二次排序 |
| R6 | 关系/标签误报 | 低置信度被填默认值 | 画像被污染且用户无法察觉 | 合成用例：低置信字段必须为空；manual 覆盖测试 | 留空 + confidence + evidence + 人工优先 |
| R7 | GUI 卡顿 | UI 线程阻塞/全量测量/按字符切 HTML | 切页卡、假死 | RECO_PERF 打点 + 页面遍历 | §11.2 七条约束 |
| R8 | SQLite 并发写冲突 | GUI 与 CLI 同时写 | database is locked | 并发写测试（两进程各写 100 事件） | 单写者队列 + busy_timeout + 短事务 |
| R9 | 事件表膨胀 | 长期使用 | 查询变慢、回放变久 | 事件量压测（10 万条） | 事件归档导出 + 快照高水位（§17 Q5） |
| R10 | 特征库泄露原文 | 误把 n-gram 字符串/片段入库或入日志 | 违反 F2 硬约束 | 代码评审 + 库内扫描「含中文长串」的字段 | 只存数值/哈希桶；日志白名单 |
| R11 | PyQt6 GPL 合规 | 分发闭源 | 法律风险 | 人审确认分发方式（§17 Q2） | 许可隔离层，可切 PySide6 |
| R12 | uv 不是系统命令 | 新环境无 .venv | 安装脚本失败 | bootstrap 在干净目录演练一次 | bootstrap 自举（pip 装 uv）+ pip editable 兜底 |
| R16 | 抓取兜底不可用（建议项 5：F-11 入险） | cncrawler Edge 兜底 0xC0000005 崩溃 | 部分来源只剩搜索摘要 | 每轮调研记录抓取状态（[抓取]/[搜索]） | 直连优先；兜底不可用时如实标注「仅摘要」，不降级伪造已核实 |
| R13 | 损坏/超大文件阻塞同步 | 用户投放大文件 | 整轮扫描卡住 | 合成用例（51 MB、截断 epub） | 分级预算 + pending 重试 + 隔离区 |
| R14 | **PyQt6 装不上/平台插件缺失**（F4 新增） | 网络/代理拦截 pypi wheel、Qt6 运行库装失败、offscreen 插件缺失 | GUI 无法启动，日常使用退化为 CLI | **干净 venv 里 pip install PyQt6 + QT_QPA_PLATFORM=offscreen 冒烟**（T9 验收必跑） | 退路=**GUI 不可用时 CLI 全功能可用**（正式验收判据，见 §10/§16 T10/T11）；PySide6 备选同样要联网、不构成离线兜底（如实写明）；许可切换仅按 §11.5/§17 Q2 |
| R15 | **DSH 运行时升级导致解释器失效**（建议项 1 新增） | DSH 更新/卸载自带运行时，RECO_PYTHON 未设 | bootstrap 与全部脚本失败 | 解释器链逐级探测测试 + 删除 RECO_PYTHON 后演练 | 解释器链逐级回退（§12）→ py launcher → PATH；全失败时明确报错并指向 bootstrap |

**最大风险（按影响排序）**：R1（身份判定错会导致标注全丢）、R2（跨卷落位）、R5（排序漂移）。

---

## 15. 失败模式与退路

| 失败 | 检测 | 退路 |
|---|---|---|
| 硬链接不可用（FAT/exFAT/网络盘/权限） | os.link 抛 EXDEV/EPERM/ENOTSUP | 复制 + 明示 copy 模式；去重仍靠 dedup_key |
| 数据根跨卷 | st_dev 不同 | 一律复制；提示「同卷可零拷贝」 |
| torch/句向量不可用 | import 失败 | 只用 TF-IDF；相似度提供者可插拔 |
| SQLite 写冲突 | database is locked | 重试 + 单写者队列；最坏提示「另一实例正在写入」 |
| WAL 文件损坏/断电 | PRAGMA integrity_check 失败 | 从最近快照 + 事件重放；失败则只读模式导出可读部分 |
| 事件流被手工删改 | 回放与 profile_versions 不一致 | 标记 partial、以现存事件为准、提示「历史不完整」 |
| 特征提取崩溃 | 单文件异常 | 该文件进隔离区，其余继续；错误类别入事件，不含正文 |
| 模型文件缺失/损坏 | 加载失败 | 降级相似度；画像页显示「当前为降级模式」 |
| GUI 平台插件缺失（offscreen） | Qt 报 Could not load the platform plugin | 明确报错退出，不静默跳过测试 |
| 无候选池 | candidate/ 空 | 返回「没有候选」状态，不伪造排序 |
| 全部候选被否 | 负样本饱和 | 权重下限（不归零）+ 探索配额；提示「口味可能已变」 |
| PyQt6 许可不合规 | 人审 | 切 PySide6（只改许可隔离层） |
| uv 不可用 | 命令不存在 | pip install -e 逐包安装（已验证） |
| PyQt6 装不上 / Qt 平台插件缺失（F4 新增） | 干净 venv pip install PyQt6 失败，或 offscreen 冒烟报 Could not load the platform plugin | GUI 停用但 **CLI 全功能可用**（扫描/推荐/反馈/画像/回放/评估全部经 CLI 完成，正式验收判据）；PySide6 备选同样需联网、不是离线兜底 |

---

## 16. 建议的工单切分（供队长排期，非本文件强制）

| 序号 | 主题 | 依赖 | 交付判据 |
|---|---|---|---|
| T1 | store：schema + 迁移 + 事件追加 | — | 合成库上迁移/回放/并发写测试通过 |
| T2 | io：扫描 + 增量判定 + 指纹注入 | T1 | 移动/删除/变化/跨卷用例全绿 |
| T3 | labels：taxonomy + alias 命中 + 合并/归档 | T1 | 合并保留历史、轴覆盖度可复现 |
| T4 | relations：图谱提取 + 逐字段覆盖 | T1,T3 | manual 不被覆盖、低置信留空 |
| T5 | core：score（相似度→权重→分类器阈值） | T1,T3,T4 | 小样本降级、AUC 门禁、排序确定性 |
| T6 | core：profile（衰减/增量/回放/漂移） | T1,T5 | 回放一致、半衰期数值表断言 |
| T7 | io：落位（硬链接/复制）+ 推荐专区写盘 | T2,T5 | 同卷硬链接、跨卷复制、去重 |
| T8 | cli：scan/recommend/feedback/profile/replay/eval | T5,T6,T7 | JSON 输出、只读入口不写库 |
| T9 | ui：PyQt6 骨架 + 按需加载 + 线程池 + 打点 | T5,T7 | 冷启动 <500 ms（首屏骨架口径）、7 坑自检清单、**§11.6 深色主题 + 间距/圆角/图标规范验收** |
| T10 | 一致性 + 性能门禁（CI 脚本） | T8,T9 | CLI/GUI 逐位一致、RECO_PERF_TABS 通过；**含 CLI 全功能降级判据（F4）：GUI 不可用（PyQt6 装不上/平台插件缺失）时，CLI 覆盖扫描/推荐/反馈/画像/回放/评估全部可用并通过**；**探索位默认 2 篇且选取确定性（§17 Q7/建议项 3）纳入验收** |
| T11 | 文档回填（algorithm-changelog、README 状态） | 全部 | 文档与实现一致；**含依赖落地记录（numpy/scipy/scikit-learn/PyQt6 实装版本）与 CLI 全功能降级判据确认（F1/F4）** |
| T12 | **依赖落地**（F1 裁定新增，与文档修订并行） | — | reco-core 声明 numpy/scipy/scikit-learn、reco-ui 声明 PyQt6；.venv 安装成功并记录版本；wheel 缓存留存（§12 安装步骤） |
| T12b | UI 自适应热加载（§11.7，工单已建） | T9 | ui_usage 表与口味事件流隔离（画像回放/打分/推荐全部忽略它，用例断言）；热集判定 uses≥3 或 Top-K 可配；主面板恒热；空闲档预热可打断且「已加载」只在成功后置位；RECO_PERF preload 打点输出；首屏骨架 <500ms 不受预热影响 |

---

## 17. 已裁定（2026-11-20，夜间授权）

> 用户已授权队长代为关闭全部待人审项（性能优先）；以下为裁定结果。未单列裁定细节的条目（Q4/Q5/Q8/Q11/Q13）按原默认做法生效。若人审日后推翻某项，按各行「影响」列评估改动面。

| # | 事项 | 裁定 | 影响 / 否决理由 |
|---|---|---|---|
| **Q1** | 时间衰减半衰期 | **分档默认**：显式标注 90 天、行为信号 30 天、稳定偏好轴（题材/关系）180 天（分档可配）；decay_weight 统一 w = 0.5^(age/half)（与 F2 修法一致） | 单一全局档被否：30 过于激进（90 天前样本权重≈0.125）、180 过于迟钝（近一季只占 0.5 权重）；分档贴合「行为快变、口味慢变」；数值表见 §5.5 |
| **Q2** | PyQt6 vs PySide6 最终选型 | **维持 PyQt6**（用户显式选择）：PyQt6/PySide6 同一 Qt C++ 内核、性能等价；本地自用不分发，GPLv3 无实际合规风险；保留 §11.5 薄适配层 | 将来若需闭源分发：只改该层切 PySide6（LGPLv3 可动态链接）；R14「装不上」风险与 CLI 全功能退路照常生效（§14/§15） |
| Q3 | 落位策略 | 默认**硬链接**（源文件保留）；EXDEV 或落位失败退回复制，并记录 placement=copy | 改「移动」破坏来源目录/网盘同步；改「一律复制」双份占用；契约见 §9 |
| Q4 | 四模型是否拆成 9 包 | **已裁定（默认生效）**：保持 7 包 + 模块级接缝（reco_core.score / reco_core.profile 独立子包） | 拆 reco-score / reco-profile：物理隔离更硬，但违背 modules.md 的 7 包清单，需同步改规格 |
| Q5 | 事件保留策略 | **已裁定（默认生效）**：事件永久保留；profile_versions 作快照；提供归档导出（不删） | 定期压缩（保留 N 年）：省空间但破坏「任意时刻可回放」 |
| Q6 | 是否允许内容哈希去重 | **不允许**（只用 size + 规范化文件名；同名同大小才判重，跨名同内容进人工合并清单——F3 裁定）；跨格式同书不自动合并 | 允许：去重更准，但要读全文、耗时、且触碰正文边界 |
| Q7 | 探索配额 | **默认 2 篇**（每次推荐生成 is_exploration=2），GUI 可提交/调整篇数；选取必须确定性——候选集指纹序 + 同分 book_id 升序（建议项 3 修法） | 探索结果同样进训练；0 会加速反馈回路收窄（规格明令防退化） |
| Q8 | 数据根卷 | **已裁定（默认生效）**：保持 F:/date/adult-reco/data（NTFS，硬链接可用） | 若迁到网络盘/移动盘：硬链接失效，退化为复制；WAL 不允许网络文件系统 |
| Q9 | 单写者模型 | **WAL + 单写者队列 + 短事务 + busy_timeout**；GUI 常驻持有写队列、CLI 批处理串行获取写锁、读不受限 | 多实例并发需文件锁 + 「另一实例写入中」提示，v1 不做 |
| Q10 | 中文 n-gram 参数 | analyzer=char、ngram_range=(2,3)、min_df=2、max_features=200000、sublinear_tf=True、**L2 归一** | 维度上限可下调（省内存、可能丢信息）；单字+bigram 可作对照实验 |
| Q11 | 标签识别深度 | **已裁定（默认生效）**：v1 只做 alias 精确命中（可解释、可控） | 引入共现统计/相近度：召回更高但误报上升，须人工确认流程配套 |
| Q12 | GUI 是否内建阅读器 | **已内建**（后续实现取代原「不内建」裁定）：txt/md、EPUB、DOCX、PDF；位置独立落表，行为信号进入事件流 | 阅读页通过后台读取与 QPlainTextEdit 渲染控制首屏成本；详情仍遵守正文不落日志/报告的边界 |
| Q13 | 报告与日志字段 | **已裁定（默认生效）**：只打路径、统计量、标签名、分数；book_id 代替书名 | 如需书名：允许（F3），但禁止与正文片段同框 |

---

## 18. 来源清单

**已抓正文并核对（cncrawler，直连成功）**
1. <https://peps.python.org/pep-0471/> — scandir 性能与 DirEntry
2. <https://docs.python.org/3/library/os.html> — os.scandir / os.link / stat_result / Windows 注意
3. <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createhardlinkw> — 硬链接限制（NTFS/文件/1023/SMB）
4. <https://sqlite.org/wal.html> — WAL 并发/同主机/单写/checkpoint
5. <https://sqlite.org/queryplanner.html> — 索引与全表扫描
6. <https://www.sqliteforum.com/p/event-sourcing-with-sqlite> — append-only 事件表与投影
7. <https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html> — analyzer/ngram_range/min_df/max_features
8. <https://arxiv.org/abs/1708.02657> — 无分词 CJK 分类与字符 n-gram
9. <https://docs.aetherdb.ai/docs/guides/recency-ranking> — 指数衰减公式与半衰期数值表
10. <https://pypi.org/project/PyQt6/> — GPLv3/商业、wheel 体积
11. <https://pypi.org/project/PySide6/> — LGPLv3/GPLv2/GPLv3/商业
12. <https://www.pythontutorial.net/pyqt/qthreadpool/> — QThreadPool/QRunnable
13. <https://relay.sparkrouter.com/development--and--tooling/headless-pyside6-screenshot-testing-for-qt-apps> — offscreen 冒烟
14. <https://docs.astral.sh/uv/concepts/projects/workspaces/> — uv workspace
15. <https://pip.pypa.io/en/stable/topics/local-project-installs/> — pip install -e
16. <https://hypothesis.readthedocs.io/en/latest/quickstart.html> — 性质测试
17. <https://arxiv.org/abs/2412.11127> — 用户兴趣异构时长（IEEE TSMC:Systems 2018）→ 分档半衰期依据（A3' 2026-11-20）
18. <https://riverbankcomputing.com/commercial/license-faq> — Riverbank 许可 FAQ 一手（A3' 2026-11-20）
19. <https://arxiv.org/abs/2107.05151> — 词嵌入 vs TF-IDF 对比（A3' 由[搜索]升级[抓取]）
20. <https://python-watchdog.readthedocs.io/en/latest/_modules/watchdog/utils/dirsnapshot.html> — inode 快照差分（升级）
21. <https://sqlite.org/isolation.html>、<https://sqlite.org/src/doc/tip/doc/wal-lock.md> — 隔离语义与 WAL 阻塞锁（升级）
22. <https://bugs.python.org/issue10646> — ntpath.samefile 硬链接缺陷（升级）
23. <https://doc.pytest.org/en/stable/how-to/tmp_path.html> — tmp_path 官方文档（升级）

> **A3' 补充核验（2026-11-20）**：anysearch 主用（4 key 正常）、tavily 429 不可用（如实标注）；searchcli 全程 --save 留痕（F:/date/爬虫CLI/搜索CLI/.state/search-results，搜索生成时间 2026-11-20 23:41–23:45，23:46 起为抓取核对窗口）；正文抓取只走 cncrawler（find arxiv 垂直源停用、sogou invalid-response、mdpi/doaj 抓取超时，均如实标注）。7 条来源由[搜索]升级为[抓取]，新增一手 2 条（第 17/18 条），零结论改判、零冲突项。

**仅有搜索候选/摘要（站点不可达或被拦，未抓正文）**
- <https://www.pkrc.net/detect-inode-moves.html> — inode 判移动（不可达，二手；一手支撑见第 2/20 条）
- <https://github.com/python/cpython/issues/53125> — Windows os.link 实现（不可达，二手）
- <https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2> — 句向量模型卡（二手）
- <https://osher.com.au/blog/automatically-relate-blogs-tf-idf-cosine-similarity/> — TF-IDF 实践（二手；表示对比论文已升级为第 19 条）
- <https://github.com/huggingface/sentence-transformers/blob/main/docs/sentence_transformer/usage/efficiency.rst> — 推理成本（二手）
- <https://ceur-ws.org/Vol-2038/paper1.pdf>、<https://thesai.org/Downloads/Volume13No10/Paper_71-Exponential_Decay_Function_Based_Time_Aware_Recommender_System.pdf>、<https://christophtrattner.com/pubs/recsys2015b.pdf>、<https://doi.org/10.3390/systems13111034>（mdpi 抓取超时） — 衰减与近期效应（二手/仅摘要）
- <https://aclanthology.org/W03-1106.pdf>、<https://link.springer.com/article/10.2991/ijcis.2009.2.4.5>、<http://lexicometrica.univ-paris3.fr/jadt/jadt2008/pdf/wei-chauchat-miao.pdf>、<https://doi.org/10.1109/das.2012.43>、<https://doi.org/10.1109/icdar.2013.73> — 无分词分类与中文 n-gram（二手/仅摘要）
- <https://doc.qt.io/qt-6/qpa.html>（403）、<https://www.pythonguis.com/faq/licensing-differences-between-pyqt6-and-pyside6/>（不可达） — Qt 平台抽象与绑定许可对比（二手；许可一手见第 10/11/18 条）
- <https://github.com/astral-sh/uv/issues/8195>、<https://articlevil.substack.com/p/battle-of-python-package-managers>、<https://github.com/davidkun/hatch-vs-poetry-vs-rye> — 工作区与打包工具对比（二手）
- <https://github.com/HypothesisWorks/hypothesis/pull/4050> — 性质测试实践（二手；tmp_path 官方文档已升级为第 23 条）

**本机实测（不适用链接）**
- F-2/F-4/F-5/F-6/F-7/F-8/F-9/F-12/F-13 见 §2；探针脚本与临时文件均已删除。

---

## 19. 与规格文档的对应关系

| 规格要求 | 本文件落点 |
|---|---|
| 不读正文、正文不出计算内部 | §5.6 只落数值/哈希桶；§7 库内无文本；§13 全合成；§14 R10 |
| 不做硬排除 | §6.3 负样本下限权重、不归零；§13「负样本饱和」用例；§15 退路 |
| CLI/GUI 排序一致 | §10 唯一 rank()；§13 一致用例；§14 R5 |
| 全部合成数据测试 | §13 用例矩阵；§5.9 |
| 模块独立可替换 | §4.2/§4.3、§6 各组「替换方式」、§6.5 反例清单 |
| 四模型各自独立（本轮新增） | §6.1–§6.5、§7 四组各自建表 |
| 权重连续变化、不设阶段开关 | §6.3 weight_schedule；§14 R4 |
| 可回放、可降级、增量 | §6.3/§6.4/§7.3；§15 退路 |
| GUI 性能 7 坑 + RECO_PERF | §11.2/§11.3 |
| 时间衰减半衰期、PyQt6/PySide6 | §17 Q1/Q2 |
