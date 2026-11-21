# 工单 04（W4/t6）特征提取——口径 v1 与手算期望值样例（prep：implementer-4，2026-11-21）

> 状态：t6 被 t2 阻塞，未认领。本文件是红测试期望值的唯一来源（audit E3 手算 / E4 期望值独立于实现）。
> 架构依据：ARCHITECTURE §5.6 统计口径、§7.2 features/feature_vectors、§8 第 5 步（事务外提取）、§6.3/F5 延迟导入、issue 04 四条验收、audit-checklist 04 必查 B2/B5/C11/E3。

## 0. 接缝裁定（报队长备案）
1. 统计提取与文件级抽取 → `packages/reco-io/src/reco_io/features.py`（§4.1：reco-io=统计量提取）。
2. 语料级 char n-gram TF-IDF → `packages/reco-core/src/reco_core/vectorize.py`（§4.1：reco-core=特征规整）。
3. 工单 04 全程零 sklearn：向量化用纯哈希桶+数值实现；sklearn 仅允许在 08 的 train_pairwise/similarity 内**延迟导入**。import 卫生测试（子进程 import 七包 → sys.modules 无 sklearn/torch）守护 F5 与「首屏/import reco_core 不触发」。
4. feature_vectors 的索引 = n-gram 哈希桶号（blake2b 8 字节大端 mod num_buckets，跨运行稳定）；max_features=200k 即桶数上限。这样才能满足 §5.6「不落 n-gram 字符串本身」且跨书索引可比。
5. 「schema 断言特征库任何表不含正文列」落在工单 05（库尚不存在）；本票以「金丝雀正文不出现在 FeatureResult 序列化 + monkeypatch 禁 tempfile + 目录快照前后一致」守住 B2 的提取侧。

## 1. 口径 v1（EXTRACTOR_VERSION = "reco-stats/1"）
1. 预处理：\r\n→\n；一切统计基于「非空白字符」（str.isspace() 为空白）。
2. 标点 = unicodedata.category(c) 以 P 开头；punct_density = punct_count / char_count；exclamation/question/ellipsis_ratio 的分母 = punct_count（punct_count=0 → 0.0，不瞎填）。ellipsis 按 U+2026（…）逐字符计。
3. CJK = U+4E00–U+9FFF ∪ U+3400–U+4DBF；cjk_char_ratio = cjk_count / char_count。
4. 段落 = 空行分隔的非空行块；avg_paragraph_chars = 各段非空白字符数的均值（0 段 → 0.0）。
5. 对白（v1 不支持嵌套引号；未闭合的开引号忽略）：引号对 “”/\u0022\u0022/「」/『』 内的非空白字符 + 行首（lstrip 后）以 —— 或 — 开头的整行非空白字符（含破折号本身）。dialogue_char_ratio = 对白字符 / char_count；dialogue_turn_count = 相邻段落对白状态的翻转次数（≤1 段 → 0）。
6. 行统计：非空行（去空白后 >0 字符）的行长（非空白字符数）；line_len_mean = 均值；line_len_p95 = nearest-rank（sorted[ceil(0.95·n)-1]）。无非空行 → 0.0。
7. char_freq_topk：非空白字符计数 Top-K（K 默认 512；并列按码点升序）；存储层再做 hash_slot 映射，提取层返回 (char, count)。
8. repeat_phrase_ratio：去空白全串滑窗 2-gram 与 3-gram；高频 = 出现次数 ≥ 3（high_freq_min）；ratio = 高频 n-gram 出现总次数 / (2-gram 总次数 + 3-gram 总次数)；分母 0 → 0.0。
9. n-gram 桶：slot = int.from_bytes(blake2b(ngram.encode(), digest_size=8).digest(), "big") % num_buckets；输出 (slot, count) 按 slot 升序；绝不出字符串。
10. 向量化（reco_core.vectorize）：去空白文本 → 2/3-gram → 哈希桶计数；df 按桶；min_df 过滤；idf = ln((1+n)/(1+df)) + 1（smooth）；tf = 1 + ln(count)（sublinear）；L2 归一；max_features 截断按语料总频次降序、并列按 slot 升序；返回 (indices, weights)/文本 + norm + dim + idf_version（参数+保留桶+idf 的确定性摘要）。单文档语料 min_df=2 → 全滤 → 空向量，诚实返回。
11. 解码链：utf-8-sig →（有 BOM 时 utf-16）→ gb18030，全部 strict；全败 → undecodable，不猜编码不填数。
12. 降级矩阵（不崩不瞎填；未知一律 None，真零才写 0）：
    - empty：0 字节/全空白 → 全零真值，stats_complete=True。
    - undecodable：解码失败 → 数值全 None，char_freq=()，buckets=None，stats_complete=False。双败样例已实测验证：b"\x80\x80\x80" 与 b"\xfd\x30" 在 utf-8 与 gb18030 下均 UnicodeDecodeError（2026-11-21 venv python 3.12.14 实测）。
    - corrupt_container：.epub 非法 zip → 同上。
    - oversized_streamed：> oversized_threshold_bytes（默认 50MB）→ 分块流式（增量解码器处理跨块多字节），统计与整读一致；repeat 只统计前 repeat_window_chars（默认 1MB）并标窗口截断。
    - pending_timeout：预算 budget_seconds（默认 30s，time_provider 可注入）在块间超时 → 停止，stats_complete=False，buckets=None。
    - .txt/.md 走纯文本；.epub 走 zip 内 .xhtml/.html/.htm（排序、去 style/script、去标签、html.unescape、成员间 \n\n 连接）。

