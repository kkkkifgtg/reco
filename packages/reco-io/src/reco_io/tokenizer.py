"""多分词器接口（用户需求：不同文本的切分方式存在差异，需多引擎并存）。

三个引擎：
- **jieba**：中文分词事实标准（前缀词典 + HMM 新词发现），擅长切出有意义的词
- **pkuseg**：北大领域分词（若可用），针对特定领域语料训练
- **ngram**：纯字符 2-3gram，零依赖、确定性、永远可用——兜底引擎

统一入口 `tokenize(text, engine=...)`。请求的引擎不可用时**诚实降级**到 ngram，
并通过 `tokenize_with_report` 报告实际用了哪个（不静默假装成功）。

虚词过滤（`is_function_word`）：代词/助词/连词/数量词不入标签候选，
避免把「的」「了」「一个」变成标签。

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
_FUNCTION_WORDS = frozenset(
    """
    的 了 是 在 和 与 及 或 也 都 就 而 但 却 还 又 很 太 更 最 不 没 无 有
    我 你 他 她 它 我们 你们 他们 她们 它们 自己 别人 大家
    这 那 这个 那个 这些 那些 这样 那样 这里 那里
    一个 一些 一种 一样 一直 一起 一定 一般 一边
    什么 怎么 怎样 为什么 因为 所以 如果 虽然 但是 然后 于是 而且 并且
    上 下 前 后 里 外 中 内 之间 之中
    来 去 说 做 看 想 知道 觉得 认为 可以 应该 需要 能够 可能
    时 时候 已经 正在 将要 曾经 刚才 现在 今天 昨天 明天
    地 得 着 过 吧 呢 吗 啊 呀 哦 嗯 哈
    """.split()
)

# 单字虚词（长度 1 且在此集合 → 过滤；避免「受」「攻」这类单字实词被误杀）
_SINGLE_CHAR_FILTER = frozenset("的地得了着过是在和与及或也都就而但却还又很太更最不没")


def is_function_word(word: str) -> bool:
    """虚词/通用词判定：True = 不应进入标签候选。

    规则：整体命中停用表，或单字命中单字过滤集。
    单字实词（如「攻」「受」「奴」）不在此列——它们是有效标签。
    """
    w = (word or "").strip()
    if not w:
        return True
    if w in _FUNCTION_WORDS:
        return True
    if len(w) == 1 and w in _SINGLE_CHAR_FILTER:
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
