"""多分词器接口（用户需求：不同文本的切分方式存在差异，需多引擎并存）。

三个引擎：
- **jieba**：中文分词事实标准（前缀词典 + HMM 新词发现），擅长切出有意义的词
- **pkuseg**：北大领域分词（若可用），针对特定领域语料训练
- **ngram**：纯字符 2-3gram，零依赖、确定性、永远可用——兜底引擎

统一入口 `tokenize(text, engine=...)`。请求的引擎不可用时**诚实降级**到 ngram，
并通过 `tokenize_with_report` 报告实际用了哪个（不静默假装成功）。

常规词过滤（`is_function_word`）：常规词表（虚词/代词/助词/连词/常用动词
形容词/通用名词，871 词）整体命中不入候选；含阿拉伯数字或标点符号的词
（如「100」「第3章」「！！」）不入候选——#83 报告人纪律：数字和标点自己去掉。
避免把「的」「了」「一个」「走廊」「潮水」变成标签。

依赖纪律：本模块的第三方 import 全部延迟到函数内（`import reco_io.tokenizer`
本身零成本，且测试环境缺库时仍可降级运行）。
"""
from __future__ import annotations

from collections import Counter

__all__ = [
    "available_engines",
    "is_function_word",
    "tokenize",
    "tokenize_with_report",
    "top_words",
]