## 2. 样例与手算期望值（每统计量一条；文本全部合成）

### S1 char_count / cjk_char_ratio
text: `Hi 你好\n \t世界 X`
非空白 = H,i,你,好,世,界,X = **7**；CJK = 你好世界 = 4 → ratio = **4/7 ≈ 0.571429**。

### S2 paragraph_count / avg_paragraph_chars
text: `第一段三四五。\n\n第二段六。\n\n\n第三段七八九。\n\n`
段 = 3；各段非空白 = 7/5/7 → avg = **19/3 ≈ 6.333333**。

### S3 dialogue_char_ratio / dialogue_turn_count
text（四段）:
```
他推门进来。

“你来了。”她说。
“嗯。”他应了一声。

窗外起风了。

——走吧，天不早了。
```
行非空白：6 / 9 / 10 / 6 / 10 → char_count = **41**；对白字符 = 引内(你来了。=4 + 嗯。=2) + 破折号行整行 10 = **16** → ratio = **16/41 ≈ 0.390244**。
段状态 = 非/对/非/对 → 翻转 **3** 次。
S3b 未闭合引号：`他说“你好`（5 字符）→ 对白 = 0 → ratio **0.0**。
S3c 跨行引号：`“你好\n朋友”再见` → char_count=8，内=你好朋友=4 → ratio **0.5**（引号扫描是全文级，不按行截断）。
S3d 直角引号：`「进来吧。」他说` → char_count=8，内=进来吧。=4 → ratio **0.5**。

### S4 标点族
text: `好！行！真的？等等……好吧。`
char_count = **14**（好行真的等等好吧 = 8 CJK + ！×2 + ？+ …×2 + 。 = 6 标点）；punct_density = **6/14 ≈ 0.428571**；exclamation = **2/6**；question = **1/6**；ellipsis = **2/6**。
零标点样例 `纯文字没有标点` → 三 ratio 与 punct_density 全 **0.0**。

### S5 line_len_mean / line_len_p95
20 条非空行（10×4 字 + 9×6 字 + 1×20 字），行间插一个空行（\n\n 连接）：
mean = (40+54+20)/20 = **5.7**；sorted = [4]×10+[6]×9+[20]，n=20 → nearest-rank 索引 ceil(19)-1=18 → p95 = **6**（线性插值会给 6.7，本断言钉死 nearest-rank 口径）。附带 paragraph_count=20。

### S6 char_freq_topk
text: `a a b b c 中中中\n` → 中:3, a:2, b:2, c:1（空白不计）。
top2 = **[(中,3),(a,2)]**（并列按码点升序）；top512 = **[(中,3),(a,2),(b,2),(c,1)]**。

### S7 repeat_phrase_ratio
- `哈哈哈哈哈`：2-gram 哈哈×4，3-gram 哈哈哈×3 → 全高频 → **1.0**。
- `好好学习天天向上`：2-gram 7 个、3-gram 6 个全 count=1 → **0.0**。
- `ababab`：ab:3, ba:2, aba:2, bab:2；总 9，高频只有 ab(3) → **3/9 ≈ 0.333333**。

