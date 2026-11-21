# RR1 交接文档 — 阅读模块实现（t2，implementer-r1）

> 日期：2026-11-21T16:20+0800 ｜ 作者：implementer-r1（模型切换前落盘）
> 状态：**t2 in_progress，attempt 2f49b34c-2bbd-4392-942a-11d30d10ddac，零代码落盘**——本文件是全部调研消化成果，接手者从这里直接开工，无须重做侦察。
> 基线：origin/master = 481f4d9（含 PR #27 G2r 审计基线 + PR #28 RS1 调研，均已 merge）。建分支 `feat/reader-rr1` 从 origin/master 起。

## 0. 票面与验收（t2/RR1，动工前重读）

- ①列表双击→阅读页（按扩展名分派：txt 分块直读、epub→HTML、docx→文本流、pdf→文本流）
- ②翻页/滚动+进度记忆（per-book db 表，重开续读）
- ③正文边界：阅读器显示正文=界面例外（合法），但正文不得写日志/事件流/db/诊断（金丝雀沿用）
- ④性能：10MB TXT 首帧 <500ms（分块）；不阻塞主窗
- ⑤RECO_PERF span。验收：四格式合成样例打开/翻页/续读全绿。
- 审计强制：§R1-R8 全查 + §B1-B7 全查（audit-checklist.md:104-114、126-131）；R2 零落盘、R3 诊断零正文、R4 事件白名单、R5 渲染层无驻留、R8 fixture 全合成。
- **越界禁区**：自动标签 = RR2/t3 的票（勿碰 reco_labels）；不引 PyMuPDF（AGPL）/QWebEngine/任何新依赖。

## 1. 已实测的关键探针结论（接手者免重验）

1. **QPlainTextEdit.setDocument 拒绝富文本文档**：实测警告 "Document set does not support QPlainTextDocumentLayout"，toPlainText() 返回空。⇒ **EPUB 逐章渲染必须用 QTextBrowser**（实测 setHtml→toPlainText/blockCount 正常）；TXT/DOCX/PDF 纯文本流仍用 QPlainTextEdit（只读）。QTextBrowser 只装「单章级」HTML（RS1 否决的是整本灌入），不违反 RS1 结论。
2. **qt.py 现状缺控件**：QPlainTextEdit/QTextBrowser/QTextCursor/QScrollBar 均未从 reco_ui/qt.py 导出（qt.py:8-41、__all__）。必须加进 qt.py 再用；**test_theme.py:47-55 禁止 qt.py 之外出现任何 PyQt6 import（含缩进的函数内 import）**——reader.py 一律 `from reco_ui.qt import ...`。
3. **pages.py 无双击接线**：ZonePage（pages.py:529-724）目前只有右键菜单；加 `self._table.doubleClicked.connect(...)`（信号是实例属性，pages.py 无需新增 PyQt6 导入）。
4. **run_tabs 无需改动**（app.py:617-638）：它只遍历 PAGE_ORDER 并对 hasattr(page,"refresh"/"load_now") 的页打点；ReaderPage 不进 PAGE_ORDER/nav，加进 _stack 即可被穿透。
5. **venv 可用**：`.venv/Scripts/python.exe -m pytest`（pytest 9.x、PyQt6、python-docx、pypdf 齐备）；基线 UI 测试 `test_tabs.py + test_actions.py = 9 passed`（0.6s）。
6. **theme.py 无 setStyleSheet 散落**：阅读页样式走 DARK_QSS 追加规则（theme.py），不得在 reader.py 调 setStyleSheet（test_theme.py:36-44）。

## 2. reading_progress 表设计裁定（含两处对 RS1 的修正）

**RS1（PR #28）建议**：新表 reading_progress（book_id 主键 + char_offset / epub_spine_index / epub_block_pos / percent 冗余列 / updated_at / schema_version / config_fingerprint）——**DDL 照抄可用**（RS1 §4.2 的 SQL 原样）。

