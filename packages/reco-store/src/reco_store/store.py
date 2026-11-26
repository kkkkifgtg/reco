"""SQLite 特征库 + append-only 事件流（工单 05；契约 §7、ADR-0003）。

- 库级设置（§7.1）：journal_mode=WAL、synchronous=NORMAL、busy_timeout=5000、
  foreign_keys=ON；写连接唯一（单写者队列），读用独立连接；事务短小，
  特征提取在事务外（本模块不含任何提取逻辑）。
- events 只允许 INSERT：写入层校验 + 触发器硬保险拒绝 UPDATE/DELETE（C1）。
- payload 白名单：仅数值/枚举/哈希/标签/路径/原因；effective_weight 显式
  禁止——折叠时由 occurred_at + config_fingerprint 重算，不存列（B6）。
- book_tags 人工优先双保护（W6 收敛）：manual/user 行不被 label_set(auto)
  覆盖、不被 label_cleared 清理；只有 auto 行可被清理（D1+F2）。
- 去重唯一硬过滤（F3/§9）：dedup_key = sha256(size_bytes + \x1f + 规范化名)；
  规范化 = NFKC → casefold → 空白折叠去首尾；括号与扩展名保留参与比较。
- verify_replay：事件流重放进全新库，与存量投影逐表逐位比对（ADR-0003）；
  投影被绕过事件流直改同样被发现（双向漂移检测）。
- 正文绝不入库：字段全部为数值/枚举/哈希/标签/路径（B2）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

SCHEMA_VERSION = "1"
APP_VERSION = "0.1.0"

PayloadValue = str | int | float | bool | None


class PayloadViolationError(Exception):
    """payload 违反白名单（未知键 / 非标量值 / effective_weight）。"""


class Synchronous(Enum):
    """PRAGMA synchronous 数值口径（SQLite 官方：0 OFF / 1 NORMAL / 2 FULL / 3 EXTRA）。"""

    OFF = 0
    NORMAL = 1
    FULL = 2
    EXTRA = 3


@dataclass(frozen=True)
class Event:
    event_id: int
    event_type: str
    book_id: int | None
    payload_json: str
    occurred_at: str
    recorded_at: str
    actor: str
    schema_version: str
    config_fingerprint: str


@dataclass(frozen=True)
class ReplayReport:
    ok: bool
    diffs: tuple[str, ...]
    compare_rows: dict[str, list[tuple]] | None = None  # W12b：逐表行快照（C12 隔离断言用）


# ---------------------------------------------------------------- 规范化与指纹

_FULLWIDTH_SPACE_RE = re.compile(r"[\s\u3000]+")
_SPACE_RUN_RE = re.compile(r"\s+")


def normalize_book_name(name: str) -> str:
    """§9 normalized_name 规则：①NFKC ②casefold ③连续空白折叠为单空格并去首尾；
    ④全角→半角（NFKC 兼容分解自然覆盖全角字母/标点/空格）；
    ⑤括号与扩展名保留参与比较。"""
    s = unicodedata.normalize("NFKC", name)
    s = s.casefold()
    s = _SPACE_RUN_RE.sub(" ", s).strip()
    return s


def dedup_key(size_bytes: int, display_name: str) -> str:
    """dedup_key = sha256(size_bytes + 0x1F + 规范化名)；同名且同大小才判重。"""
    raw = f"{size_bytes}\x1f{normalize_book_name(display_name)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- payload 白名单

_FORBIDDEN_KEYS = frozenset({"effective_weight"})

_ALLOWED_KEYS = frozenset({
    # 扫描/文件身份
    "path", "new_path", "zone", "ext", "size_bytes", "mtime_ns", "st_dev",
    "st_ino", "display_name", "normalized_name", "dedup_key", "mount_hint", "present",
    # 标签
    "tag_id", "axis", "label", "parent", "status", "merged_into", "created_by",
    "source", "confidence", "basis_json", "alias", "aliases",
    # #41（tag_merged 投影契约）：投影读 p["into"] 作为合并目标，into 必须与
    # tag_id 成对合法——否则合法合并事件不可构造、合并态重放不恢复。
    "into",
    # #40（proposal_created 投影契约）：投影读 proposals 的 payload_json /
    # evidence_json 两列，二者是标量 JSON 字符串（正文仍绝不入库——B2 内容纪律
    # 由调用方遵守，白名单管的是键面与标量性，不管内容语义）。
    # 为何选「扩白名单」而非「改投影派生」：事件流是唯一事实源（ADR-0003），
    # 投影不该自造提案内容——派生方案要么丢业务负载（candidate/count/threshold
    # 无处可去），要么在投影层重算提案语义（投影变成第二份真相，逐位重放必裂）。
    # 若后续要收紧这个审计敏感面（事件里带 JSON 字符串列），另走 ADR 裁定。
    "payload_json", "evidence_json",
    # #42（proposal_updated 键面）：提案状态变更事件的载荷 = proposal_id + status。
    "proposal_id",
    # 行为信号（不含 effective_weight——折叠时重算）
    "kind", "value", "session_id",
    # 静音持久化（工单 11：mutes 投影事件 payload）
    "dedup_key", "remaining_days", "expired_at",
    # 关系
    "graph_json", "field_path", "value_json", "extractor_version",
    # 特征
    "stats_json", "char_freq_hash_hex", "ngram_hash_digest_hex",
    "source_mtime_ns", "dim", "vector_blob_hex", "idf_version", "norm",
    # 画像 / 推荐 / 评估
    "axis_weights_json", "sample_counts_json", "top_tags_json", "weights_json",
    "half_life_days_json", "reason", "event_id_high_water",
    "run_id", "profile_version", "candidate_count", "exploration_count", "items_json",
    "method", "n_pos", "n_neg", "folds", "auc", "ci_low", "ci_high", "notes",
})


def _validate_payload(payload: dict) -> str:
    for key, val in payload.items():
        if key in _FORBIDDEN_KEYS or key not in _ALLOWED_KEYS:
            raise PayloadViolationError(f"payload 含白名单外键: {key!r}")
        if val is None:
            continue
        if isinstance(val, int) and not isinstance(val, bool):
            # #107：Windows 的 st_dev/st_ino 是无符号 64 位，超出 SQLite 有符号
            # int64 时 sqlite3 会抛裸 OverflowError（事件整体回滚、无上下文）。
            # 编码层统一拒绝，并指向唯一的编码函数。
            if not -(2**63) <= val < 2**63:
                raise PayloadViolationError(
                    f"payload 整数超出 int64: {key!r}={val}；"
                    "无符号 64 位身份请先用 reco_io.int64.to_int64 编码"
                )
            continue
        if isinstance(val, (str, float, bool)):
            continue
        # RR2/t3：唯一例外 aliases——标量字符串数组（种子别名一体落库），
        # 其余键仍必须为标量（B6 白名单纪律不放松）。
        if key == "aliases" and isinstance(val, list) and all(isinstance(x, str) for x in val):
            continue
        raise PayloadViolationError(f"payload 值必须为标量: {key!r}")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------- schema

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS volumes(
  volume_id INTEGER PRIMARY KEY AUTOINCREMENT,
  st_dev INTEGER NOT NULL UNIQUE,
  mount_hint TEXT,
  first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS books(
  book_id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  ext TEXT NOT NULL,
  dedup_key TEXT NOT NULL UNIQUE,
  normalized_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_books_size ON books(size_bytes);

CREATE TABLE IF NOT EXISTS files(
  file_id INTEGER PRIMARY KEY AUTOINCREMENT,
  volume_id INTEGER REFERENCES volumes(volume_id),
  st_dev INTEGER NOT NULL,
  st_ino INTEGER NOT NULL,
  path TEXT NOT NULL UNIQUE,
  zone TEXT NOT NULL CHECK(zone IN ('liked','candidate','推荐专区','quarantine','unknown')),
  size_bytes INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  ext TEXT NOT NULL,
  book_id INTEGER REFERENCES books(book_id),
  present INTEGER NOT NULL DEFAULT 1 CHECK(present IN (0,1)),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  moved_from_path TEXT
);
-- #97：同 inode 多路径（硬链接孪生）是合法形态，身份不唯一；路径唯一性由 files.path 承担。
CREATE INDEX IF NOT EXISTS ix_files_identity ON files(volume_id, st_ino) WHERE present = 1;
CREATE INDEX IF NOT EXISTS ix_files_zone ON files(zone, present);
CREATE INDEX IF NOT EXISTS ix_files_book ON files(book_id);

CREATE TABLE IF NOT EXISTS features(
  book_id INTEGER PRIMARY KEY REFERENCES books(book_id),
  extractor_version TEXT NOT NULL,
  stats_json TEXT NOT NULL,
  char_freq_hash BLOB,
  ngram_hash_digest BLOB,
  computed_at TEXT NOT NULL,
  source_mtime_ns INTEGER
);

CREATE TABLE IF NOT EXISTS feature_vectors(
  book_id INTEGER PRIMARY KEY REFERENCES books(book_id),
  vector_blob BLOB NOT NULL,
  dim INTEGER NOT NULL,
  idf_version TEXT,
  norm REAL
);

CREATE TABLE IF NOT EXISTS tags(
  tag_id TEXT PRIMARY KEY,
  axis TEXT NOT NULL,
  label TEXT NOT NULL,
  parent TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  merged_into TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tags_axis ON tags(axis, status);
CREATE INDEX IF NOT EXISTS ix_tags_parent ON tags(parent);

CREATE TABLE IF NOT EXISTS tag_aliases(
  alias TEXT PRIMARY KEY,
  tag_id TEXT NOT NULL REFERENCES tags(tag_id),
  source TEXT
);
CREATE INDEX IF NOT EXISTS ix_aliases_tag ON tag_aliases(tag_id);

CREATE TABLE IF NOT EXISTS book_tags(
  book_id INTEGER NOT NULL REFERENCES books(book_id),
  tag_id TEXT NOT NULL REFERENCES tags(tag_id),
  source TEXT NOT NULL CHECK(source IN ('auto','manual','user')),
  confidence REAL,
  basis_json TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(book_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_book_tags_tag ON book_tags(tag_id);
CREATE INDEX IF NOT EXISTS ix_book_tags_source ON book_tags(book_id, source);

CREATE TABLE IF NOT EXISTS relation_graphs(
  book_id INTEGER PRIMARY KEY REFERENCES books(book_id),
  graph_json TEXT NOT NULL,
  extractor_version TEXT,
  confidence REAL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relation_fields(
  book_id INTEGER NOT NULL REFERENCES books(book_id),
  field_path TEXT NOT NULL,
  value_json TEXT,
  source TEXT NOT NULL,
  confidence REAL,
  basis_json TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(book_id, field_path)
);
CREATE INDEX IF NOT EXISTS ix_relation_fields_source ON relation_fields(book_id, source);

CREATE TABLE IF NOT EXISTS signals(
  signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER REFERENCES books(book_id),
  kind TEXT NOT NULL,
  value REAL,
  occurred_at TEXT NOT NULL,
  source TEXT,
  session_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals_book ON signals(book_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_signals_kind ON signals(kind, occurred_at);

CREATE TABLE IF NOT EXISTS events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  book_id INTEGER,
  payload_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system',
  schema_version TEXT NOT NULL,
  config_fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_occurred ON events(occurred_at);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(event_type, occurred_at);
CREATE INDEX IF NOT EXISTS ix_events_book ON events(book_id, occurred_at);

CREATE TRIGGER IF NOT EXISTS trg_events_no_update BEFORE UPDATE ON events
BEGIN
  SELECT RAISE(ABORT, 'events is append-only: UPDATE refused');
END;

CREATE TRIGGER IF NOT EXISTS trg_events_no_delete BEFORE DELETE ON events
BEGIN
  SELECT RAISE(ABORT, 'events is append-only: DELETE refused');
END;

CREATE TABLE IF NOT EXISTS profile_versions(
  version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  axis_weights_json TEXT NOT NULL,
  sample_counts_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  top_tags_json TEXT,
  weights_json TEXT,
  half_life_days_json TEXT,
  reason TEXT,
  event_id_high_water INTEGER,
  schema_version TEXT NOT NULL,
  config_fingerprint TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rec_runs(
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  actor TEXT,
  profile_version INTEGER,
  candidate_count INTEGER,
  exploration_count INTEGER,
  config_fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS recommendations(
  run_id TEXT NOT NULL,
  book_id INTEGER NOT NULL,
  rank INTEGER NOT NULL,
  score REAL NOT NULL,
  reasons_json TEXT,
  is_exploration INTEGER NOT NULL DEFAULT 0 CHECK(is_exploration IN (0,1)),
  PRIMARY KEY(run_id, book_id)
);
CREATE INDEX IF NOT EXISTS ix_recs_rank ON recommendations(run_id, rank);

CREATE TABLE IF NOT EXISTS evaluation_runs(
  eval_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  method TEXT,
  n_pos INTEGER,
  n_neg INTEGER,
  folds INTEGER,
  auc REAL,
  ci_low REAL,
  ci_high REAL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS proposals(
  proposal_id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  payload_json TEXT,
  evidence_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mutes(
  book_id INTEGER PRIMARY KEY REFERENCES books(book_id),
  dedup_key TEXT,
  axis TEXT NOT NULL,
  kind TEXT NOT NULL,
  expired_at TEXT NOT NULL,
  remaining_days REAL NOT NULL,
  updated_at TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  config_fingerprint TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_usage(
  page_id TEXT PRIMARY KEY,
  uses INTEGER NOT NULL DEFAULT 0,
  last_used TEXT
);

CREATE TABLE IF NOT EXISTS reading_progress(
  book_id INTEGER PRIMARY KEY REFERENCES books(book_id),
  char_offset INTEGER,
  epub_spine_index INTEGER,
  epub_block_pos INTEGER,
  percent REAL,
  updated_at TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  config_fingerprint TEXT NOT NULL
);
"""