### S8 n-gram 哈希桶
`好好学习天天向上`，n=(2,3) → 13 个不同 n-gram → 注入式单射 stub hash → **13 桶各 count 1**；stub 把 好好/好学 映到同槽 → **12 桶、该槽 count 2**（碰撞相加）。
真哈希（blake2b 8B mod 200000）：两次调用逐位相等；slot(好好学习) 的字面值在实现期用 python 算一次后冻结进测试。

### S9 TF-IDF 金标（reco_core.vectorize）
docs = `["abab", "baba", "abc"]`，n=(2,3)，min_df=2，sublinear，smooth idf，L2，真哈希无碰撞：
df：ab=3，ba=aba=bab=2，bc=abc=1（被 min_df 滤掉）→ dim=4；n=3。
idf(ab)=ln(4/4)+1=**1.0**（锚点）；idf(ba/aba/bab)=ln(4/3)+1≈**1.2876820725**。
d1=abab：raw ab=(1+ln2)·1≈1.6931471806，其余各 1.2876820725 → norm≈2.8002005 →
w_ab≈**0.6046521**，w_ba=w_aba=w_bab≈**0.4598536**。
d2=baba：raw ba=(1+ln2)·1.2876820725≈2.1802360，ab=1，aba=bab=1.2876820725 → norm≈3.0115904 →
w_ba≈**0.7239485**，w_ab≈**0.3320516**，w_aba=w_bab≈**0.4275757**。
d3=abc → **空向量**（min_df 全滤，诚实）。norm 断言 ≈1（L2）。
（字面值实现期 python 复核后冻结，断言容差 1e-6。）

### S10 max_features 截断 + stub hash 注入
单文档 `abababab`（2-gram ab×4, ba×3；3-gram aba×3, bab×3），min_df=1，max_features=2，stub{ab:9, ba:5, aba:7, bab:3}：
按总频次保留 ab(4) 与槽最小者 bab(slot 3) → 桶集 = **{9, 3}**；idf 全 1；raw ab=1+ln4≈2.3862943611，bab=1+ln3≈2.0986122887 → norm≈3.177826 → w_ab≈**0.7509204**，w_bab≈**0.6604051**。
单文档 min_df=2 → 空向量（诚实空）。

### S11 文件级降级矩阵（extract_features，全部注入阈值/时钟，不用真 51MB）
- ok：UTF-8 中文 → flag=ok，stats 与 extract_statistics(text) 全等。
- empty：0 字节 → flag=empty，char_count=0（真零），stats_complete=True。
- 全空白 `\n\n  \t` → 同 empty。
- undecodable：b"\x80\x80\x80"（utf-8/gb18030 全败）→ flag=undecodable，数值全 None，buckets=None，complete=False。
- gb18030："你好".encode("gb18030") → flag=ok，char_count=2。
- UTF-8 BOM：\xef\xbb\xbf + 你好 → flag=ok。
- 流式一致：threshold=16、chunk=7，文本含跨块引号与破折号行（“你好呀朋友”…——好的。…）→ 全部统计量与整读逐项相等（同一累加器两条驱动路径）；且流式路径的 S4 文本 punct_density 仍=6/14（手算钉死流式数值）。
- 超时：time_provider 注入（第 2 次调用跳变）→ flag=pending_timeout，char_count=首块真值（7 字节=「一二」+半字被增量解码器持有 → 2），complete=False，buckets=None。
- epub ok：测试内 zipfile 构建两个 xhtml 成员（可见文本 第一幕/第二幕）+ 忽略 .css → 拼接 char_count=6、paragraph_count=2、avg=3.0。
- epub 截断：b"PK\x03\x04trunc" → flag=corrupt_container。

### S12 泄漏与落盘审计（B2/读完即弃）
- 金丝雀：正文含 `金丝雀绝密正文` → json.dumps(result, ensure_ascii=False) 不含「金丝雀」/「绝密」；undecodable 的错误路径同样只有 flag 无内容。
- 禁落盘：monkeypatch tempfile.NamedTemporaryFile/mkdtemp → raise；tmp_path 目录快照（名+大小）前后一致。
- extractor_version 每个结果（含降级）都等于 "reco-stats/1"。

### S13 import 卫生（F5）
子进程 `import reco_core, reco_io, reco_store, reco_labels, reco_relations` → sys.modules 无 sklearn、无 torch。

