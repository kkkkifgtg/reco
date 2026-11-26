"""T1：特征提取 → 向量生成 事件管线（reco-io 装配层）。

职责：把 reco_io.features.extract_features 的纯数值结果装配成两条 append-only
事件（features_computed / vector_computed），经 Store 单写者队列落库并由投影器
写 features / feature_vectors 两表。正文与片段始终留在计算内部（§5.6/B2）。

公开 API：
- VECTOR_DIM_CAP / VECTOR_IDF_VERSION          向量口径常量
- features_payload_from_features(fr, ...)      features_computed payload
- vector_payload_from_features(fr)             vector_computed payload
- char_freq_hash_bytes / ngram_hash_digest_bytes  下方 payload 契约的编码函数
- extract_and_record(store, book_id, path, *, now, config_fingerprint) 落库入口

payload 契约（键集恰为 store._ALLOWED_KEYS 白名单内的下列键，不多不少）：
- features_computed：extractor_version / stats_json / char_freq_hash_hex /
  ngram_hash_digest_hex / source_mtime_ns；
  - stats_json = 数值统计量（§5.6），**不含** char_freq 原文单字；
  - char_freq_hash = 单字频率 Top-K 的哈希桶：slot 升序、(slot, count) 大端
    >II 对（碰撞计数合并；K≤512，由 extract_features 的 topk 保证）；
  - ngram_hash_digest = 全量 2–3gram 桶（slot 升序、(slot,count) 大端 >II 对）
    的 blake2b(32B) 摘要——全量桶逐桶落库会随书膨胀，摘要即变更指纹；
- vector_computed：vector_blob_hex / dim / idf_version / norm（恰四键）。
  blob = >{dim}i 索引（升序）+ >{dim}d 权重（大端），契约与
  reco_store.recommend.decode_vector_blob 往返一致；dim=非零桶数（≤64，
  Top-64 按计数降序、并列 slot 升序）；权重已 L2 归一，norm = 归一前
  sqrt(Σcount²)（余弦用它还原尺度）。无向量（空/降级）时 dim=0、空 blob，
  调用方不写 vector_computed（decode_vector_blob 对 dim=0 显式 ValueError）。

幂等：payload 是 (文件内容, extractor_version) 的纯函数；投影按 book_id upsert →
两表行值与行数不变（事件流 append-only，ADR-0003）。#109 起还有重算门：
(extractor_version, source_mtime_ns, 扫描时 size_bytes) 都没变就直接跳过、
一条事件也不追加；force=True 可强制重算。
坏文件（解码失败/坏容器/超时/不可读）降级为诚实空值（stats_json="{}"、
空哈希桶、不写向量），不抛异常、不猜内容。
"""
from __future__ import annotations

import json
import math
import os
import struct
from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path

from reco_io.features import (
    DEFAULT_NUM_BUCKETS,
    EXTRACTOR_VERSION,
    FeatureResult,
    extract_features,
)

VECTOR_DIM_CAP = 64
# 权重口径：桶计数 L2 归一（本管线不做语料级 IDF；idf_version 如实标注无 IDF）
VECTOR_IDF_VERSION = "reco-ngram-counts/1"
# 降级矩阵之外的落库兜底：文件不可读/不存在（整轮提取不因单文件中断）
UNREADABLE_FLAG = "unreadable"

_PAIR = struct.Struct(">II")  # (slot: uint32, count: uint32)


@dataclass(frozen=True)
class ExtractResult:
    """单本提取落库摘要（数值/枚举，无正文——审计口径 B 类）。"""

    book_id: int
    quality_flag: str  # features.FeatureResult 口径 + unreadable
    stats_complete: bool
    vector_written: bool
    dim: int
    norm: float
    source_mtime_ns: int | None
    skipped: bool = False  # True = 文件与提取口径都没变，本轮没有追加任何事件


def _char_slot(char: str) -> int:
    """单字哈希槽 = blake2b(char, 8B) 大端 mod 桶数（与 features 桶口径同构、跨运行稳定）。"""
    return int.from_bytes(blake2b(char.encode("utf-8"), digest_size=8).digest(), "big") % DEFAULT_NUM_BUCKETS


def _pair_bytes(pairs) -> bytes:
    return b"".join(_PAIR.pack(int(slot), int(count)) for slot, count in sorted(pairs))


def char_freq_hash_bytes(char_freq) -> bytes:
    """单字频率 Top-K → slot→count 桶字节（只落数值，不落原文单字）。"""
    merged: dict[int, int] = {}
    for char, count in char_freq or ():
        slot = _char_slot(str(char))
        merged[slot] = merged.get(slot, 0) + int(count)
    return _pair_bytes(merged.items())


def ngram_hash_digest_bytes(buckets) -> bytes:
    """全量 2–3gram 桶的确定性摘要（blake2b-256；桶数无关的固定 32B）。"""
    return blake2b(_pair_bytes(buckets or ()), digest_size=32).digest()