**修正 1（不进 COMPARE_TABLES）**：research doc 说 "COMPARE_TABLES 同步加入，否则 verify_replay 漏表"——**照做会红**。位置是 UI 直写 upsert（不走 append_event，见修正 2），重放库不会有该表数据，进 COMPARE_TABLES 必然 verify_replay 全红。正确口径 = **照 ui_usage 先例**（store.py:356-361 表定义、:713-739 bump/get、test_ui_usage.py:37-38 "不进 COMPARE_TABLES"）：表进 SCHEMA_SQL（store.py:361 ui_usage 之后追加）、COMPARE_TABLES 不动（store.py:363-368）、SCHEMA_VERSION 保持 "1"（CREATE IF NOT EXISTS 向后兼容）。

**修正 2（不落 progress_saved 事件）**：research doc 的「事件流只记一条 progress_saved 轻事件」**放弃**——①payload 白名单（store.py:97-120）没有进度键，加白名单=改审计敏感面；②ADR-0003 回放确定性：高频覆写位置进 append-only 流，重放结果依赖事件序，且 R4 只允许数值/枚举/路径/进度键——干脆零事件，位置真值只在表（§R4 天然满足）。**测试须断言：进度保存前后 store.read_events() 条数不变。**

**Store API**（照 bump_ui_usage/get_ui_usage 形状，store.py:713-739）：
```python
def upsert_reading_progress(self, book_id: int, *, ext: str, char_offset=None,
                            epub_spine_index=None, epub_block_pos=None, percent: float,
                            updated_at: str, config_fingerprint: str = "ui/reader/1") -> None
    # submit 单写者队列；INSERT ... ON CONFLICT(book_id) DO UPDATE
def get_reading_progress(self, book_id: int) -> dict | None
    # read_conn；返回 {char_offset, epub_spine_index, epub_block_pos, percent, updated_at} 或 None
```

## 3. 新模块 packages/reco-ui/src/reco_ui/reader.py（结构蓝图）

头部 docstring 引工单 RR1 + RS1 + §R1-R8 契约；`from __future__ import annotations`。

**ReaderError(Exception)**：`code` ∈ {"corrupt_container","undecodable","not_found","oversized"}；`__str__ = f"{Path(path).name}: {code}"`——只报文件名+枚举（R3/B1）。

**parse_epub_spine(path) -> list[dict]**（纯函数，零 Qt、零第三方）：
- zipfile → `META-INF/container.xml` → rootfile[@full-path] → OPF → manifest（id→href）→ spine itemref[@idref] 顺序 → 逐章 `{"index":i,"title":…,"html":…,"text":…}`
- 章内 decode：utf-8 失败 → gb18030（strict，再失败 ReaderError("undecodable")）——与 features.py:419-422 同口径
- title：取 XHTML `<title>` 或首个 `<h1>` 文本，缺失用 `第 {i+1} 章`
- 清洗：正则剥 `<script|style|iframe>`（复用 _BLOCK_RE 思路）；html 留给 QTextBrowser.setHtml，text 由去标签+html.unescape 生成（进度百分比用）
- 坏 zip / 缺 container / 缺 OPF / spine 空 → ReaderError("corrupt_container")；**spine 顺序 ≠ zip 内成员序 ≠ 字母序**（features._epub_text 是按名排序的统计口径，阅读口径必须按 spine——测试钉死这一点）
- 不解压落盘（R2）

**load_book_text(path) -> dict**（按扩展名分派；给 QPlainTextEdit 用）：
- `.txt/.md`：rb 读全量 → utf-8 失败 → gb18030 → 再失败 ReaderError("undecodable")（test_features_file.py:70-81 口径）
- `.docx/.pdf`：**直接复用 `reco_io.features._READERS[ext](path)`**（阅读页与打标签同源，RS1 §③；`_READERS` 键集见 features.py:473-477）
- 返回 `{"format": "text", "text": str, "size": int}`；epub 返回 `{"format":"epub","chapters":[…]}`
- 全程零 print/logging/落盘