# 虚词与通用词（不入标签候选）：代词、助词、连词、介词、数量词、常见动词补语
# 常规词表（虚词 + 高频通用词；合并哈工大/百度停用词表主体与叙事常用词）。
# #83 报告人纪律：这些词不进标签候选——「剔除掉那些虚词和常用词」。
_FUNCTION_WORDS = frozenset("""
的 了 是 在 和 与 及 或 也 都 就 而 且 但 却 还 又 很 太 更 最 不 没 没有 无 非 只 才 刚 刚刚 已 已经 曾 曾经 将 将要 会 能 可以 应该 必须 需要 想要 要 得 地 着 过 吧 呢 吗 啊 呀 哦 嗯 唉 哎 嘛 咯 啦 嘿 喂 哇 呜 嘟 我 你 他 她 它 我们 你们 他们 她们 它们 自己 别人 大家 咱 咱们 人家 谁 什么 怎么 怎样 怎么样 为什么 哪 哪个 哪里 哪儿 多少 几 多么 这 那 这个 那个 这些 那些 这样 那样 这里 那里 这儿 那儿 这么 那么 这么样 如此 一 二 三 四 五 六 七 八 九 十 百 千 万 亿 零 半 个 些 位 名 只 条 张 件 支 枝 台 辆 艘 架 座 间 所 处 次 遍 回 番 阵 份 则 篇 章 节 页 行 字 句 段 段落
一个 一些 一种 一样 一直 一起 一定 一般 一边 一时 一旦 一再 一味 一律 一概 一共 一连 一度 一向 一带 一流 第一次 第二次 第三次 最后 之一 一切 所有 每个 每一 诸 各 某 某个 某些 另 另一 另一些 另外 其 其他 其余 其余的 时 时候 现在 目前 当前 如今 今天 昨天 明天 前天 后天 早上 早晨 上午 中午 下午 傍晚 晚上 夜里 夜间 深夜 白天 白日 春天 春 夏天 夏 秋天 秋 冬天 冬 年 月 日 周 星期 月份 时代 时期 年代 年份 以前 之后 后来 然后 接着 紧接着 突然 忽然 猛然 立刻 马上 即刻 顿时 当即 随即 终于 总算 暂时 暂且 临时 长期 长久 久久 一直 依然 仍然 仍旧 还是 早已 早就 迟早 早晚 上 下 左 右 前 后 里 外 中 内 间 旁 旁边 上面 下方 前面 后面 里面 外面 中间 之间 之上 之下 之前 之后 以内 以外 以东 以西 以南 以北 对面 背后 身边 附近 周围 到处 处处 四处 随处 哪里 极 非常 十分 特别 格外 尤其 更加 越发 越 顶 挺 蛮 颇 略 稍 稍微 略微 几乎 简直 完全 全部 整个 全然 根本 大致 大体 大概 大约 约莫 差不多 似乎 好像 仿佛 宛如 犹如 简直 以及 或者 或是 而且 并且 同时 另外 此外 除了 除非 如果 假如 假设 倘若 若是 要是 既然 因为 所以 因此 于是 因而 从而 以至 以致 甚至 何况 况且 无论 不管 只要 只有 即使 就算 哪怕 尽管 虽然 但是 可是 然而 不过 只是 倒是 反而 反正 反之 就是 就是说 也就是说 换句话说 总之 总而言之 综上所述 一方面 另一方面 是 有 在 说 看 着 去 来 走 跑 跳 飞 坐 站 躺 睡 吃 喝 想 要 做 作 用 给 拿 放 收 送 带 拉 推 打 敲 摸 抓 握 扔 抛 接 问 答 叫 喊 哭 笑 听 闻 尝 试试 开始 继续 停止 结束 完成 完毕 发生 成为 变成 变为 变得 觉得 感到 感觉 知道 明白 懂 得以 能够 可能 会 应当 务必 记得 忘记 记住 想起 想到 打算 准备 计划 决定 同意 反对 允许 让 使 令 帮 帮助 帮忙 配合 支持 相信 信任 怀疑 猜 测 估计 认为 以为 观察 注意 留意 小心 当心 希望 期待 盼望 等待 等等 之类 诸如此类 大 小 多 少 高 低 长 短 宽 窄 厚 薄 深 浅 粗 细 快 慢 早 晚 新 旧 好 坏 优 劣 真 假 对 错 难 易 简单 复杂 容易 困难 清楚 清晰 模糊 明显 显然 重要 重大 关键 主要 次要 普通 特殊 奇怪 奇异 正常 异常 平常 平凡 一般 相同 不同 一样 类似 相似 唯一 单独 独自 孤单 孤独 寂寞 安静 平静 宁静 冷静 镇定 紧张 放松 轻松 舒服 舒适 难受 难过 伤心 悲伤 高兴 快乐 开心 幸福 痛苦 烦恼 愤怒 生气 惊讶 惊喜 失望 绝望 害怕 恐惧 担心 着急 焦虑 羞愧 尴尬 东西 事情 事物 问题 方面 方式 方法 办法 做法 样子 模样 情况 状态 状况 形势 局面 原因 理由 结果 后果 影响 作用 意义 价值 目的 目标 计划 方案 想法 念头 主意 意见 建议 态度 语气 声音 声响 动静 感觉 感受 情绪 心情 心理 精神 力气 力量 能力 实力 水平 程度 范围 部分 一半 多数 少数 大部分 绝大多数 关系 关联 联系 区别 差别 差异 共同点 特点 特征 性质 本质 核心 重点 中心 边缘 角落 位置 地方 地点 场所 场景 场面 情景 景象 风景 景色 环境 背景 前提 条件 基础 根据 依据 证明 证据 线索 答案 真相 事实 现实 梦境 梦想 理想 未来 过去 历史 记忆 印象 感情 情感 爱情 友情 亲情 心跳 呼吸 目光 眼神 表情 神色 脸色 面容 容貌 身材 体型 姿势 动作 行为 行动 举动 举止 谈话 对话 交谈 交流 沟通 消息 信息 音讯 电话 短信 邮件 信件 文件 资料 数据 内容 正文 标题 名字 名称 姓名 称呼 绰号 外号 人 男人 女人 男孩 女孩 大人 孩子 小孩 儿童 少年 少女 青年 中年 老年 老人 老头 老太 姑娘 小伙 小伙子 小姐 先生 女士 太太 夫人 老板 顾客 客人 朋友 好友 伙伴 同伴 同事 同学 邻居 陌生人 路人 群众 人们 众人 人群 队伍 组织 团体 集体 国家 社会 政府 公司 学校 医院 商店 饭店 宾馆 旅馆 家 家庭 房间 教室 办公室 会议 婚礼 葬礼 节日 生日 的确 确实 实在 真正 真实 拥有 具备 具有 属于 变为 显得 看起来 看上去 听起来 表示 代表 意味着 包括 包含 含有 存在 出现 消失 发展 变化 增加 减少 提高 降低 提升 下降 上升 增长 改善 改变 保持 维持 持续 不断 不停 反复 再三 屡次 再次 再度 重新 一同 一块 一道 分别 各自 亲自 亲手 顺便 特意 故意 无意 骤然 蓦地 蓦然 霍然
""".split())

# 单字虚词（长度 1 且在此集合 → 过滤；避免「受」「攻」这类单字实词被误杀）
_SINGLE_CHAR_FILTER = frozenset("的地得了着过是在和与及或也都就而但却还又很太更最不没")


