"""N1——file_added/file_moved 投影的再扫描契约（store 层，事件直落）。

- file_added 幂等：同 path 二次落库 upsert（不重复建行、不撞 UNIQUE(path)）；
- file_removed → present=0（墓碑）；其后同 path file_added 复活同一行（不留鬼行）；
- file_moved 携带 zone → 同步搬迁专区（跨专区移动语义）；不带 → 保持原专区；
- int64 回绕身份：payload 原样入库（store 不做数值变换），无符号配对在上层还原。
回放安全：全部语义经 _apply_event 单点实现，rebuild_projections 重放逐位一致。
"""

from __future__ import annotations

import pytest


def _payload(store_module, **over):
    p = {
        "path": "F:/liked/a.txt",
        "zone": "liked",
        "ext": ".txt",
        "size_bytes": 100,
        "mtime_ns": 111,
        "st_dev": 1,
        "st_ino": 10,
        "display_name": "a.txt",
        "normalized_name": "a.txt",
        "dedup_key": store_module.dedup_key(100, "a.txt"),
    }
    p.update(over)
    return p


def test_file_added_twice_upserts_without_new_rows(store, store_module):
    payload = _payload(store_module)
    store.append_event("file_added", payload=payload,
                       occurred_at="2026-11-21T01:00:00", config_fingerprint="t/n1")
    payload2 = dict(payload, mtime_ns=222)
    store.append_event("file_added", payload=payload2,
                       occurred_at="2026-11-21T01:00:01", config_fingerprint="t/n1")
    conn = store.read_conn()
    try:
        rows = conn.execute("SELECT path, present, mtime_ns, last_seen_at FROM files").fetchall()
        n_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        conn.close()
    assert len(rows) == 1, "同 path 二次 file_added 必须 upsert 同一行，不得撞 UNIQUE(path)"
    assert rows[0][1] == 1 and rows[0][2] == 222, "同身份内容刷新必须更新统计位"
    assert rows[0][3] == "2026-11-21T01:00:01", "last_seen_at 必须随最后一次出现推进"
    assert n_books == 1


def test_file_added_after_remove_revives_same_row(store, store_module):
    payload = _payload(store_module)
    store.append_event("file_added", payload=payload,
                       occurred_at="2026-11-21T01:00:00", config_fingerprint="t/n1")
    store.append_event("file_removed", payload={"path": payload["path"]},
                       occurred_at="2026-11-21T01:00:01", config_fingerprint="t/n1")
    # 真实重扫场景：删除后重建，st_ino 变化、同路径同名同大小（同 dedup_key）
    payload3 = dict(payload, st_ino=555, mtime_ns=333)
    store.append_event("file_added", payload=payload3,
                       occurred_at="2026-11-21T01:00:02", config_fingerprint="t/n1")
    conn = store.read_conn()
    try:
        rows = conn.execute(
            "SELECT path, present, st_ino FROM files ORDER BY file_id").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "复活必须复用原行，不得留墓碑鬼行（用户重扫主场景）"
    assert rows[0][1] == 1 and rows[0][2] == 555


def test_file_moved_with_zone_moves_zone(store, store_module):
    payload = _payload(store_module)
    store.append_event("file_added", payload=payload,
                       occurred_at="2026-11-21T01:00:00", config_fingerprint="t/n1")
    store.append_event(
        "file_moved",
        payload={"path": payload["path"], "new_path": "F:/candidate/a.txt", "zone": "candidate"},
        occurred_at="2026-11-21T01:00:01", config_fingerprint="t/n1")
    conn = store.read_conn()
    try:
        path, zone, moved_from = conn.execute(
            "SELECT path, zone, moved_from_path FROM files").fetchone()
    finally:
        conn.close()
    assert path == "F:/candidate/a.txt" and zone == "candidate", "file_moved 携带 zone 必须同步搬迁专区"
    assert moved_from == "F:/liked/a.txt"


def test_file_moved_without_zone_keeps_zone(store, store_module):
    payload = _payload(store_module)
    store.append_event("file_added", payload=payload,
                       occurred_at="2026-11-21T01:00:00", config_fingerprint="t/n1")
    store.append_event(
        "file_moved",
        payload={"path": payload["path"], "new_path": "F:/liked/sub/a.txt"},
        occurred_at="2026-11-21T01:00:01", config_fingerprint="t/n1")
    conn = store.read_conn()
    try:
        zone = conn.execute("SELECT zone FROM files").fetchone()[0]
    finally:
        conn.close()
    assert zone == "liked", "不带 zone 的 file_moved 保持原专区（向后兼容）"


def test_wrapped_identity_stored_verbatim_and_replay_ok(store, store_module):
    st_dev = -(1 << 63)  # 无符号 2^63 的补码回绕形态（payload 编码契约的入库侧）
    payload = _payload(store_module, st_dev=st_dev)
    store.append_event("file_added", payload=payload,
                       occurred_at="2026-11-21T01:00:00", config_fingerprint="t/n1")
    conn = store.read_conn()
    try:
        vol_dev, file_dev = conn.execute(
            "SELECT v.st_dev, f.st_dev FROM files f JOIN volumes v ON v.volume_id = f.volume_id"
        ).fetchone()
    finally:
        conn.close()
    assert vol_dev == st_dev and file_dev == st_dev, "store 原样存回绕值，不做二次变换"
    assert st_dev % (1 << 64) == (1 << 63), "按无符号解释可还原原身份（配对在上层）"
    assert store.verify_replay().ok, "upsert 语义必须回放一致（ADR-0003）"
