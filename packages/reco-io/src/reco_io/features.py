"""工单 04——特征提取（无分词器中文统计）。

公开 API：
- EXTRACTOR_VERSION                     口径版本（"reco-stats/1"）
- extract_statistics(text, ...) -> dict 单文本统计量（手算口径见 notes/04-handcomputed-samples.md）
- char_ngram_buckets(text, ...) -> tuple char 2-3gram 哈希桶（只出 (slot, count) 数值对）
- extract_features(path, ...) -> FeatureResult 文件级提取：解码链/降级/流式/预算

纪律（架构 §5.6/§7/§8；审计 B2/B5/C11/E3）：
- 读完即弃：正文与片段绝不写入结果、日志或临时文件；结果只含数值/枚举/计数。
- 纯数值实现：本模块不 import sklearn/torch/numpy（F5 由 test_import_hygiene 守护）。
- 降级不瞎填：未知一律 None，真零才写 0；损坏/超时打 quality_flag，不崩不编造。
"""
from __future__ import annotations

import codecs
import html
import math
import os
import re
import time
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from hashlib import blake2b
from typing import Callable, Mapping, Sequence

import reco_io.perf as perf

EXTRACTOR_VERSION = "reco-stats/1"

DEFAULT_TOPK = 512
DEFAULT_NGRAM_RANGE = (2, 3)
DEFAULT_HIGH_FREQ_MIN = 3
DEFAULT_REPEAT_WINDOW_CHARS = 1_000_000
DEFAULT_NUM_BUCKETS = 200_000
DEFAULT_OVERSIZED_THRESHOLD_BYTES = 50_000_000
DEFAULT_CHUNK_BYTES = 1_000_000
DEFAULT_BUDGET_SECONDS = 30.0

