# READER-RESEARCH —— 阅读器方案联网调研（RS1）

> 日期：2026-11-21 ｜ 调研员：researcher（reco-reader 团队 t1/rs1）
> 范围：**技术方案调研**（PyQt 长文渲染 / EPUB / DOCX / PDF / 进度记忆 / 自动标签策略）——不含任何小说内容。
> 上下文：阅读页尚未开发（本仓为全新模块）；TXT/EPUB/MD/DOCX/PDF 的**特征提取读取器**已存在（reco-io `features._READERS`，路径→纯文本零落盘，tests/test_formats_w17.py:117-120），但那是打标签用的统计管，不是阅读页渲染管。性能预算：**10MB TXT 首帧 < 500ms** 且不阻塞主窗（列表双击打开阅读页，支持翻页/滚动与重开续读）。
> 检索方式：exa/bing/perplexity 多引擎 + 官方文档与一手帖子全文抓取；Tavily 当日额度耗尽、doc.qt.io 直连 403（改用镜像与转述），已在引用处标注。

---

## 0. 结论速览（TL;DR）

| 决策点 | 结论 | 关键依据 |
|---|---|---|
| ① TXT 渲染器 | **QPlainTextEdit（只读）分块加载**；不用 QTextBrowser 整本灌入；不引入 QWebEngineView | QPlainTextEdit 整段 14MB 加载快（Qt Forum 实测）；QTextBrowser/QTextEdit 14MB≈1min（debug）/release 也明显卡；WebEngine = Chromium 多进程开销，违反 500ms 预算 |
| ② EPUB | **v1 自研 `zipfile` + OCF/container.xml→OPF spine→XHTML**，逐章 HTML→QTextDocument.setHtml()；不新增第三方依赖 | EPUB 本质 = ZIP 容器（OCF 规范）；Qt 富文本引擎支持 HTML 子集含脚注/图片/浮动的有限 CSS（defaultStyleSheet 可补默认样式）；ebooklib 是更优雅的 API 但引入 bs4/lxml 生态依赖 |
| ③ DOCX/PDF | **维持 v1 纯文本流**（复用 `_READERS` 的 _docx_text/_pdf_text），原版式 v2 再议 | pypdf BSD 与既有依赖一致、纯文本流最省；PyMuPDF 快 10–50× 但 AGPL 传染，禁止引入 |
| ④ 进度记忆 | **新表 `reading_progress`**（book_id 主键 + format 分列位置：TXT/DOCX/PDF=字符偏移，EPUB=spine_index+块内偏移+百分比），UI 单写者连接 upsert | 对齐 calibre last_read_positions（book_id+format 粒度）与 Readwise fraction(0-1) 先例；append-only events 不适合高频覆写位置 |
| ⑤ 自动标签 | **混合策略（推荐）**：命中**已存在标签**（alias 精确）→直接写 book_tags(source=auto)（人工优先双保护已兜底）；未知词→proposals pending 待确认；**不自动创建新 tags 行** | tags.md 规范要求「自动发现只提议须确认」；用户点名 liked 自动打——两者不冲突：存量标签自动挂（可回滚、不污染词表），新词仍走人工确认队列 |

---

## ① PyQt6 长文本渲染：QTextBrowser vs QPlainTextEdit 分块 vs QWebEngineView

### 1.1 三方案实测/文档事实

**A. QTextBrowser / QTextEdit 整本加载 —— 否决**
- Qt Forum 一手实测帖（2015，Violet Giraffe）：`QTextBrowser::setPlainText` 载入 **14MB ASCII 文本耗时约 1 分钟（debug build）**，内存占用 170–180MB；换 release build「快一点但仍然基本不可用」（"faster in release but not by much. Still pretty much unusable"）；`setSource()` 无差别；读文件入 QString 不是瓶颈，「填充 QTextBrowser 才是」。加载完成后滚动流畅。
  来源：https://forum.qt.io/topic/55624/qtextbrowser-setplaintext-and-setsource-calls-take-extremely-long-for-large-texts （全文已核）