**chunk_text(text, chunk_chars=120_000) -> list[str]**：按 `\n\n` 段界切；超长单段硬切；确定性（同输入同输出，E9）。

**ReaderPage(QWidget)**（qt.py 新控件经此使用）：
- 布局：标题 QLabel（书名+percent）、正文区（text 格式 → QPlainTextEdit 只读；epub → QTextBrowser）、状态 QLabel、上一章/下一章（epub）、返回按钮
- **复用同一 widget 实例换书**（RS1 ①A：QPlainTextEdit 销毁重建慢）
- **ReaderLoadWorker(QRunnable)**：池线程跑 load_book_text/parse_epub_spine；`_ACTIVE_READERS: set` 持活注册表（pages.py:134-137 同款）；emit 前 RuntimeError 守卫（pages.py:210-216 同款）
- **分块首帧**：open() → worker.load → setPlainText(chunk[0]) 立即 show（perf_span("reader.first_frame") 到此为止），余块 QScrollBar.valueChanged 接近底值时追加（perf_span("reader.chunk.append")）；**不用 setMaximumBlockCount**（RS1：丢头部块+性能回归）
- **进度**：QTimer(1000ms) singleShot 节流保存（滚动重启计时）+ 返回时 flush + MainWindow.closeEvent flush（app.py:387-390 处挂 `self._reader.flush_progress()`）；char_offset=textCursor().position()；epub 存 spine_index+章内 scroll 值；percent=text 格式 offset/len(text)，epub 站 spine_index/章数
- **续读**：open 时 store.get_reading_progress(book_id) 命中 → 先把覆盖目标偏移的块全部装入，再定位 cursor/scroll（E8：不 sleep，直接断言 cursor position）
- **RECO_PERF span**：reader.load（worker 全量解析）、reader.first_frame、reader.chunk.append、reader.epub.parse
- ui_usage：open 成功后 `store.bump_ui_usage("reader", now)`（C12 合规，独立表）

## 4. 接线点（改动面清单，全部已核实行号）

| 文件 | 改动 |
|---|---|
| packages/reco-ui/src/reco_ui/qt.py | 导入+__all__ 增补：QPlainTextEdit、QTextBrowser、QTextCursor、QScrollBar |
| packages/reco-store/src/reco_store/store.py | SCHEMA_SQL 末尾（:361 后）加 reading_progress DDL；:713-739 区域后加 upsert_reading_progress/get_reading_progress |
| packages/reco-ui/src/reco_ui/pages.py | ZonePage.__init__ 加 doubleClicked 接线 + `reader_requested = pyqtSignal(str, int)` + `_open_reader(index)`（查 rows→(path,ext)：`SELECT f.path, b.ext FROM files f JOIN books b ON b.book_id=f.book_id WHERE f.book_id=? AND f.present=1 ORDER BY f.file_id LIMIT 1`；path 缺失→状态行诚实提示） |
| packages/reco-ui/src/reco_ui/app.py | import ReaderPage；_build_skeleton 建 self._reader 加进 _stack（不进 PAGE_ORDER/nav）；两 zone 页 reader_requested → `_open_reader(path, book_id)`（记旧 stack 序号→切阅读页+标题+ui_usage bump）；`_close_reader`（flush 进度→切回原页）；closeEvent 挂 flush |
| packages/reco-ui/src/reco_ui/theme.py | DARK_QSS 追加 QPlainTextEdit/QTextBrowser 规则（色板沿用 BG/SURFACE/OVERLAY/TEXT） |
| packages/reco-ui/src/reco_ui/reader.py | 新文件（§3） |