def _stats_json(stats: dict | None) -> str:
    """§5.6 数值统计量 JSON（确定性键序）；char_freq 原文单字改由哈希桶承载。"""
    body = {} if not stats else {k: v for k, v in stats.items() if k != "char_freq"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def features_payload_from_features(fr: FeatureResult, *, source_mtime_ns: int | None = None) -> dict:
    """FeatureResult → features_computed payload（键集恰为白名单五键）。"""
    return {
        "extractor_version": fr.extractor_version,
        "stats_json": _stats_json(fr.stats),
        "char_freq_hash_hex": char_freq_hash_bytes(fr.char_freq).hex(),
        "ngram_hash_digest_hex": ngram_hash_digest_bytes(fr.ngram_buckets).hex(),
        "source_mtime_ns": source_mtime_ns,
    }


def vector_payload_from_features(fr: FeatureResult) -> dict:
    """FeatureResult → vector_computed payload（键集恰为白名单四键）。

    非零桶 → Top-64（计数降序、并列 slot 升序）→ slot 升序 → 计数 L2 归一；
    blob 大端布局与 reco_store.recommend.decode_vector_blob 逐值往返。
    """
    nonzero = [(int(slot), int(count)) for slot, count in (fr.ngram_buckets or ()) if count]
    if len(nonzero) > VECTOR_DIM_CAP:
        nonzero = sorted(nonzero, key=lambda pair: (-pair[1], pair[0]))[:VECTOR_DIM_CAP]
    nonzero.sort()
    dim = len(nonzero)
    if dim == 0:
        return {"vector_blob_hex": "", "dim": 0, "idf_version": VECTOR_IDF_VERSION, "norm": 0.0}
    norm = math.sqrt(sum(float(count) ** 2 for _s, count in nonzero))
    weights = [float(count) / norm for _s, count in nonzero]
    blob = (
        struct.pack(f">{dim}i", *(slot for slot, _c in nonzero))
        + struct.pack(f">{dim}d", *weights)
    )
    return {
        "vector_blob_hex": blob.hex(),
        "dim": dim,
        "idf_version": VECTOR_IDF_VERSION,
        "norm": norm,
    }


def _unreadable_result() -> FeatureResult:
    return FeatureResult(
        extractor_version=EXTRACTOR_VERSION,
        quality_flag=UNREADABLE_FLAG,
        stats_complete=False,
        stats=None,
        char_freq=(),
        ngram_buckets=None,
        alias_counts={},
    )


def _unchanged(store, book_id: int, path, st) -> bool:
    """重算门：提取器版本、来源 mtime、扫描时大小三者都未变才跳过（#109）。

    三者缺一不可——只看 mtime 会漏掉「换了文件但 mtime 被复制保留」的替换，
    只看大小会漏掉同尺寸改字。扫描行不存在时视为必须重算（无法证明未变）。
    """
    conn = store.read_conn()
    try:
        feature_row = conn.execute(
            "SELECT extractor_version, source_mtime_ns FROM features WHERE book_id = ?",
            (int(book_id),),
        ).fetchone()
        file_row = conn.execute(
            "SELECT size_bytes FROM files WHERE book_id = ? AND path = ? AND present = 1",
            (int(book_id), str(path)),
        ).fetchone()
    finally:
        conn.close()
    if feature_row is None or file_row is None:
        return False
    version, recorded_mtime = feature_row[0], feature_row[1]
    return (
        str(version) == EXTRACTOR_VERSION
        and recorded_mtime is not None
        and int(recorded_mtime) == int(st.st_mtime_ns)
        and int(file_row[0]) == int(st.st_size)
    )


def extract_and_record(
    store,
    book_id: int,
    path,
    *,
    now: str,
    config_fingerprint: str,
    force: bool = False,
) -> ExtractResult:
    """单本提取 → 两条事件落库；幂等（payload 纯函数 + 投影 upsert）。

    重算门（#109）：文件与提取口径都没变时直接跳过、不追加事件——事件流是
    append-only 的历史，重复扫描不该往里灌噪声。force=True 可强制重算。
    坏文件/不可读降级为空值并照常记 features_computed（可审计），不写向量、
    不抛异常；整轮批处理不因单本中断（§8 第 5 步）。
    """
    source_mtime_ns: int | None = None
    st = None
    try:
        st = os.stat(path, follow_symlinks=False)
        source_mtime_ns = int(st.st_mtime_ns)
        if not force and _unchanged(store, book_id, path, st):
            return ExtractResult(
                book_id=book_id, quality_flag="unchanged", stats_complete=True,
                vector_written=False, dim=0, norm=0.0,
                source_mtime_ns=source_mtime_ns, skipped=True,
            )
        fr = extract_features(Path(path))
    except OSError:
        fr = _unreadable_result()

    store.append_event(
        "features_computed",
        book_id=book_id,
        payload=features_payload_from_features(fr, source_mtime_ns=source_mtime_ns),
        occurred_at=now,
        config_fingerprint=config_fingerprint,
    )

    vector = vector_payload_from_features(fr)
    vector_written = vector["dim"] > 0
    if vector_written:
        store.append_event(
            "vector_computed",
            book_id=book_id,
            payload=vector,
            occurred_at=now,
            config_fingerprint=config_fingerprint,
        )

    return ExtractResult(
        book_id=book_id,
        quality_flag=fr.quality_flag,
        stats_complete=fr.stats_complete,
        vector_written=vector_written,
        dim=int(vector["dim"]),
        norm=float(vector["norm"]),
        source_mtime_ns=source_mtime_ns,
    )