- 同帖结论 #6：**QPlainTextEdit 快、QTextEdit/QTextBrowser 慢**（QTextDocument 富文本布局是差异根源）。注意 QPlainTextEdit 清空时释放 QTextBlockData 也慢——阅读页应**复用同一 widget 实例换内容**，避免频繁销毁重建。
- Qt Forum 另帖：60MB 级 HTML 全量塞进 QTextDocument 需 35s 绘 20 页（https://forum.qt.io/topic/82513/qtextdocument-is-faster-do-show-big-html-file-as-chrome-browser ）——印证富文本引擎对超大文档是 O(全文档) 布局。

**B. QPlainTextEdit（只读）—— 推荐，且无需极端分块**
- Qt 官方定位：QPlainTextEdit 为纯文本查看/编辑优化，按 QTextBlock 增量布局，视口外不排版，对大文档「已高度优化」（runebook 镜像转述 Qt 文档：loading several MB at once 可行，后台线程只用于文件 I/O）。官方类文档：https://doc.qt.io/qt-6/qplaintextedit.html （doc.qt.io 403，转述自 runebook.dev/en/docs/qt/qplaintextedit 镜像）
- 10MB TXT ≈ 200–400 万字符，QPlainTextEdit 整文档加载在 release/纯 Python 绑定下典型为亚秒级；首帧 500ms 预算下建议再加两道保险：
  1. **文件读取放 QThread**（worker 读 bytes→decode→setPlainText），主窗绝不阻塞（预算明文要求）；
  2. 首帧只灌入前 N 段（如前 2000 块），滚动到底部再用 QTimer/QScrollBar 事件追加下一批（分块懒加载兜底极端文件）。
- 分块实现要点：按**段落（\n\n）边界**切块而不是固定字节数，避免把段落劈成两半；`document().blockCount()` 与 `QScrollBar.value()` 驱动追加载入。已知坑：`setMaximumBlockCount` 会触发性能回归且会丢弃头部块（与续读语义冲突），**不要用**（http://blog.pankajp.com/2013/07/qplaintextedit-with-setmaximumblockcoun.html ；https://gitlab.com/-/snippets/1772324 ）。

**C. QWebEngineView —— 否决（预算原因）**
- Qt 官方：WebEngine 把页面渲染与 JS 执行分离到独立的 **QtWebEngine Process**，是必须随应用分发的独立库（https://doc.qt.io/qt-6.11/qtwebengine-overview.html ，exa 命中摘要已核）。
- Chromium 多进程模型（渲染/网络/GPU utility 进程）带来固定内存与启动开销；qutebrowser issue #1476 记录其高内存占用与 process-model 取舍（https://github.com/qutebrowser/qutebrowser/issues/1476 ）。
- 对本仓的三个具体代价：① 首帧要等 Chromium 进程冷启动，500ms 预算天然危险；② PyQt6 现有离线 wheels **不含 PyQt6-WebEngine**（DEPENDENCIES.md 轮子清单无此包），新增 100MB+ 级轮子与断网演练成本；③ 双击列表→阅读页属于轻量本地阅读，没有 CSS 富排版硬需求。
- 结论：除非 v2 要做原版式 EPUB（完整 CSS/字体/图片排版），否则不值得背 Chromium。

### 1.2 首帧 500ms 预算分解（10MB TXT）

| 阶段 | 方案 | 预算 |
|---|---|---|
| 读文件+解码 | QThread 后台，10MB 顺序读 | ~50–150ms（HDD/SSD 差异） |
| setPlainText | QPlainTextEdit 纯文本增量布局 | ~100–300ms（release） |
| 首帧显示 | 先灌前 N 块立即 show，余量懒加载 | <50ms |
| 合计 | | **~200–500ms，达标且不阻塞主窗** |

QTextBrowser 整本灌入在同预算下必爆（14MB≈分钟级）；QWebEngine 冷启动+多进程亦不稳。**翻页/滚动**：QPlainTextEdit 原生滚动流畅；「翻页模式」可用 QScrollBar 按视口高度步进实现（page step = viewport height），无需换控件。

---

## ② EPUB：zipfile+HTML→QTextDocument vs 第三方库

### 2.1 EPUB 格式事实
- EPUB = **ZIP 容器**（OCF）：`META-INF/container.xml` 指向 OPF 包文档，OPF 的 `<spine>` 按阅读顺序列出 XHTML 内容文档（W3C EPUB 3 规范；doc.qt.io/w3.org 直连受限，结构事实为多源一致常识，可由本地样例书直接验证）。
- 自研解析最小闭环（stdlib only，~80 行）：`zipfile.ZipFile` → 读 container.xml → 定位 OPF → 解析 manifest id→href 映射 + spine itemref 顺序 → 逐项读 XHTML 字符串。**无需解压落盘**（与仓规「零落盘」一致）。