**禁改**：reco_labels/**（RR2 票域）、packages/reco.db*（运行库）、.scratch/extract-*（他人探针，未跟踪）、dist/（用户登录态）、COMPARE_TABLES、SCHEMA_VERSION、_ALLOWED_KEYS（进度零事件，无需加白）。

## 5. 测试计划 packages/reco-ui/tests/test_reader.py（TDD 红先）

模块顶部 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`（其余测试同款）；fixture 用 conftest.py 的会话级 QApplication + 泄洪。夹具**全合成**（R8/B5）：

- **EPUB 工厂**（zipfile 手搓最小合法容器：container.xml+OPF+3 章 XHTML，spine 顺序故意 ≠ 成员名序）；DOCX 工厂复制 test_formats_w17.make_docx（python-docx 在 dev 组）；PDF 工厂复制 make_pdf_with_text（自包含 20 行）；TXT 用 tmp_path 文本
- 解析组：spine 顺序保序、manifest id→href、title 提取、script/style 剥除、坏容器→ReaderError(corrupt_container)、坏编码章→undecodable、**异常 __str__ 无正文片段**（金丝雀 "金丝雀绝密正文" 灌进章内容+坏文件，断言不出现在 str(exc)/capsys/任何输出）
- chunk 组：段界不劈、超长段硬切、确定性
- 进度组：upsert/get 往返、双书互不串、`"reading_progress" not in COMPARE_TABLES`、保存前后 **read_events 条数不变**（R4）、verify_replay ok
- widget 组：open txt→viewer 含首块内容、_open_reader 发 reader_requested（造 QModelIndex row=0）、MainWindow 接线切 stack 且 page_ids() 仍 4 页、预存进度重开 cursor≈offset（E8 直调不 sleep）
- epub 组：QTextBrowser 渲染首章标题+正文、下一章→第 2 章、跨章进度恢复 spine_index
- perf 组：10MB 合成 TXT → reader.first_frame < 500ms（探针实测 QPlainTextEdit 路径余量大）；断言首帧后 viewer 文本长度 < 全文（分块生效）
- R2 组：四格式 open+close 后 tmp_path glob 计数不变（零落盘）

**提交切片建议**（每片全量 pytest 绿才进下一片，C14）：
1. feat(reader): 红测试 test_reader.py 全量（先证红 E1）
2. feat(store): reading_progress DDL+API（store 测试绿）
3. feat(reader-core): reader.py 解析/分块/解码（纯函数测试绿）
4. feat(reader-ui): qt.py+ReaderPage+pages.py+app.py+theme.py（widget 测试绿+全量绿）→ push → gh pr create（模板见 .scratch/reco-impl/.g2r-pr-body.md）→ 双闸门（auditor §R1-R8+§B、test-runner 全量）→ merge。

## 6. 环境备忘

- gh 全路径 `"D:/Program Files/GitHub.cli/gh.exe"`（bash 无裸 gh）；git 代理已配 127.0.0.1:7897
- 测试：`.venv/Scripts/python.exe -m pytest packages/reco-ui/tests/test_reader.py -q`；全量 `-q`（--ignore=packages/reco-ui 不再需要，W12 已合）
- 本检出血统：分支 chore/g2r-audit-baseline（内容=master a2a621b）；建 feat/reader-rr1 务必从 origin/master 起
- Python 3.12.14；PyQt6 绑定；offscreen 平台有字体警告（QFontDatabase），无害
- 会话超时纪律：单条 bash <120s；pytest 全量 ~分钟级放 run_in_background 或分段跑

## 7. 当前任务状态

- t2 claimed（attempt 2f49b34c-2bbd-4392-942a-11d30d10ddac，in_progress）——**本票未完成，接手者继续用同一 attempt 推进**；接手动作：直接按 §3/§4/§5 开工，完成后 agent_teams_update_task(status=completed, acceptanceResults+commandsRun+changedPaths) 并回报队长。
- 已产工件：仅本文件（.scratch/reco-impl/RR1-HANDOFF.md）；src/tests 零改动。