_EPUB_TEXT_EXTS = (".xhtml", ".html", ".htm")
_BLOCK_RE = re.compile(r"<\s*(style|script)\b[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# v1 不支持嵌套引号；" 与 ＂ 以自身开闭；未闭合引号的内文不计数（S3b）。
_OPENER_TO_CLOSER = {"“": "”", "\"": "\"", "＂": "＂", "「": "」", "『": "』"}
_CJK_RANGES = (("\u4e00", "\u9fff"), ("\u3400", "\u4dbf"))
_EXCL_MARKS = ("!", "！")
_QUES_MARKS = ("?", "？")
_ELLIPSIS = "…"


def _default_slot(gram: str, num_buckets: int) -> int:
    """桶号 = blake2b(ngram, 8B) 大端 mod num_buckets；跨运行稳定（S8 冻结字面值）。"""
    return int.from_bytes(blake2b(gram.encode("utf-8"), digest_size=8).digest(), "big") % num_buckets


def char_ngram_buckets(
    text: str,
    *,
    ngram_range: tuple[int, int] = DEFAULT_NGRAM_RANGE,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
    hash_fn: Callable[[str], int] | None = None,
) -> tuple[tuple[int, int], ...]:
    """去空白文本的 char n-gram 计数按槽合并；输出按槽升序，绝不含 n-gram 字符串。"""
    stripped = "".join(text.split())
    counts: Counter[str] = Counter()
    lo, hi = ngram_range
    for n in range(lo, hi + 1):
        if n <= 0:
            continue
        for i in range(len(stripped) - n + 1):
            counts[stripped[i : i + n]] += 1
    slot_counts: dict[int, int] = {}
    for gram, cnt in counts.items():
        slot = hash_fn(gram) if hash_fn is not None else _default_slot(gram, num_buckets)
        slot_counts[slot] = slot_counts.get(slot, 0) + cnt
    return tuple(sorted(slot_counts.items()))


class _Accumulator:
    """单一累加器：整读与流式共用同一条驱动路径（等价性由构造保证 + S11 断言）。"""

    def __init__(
        self,
        *,
        topk: int,
        ngram_range: tuple[int, int],
        high_freq_min: int,
        repeat_window_chars: int,
        alias_probe: Sequence[str],
        num_buckets: int,
        hash_fn: Callable[[str], int] | None,
    ) -> None:
        self.topk = topk
        self.ngram_range = tuple(ngram_range)
        self.high_freq_min = high_freq_min
        self.repeat_window_chars = repeat_window_chars
        self.num_buckets = num_buckets
        self.hash_fn = hash_fn

        self._counter: Counter[str] = Counter()
        self._line_lengths: list[int] = []
        self._para_sizes: list[int] = []
        self._para_states: list[bool] = []
        self._para_open = False
        self._para_chars = 0
        self._para_dialogue = 0
        self._dialogue_total = 0
        self._line_carry = ""
        self._quote_closer: str | None = None
        self._pending_dialogue = 0
        self._rep_parts: list[str] = []
        self._rep_len = 0

        seen: dict[str, str] = {}
        for alias in alias_probe:
            if not alias:
                continue
            cf = alias.casefold()
            seen.setdefault(cf, alias)
        self._alias_orig = tuple(seen.values())
        self._alias_cf = tuple(seen.keys())
        self._alias_max = max((len(cf) for cf in self._alias_cf), default=1)
        self._alias_buf = ""
        self._alias_cursors = [0] * len(self._alias_cf)
        self._alias_counts = [0] * len(self._alias_cf)

    # -------------------------------------------------- 流式喂入
    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self._counter.update(chunk)
        self._feed_alias(chunk)
        if self._rep_len < self.repeat_window_chars:
            stripped = "".join(chunk.split())
            if stripped:
                room = self.repeat_window_chars - self._rep_len
                if len(stripped) > room:
                    stripped = stripped[:room]
                self._rep_parts.append(stripped)
                self._rep_len += len(stripped)
        buf = self._line_carry + chunk
        lines = buf.split("\n")
        self._line_carry = lines.pop()
        for line in lines:
            self._feed_line(line)

    def _feed_alias(self, chunk: str) -> None:
        if not self._alias_cf:
            return
        hay = self._alias_buf + chunk.casefold()
        for i, alias in enumerate(self._alias_cf):
            pos = self._alias_cursors[i]
            while True:
                idx = hay.find(alias, pos)
                if idx < 0:
                    break
                self._alias_counts[i] += 1
                pos = idx + len(alias)
            self._alias_cursors[i] = pos
        keep = self._alias_max - 1
        drop = len(hay) - keep
        if drop > 0:
            self._alias_buf = hay[drop:]
            self._alias_cursors = [max(0, c - drop) for c in self._alias_cursors]
        else:
            self._alias_buf = hay

    def _feed_line(self, line: str) -> None:
        counts = Counter(line)
        nonblank = sum(v for c, v in counts.items() if not c.isspace())
        if nonblank == 0:
            self._close_paragraph()
            return
        if not self._para_open:
            self._para_open = True
            self._para_chars = 0
            self._para_dialogue = 0
        self._para_chars += nonblank
        self._line_lengths.append(nonblank)
        if line.lstrip().startswith("—"):
            self._para_dialogue += nonblank  # 行首破折号对白行：整行计入对白
        self._feed_quotes(line)

    def _feed_quotes(self, line: str) -> None:
        closer = self._quote_closer
        pending = self._pending_dialogue
        for ch in line:
            if closer is not None:
                if ch == closer:
                    self._para_dialogue += pending  # 闭合才落账；未闭合在 finish 丢弃（S3b）
                    pending = 0
                    closer = None
                elif not ch.isspace():
                    pending += 1
            else:
                opener = _OPENER_TO_CLOSER.get(ch)
                if opener is not None:
                    closer = opener
                    pending = 0
        self._quote_closer = closer
        self._pending_dialogue = pending

    def _close_paragraph(self) -> None:
        if self._para_open:
            self._para_sizes.append(self._para_chars)
            self._para_states.append(self._para_dialogue > 0)
            self._dialogue_total += self._para_dialogue
            self._para_open = False
            self._para_chars = 0
            self._para_dialogue = 0

    # -------------------------------------------------- 收口
    def finish(self) -> tuple[dict, tuple[tuple[int, int], ...]]:
        if self._line_carry:
            self._feed_line(self._line_carry)
            self._line_carry = ""
        self._close_paragraph()  # 未闭合引号的 pending 在此被丢弃，不落账

        total = punct = cjk = excl = ques = ellip = 0
        for ch, v in self._counter.items():
            if ch.isspace():
                continue
            total += v
            if unicodedata.category(ch)[0] == "P":
                punct += v
            if any(lo <= ch <= hi for lo, hi in _CJK_RANGES):
                cjk += v
            if ch in _EXCL_MARKS:
                excl += v
            if ch in _QUES_MARKS:
                ques += v
            if ch == _ELLIPSIS:
                ellip += v

        freq_items = sorted(
            ((c, v) for c, v in self._counter.items() if not c.isspace()),
            key=lambda kv: (-kv[1], kv[0]),
        )
        char_freq = tuple(freq_items[: self.topk])

        turns = 0
        prev: bool | None = None
        for state in self._para_states:
            if prev is not None and state != prev:
                turns += 1
            prev = state

        n_lines = len(self._line_lengths)
        if n_lines:
            mean = sum(self._line_lengths) / n_lines
            ordered = sorted(self._line_lengths)
            p95 = ordered[max(1, math.ceil(0.95 * n_lines)) - 1]
        else:
            mean = 0.0
            p95 = 0.0

        n_paras = len(self._para_sizes)
        avg = (sum(self._para_sizes) / n_paras) if n_paras else 0.0

        rep = "".join(self._rep_parts)
        rep_ratio = self._repeat_ratio(rep)
        buckets = char_ngram_buckets(
            rep,
            ngram_range=self.ngram_range,
            num_buckets=self.num_buckets,
            hash_fn=self.hash_fn,
        )
        alias_counts = dict(zip(self._alias_orig, self._alias_counts))

        stats = {
            "char_count": total,
            "cjk_char_count": cjk,
            "cjk_char_ratio": (cjk / total) if total else 0.0,
            "punct_count": punct,
            "punct_density": (punct / total) if total else 0.0,
            "paragraph_count": n_paras,
            "avg_paragraph_chars": avg,
            "dialogue_char_ratio": (self._dialogue_total / total) if total else 0.0,
            "dialogue_turn_count": turns,
            "exclamation_ratio": (excl / punct) if punct else 0.0,
            "question_ratio": (ques / punct) if punct else 0.0,
            "ellipsis_ratio": (ellip / punct) if punct else 0.0,
            "line_len_mean": mean,
            "line_len_p95": p95,
            "repeat_phrase_ratio": rep_ratio,
            "char_freq": char_freq,
            "alias_counts": alias_counts,
        }
        return stats, buckets

    def _repeat_ratio(self, stripped: str) -> float:
        lo, hi = self.ngram_range
        counts: Counter[str] = Counter()
        total = 0
        for n in range(lo, hi + 1):
            if n <= 0:
                continue
            m = len(stripped) - n + 1
            if m <= 0:
                continue
            for i in range(m):
                counts[stripped[i : i + n]] += 1
            total += m
        if not total:
            return 0.0
        high = sum(v for v in counts.values() if v >= self.high_freq_min)
        return high / total


def extract_statistics(
    text: str,
    *,
    topk: int = DEFAULT_TOPK,
    ngram_range: tuple[int, int] = DEFAULT_NGRAM_RANGE,
    high_freq_min: int = DEFAULT_HIGH_FREQ_MIN,
    repeat_window_chars: int = DEFAULT_REPEAT_WINDOW_CHARS,
    alias_probe: Sequence[str] = (),
) -> dict:
    """单文本统计量（W6/t8 接缝：alias_probe 注入词表，dict 含 alias_counts）。"""
    acc = _Accumulator(
        topk=topk,
        ngram_range=ngram_range,
        high_freq_min=high_freq_min,
        repeat_window_chars=repeat_window_chars,
        alias_probe=alias_probe,
        num_buckets=DEFAULT_NUM_BUCKETS,
        hash_fn=None,
    )
    acc.feed(text)
    stats, _buckets = acc.finish()
    return stats


@dataclass(frozen=True)
class FeatureResult:
    extractor_version: str
    quality_flag: str  # ok | empty | undecodable | corrupt_container | oversized_streamed | pending_timeout
    stats_complete: bool
    stats: dict | None
    char_freq: tuple = ()
    ngram_buckets: tuple | None = None
    alias_counts: Mapping[str, int] = field(default_factory=dict)


class _NeedsFallback(Exception):
    """utf-8 中途失败且文件可回退：携带已读原始字节，由调用方换 gb18030 重喂。"""

    def __init__(self, raw: bytes) -> None:
        super().__init__("utf-8 failed")
        self.raw = raw


def _feed_text(acc: _Accumulator, text: str, *, chunk_bytes: int, start: float,
               budget_seconds: float, time_provider: Callable[[], float]) -> str:
    for i in range(0, len(text), chunk_bytes):
        acc.feed(text[i : i + chunk_bytes])
        if time_provider() - start > budget_seconds:
            return "timeout"
    return "done"


def _feed_file(acc: _Accumulator, path, *, chunk_bytes: int, streamed: bool, start: float,
               budget_seconds: float, time_provider: Callable[[], float]) -> str:
    """字节块增量解码喂入；返回 "done"/"timeout"；utf-8 中途失败且可回退时抛 _NeedsFallback。"""
    with open(path, "rb") as fh:
        head = fh.read(3)
        if head.startswith((b"\xff\xfe", b"\xfe\xff")):
            codec = "utf-16"
        elif head.startswith(b"\xef\xbb\xbf"):
            codec = "utf-8-sig"
        else:
            codec = "utf-8"
        fh.seek(0)
        decoder = codecs.getincrementaldecoder(codec)(errors="strict")
        retain = codec == "utf-8" and not streamed
        raw_parts: list[bytes] | None = [] if retain else None
        try:
            while True:
                chunk = fh.read(chunk_bytes)
                if not chunk:
                    break
                if raw_parts is not None:
                    raw_parts.append(chunk)
                acc.feed(decoder.decode(chunk))
                if time_provider() - start > budget_seconds:
                    return "timeout"
            acc.feed(decoder.decode(b"", final=True))
            if raw_parts is not None:
                raw_parts.clear()  # 解码成功即弃缓存
            return "done"
        except UnicodeDecodeError:
            if raw_parts is None:
                raise
            while True:  # 补读剩余原始字节，供 gb18030 整体重试
                chunk = fh.read(chunk_bytes)
                if not chunk:
                    break
                raw_parts.append(chunk)
            raise _NeedsFallback(b"".join(raw_parts)) from None


def _html_to_text(text: str) -> str:
    text = _BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return html.unescape(text)


def _epub_text(path) -> str:
    """zip 容器按成员名排序取 .xhtml/.html/.htm；成员间以空行连接（段落边界）。"""
    with zipfile.ZipFile(path) as zf:
        parts = []
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if not info.filename.lower().endswith(_EPUB_TEXT_EXTS):
                continue
            data = zf.read(info)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("gb18030")  # strict：失败即 undecodable，不猜
            parts.append(_html_to_text(text))
        return "\n\n".join(parts)


def _docx_text(path) -> str:
    """DOCX：stdlib zipfile + XML 提取 word/document.xml 的 w:t 文本（零第三方依赖）。

    w:p 段落边界保留换行；正文在单一 document.xml 内，段序即文档序。
    坏 zip → BadZipFile 由 extract_features 外层统一降级 corrupt_container。
    """
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    paragraphs: list[str] = []
    for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        runs = [
            t.text or ""
            for t in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        ]
        paragraphs.append("".join(runs))
    return "\n\n".join(paragraphs)


def _pdf_text(path) -> str:
    """PDF：pypdf 延迟导入（W8 sklearn 先例，import reco_io 零 pypdf 成本）。

    加密且空密码解不开 → ValueError；无文本层 → 空串（按 empty 口径降级）。
    """
    try:
        from pypdf import PdfReader  # 函数内延迟导入
    except ImportError as exc:
        raise RuntimeError("pypdf 未安装：PDF 支持需要 pypdf（见 wheels/ 缓存）") from exc

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            reader.decrypt("")  # 空密码尝试：失败按 undecodable 降级
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as exc:
        # pypdf 异常族（PdfReadError/空密码失败/结构损坏）统一归一为
        # ValueError——extract_features 外层按 undecodable 口径降级，不瞎填。
        raise ValueError(f"pdf unreadable: {type(exc).__name__}") from None


# 读取器接缝（W17）：suffix -> 纯读取函数（路径→纯文本，零落盘零输出）。
# 新格式 = 加一个条目；统计口径不随读取器扩展变化（EXTRACTOR_VERSION 不 bump）。
_READERS: dict[str, Callable] = {
    ".epub": _epub_text,
    ".docx": _docx_text,
    ".pdf": _pdf_text,
}


def _degraded(flag: str) -> FeatureResult:
    return FeatureResult(
        extractor_version=EXTRACTOR_VERSION,
        quality_flag=flag,
        stats_complete=False,
        stats=None,
        char_freq=(),
        ngram_buckets=None,
        alias_counts={},
    )


def extract_features(
    path,
    *,
    oversized_threshold_bytes: int = DEFAULT_OVERSIZED_THRESHOLD_BYTES,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    time_provider: Callable[[], float] = time.monotonic,
    topk: int = DEFAULT_TOPK,
    ngram_range: tuple[int, int] = DEFAULT_NGRAM_RANGE,
    high_freq_min: int = DEFAULT_HIGH_FREQ_MIN,
    repeat_window_chars: int = DEFAULT_REPEAT_WINDOW_CHARS,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
    hash_fn: Callable[[str], int] | None = None,
    alias_probe: Sequence[str] = (),
) -> FeatureResult:
    with perf.perf_span("features.extract"):
        return _extract_features_impl(
            path,
            oversized_threshold_bytes=oversized_threshold_bytes,
            chunk_bytes=chunk_bytes,
            budget_seconds=budget_seconds,
            time_provider=time_provider,
            topk=topk,
            ngram_range=ngram_range,
            high_freq_min=high_freq_min,
            repeat_window_chars=repeat_window_chars,
            num_buckets=num_buckets,
            hash_fn=hash_fn,
            alias_probe=alias_probe,
        )


def _extract_features_impl(path, *, oversized_threshold_bytes, chunk_bytes, budget_seconds,
                           time_provider, topk, ngram_range, high_freq_min,
                           repeat_window_chars, num_buckets, hash_fn, alias_probe) -> FeatureResult:
    acc_params = dict(
        topk=topk,
        ngram_range=ngram_range,
        high_freq_min=high_freq_min,
        repeat_window_chars=repeat_window_chars,
        alias_probe=alias_probe,
        num_buckets=num_buckets,
        hash_fn=hash_fn,
    )
    acc = _Accumulator(**acc_params)

    def assemble(flag: str, complete: bool, stats, buckets) -> FeatureResult:
        if stats is None:
            return _degraded(flag)
        return FeatureResult(
            extractor_version=EXTRACTOR_VERSION,
            quality_flag=flag,
            stats_complete=complete,
            stats=stats,
            char_freq=stats["char_freq"],
            ngram_buckets=buckets,
            alias_counts=stats["alias_counts"],
        )

    size = os.stat(path, follow_symlinks=False).st_size
    if size == 0:
        stats, buckets = acc.finish()
        return assemble("empty", True, stats, buckets)

    start = time_provider()
    streamed = size > oversized_threshold_bytes
    try:
        reader = _READERS.get(path.suffix.lower())
        if reader is not None:
            text = reader(path)  # BadZipFile/UnicodeDecodeError 由外层统一降级
            status = _feed_text(acc, text, chunk_bytes=chunk_bytes, start=start,
                                budget_seconds=budget_seconds, time_provider=time_provider)
        else:
            try:
                status = _feed_file(acc, path, chunk_bytes=chunk_bytes, streamed=streamed,
                                    start=start, budget_seconds=budget_seconds,
                                    time_provider=time_provider)
            except _NeedsFallback as fb:
                text = fb.raw.decode("gb18030")  # strict：再失败即 undecodable
                acc = _Accumulator(**acc_params)  # 全新累加器重喂，避免半途污染
                status = _feed_text(acc, text, chunk_bytes=chunk_bytes, start=start,
                                    budget_seconds=budget_seconds, time_provider=time_provider)
    except UnicodeDecodeError:
        return _degraded("undecodable")
    except zipfile.BadZipFile:
        return _degraded("corrupt_container")
    except ValueError:
        # 读取器族统一降级（W17）：_pdf_text 把 pypdf 异常族归一为 ValueError
        # （加密/结构损坏/空密码解不开）——按 undecodable 口径降级，不瞎填。
        return _degraded("undecodable")

    if status == "timeout":
        stats, _buckets = acc.finish()  # 部分真值；buckets 未完成不给
        return assemble("pending_timeout", False, stats, None)

    stats, buckets = acc.finish()
    if streamed:
        flag = "oversized_streamed"
    else:
        flag = "ok" if stats["char_count"] > 0 else "empty"
    return assemble(flag, True, stats, buckets)