### 2.2 渲染：Qt 富文本引擎的 CSS 支持度（关键限制）
- Qt 富文本引擎只支持 **HTML 子集 + 白名单 CSS 属性表**（Qt 6.12 官方页 "Supported HTML Subset"，doc.qt.io/qt-6/richtext-html-subset.html——本次 doc.qt.io 直连 403，以下为检索摘要转述，实现时以本地 PyQt6 文档/实测复核）：
  - 支持：p/div/h1–h6/em/strong/blockquote/ul/ol/li/img/table/a/span + `font-family/font-size/font-style/font-weight/color/background-color/margin/padding/text-indent/line-height/float` 等**有限子集**；
  - **不支持**：CSS grid/flex、@font-face 网络字体、position 定位、复杂选择器、JS。
- QTextDocument 提供 `defaultStyleSheet`：对 setHtml() 插入的新 HTML 统一补默认样式（https://doc.qt.io/qt-6/qtextdocument.html ，exa 命中摘要已核）——可用它给 EPUB 正文兜底行距/段距，不依赖书内 CSS 质量。
- **脚注**：EPUB 的 asides/footnotes 是 HTML 元素（aside/epub:type），Qt 子集支持 blockquote/section 类块级元素，能显示但 epub:type 语义丢失。v1 建议：脚注正文随章内嵌显示（不弹出），可接受。

### 2.3 与第三方库对比
| 方案 | 优点 | 缺点 | 判定 |
|---|---|---|---|
| **zipfile + 自研 spine 遍历** | stdlib 零依赖；与既有 `_READERS` 接缝同风格；量级可控（本项目书籍多为网文 EPUB，结构简单） | 不解析 nav/TOC 树（可线性 spine 当目录）；CSS 只享 Qt 子集 | **v1 采用** |
| ebooklib（+bs4/lxml） | API 优雅（read_epub/items/spine），reader3.py 等开源实现可参考（https://github.com/karpathy/reader3/blob/master/reader3.py 全文已核） | 新增 lxml/bs4 依赖链与 wheels 缓存负担；且**渲染端仍是 QTextDocument**，库只省了 ~80 行解析 | v2 若需 TOC 树/元数据完备再引 |
| 完整 QtWebEngine 渲染 | 完整 CSS 排版 | Chromium 多进程+新 wheel，见 ①C 否决 | 否决 |

**推荐管线**：EPUB → zipfile 解析 spine → 逐章 XHTML **清洗**（剥 script/style/iframe；reader3 的 clean_html 思路）→ `QTextDocument.setHtml()` 每章一个 QTextDocument → QPlainTextEdit 逐章展示（章=分块单位，翻章=换 document）。逐章渲染天然绕开 QTextDocument 全文档布局慢的问题（①A 证据）。

---

## ③ DOCX/PDF：v1 纯文本流 vs 原版式

**现状盘点**：reco-io `features._READERS` 已有 `.docx`（stdlib zipfile+XML）与 `.pdf`（pypdf>=7.1，reco-io/pyproject.toml:14）的**路径→纯文本**读取函数，零落盘、有 quality_flag 降级（扫描版无文本层）。W17 已做过依赖决策：pypdf 是唯一 PDF 新依赖、wheels 缓存+断网演练完成。

**对比与取舍**：
| 维度 | v1 纯文本流（复用 _READERS） | 原版式（PDF 页渲染 / DOCX 版面） |
|---|---|---|
| 依赖 | **0 新增**（pypdf 已在） | pypdfium2(QPDF,BSD/Apache) 或 PyMuPDF(AGPL!)；DOCX 版面无轻量方案 |
| 速度 | pypdf 纯 Python 提取较慢（PyMuPDF 快 10–50×，https://www.file2markdown.ai/blog/pypdf-vs-pymupdf 全文已核）但只在打开时跑一次，QThread 化后首帧可接受 | 原版式渲染快但重 |
| 许可 | pypdf BSD-3 安全 | **PyMuPDF AGPL-3.0 传染——禁止引入**（本仓私有自用尚可，但依赖纪律禁止无谓 copyleft） |
| 保真 | 丢排版/图（对网文小说可接受） | 完整版式 |
| 复用度 | 直接复用阅读打标签的同一读取管 | 全新渲染栈 |