## 3. 红测试落点与公开签名
- packages/reco-io/tests/test_features_stats.py（S1–S7）
- packages/reco-io/tests/test_features_buckets.py（S8）
- packages/reco-io/tests/test_features_file.py（S11/S12 + perf 打点 phase=features.extract）
- packages/reco-core/tests/test_vectorize.py（S9/S10/idf_version 确定性/空语料）
- packages/reco-core/tests/test_import_hygiene.py（S13）

```python
# reco_io.features
EXTRACTOR_VERSION = "reco-stats/1"
extract_statistics(text, *, topk=512, ngram_range=(2,3), high_freq_min=3, repeat_window_chars=1_000_000) -> dict
    # keys: char_count, cjk_char_count, cjk_char_ratio, punct_count, punct_density,
    #       paragraph_count, avg_paragraph_chars, dialogue_char_ratio, dialogue_turn_count,
    #       exclamation_ratio, question_ratio, ellipsis_ratio,
    #       line_len_mean, line_len_p95, repeat_phrase_ratio, char_freq(tuple[(char,count)])
char_ngram_buckets(text, *, ngram_range=(2,3), num_buckets=200_000, hash_fn=None) -> tuple[(slot,count)]
extract_features(path, *, oversized_threshold_bytes=50_000_000, chunk_bytes=1_000_000,
                 budget_seconds=30.0, time_provider=time.monotonic, topk=512, num_buckets=200_000) -> FeatureResult
FeatureResult(extractor_version, quality_flag, stats_complete, stats, char_freq, ngram_buckets)
# quality_flag ∈ ok | empty | undecodable | corrupt_container | oversized_streamed | pending_timeout

# reco_core.vectorize
char_ngram_bucket_tfidf(texts, *, ngram_range=(2,3), min_df=2, max_features=200_000,
                        sublinear_tf=True, num_buckets=None, hash_fn=None) -> VectorizationResult
VectorizationResult(vectors, norms, dim, idf_version)  # vectors[i] = (indices_tuple, weights_tuple) 按 index 升序
```

## 4. 验收对照（工单 04 四条 checkbox）
1. 每统计量手算断言 → S1–S7（+S9/S10 向量金标）。
2. extractor_version 版本化 / schema 无正文列 → S12 + 版本断言；schema 断言移交 05（§0.5 已备案）。
3. 空/损坏/超大降级 → S11 矩阵。
4. 读完即弃、无正文中间产物 → S12 金丝雀 + 禁 tempfile + 目录快照。
audit 04 必查：B2（金丝雀+桶纯数值）、B5（全合成）、C11（features/vectorize 零 sqlite import，结构满足事务外）、E3（本文件）。

## 5. 跨票接缝（W6/t8，implementer-2 请求，2026-11-21 接受）
- extract_statistics(text, ..., alias_probe: Sequence[str] = ()) → dict 增加 key "alias_counts"（Mapping[str,int]；probe 为空 → {}）。
- extract_features(..., alias_probe=()) → FeatureResult.alias_counts（从 stats 中提升为独立字段；降级结果一律 {}，不瞎填）。
- 计数语义：alias 与全文各自 casefold 后做非重叠子串计数（str.count 语义，含标点上下文）；空 alias 忽略；probe 内每个 alias 都出现在结果里（真零记 0，供 W6 判覆盖度）。
- 流式路径边界安全：块间携带 max(alias_len)-1 字符尾巴再计数，与整读等价（S11 等价断言覆盖）；别名跨块不漏计。
- 数据驱动：词表由调用方注入，reco_io 不内置任何机制词表（§5.6 硬约束）；match_tags 由 W6 鸭子读 .alias_counts，不 import 本实现。


## 读取器扩展（W17，2026-11-21）

_READERS 仅做容器→文本（.epub/.docx/.pdf），统计口径不动：EXTRACTOR_VERSION 仍为 reco-stats/1，不 bump。理由（implementer-4 裁定）：统计公式与口径不变；旧可读格式（txt/md/epub）特征逐位不变（S 系列 golden 全绿=不变量证据）；.docx/.pdf 此前不进 scanner、不存在旧行，无需重算；可追溯性由 files.ext 区分。

- .docx：stdlib zipfile + XML 提取 word/document.xml w:t；w:p 段落边界换行
- .pdf：pypdf 延迟导入；异常族归一 ValueError → undecodable 降级；无文本层 → empty
- quality_flag 复用现有枚举，不加新旗标
- RTF：时间盒裁定移交（见 rtf-handoff.md）