COMPARE_TABLES: tuple[str, ...] = (
    "volumes", "files", "books", "features", "feature_vectors", "tags",
    "tag_aliases", "book_tags", "relation_graphs", "relation_fields",
    "signals", "events", "profile_versions", "rec_runs", "recommendations",
    "mutes",
)


# ---------------------------------------------------------------- 投影器

def _ensure_volume(conn: sqlite3.Connection, st_dev: int, mount_hint: str | None, at: str) -> int:
    row = conn.execute("SELECT volume_id FROM volumes WHERE st_dev = ?", (st_dev,)).fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO volumes(st_dev, mount_hint, first_seen_at) VALUES(?,?,?)",
        (st_dev, mount_hint, at),
    )
    return int(cur.lastrowid)


def _resolve_or_create_book(conn: sqlite3.Connection, p: dict, at: str) -> int:
    row = conn.execute("SELECT book_id FROM books WHERE dedup_key = ?", (p["dedup_key"],)).fetchone()
    if row is not None:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO books(display_name, size_bytes, ext, dedup_key, normalized_name, status, first_seen_at)"
        " VALUES(?,?,?,?,?,'active',?)",
        (p["display_name"], p["size_bytes"], p["ext"], p["dedup_key"], p["normalized_name"], at),
    )
    return int(cur.lastrowid)