_CJK_NUMERALS = frozenset("零一二两三四五六七八九十百千万亿")


def _has_digit_or_punct(w: str) -> bool:
    """含阿拉伯数字、中文数字或标点/符号 → 常规内容，不作标签候选（#83 报告人纪律）。"""
    for ch in w:
        o = ord(ch)
        is_cjk = 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
        is_letter = ch.isascii() and ch.isalpha()
        if not (is_cjk or is_letter):
            return True  # 数字、标点、符号
        if ch in _CJK_NUMERALS:
            return True  # 中文数字（第一本/三个/百分之…）
    return False


def is_function_word(word: str) -> bool:
    """虚词/通用词/数字/标点判定：True = 不应进入标签候选。

    规则：整体命中常规词表；单字命中单字过滤集；含数字或标点符号。
    单字实词（如「攻」「受」「奴」）不在此列——它们是有效标签。
    """
    w = (word or "").strip()
    if not w:
        return True
    if w in _FUNCTION_WORDS:
        return True
    if len(w) == 1 and w in _SINGLE_CHAR_FILTER:
        return True
    if _has_digit_or_punct(w):
        return True
    return False


def _has_module(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def available_engines() -> dict[str, bool]:
    """引擎可用性：{引擎名: 是否可用}。ngram 恒为 True（零依赖兜底）。"""
    return {
        "jieba": _has_module("jieba"),
        "pkuseg": _has_module("pkuseg"),
        "ngram": True,
    }


# ---------------------------------------------------------------- 各引擎实现
def _tokenize_jieba(text: str) -> list[str]:
    import jieba  # 延迟导入

    return [w.strip() for w in jieba.cut(text) if w.strip()]


def _tokenize_pkuseg(text: str) -> list[str]:
    import pkuseg  # 延迟导入

    # 每次新建代价高；进程内复用单例
    global _PKUSEG_INSTANCE
    try:
        seg = _PKUSEG_INSTANCE
    except NameError:
        seg = pkuseg.pkuseg()
        _PKUSEG_INSTANCE = seg
    return [w.strip() for w in seg.cut(text) if w.strip()]


def _tokenize_ngram(text: str) -> list[str]:
    """字符 2-3gram（与 features.char_ngram_buckets 同口径，便于对照）。"""
    clean = "".join((text or "").split())
    grams: list[str] = []
    for n in (2, 3):
        for i in range(len(clean) - n + 1):
            grams.append(clean[i:i + n])
    return grams


_ENGINES = {
    "jieba": _tokenize_jieba,
    "pkuseg": _tokenize_pkuseg,
    "ngram": _tokenize_ngram,
}


def tokenize_with_report(text: str, *, engine: str = "jieba") -> tuple[list[str], dict]:
    """分词并返回诊断报告：实际用了哪个引擎、是否降级、原因。"""
    avail = available_engines()
    used = engine
    degraded = False
    reason = ""
    if engine not in _ENGINES:
        used, degraded, reason = "ngram", True, f"未知引擎 {engine!r}"
    elif not avail.get(engine, False):
        used, degraded, reason = "ngram", True, f"引擎 {engine!r} 未安装"
    try:
        words = _ENGINES[used](text)
    except Exception as exc:  # 引擎内部异常也降级，不炸整条链路
        words = _ENGINES["ngram"](text)
        used, degraded, reason = "ngram", True, f"{type(exc).__name__}: {exc}"
    return words, {
        "engine_requested": engine,
        "engine_used": used,
        "degraded": degraded,
        "reason": reason,
    }


def tokenize(text: str, *, engine: str = "jieba") -> list[str]:
    """统一分词入口（降级后仍返回结果）。"""
    words, _report = tokenize_with_report(text, engine=engine)
    return words


def top_words(text: str, *, engine: str = "jieba", limit: int = 100,
              filter_function: bool = True) -> list[tuple[str, int]]:
    """词频 Top-N：按出现次数降序，可选过滤虚词。用于词云与标签候选。"""
    words = tokenize(text, engine=engine)
    counts: Counter[str] = Counter()
    for w in words:
        if filter_function and is_function_word(w):
            continue
        if len(w) < 2:  # 单字噪声大，标签候选只取多字词
            continue
        counts[w] += 1
    return counts.most_common(limit)