**建议**：**v1 纯文本流**。理由：① 目标书籍以小说为主，排版价值低；② 零新依赖、与 features._READERS 同源（阅读页与打标签看到同一份文本，行为一致性可测试）；③ pypdf 逐页提取放 QThread，进度条可控。**v2 升级路径**：若确需原版式，PDF 用 pypdfium2（BSD/Apache 系）而非 PyMuPDF；DOCX 保持文本流（DOCX 本就无稳定版面）。扫描版 PDF（无文本层）v1 显示 quality_flag 降级提示即可。

---

## ④ 阅读进度记忆：per-book 位置持久化 db 表设计

### 4.1 先例
- **calibre**：`last_read_positions` 表按 (book_id, format) 粒度存位置，阅读器打开时恢复（calibre 源码 `src/calibre/db/schema.py` 定义该表；本次抓取源码 404，结构为检索多源一致结论——book+format 两维是 calibre 的既有设计）。
- **Readwise Reader API**：位置存 `reading_progress` fraction 0–1（https://readwise.io/reader_api 命中摘要已核）。
- **epub.js**：CFI 定位 + locations 百分比，打开时按 CFI/百分比恢复（https://stackoverflow.com/questions/71638418/how-to-load-the-last-visited-page-in-epub-js ；https://github.com/futurepress/epub.js/blob/master/examples/locations.html ）。

### 4.2 设计（适配本仓 schema，store.py 风格）
本仓 books 表主键 book_id（dedup_key 去重），格式列 ext 在 books 上；位置读写走 UI 唯一写连接。**不用 events 流**：events 是 append-only 审计流（触发器拒 UPDATE/DELETE），阅读位置是高频覆写值（每页/每滚动节流写），塞 events 会爆审计流且重放无意义。

```sql
-- schema 新增（SCHEMA_VERSION 顺延；COMPARE_TABLES 同步加入，否则 verify_replay 逐表比对漏表）
CREATE TABLE IF NOT EXISTS reading_progress(
  book_id     INTEGER PRIMARY KEY REFERENCES books(book_id),
  -- 统一存两类坐标：字符偏移（TXT/DOCX/PDF 文本流）与结构坐标（EPUB 章内偏移）
  char_offset INTEGER,            -- TXT/DOCX/PDF：全文纯文本中的字符偏移
  epub_spine_index INTEGER,       -- EPUB：spine 序号（0-based）
  epub_block_pos   INTEGER,       -- EPUB：章内 QTextDocument block 序号
  percent     REAL,               -- 0.0–1.0（冗余列，跨格式统一展示/排序用；Readwise 同款先例）
  updated_at  TEXT NOT NULL,      -- ISO8601
  schema_version TEXT NOT NULL,
  config_fingerprint TEXT NOT NULL
);
```
- **写策略**：UI 侧节流（滚动停止 ~1s 或翻页/关窗时）upsert；事件流只记一条 `progress_saved` 轻事件（可选）供审计，位置真值以表为准。
- **恢复策略**：双击打开→查表→有记录则定位（QPlainTextEdit `setTextCursor`/`scrollToAnchor` 到对应 block）；无记录从头。percent 冗余列让「上次读到哪」列表页零计算。
- **备选方案**（不推荐）：全塞 events 流（重放恢复）——位置高频写会把 append-only 审计流变垃圾场，重放 O(n) 慢；JSON 侧文件——脱离单库，违背本仓「正文不入库、位置入 store」的一致性边界（位置是元数据不是正文，允许入库）。

---

## ⑤ 自动标签策略：tags.md「自动提议+用户确认」vs 用户点名 liked 自动打

### 5.1 两方案

**方案 A：严格走 proposals（纯规范派）**
liked/ 24 本全部命中结果先写 proposals(pending)，用户逐条确认后才写 book_tags。
- 优点：完全符合 tags.md/架构 §6.2「created_by：自动发现只提议，须用户确认」的字面纪律（docs/ARCHITECTURE.md:422）；词表零噪音。
- 缺点：**24 本 × 数十命中 = 数百次人工点击**，用户已明确点名「liked 自动打标签」，方案 A 直接违背需求意图；用户确认疲劳后队列堆积，画像轴（W9 权重）迟迟接不到真数据。

