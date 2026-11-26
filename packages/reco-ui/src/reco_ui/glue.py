"""GUI 反馈与数据 glue（工单 12 切片 2）。

原则：GUI 不造第二条写路径——全部落库经 reco_store.Store 单写者队列；
静音窗口与文案由 reco_store.feedback 统一完成（与 CLI 同一个入口，不各写一遍）；
ui_usage 与口味事件流严格隔离（C12，本模块不触碰 ui_usage）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from reco_store import Store, dedup_key, normalize_book_name
from reco_store.reads import count_present_in_zone, zone_page
from reco_store.recommend import load_axis_ids

GUI_SEED_FP = "gui/seed/1"
GUI_FEEDBACK_FP = "gui/feedback/1"

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
    rows = zone_page(store, zone, keyword=keyword, limit=limit, offset=offset)
    out: list[tuple] = []
    for bid, name, size, zones in rows:
        note = ""
        if zones:
            names = "、".join(_ZONE_DISPLAY.get(z, z) for z in sorted(zones))
            note = f"另见：{names}"
        out.append((bid, name, size, note))
    return out


def record_feedback(store: Store, kind: str, book_id, axis=None, value=1.0, now=None) -> int:
    """三按钮唯一写入口（委托 reco_store.feedback）：返回 event_id。

    静音窗、文案、画像快照都在那个入口里完成——GUI 不再自己编排一遍（#101）。
    """
    from reco_store.feedback import record_feedback as _store_record

    receipt = _store_record(store, kind, int(book_id), axis=axis, value=value,
                            now=_coerce_now(now),
                            config_fingerprint=GUI_FEEDBACK_FP,
                            axes=load_axis_ids(store))
    return int(receipt["event_id"])

def zone_meta(store: Store, zone: str, *, limit: int = 200) -> dict:
    """专区元信息：总数与是否被截断（#75 静默截断修复）。

    修复前用户看到 200 行却不知库里还有 1329 本；这里给出 {total, shown, truncated}，
    界面据此显示「显示 200 / 共 1529」。
    """
    total = count_present_in_zone(store, zone)
    shown = min(total, int(limit))
    return {"zone": str(zone), "total": total, "shown": shown,
            "truncated": total > shown, "limit": int(limit)}