def _apply_label_set(conn: sqlite3.Connection, book_id: int | None, p: dict, at: str) -> None:
    """D1+F2（W6 收敛）：人工优先双保护——auto 来源的 label_set 永不覆盖
    manual/user 行（写入层拒绝，非调用方自觉）；manual/user 属人工权威、
    彼此可接管；label_cleared 只清理 auto 行。
    """
    source = p.get("source", "auto")
    row = conn.execute(
        "SELECT source FROM book_tags WHERE book_id = ? AND tag_id = ?",
        (book_id, p["tag_id"]),
    ).fetchone()
    if row is not None and row[0] in ("manual", "user") and source == "auto":
        return  # 双保护：auto 不覆盖人工行（W6 协议 ManualTagProtected 对应位）
    conn.execute(
        "INSERT INTO book_tags(book_id, tag_id, source, confidence, basis_json, updated_at)"
        " VALUES(?,?,?,?,?,?)"
        " ON CONFLICT(book_id, tag_id) DO UPDATE SET source=excluded.source,"
        " confidence=excluded.confidence, basis_json=excluded.basis_json, updated_at=excluded.updated_at",
        (book_id, p["tag_id"], source, p.get("confidence"), p.get("basis_json"), at),
    )


def _apply_event(conn: sqlite3.Connection, event: Event) -> None:
    p = json.loads(event.payload_json)
    at = event.occurred_at
    t = event.event_type
    if t == "file_added":
        volume_id = _ensure_volume(conn, p["st_dev"], p.get("mount_hint"), at)
        book_id = _resolve_or_create_book(conn, p, at)
        # N1 再扫描幂等（ADR-0003 投影层语义）：同 path 重扫/复活 upsert 同一行——
        # 墓碑行（present=0）复活不留鬼行；首见照旧 INSERT（last_seen_at 推进）。
        conn.execute(
            "INSERT INTO files(volume_id, st_dev, st_ino, path, zone, size_bytes, mtime_ns, ext,"
            " book_id, present, first_seen_at, last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)"
            " ON CONFLICT(path) DO UPDATE SET volume_id=excluded.volume_id,"
            " st_dev=excluded.st_dev, st_ino=excluded.st_ino, zone=excluded.zone,"
            " size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns, ext=excluded.ext,"
            " book_id=excluded.book_id, present=1, last_seen_at=excluded.last_seen_at,"
            " moved_from_path=NULL",
            (volume_id, p["st_dev"], p["st_ino"], p["path"], p["zone"], p["size_bytes"],
             p["mtime_ns"], p["ext"], book_id, at, at),
        )
    elif t == "file_removed":
        conn.execute("UPDATE files SET present = 0, last_seen_at = ? WHERE path = ?", (at, p["path"]))
    elif t == "file_moved":
        # N1/W19：跨专区移动携带 zone → 同步搬迁专区（COALESCE；不带 zone 保持原专区，向后兼容——旧移动事件可逐位重放，ADR-0003；zone 变更与路径变更同一事件同一事务）。
        conn.execute(
            "UPDATE files SET path = ?, last_seen_at = ?, moved_from_path = ?,"
            " zone = COALESCE(?, zone) WHERE path = ?",
            (p["new_path"], at, p["path"], p.get("zone"), p["path"]),
        )
    elif t == "tag_added":
        conn.execute(
            "INSERT OR IGNORE INTO tags(tag_id, axis, label, parent, status, merged_into, created_by, created_at)"
            " VALUES(?,?,?,?,'active',NULL,?,?)",
            (p["tag_id"], p["axis"], p["label"], p.get("parent"), p.get("created_by"), at),
        )
        # RR2/t3：种子别名随 tag_added 一体落库（aliases 为字符串数组；
        # 逐条 INSERT OR IGNORE 幂等，重放同事件不重复建行）。
        for alias in p.get("aliases") or []:
            if str(alias).strip():
                conn.execute(
                    "INSERT OR IGNORE INTO tag_aliases(alias, tag_id, source) VALUES(?,?,?)",
                    (str(alias), p["tag_id"], p.get("created_by") or "seed"),
                )
    elif t == "tag_merged":
        conn.execute("UPDATE tags SET status='merged', merged_into=? WHERE tag_id=?", (p["into"], p["tag_id"]))
        conn.execute("INSERT OR IGNORE INTO tag_aliases(alias, tag_id, source) VALUES(?,?,?)",
                     (p["tag_id"], p["into"], "merge"))
    elif t == "tag_archived":
        conn.execute("UPDATE tags SET status='archived' WHERE tag_id=?", (p["tag_id"],))
    elif t == "label_set":
        _apply_label_set(conn, event.book_id, p, at)
    elif t == "label_cleared":
        conn.execute("DELETE FROM book_tags WHERE book_id = ? AND tag_id = ? AND source = 'auto'",
                     (event.book_id, p["tag_id"]))  # F2：只清 auto 行；manual/user 双保护
    elif t == "label_moved":
        # #104：合并搬迁人工行（merge 是用户动作，不是自动提取——D1 保护的是
        # auto 覆盖，不是人工合并）。行内 source/confidence/basis/updated_at 原样
        # 保留，只改指向；目标已有行时保留目标行（人工已有判断优先）。
        row = conn.execute(
            "SELECT source, confidence, basis_json, updated_at FROM book_tags"
            " WHERE book_id = ? AND tag_id = ?",
            (event.book_id, p["tag_id"]),
        ).fetchone()
        if row is not None:
            conn.execute("DELETE FROM book_tags WHERE book_id = ? AND tag_id = ?",
                         (event.book_id, p["tag_id"]))
            exists = conn.execute(
                "SELECT 1 FROM book_tags WHERE book_id = ? AND tag_id = ?",
                (event.book_id, p["into"]),
            ).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO book_tags(book_id, tag_id, source, confidence, basis_json, updated_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (event.book_id, p["into"], *row),
                )
    elif t == "signal_recorded":
        conn.execute(
            "INSERT INTO signals(book_id, kind, value, occurred_at, source, session_id) VALUES(?,?,?,?,?,?)",
            (event.book_id, p["kind"], p.get("value"), at, p.get("source"), p.get("session_id")),
        )
    elif t == "relation_updated":
        conn.execute(
            "INSERT INTO relation_graphs(book_id, graph_json, extractor_version, confidence, updated_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(book_id) DO UPDATE SET graph_json=excluded.graph_json,"
            " extractor_version=excluded.extractor_version, confidence=excluded.confidence,"
            " updated_at=excluded.updated_at",
            (event.book_id, p["graph_json"], p.get("extractor_version"), p.get("confidence"), at),
        )
    elif t == "manual_set":
        conn.execute(
            "INSERT INTO relation_fields(book_id, field_path, value_json, source, confidence, basis_json, updated_at)"
            " VALUES(?,?,?,'manual',?,?,?)"
            " ON CONFLICT(book_id, field_path) DO UPDATE SET value_json=excluded.value_json,"
            " confidence=excluded.confidence, basis_json=excluded.basis_json, updated_at=excluded.updated_at",
            (event.book_id, p["field_path"], p.get("value_json"), p.get("confidence"), p.get("basis_json"), at),
        )
    elif t == "features_computed":
        conn.execute(
            "INSERT INTO features(book_id, extractor_version, stats_json, char_freq_hash,"
            " ngram_hash_digest, computed_at, source_mtime_ns) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(book_id) DO UPDATE SET extractor_version=excluded.extractor_version,"
            " stats_json=excluded.stats_json, char_freq_hash=excluded.char_freq_hash,"
            " ngram_hash_digest=excluded.ngram_hash_digest, computed_at=excluded.computed_at,"
            " source_mtime_ns=excluded.source_mtime_ns",
            (event.book_id, p["extractor_version"], p["stats_json"],
             bytes.fromhex(p["char_freq_hash_hex"]), bytes.fromhex(p["ngram_hash_digest_hex"]),
             at, p.get("source_mtime_ns")),
        )
    elif t == "vector_computed":  # 键名-编码契约：vector_blob_hex 承诺 hex，解码 bytes.fromhex（W5-N）
        conn.execute(
            "INSERT INTO feature_vectors(book_id, vector_blob, dim, idf_version, norm) VALUES(?,?,?,?,?)"
            " ON CONFLICT(book_id) DO UPDATE SET vector_blob=excluded.vector_blob, dim=excluded.dim,"
            " idf_version=excluded.idf_version, norm=excluded.norm",
            (event.book_id, bytes.fromhex(p["vector_blob_hex"]), p["dim"], p.get("idf_version"), p.get("norm")),
        )
    elif t == "profile_updated":
        conn.execute(
            "INSERT INTO profile_versions(created_at, axis_weights_json, sample_counts_json, confidence,"
            " top_tags_json, weights_json, half_life_days_json, reason, event_id_high_water,"
            " schema_version, config_fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (at, p["axis_weights_json"], p["sample_counts_json"], p["confidence"], p.get("top_tags_json"),
             p.get("weights_json"), p.get("half_life_days_json"), p.get("reason"),
             p.get("event_id_high_water"), event.schema_version, event.config_fingerprint),
        )
    elif t == "rec_generated":
        # 幂等：同一 run_id 重放/重写时先清旧行（rec_runs.run_id 是主键，
        # recommendations 以 run_id 外键关联；重复插入会撞 UNIQUE）
        conn.execute("DELETE FROM recommendations WHERE run_id = ?", (p["run_id"],))
        conn.execute("DELETE FROM rec_runs WHERE run_id = ?", (p["run_id"],))
        conn.execute(
            "INSERT INTO rec_runs(run_id, created_at, actor, profile_version, candidate_count,"
            " exploration_count, config_fingerprint) VALUES(?,?,?,?,?,?,?)",
            (p["run_id"], at, event.actor, p.get("profile_version"), p.get("candidate_count"),
             p.get("exploration_count"), event.config_fingerprint),
        )
        for item in json.loads(p["items_json"]):
            conn.execute(
                "INSERT INTO recommendations(run_id, book_id, rank, score, reasons_json, is_exploration)"
                " VALUES(?,?,?,?,?,?)",
                (p["run_id"], item["book_id"], item["rank"], item["score"], item.get("reasons_json"),
                 1 if item.get("is_exploration") else 0),
            )
    elif t == "eval_run":
        conn.execute(
            "INSERT INTO evaluation_runs(created_at, method, n_pos, n_neg, folds, auc, ci_low, ci_high, notes)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (at, p.get("method"), p.get("n_pos"), p.get("n_neg"), p.get("folds"), p.get("auc"),
             p.get("ci_low"), p.get("ci_high"), p.get("notes")),
        )
    elif t == "proposal_created":
        conn.execute(
            "INSERT INTO proposals(kind, payload_json, evidence_json, status, created_at) VALUES(?,?,?,'pending',?)",
            (p["kind"], p.get("payload_json"), p.get("evidence_json"), at),
        )
    elif t == "proposal_updated":
        # #42：提案状态变更（pending→accepted/rejected）经事件流落库，重放逐位
        # 恢复 status；不新增提案行。状态枚举守卫在写入方（reco-labels 适配器），
        # 投影层照旧只做赋值——与其他 UPDATE 分支同形，保证重放确定性。
        conn.execute("UPDATE proposals SET status = ? WHERE proposal_id = ?",
                     (p["status"], int(p["proposal_id"])))
    elif t == "mute_upserted":
        conn.execute(
            "INSERT INTO mutes(book_id, dedup_key, axis, kind, expired_at, remaining_days,"
            " updated_at, schema_version, config_fingerprint) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(book_id) DO UPDATE SET dedup_key=excluded.dedup_key,"
            " axis=excluded.axis, kind=excluded.kind, expired_at=excluded.expired_at,"
            " remaining_days=excluded.remaining_days, updated_at=excluded.updated_at,"
            " schema_version=excluded.schema_version, config_fingerprint=excluded.config_fingerprint",
            (event.book_id, p.get("dedup_key"), p["axis"], p["kind"], p["expired_at"],
             p.get("remaining_days"), at, event.schema_version, event.config_fingerprint),
        )
    elif t == "mute_released":
        conn.execute("DELETE FROM mutes WHERE book_id = ?", (event.book_id,))
    elif t in ("scan_started", "scan_completed", "rec_feedback", "perf_sample", "pool_added", "tag_hit"):
        pass  # 记账型事件：只进事件流，不投影（pool_added 交互类，§7 隔离；
        # tag_hit 口味事件由画像重放消费折叠 axis_weights，§7 数据模型）
    else:
        raise ValueError(f"未知事件类型: {t!r}")