**方案 B：全自动直写（激进派）**
alias 命中即写 book_tags(auto)，新词也自动建 tags 行。
- 优点：零人工。
- 缺点：**自动创建新 tags 行违反「表是种子不是封闭清单」的可控词表纪律**（proposals.py:3-4 注释明示 propose 绝不创建 tags 行）；未知词直进词表会污染 alias 命中面（下一次误命中），错误标签还会进画像轴权重，污染可传播。

**方案 C（推荐）：混合——存量标签自动挂、新词走 proposals**
- **命中已存在标签**（match_tags 的 alias 精确命中，confidence=1.0 事实级，match.py:5）：直接写 `book_tags(source='auto', confidence, basis_json)`。
  - 安全性由既有**人工优先双保护**兜底：manual/user 行永不被 label_set(auto) 覆盖、不被 label_cleared 清理，只有 auto 行可清理（store.py:9-10、store.py:396-399 D1+F2）——自动挂错可被 label_cleared 全量回滚，不动人工权威。
  - 数据闭环现成：`_apply_label_set` + `book_tags` 唯一键 (book_id, tag_id) 幂等。
- **未知词/新机制**（taxonomy._alias_index 未命中，match.py:59「进 proposals 由上层处理」）：维持现有 propose_tags→pending→accept/reject 链路（proposals.py 全文已核），**不自动建 tags 行**。用户确认后 accept_proposal 建 user 标签，下一轮 alias 命中即自动挂。
- candidate/ 不自动打（未读就打会污染画像，既有裁定）；liked/ 新入书在入区钩子处触发同一混合管线。

### 5.2 建议
**采用方案 C**，并落两条合同给 implementer-r2：
1. `label_set(auto)` 只允许命中**存量 active 标签**（alias 精确），basis_json 记 {alias, count}（match_tags Evidence 结构），绝不建新 tags 行；
2. 未知词聚合去重后批量写 proposals（复用 threshold 去噪），UI 标签页展示 pending 队列；用户 accept 后自动标签闭环自动扩大覆盖。
这样「用户点名的 liked 自动打」落在**可回滚的 book_tags(auto) 层**（且人工行双保护不受影响），「tags.md 的确认纪律」落在**词表演化层**——两层各守其职，规范与需求不再冲突。

---

## ⑥ 实现者补充注记（额度收尾补录，未新增检索）

> 队长指示「额度即将耗尽，未落盘结论立即落盘」——以下为本轮调研已取得但未写入正文的实现级细节，全部来自已核来源，无新增调研。

1. **QWebEngineView 本地文件安全限制（v2 备选必读）**：`load(QUrl::fromLocalFile(...))` 受 Chromium 安全策略限制可能白屏；须用 `setHtml(html, baseUrl)`/`setContent()`，或把资源放进 Qt 资源系统 `qrc:///` 加载。**图片相对路径解析依赖 baseUrl**——v2 原版式 EPUB 若走 WebEngine，把解包图片目录作为 baseUrl 是先决条件（来源 #17）。
2. **PDF 进度坐标细化**：pypdf 按**页**提取（逐页 `page.extract_text()`）。reading_progress 对 PDF 建议记 `(pdf_page_index, 页内 char_offset)` 双坐标而非全文 char_offset——全文偏移需逐页拼接重建，页序号天然稳定，恢复时只需重新提取目标页±邻页，省时。可在 §④ DDL 上加 `pdf_page_index INTEGER` 列。
3. **EPUB percent 计算需章长缓存**：percent=(前序章累计字符+章内偏移)/全书字符。打开书时一次性线性扫描 spine 各章字符数缓存（进程内即可，不必落库）；这就是 epub.js `locations.generate()` 的 v1 等价物——epub.js 生成 locations 是全书遍历的已知成本，v1 压成打开时线性扫描即可（来源 #14）。
4. **QPlainTextEdit 分块追加的位置保持**：懒加载追加下一批块后应显式回设 `QScrollBar.setValue(旧值)`（用户在底部附近时保持跟随，防视口跳动）；`setMaximumBlockCount` 禁用（§① 1.1 B 已记）；追加只从尾部 append（`QTextCursor` movePosition(End) 后 insertBlock/insertText），**绝不头部插入**——会把已读内容推走并触发全文档重排。
5. **QTextDocument.setHtml 逐章新建实例**：setHtml 会重置 undo/redo 栈（来源 #4 已核语义）；按 §② 2.3 管线每章新建 QTextDocument 天然规避复用副作用，翻章旧 document 直接丢弃让 GC 回收，**不要手动 clear()**（慢路径，与 §① 1.1 A #6 清空慢同源）。
6. **pypdf 侧补充**：纯 Python 无 C 扩展（弱项：无图像渲染、layout 感知有限、表格提取弱——来源 #11 已核）；`page.extract_text()` 逐页可中断，配合 QThread 进度条天然分页，与 §③ QThread 化建议一致。

