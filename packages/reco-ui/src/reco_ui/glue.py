"""GUI 反馈与数据 glue（工单 12 切片 2）。

原则：GUI 不造第二条写路径——全部落库经 reco_store.Store 单写者队列；
静音窗口复用 reco_core.feedback.feedback_effects（与 CLI 同一套契约）；
ui_usage 与口味事件流严格隔离（C12，本模块不触碰 ui_usage）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from reco_core.feedback import feedback_effects, record_feedback as _record_feedback
from reco_store import Store, dedup_key, normalize_book_name

GUI_SEED_FP = "gui/seed/1"
GUI_FEEDBACK_FP = "gui/feedback/1"

_VALID_KINDS = ("dislike", "like", "add_to_pool")


def _coerce_now(now) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now
    return datetime.fromisoformat(str(now))


def seed_books(store: Store, *, books: int, liked: int, candidate: int, now=None) -> list[str]:
    """合成数据播种：三专区 file_added（测试与离线演示用；全合成，无正文）。"""
    if books < liked + candidate:
        raise ValueError("books 必须 ≥ liked + candidate")
    at = _coerce_now(now).isoformat()
    zones = ["liked"] * liked + ["candidate"] * candidate + ["推荐专区"] * (books - liked - candidate)
    names: list[str] = []
    for i, zone in enumerate(zones, start=1):
        display = f"book-{i}.txt"
        payload = {
            "path": f"Z:/synthetic/{zone}/book-{i}.txt",
            "zone": zone,
            "ext": ".txt",
            "size_bytes": 1000 + i,
            "mtime_ns": 1_000_000_000 + i,
            "st_dev": 1,
            "st_ino": 100 + i,
            "display_name": display,
            "normalized_name": normalize_book_name(display),
            "dedup_key": dedup_key(1000 + i, display),
        }
        store.append_event("file_added", payload=payload, occurred_at=at,
                           config_fingerprint=GUI_SEED_FP, actor="system")
        names.append(display)
    return names


_ZONE_DISPLAY = {"liked": "喜欢专区", "candidate": "候选池", "推荐专区": "推荐专区"}


def zone_rows(store: Store, zone: str, *, limit: int = 200,
              offset: int = 0, keyword: str = "") -> list[tuple]:
    """专区行读取（独立只读连接；无正文，B 类审计点）。

    行 = (book_id, 名称, 大小, 备注)。

    #74：同一本书可能在多个专区各有一份实体文件（用户手动复制或历史遗留）。
    此时在备注列标注「另见：<其他专区>」，让用户看得见、自己决定；
    **不删除任何文件、不改变 book 归属**（擅自处理用户文件风险过高）。
    """
    conn = store.read_conn()
    try:
        # #76：keyword 走 SQL LIKE，因此能命中 limit 之外的书（不只是已加载的 200 行）
        kw = (keyword or "").strip()
        where = "f.zone = ? AND f.present = 1"
        params: list = [zone]
        if kw:
            # #91：关键词同时命中书名与标签（标签显示名/tag_id 子串）——按标签筛选书籍
            where += (" AND (b.display_name LIKE ? OR EXISTS ("
                      "SELECT 1 FROM book_tags bt JOIN tags t ON t.tag_id = bt.tag_id"
                      " WHERE bt.book_id = b.book_id"
                      " AND (t.label LIKE ? OR t.tag_id LIKE ?)))")
            params += ["%" + kw + "%", "%" + kw + "%", "%" + kw + "%"]
        raw = conn.execute(
            "SELECT b.book_id, b.display_name, b.size_bytes"
            " FROM files f JOIN books b ON b.book_id = f.book_id"
            f" WHERE {where}"
            " ORDER BY b.book_id LIMIT ? OFFSET ?",
            (*params, int(limit), int(max(0, offset))),
        ).fetchall()
        # 该页所有 book_id 在其他专区的分布
        ids = [int(r[0]) for r in raw]
        others: dict[int, set[str]] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for bid, z in conn.execute(
                f"SELECT DISTINCT book_id, zone FROM files"
                f" WHERE present = 1 AND zone <> ? AND book_id IN ({marks})",
                (zone, *ids),
            ).fetchall():
                others.setdefault(int(bid), set()).add(str(z))
    finally:
        conn.close()

    out: list[tuple] = []
    for r in raw:
        bid = int(r[0])
        zones = others.get(bid) or set()
        note = ""
        if zones:
            names = "、".join(_ZONE_DISPLAY.get(z, z) for z in sorted(zones))
            note = f"另见：{names}"
        out.append((bid, str(r[1]), int(r[2]), note))
    return out


def _dedup_of(store: Store, book_id: int) -> str | None:
    conn = store.read_conn()
    try:
        row = conn.execute("SELECT dedup_key FROM books WHERE book_id = ?", (book_id,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def record_feedback(store: Store, kind: str, book_id, axis=None, value=1.0, now=None) -> int:
    """三按钮唯一写入口：persist_feedback（append-only）+ dislike 静音窗口落库。

    dislike/like → rec_feedback（口味事件流）；add_to_pool → pool_added（不折叠）。
    返回 event_id。不触碰 ui_usage（C12）。
    """
    if kind not in _VALID_KINDS:
        raise ValueError(f"未知反馈类型: {kind!r}（允许 {_VALID_KINDS}）")
    now_dt = _coerce_now(now)
    book = int(book_id)
    # 共用写入口：除反馈事件外还折叠画像并落 profile_versions 快照（#71）。
    result = _record_feedback(kind, book_id=book, axis=axis, value=value,
                              store=store, now=now_dt)
    event_id = int(result["event"]["event_id"])
    if kind == "dislike":
        effects = feedback_effects(
            {"event_type": "rec_feedback", "event_id": event_id,
             "occurred_at": now_dt.isoformat(),
             "payload": {"axis": axis, "kind": kind, "value": float(value)},
             "book_id": book},
            now=now_dt)
        window = effects.get("mute_window")
        if window is not None:
            store.record_mute(
                book,
                dedup_key=_dedup_of(store, book),
                axis=str(axis),
                kind="dislike",
                expired_at=str(window.expired_at),
                remaining_days=float(window.remaining_days),
                occurred_at=now_dt.isoformat(),
                config_fingerprint=GUI_FEEDBACK_FP,
            )
    return event_id

def zone_meta(store: Store, zone: str, *, limit: int = 200) -> dict:
    """专区元信息：总数与是否被截断（#75 静默截断修复）。

    修复前用户看到 200 行却不知库里还有 1329 本；这里给出 {total, shown, truncated}，
    界面据此显示「显示 200 / 共 1529」。
    """
    conn = store.read_conn()
    try:
        total = int(conn.execute(
            "SELECT COUNT(*) FROM files WHERE zone = ? AND present = 1", (zone,)
        ).fetchone()[0])
    finally:
        conn.close()
    shown = min(total, int(limit))
    return {"zone": str(zone), "total": total, "shown": shown,
            "truncated": total > shown, "limit": int(limit)}
