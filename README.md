# reco — 本地小说推荐程序

完全本地的推荐程序：用户用目录组织作品，程序从统计特征学习口味并推荐。
**正文只在计算内部流转，绝不写入日志、报告、提交或任何外部位置。**

## 快速开始

```bash
# 首次：建虚拟环境并装依赖（Python 3.12）
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e packages/reco-core -e packages/reco-io \
    -e packages/reco-store -e packages/reco-labels -e packages/reco-relations \
    -e packages/reco-cli -e packages/reco-ui
.venv/Scripts/python.exe -m pip install pytest python-docx pypdf PyQt6 bandit

# 启动 GUI（或双击桌面 reco 图标）
scripts/reco-ui.bat
```

首次使用需重建桌面图标：`powershell -File scripts/make-desktop-shortcut.ps1`

## 数据布局

```
F:\date\adult-reco\data\     # 数据根（可用 --data-root 或 config 覆盖）
├── liked/          # 我喜欢的（正样本）
├── candidate/      # 候选池（推荐源）
└── 推荐专区/        # 程序输出落位
```

## 功能

### 推荐

候选集 = **candidate 专区**（liked 只作正样本、不进候选；同书双区也排除）。

1. 候选池专区点「开始推荐」→ 走 `rank_candidates` 唯一装配层
2. 打分维度：相似度（TF-IDF 字符 n-gram）+ 画像权重 + 行为信号
3. 每次固定划出**探索位**（默认 2 篇，界面可调），跨出舒适区
4. 推荐列表可「加入候选池」；「不合口味」（负样本，带衰减、永不硬排除）与「标为喜欢」（真移动进 liked/）在阅读界面给出（双击作品进入阅读后可见；从「我喜欢」专区进入阅读时不显示）

**推荐需要向量**。库中没有向量时界面会提示「请运行提取」：

```bash
.venv/Scripts/python.exe -m reco_cli extract --db reco.db
```

### 阅读

双击 liked / candidate / **推荐专区**任意一行的书 → 打开阅读页。支持：

| 格式 | 渲染方式 |
|---|---|
| txt / md | 分块直读（大文件首帧 < 500ms，滚动追加） |
| epub | 逐章渲染（按 OPF spine 顺序，非文件名字典序） |
| docx / pdf | 复用特征提取同一套读取器 |

进度按书记忆（存 `reading_progress` 表，零事件），重开续读。

### 标签

- 自动打标签：仅限 **liked 专区**（candidate 未读未确认，不污染正样本）
- 已知别名命中 → 直接写 `book_tags`（source=auto）
- 新词 → 进 `proposals(pending)`，**绝不自动建标签**
- 画像页「待确认提案」→ 逐条采纳/拒绝

**悬停任意书籍行**即显示该书标签（暂无则明说「暂无标签」）。

### 命令行

```bash
.venv/Scripts/python.exe -m reco_cli --help
# 子命令：report / scan / extract / recommend / feedback / explain / replay / eval
```

## 数据与数据库

- **唯一运行数据库 = 仓库根 `reco.db`**；`*.db` 已在 `.gitignore`，仓库不跟踪任何数据库文件。
- `packages/` 下不得存放数据库副本（曾因此踩坑：GUI 打开空库导致推荐页误报「缺少特征向量」）。
- 默认库路径由 `reco_ui.app._default_db_path()` 向上探测「含 `pyproject.toml` + `packages/` 的目录」得出，不依赖目录嵌套层数。

## 开发

### 测试

```bash
.venv/Scripts/python.exe -m pytest packages tests -q
```

### 提交前必须审计

每次 `git commit` 前先跑第三方安全审计（Bandit HIGH 级门禁）：

```bash
.venv/Scripts/python.exe scripts/audit.py    # 退出码 0 才可提交
git config core.hooksPath .githooks          # 启用提交钩子（仅首次）
```

CI 三层兜底：Bandit（Python SAST）、Semgrep（多语言 SAST）、OSV-Scanner（依赖漏洞）。
详见 `.dsh/skills/pre-commit-audit/SKILL.md`。

### 架构与约定

- `docs/ARCHITECTURE.md` — 分层、依赖方向、渲染边界
- `docs/CODING-STANDARDS.md` — 编码规范（含跨包测试 §2）
- `CONTEXT.md` — 领域词汇表（**写代码/票/PR 时用这里的词**）
- `docs/adr/` — 架构决策记录

## 约束（不可违背）

- 程序内部可计算正文统计量，但**正文不得出现在日志、报告、commit、对话中**。
- 不做硬排除；唯一硬过滤是去重。
- 标签与人物关系是**数据**，不是代码常量。
- 领域文档 single-context 布局：根 `CONTEXT.md` + `docs/adr/`。