---

## 来源清单（全部为技术文档/开源仓库/技术社区，无小说内容）

| # | 来源 | 用途 | 核验方式 |
|---|---|---|---|
| 1 | https://forum.qt.io/topic/55624/qtextbrowser-setplaintext-and-setsource-calls-take-extremely-long-for-large-texts | QTextBrowser 14MB 实测慢、QPlainTextEdit 快 | 全文抓取 |
| 2 | https://doc.qt.io/qt-6/qplaintextedit.html （runebook.dev/en/docs/qt/qplaintextedit 镜像转述） | QPlainTextEdit 大文档优化定位 | 镜像全文 |
| 3 | https://doc.qt.io/qt-6/richtext-html-subset.html | Qt HTML 子集+CSS 白名单（403，检索摘要转述，实现时本地复核） | 引擎摘要 |
| 4 | https://doc.qt.io/qt-6/qtextdocument.html | setHtml/defaultStyleSheet 语义 | 引擎摘要+片段 |
| 5 | https://doc.qt.io/qt-6.11/qtwebengine-overview.html | QtWebEngine 独立进程模型 | 引擎摘要 |
| 6 | https://github.com/qutebrowser/qutebrowser/issues/1476 | WebEngine 内存占用/进程模型取舍 | 引擎摘要 |
| 7 | https://forum.qt.io/topic/82513/qtextdocument-is-faster-do-show-big-html-file-as-chrome-browser | 超大 HTML 全量布局慢（60MB/35s） | 引擎摘要 |
| 8 | http://blog.pankajp.com/2013/07/qplaintextedit-with-setmaximumblockcoun.html + https://gitlab.com/-/snippets/1772324 | maximumBlockCount 回归坑/setPlainText profile | 引擎摘要+抓取 |
| 9 | https://github.com/karpathy/reader3/blob/master/reader3.py | EPUB spine 解析+HTML 清洗参考实现 | 全文抓取 |
| 10 | https://docs.sourcefabric.org/projects/ebooklib/en/latest/tutorial.html | ebooklib API 面 | 引擎摘要 |
| 11 | https://www.file2markdown.ai/blog/pypdf-vs-pymupdf | pypdf BSD vs PyMuPDF AGPL、10–50× 速度差 | 全文抓取 |
| 12 | https://github.com/py-pdf/pypdf | pypdf 能力/许可 | 引擎摘要 |
| 13 | https://readwise.io/reader_api | reading_progress fraction(0-1) 进度先例 | 引擎摘要 |
| 14 | https://stackoverflow.com/questions/71638418/how-to-load-the-last-visited-page-in-epub-js + https://github.com/futurepress/epub.js/blob/master/examples/locations.html | CFI/百分比恢复先例 | 引擎摘要 |
| 15 | calibre `last_read_positions`（book_id+format 粒度；源码抓取 404，多源一致结论） | 进度表粒度先例 | 检索一致 |
| 16 | 本仓一手代码：store.py:217-227（book_tags）、proposals.py（pending 链）、match.py（alias 命中）、test_formats_w17.py（_READERS）、DEPENDENCIES.md（wheels 无 WebEngine） | 方案落地约束 | 直接读取 |
| 17 | https://runebook.dev/zh/docs/qt/qwebengineview （Qt 文档镜像转述） | WebEngine 本地文件白屏/baseUrl 限制（§⑥.1） | 抓取全文 |