# ---------------------------------------------------------------- Store

# PRAGMA 语句写成常量字面量：值来自 Synchronous 枚举（编译期常量），
# 不拼接、不插值（Code Scanning #6 的消除方式）。
_PRAGMA_SYNCHRONOUS = "PRAGMA synchronous = 1"   # Synchronous.NORMAL
_PRAGMA_BUSY_TIMEOUT = "PRAGMA busy_timeout = 5000"


def _configure(conn: sqlite3.Connection, *, writer: bool) -> None:
    conn.execute(_PRAGMA_SYNCHRONOUS)
    conn.execute(_PRAGMA_BUSY_TIMEOUT)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    if writer:
        conn.execute("PRAGMA journal_mode = WAL")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """单写者队列封装：所有写走唯一写连接（executor 串行化），读走独立连接。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.write_conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        _configure(self.write_conn, writer=True)
        self.write_conn.executescript(SCHEMA_SQL)
        # 老库迁移：身份唯一索引会把硬链接孪生判成冲突（#97），幂等拆除。
        self.write_conn.execute("DROP INDEX IF EXISTS uq_files_present")
        row = self.write_conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.write_conn.executemany(
                "INSERT INTO schema_meta(key, value) VALUES(?,?)",
                [("schema_version", SCHEMA_VERSION), ("created_at", _now_iso()), ("app_version", APP_VERSION)],
            )
        self._writer_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reco-single-writer")

    def close(self) -> None:
        self._writer_pool.shutdown(wait=True)
        self.write_conn.close()

    def read_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        _configure(conn, writer=False)
        return conn

    def submit(self, fn: Callable[[sqlite3.Connection], object]) -> "object":
        """把写任务排进单写者队列；返回 Future。任务持有唯一写连接。"""
        return self._writer_pool.submit(fn, self.write_conn)

    def append_event(
        self,
        event_type: str,
        *,
        book_id: int | None = None,
        payload: dict | None = None,
        occurred_at: str,
        config_fingerprint: str,
        actor: str = "system",
    ) -> int:
        """校验 payload → 写事件行 → 投影，同一短事务；返回 event_id。"""
        if not isinstance(event_type, str) or not event_type:
            raise PayloadViolationError("event_type 必须为非空字符串")
        payload_json = _validate_payload(dict(payload or {}))
        recorded_at = _now_iso()

        def job(conn: sqlite3.Connection) -> int:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    "INSERT INTO events(event_type, book_id, payload_json, occurred_at, recorded_at,"
                    " actor, schema_version, config_fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                    (event_type, book_id, payload_json, occurred_at, recorded_at, actor,
                     SCHEMA_VERSION, config_fingerprint),
                )
                event_id = int(cur.lastrowid)
                _apply_event(conn, Event(event_id, event_type, book_id, payload_json,
                                         occurred_at, recorded_at, actor, SCHEMA_VERSION, config_fingerprint))
                conn.execute("COMMIT")
                return event_id
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        return self.submit(job).result()

    def append(self, event: dict) -> int:
        """persist_feedback 鸭子协议适配（工单 10 约定：dict 事件交 store.append）。"""
        return self.append_event(
            str(event.get("event_type")),
            book_id=event.get("book_id"),
            payload=dict(event.get("payload") or {}),
            occurred_at=str(event["occurred_at"]),
            config_fingerprint=str(event.get("config_fingerprint") or "feedback/1"),
            actor="user",
        )

    def record_mute(
        self,
        book_id: int,
        *,
        dedup_key: str | None,
        axis: str,
        kind: str,
        expired_at: str,
        remaining_days: float,
        occurred_at: str,
        config_fingerprint: str,
        actor: str = "user",
    ) -> int:
        """静音落库（替换窗口语义）：mute_upserted 事件 + mutes 投影，同事务。"""
        return self.append_event(
            "mute_upserted",
            book_id=int(book_id),
            payload={
                "dedup_key": dedup_key, "axis": axis, "kind": kind,
                "expired_at": expired_at, "remaining_days": float(remaining_days),
            },
            occurred_at=occurred_at,
            config_fingerprint=config_fingerprint,
            actor=actor,
        )

    def release_mute(
        self, book_id: int, *, occurred_at: str, config_fingerprint: str,
        actor: str = "system",
    ) -> int:
        """静音解除（过期自动解除/人工解除）：mute_released 事件 + 零残留投影。"""
        return self.append_event(
            "mute_released", book_id=int(book_id), payload={},
            occurred_at=occurred_at, config_fingerprint=config_fingerprint, actor=actor,
        )

    def load_mutes(self) -> list[dict]:
        """读取当前静音表（重启恢复载体；返回数值/枚举行，不含正文）。"""
        conn = self.read_conn()
        try:
            rows = conn.execute(
                "SELECT book_id, dedup_key, axis, kind, expired_at, remaining_days,"
                " updated_at FROM mutes ORDER BY book_id"
            ).fetchall()
        finally:
            conn.close()
        return [
            {"book_id": int(r[0]), "dedup_key": r[1], "axis": r[2], "kind": r[3],
             "expired_at": r[4], "remaining_days": float(r[5]), "updated_at": r[6]}
            for r in rows
        ]

    def read_events(self) -> list[Event]:
        conn = self.read_conn()
        try:
            rows = conn.execute(
                "SELECT event_id, event_type, book_id, payload_json, occurred_at, recorded_at,"
                " actor, schema_version, config_fingerprint FROM events ORDER BY event_id"
            ).fetchall()
        finally:
            conn.close()
        return [Event(*r) for r in rows]

    # ---- ui_usage（W12b；§11.7 性能数据——独立表，绝不进口味事件流，C12）----

    def bump_ui_usage(self, page_id: str, *, now: str) -> None:
        """导航/功能点击计数 +1（page_id, uses, last_used）。

        写走单写者队列（submit）；ui_usage 不进事件流、不投影、不参与回放比对。
        """
        if not isinstance(page_id, str) or not page_id.strip():
            raise ValueError("page_id 必须为非空字符串")

        def job(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO ui_usage(page_id, uses, last_used) VALUES(?, 1, ?) "
                "ON CONFLICT(page_id) DO UPDATE SET uses = uses + 1, last_used = excluded.last_used",
                (page_id, now),
            )

        self.submit(job).result()

    def get_ui_usage(self) -> dict[str, dict]:
        """读 ui_usage 快照：{page_id: {uses, last_used}}（read_conn，WAL 读不阻塞写）。"""
        conn = self.read_conn()
        try:
            rows = conn.execute(
                "SELECT page_id, uses, last_used FROM ui_usage ORDER BY page_id"
            ).fetchall()
        finally:
            conn.close()
        return {r[0]: {"uses": int(r[1]), "last_used": r[2]} for r in rows}

    # ---- reading_progress（RR1/t1；§R4 阅读位置独立表，绝不进口味事件流）----

    def upsert_reading_progress(
        self,
        book_id: int,
        *,
        ext: str,
        char_offset: int | None = None,
        epub_spine_index: int | None = None,
        epub_block_pos: int | None = None,
        percent: float,
        updated_at: str,
        config_fingerprint: str = "ui/reader/1",
    ) -> None:
        """保存阅读位置（book_id 主键单行 upsert）。

        写走单写者队列（submit）；reading_progress 不进事件流、不投影、
        不参与回放比对（照 ui_usage 先例）。ext 为格式声明，坐标列自证格式。
        """
        if not isinstance(ext, str) or not ext.strip():
            raise ValueError("ext 必须为非空字符串")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise ValueError("updated_at 必须为非空 ISO8601 字符串")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            raise ValueError("percent 必须为数值")

        def job(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO reading_progress(book_id, char_offset, epub_spine_index,"
                " epub_block_pos, percent, updated_at, schema_version, config_fingerprint)"
                " VALUES(?,?,?,?,?,?,?,?)"
                " ON CONFLICT(book_id) DO UPDATE SET char_offset = excluded.char_offset,"
                " epub_spine_index = excluded.epub_spine_index,"
                " epub_block_pos = excluded.epub_block_pos, percent = excluded.percent,"
                " updated_at = excluded.updated_at,"
                " schema_version = excluded.schema_version,"
                " config_fingerprint = excluded.config_fingerprint",
                (int(book_id), char_offset, epub_spine_index, epub_block_pos,
                 float(percent), updated_at, SCHEMA_VERSION, config_fingerprint),
            )

        self.submit(job).result()

    def get_reading_progress(self, book_id: int) -> dict | None:
        """读单书阅读位置：{char_offset, epub_spine_index, epub_block_pos, percent,
        updated_at} 或 None（read_conn，WAL 读不阻塞写）。"""
        conn = self.read_conn()
        try:
            row = conn.execute(
                "SELECT char_offset, epub_spine_index, epub_block_pos, percent, updated_at"
                " FROM reading_progress WHERE book_id = ?",
                (int(book_id),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "char_offset": row[0],
            "epub_spine_index": row[1],
            "epub_block_pos": row[2],
            "percent": row[3],
            "updated_at": row[4],
        }

    def verify_replay(self) -> ReplayReport:
        """ADR-0003：事件流重放进全新库，逐表逐位比对；任何漂移都报告。"""
        events = self.read_events()
        diffs: list[str] = []
        with tempfile.TemporaryDirectory() as td:
            replay_path = Path(td) / "replay.db"
            rebuild_projections(replay_path, events)
            for table in COMPARE_TABLES:
                if _dump_rows(self.path, table) != _dump_rows(replay_path, table):
                    diffs.append(f"{table}: live={len(_dump_rows(self.path, table))} rows, replay differs")
        compare_rows = {table: _dump_rows(self.path, table) for table in COMPARE_TABLES}
        return ReplayReport(ok=not diffs, diffs=tuple(diffs), compare_rows=compare_rows)


_DUMP_SQL: dict[str, str] = {
    "volumes": "SELECT * FROM volumes ORDER BY rowid",
    "files": "SELECT * FROM files ORDER BY rowid",
    "books": "SELECT * FROM books ORDER BY rowid",
    "features": "SELECT * FROM features ORDER BY rowid",
    "feature_vectors": "SELECT * FROM feature_vectors ORDER BY rowid",
    "tags": "SELECT * FROM tags ORDER BY rowid",
    "tag_aliases": "SELECT * FROM tag_aliases ORDER BY rowid",
    "book_tags": "SELECT * FROM book_tags ORDER BY rowid",
    "relation_graphs": "SELECT * FROM relation_graphs ORDER BY rowid",
    "relation_fields": "SELECT * FROM relation_fields ORDER BY rowid",
    "signals": "SELECT * FROM signals ORDER BY rowid",
    "events": "SELECT * FROM events ORDER BY rowid",
    "profile_versions": "SELECT * FROM profile_versions ORDER BY rowid",
    "rec_runs": "SELECT * FROM rec_runs ORDER BY rowid",
    "recommendations": "SELECT * FROM recommendations ORDER BY rowid",
    "mutes": "SELECT * FROM mutes ORDER BY rowid",
}


def _dump_rows(db_path: str | Path, table: str) -> list[tuple]:
    """整表快照（重放比对用）：语句取自**常量映射**，不做任何字符串拼接。

    重放比对需遍历 15 张表，而 SQLite 不支持参数化标识符；这里把每条语句
    预先写成常量字面量，表名只用作字典键——无拼接、无用户输入路径
    （Code Scanning #6/#7 的消除方式）。
    """
    try:
        sql = _DUMP_SQL[table]
    except KeyError:
        raise ValueError(f"_dump_rows 只接受 COMPARE_TABLES 内的表名: {table!r}") from None
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def rebuild_projections(target: str | Path, events: Sequence[Event]) -> None:
    """把事件流重放进全新库（events 原样回放 + 逐事件投影）；目标库必须可新建。"""
    target_path = Path(target)
    if target_path.exists():
        target_path.unlink()
    store = open_store(target_path)
    try:
        conn = store.write_conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            for ev in events:
                conn.execute(
                    "INSERT INTO events(event_id, event_type, book_id, payload_json, occurred_at,"
                    " recorded_at, actor, schema_version, config_fingerprint) VALUES(?,?,?,?,?,?,?,?,?)",
                    (ev.event_id, ev.event_type, ev.book_id, ev.payload_json, ev.occurred_at,
                     ev.recorded_at, ev.actor, ev.schema_version, ev.config_fingerprint),
                )
                _apply_event(conn, ev)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        store.close()


def open_store(path: str | Path) -> Store:
    """打开（或创建）特征库；库级设置与 schema 初始化一次完成。

    新建库时收紧权限（POSIX 0600，CWE-732）：库内包含书名/路径/反馈等私人
    数据；Windows 无 POSIX 权限位，跳过。既有库不改权限。
    """
    p = Path(path)
    existed = p.exists()
    store = Store(p)
    if not existed and os.name == "posix":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return store