"""每专区目录→库再扫描同步（工单 N1；GUI「刷新」与定时自动扫描的落库层）。

职责边界：
- 本模块 = 装配层：扫描快照（reco_io.scan_zones）+ 库前史（store 投影）
  → classify_delta 三态判定 → file_added/file_removed/file_moved 事件落库
  （Store 单写者队列，append-only 事件流 ADR-0003）；
- 零第二条写路径：所有写经 store.append_event；UI 层（app.py/pages.py）只
  发起与渲染，不触碰文件身份或 payload 构造；
- 身份域全程无符号 64 位（st_dev/st_ino 原值）；落库编码 = reco_io.int64
  补码回绕（Windows 无符号值超 SQLite INTEGER 上界的契约处理）；
- 专区缺目录不报错：sync 返回零动作（刷新按钮在空目录态也可点，诚实反馈）。

前史形态（files 投影行，按 path 索引，present=1 且专区匹配）：
- (dev, ino) 从 db 侧 int64 还原无符号（from_int64），与扫描快照无符号配对；
- 专区口径：本专区 present=1 的行才进前史——跨专区移动 = 旧专区「移除」
  （墓碑）+ 新专区「新增」（目标专区对该身份无前史；dedup 按 books 表归并，
  book_id 承接见 store 层契约测试）。

三态落库口径（ARCHITECTURE §8 流程 4 的本票实现）：
- added/removed/moved 各一条事件；内容变化（同身份同路径 size/mtime 变）
  收敛进 file_added upsert（统计位刷新，不逐轮重报）；
- 无变化零事件（幂等）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from reco_io.int64 import from_int64, to_int64
from reco_io.scanner import scan_zones
from reco_io.perf import perf_span
from reco_store import Store, dedup_key, normalize_book_name
from reco_store.reads import zone_file_identities

# GUI 再扫描落库口径（config_fingerprint 留痕；扫描/导入/种子各有前缀不混流）
SYNC_SCAN_FP = "gui/resync/1"

# 允许落库的专区名（files.zone CHECK 域；page_id 与专区名同构）
SYNC_ZONES = ("liked", "candidate", "推荐专区")


@dataclass(frozen=True)
class SyncResult:
    """单专区再扫描摘要（数值/枚举，无正文——审计口径 B 类）。"""

    added: int
    removed: int
    moved: int
    changed: int = 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprints(result) -> tuple:
    return tuple(sorted(result.fingerprints))


def _zone_history(store: Store, zone: str) -> dict[str, tuple[int, int, int, int]]:
    """本专区 present=1 前史：path → (dev, ino, size, mtime_ns)（身份域无符号）。"""
    history: dict[str, tuple[int, int, int, int]] = {}
    for path, st_dev, st_ino, size, mtime in zone_file_identities(store, zone):
        history[path] = (
            from_int64(int(st_dev)),  # db 侧 int64 → 无符号（配对域）
            from_int64(int(st_ino)),
            int(size),
            int(mtime),
        )
    return history


def _file_added_payload(fp, *, zone: str) -> dict:
    """FileFingerprint → file_added payload（键集与 seed_books/ImportWorker 同构）。"""
    name = os.path.basename(fp.path)
    return {
        "path": str(fp.path),
        "zone": zone,
        "ext": str(fp.ext),
        "size_bytes": int(fp.size_bytes),
        "mtime_ns": int(fp.mtime_ns),
        "st_dev": to_int64(int(fp.st_dev)),
        "st_ino": to_int64(int(fp.st_ino)),
        "display_name": name,
        "normalized_name": normalize_book_name(name),
        "dedup_key": dedup_key(int(fp.size_bytes), name),
    }


def sync_zone(
    store: Store,
    zone: str,
    data_root,
    *,
    zones: tuple[str, ...] = SYNC_ZONES,
    stat_provider=None,
    occurred_at: str | None = None,
) -> SyncResult:
    """单专区目录→库同步：扫描 → 差分 → 三态事件落库（短事务，单写者队列）。

    - zones 形参为 scan_zones 的完整专区表（专区的缺失目录由扫描器上报，
      不影响本专区判定）；
    - stat_provider 可注入 FakeStat（§13 测试口径）；
    - 返回 SyncResult(added, removed, moved, changed)。
    """
    if zone not in SYNC_ZONES:
        raise ValueError(f"未知专区: {zone!r}（允许 {SYNC_ZONES}）")
    now = occurred_at or _now_iso()
    root = Path(data_root)
    result = scan_zones(root, zones, stat_provider=stat_provider) if os.path.isdir(root / zone) else None
    if result is None:
        return SyncResult(added=0, removed=0, moved=0)  # 专区缺目录：零动作（不猜意图，§8.1）
    # scan_zones 返回全部专区指纹；本专区同步只消费本专区的当前快照
    # （跨专区移动由源专区「移除」+目标专区「新增」两轮各落一态，§8 口径）。
    current = tuple(fp for fp in _fingerprints(result) if fp.zone == zone)
    history = _zone_history(store, zone)
    with perf_span("ui.sync_zone"):
        added: list = []
        changed: list = []
        for fp in current:
            prev = history.pop(fp.path, None)
            if prev is None:
                added.append(fp)
            elif (prev[2], prev[3]) != (int(fp.size_bytes), int(fp.mtime_ns)):
                changed.append(fp)  # 同身份同路径内容变化 → file_added upsert 刷新
            else:
                pass  # 未变：零事件
        # 同专区移动判定（本票口径）：新增候选与「消失前史」按无符号身份四元组
        # (dev, ino, size, mtime_ns) 配对 → file_moved（dedup_key 继承 book_id）。
        # 配对确定性：added 按 path 序消费；每身份至多配一个（重复身份=硬链接
        # 孪生形态，桶按计数消费）。配不上的消失前史 = 移除（墓碑）。
        vanished: dict[tuple[int, int, int, int], list[tuple[str, tuple]]] = {}
        for path, prev in history.items():
            vanished.setdefault(prev, []).append((path, prev))
        moved: list[tuple[str, object]] = []
        still_added: list = []
        for fp in sorted(added, key=lambda f: f.path):
            key = (int(fp.st_dev), int(fp.st_ino), int(fp.size_bytes), int(fp.mtime_ns))
            bucket = vanished.get(key)
            if bucket:
                old_path, _prev = bucket.pop(0)
                if not bucket:
                    del vanished[key]
                moved.append((old_path, fp))
            else:
                still_added.append(fp)
        removed = [path for bucket in vanished.values() for path, _prev in bucket]
        for fp in still_added:
            store.append_event("file_added", payload=_file_added_payload(fp, zone=zone),
                               occurred_at=now, config_fingerprint=SYNC_SCAN_FP, actor="system")
        for fp in changed:
            store.append_event("file_added", payload=_file_added_payload(fp, zone=zone),
                               occurred_at=now, config_fingerprint=SYNC_SCAN_FP, actor="system")
        for path in removed:
            store.append_event("file_removed", payload={"path": path},
                               occurred_at=now, config_fingerprint=SYNC_SCAN_FP, actor="system")
        for old_path, fp in moved:
            store.append_event("file_moved", payload={
                "path": old_path,
                "new_path": str(fp.path),
                "zone": zone,
            }, occurred_at=now, config_fingerprint=SYNC_SCAN_FP, actor="system")
        return SyncResult(added=len(still_added), removed=len(removed), moved=len(moved), changed=len(changed))