"""候选词降噪（地图 #119 finding 10，2026-11-26）。

待审提案按 `count` 降序（#90）时，榜首常年是小说人名：实测 51,546 行里
徐明闻 5844、杨延亭 5643、凌皓 5410——「高频=高价值」在人名上不成立。

本模块只回答一个问题：**这词像不像噪声**（人名 / 停用词）。它从不改排序语义，
也不删数据；调用方自己决定降权（排到后面）还是折叠（不进首屏），提案行始终可查。

词表是显式常量，不联网、不猜：要扩表只改这一个文件，判定规则也只在这里。
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

__all__ = [
    "STOPWORDS",
    "SURNAMES",
    "is_noise_candidate",
    "looks_like_person_name",
    "partition_candidates",
]

# 常见姓氏（单姓前 120 多个，覆盖通俗小说里的绝大多数人名）。
SURNAMES = frozenset(
    "王李张刘陈杨黄赵周吴徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
    "于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江"
    "尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤常温康施文牛樊葛邢安齐"
    "易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌毕"
    "单季裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆"
    "农司卓古吉缪简车项连芦麦褚娄窦戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵苟"
    "邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻苑迟邝"
)

# 高频虚词/代词/泛指词：它们出现得多，但从来不是可确认的标签。
STOPWORDS = frozenset({
    "的", "了", "着", "是", "在", "和", "与", "就", "都", "也", "还", "又",
    "很", "更", "最", "不", "没", "没有", "无", "这", "那", "这个", "那个",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "自己",
    "什么", "怎么", "为什么", "因为", "所以", "但是", "如果", "已经", "还是",
    "一个", "一下", "一起", "一些", "一样", "这样", "那样", "这里", "那里",
    "时候", "现在", "之后", "之前", "起来", "出来", "过来", "下去", "上去",
    "知道", "觉得", "看着", "说道", "东西", "事情", "问题", "地方", "样子",
})

# 名字里几乎不出现的字（方位/虚词/泛指）：用来把「江山」「林间」「白天」这类
# 姓氏开头的普通词排除在人名之外。宁可漏判噪声，也不要把真标签折叠掉。
_NOT_IN_NAMES = frozenset(
    "的了着过是在有和与就都也还又很更最不没无上下中前外里边面个们这那"
    "时年月日天地人家事话样东西里间头尾处所"
    "山水河湖原野草木石土田林花草"
)


def _is_han(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def looks_like_person_name(word: str) -> bool:
    """2–3 个汉字、首字是常见姓氏、且名部不含虚词/方位字 → 判定像人名。

    宁可漏判：误判会把真标签折叠到第二屏（仍可见、可采纳），漏判只是噪声
    留在首屏。两类错误都不改数据。
    """
    w = str(word).strip()
    if not 2 <= len(w) <= 3:
        return False
    if not all(_is_han(ch) for ch in w):
        return False
    if w[0] not in SURNAMES:
        return False
    return not any(ch in _NOT_IN_NAMES for ch in w[1:])


def is_noise_candidate(word: str) -> bool:
    """候选词是否属噪声（人名 / 停用词）。只判定，不排序、不删行。"""
    w = str(word).strip()
    if not w:
        return True
    if w in STOPWORDS:
        return True
    return looks_like_person_name(w)


def partition_candidates(
    rows: Iterable[dict], *, key: str = "candidate"
) -> tuple[list[dict], list[dict]]:
    """把行按 noise 分成 (正常, 噪声) 两组，组内保持原顺序。

    原顺序是数据层给的（#90：count 降序、candidate 升序），本函数不重排——
    「折叠」只是把噪声挪到第二屏，不是换一套排序。
    """
    kept: list[dict] = []
    folded: list[dict] = []
    for row in rows:
        (folded if is_noise_candidate(str(row.get(key) or "")) else kept).append(row)
    return kept, folded


def with_noise_flag(rows: Sequence[dict], *, key: str = "candidate") -> list[dict]:
    """给每行补 `noise` 布尔（复制，不改入参）。UI 与测试共用同一判定。"""
    out: list[dict] = []
    for row in rows:
        item = dict(row)
        item["noise"] = is_noise_candidate(str(row.get(key) or ""))
        out.append(item)
    return out
