"""files/books 读面唯一实现（#105）。

UI/CLI 过去各自手写 FROM files：口径（present=1、book_id 非空、file_id 定序、
双区提示、last_seen_at 过滤）散在十来个调用点，改投影或改口径时漏改就是第二种
真相。这里把读 SQL 收成一处，调用方只拿行/None。

纪律：只读（store.read_conn）、零写库；返回的行已转成 Python 标量，调用方不再
关心 sqlite 的行形态。
"""
from __future__ import annotations

__all__ = [
    "book_id_by_path",
    "count_present_in_zone",
    "first_present_file",
    "first_present_path",
    "present_book_zones",
    "present_files",
    "present_files_in_zone",
    "source_row",
    "zone_file_identities",
    "zone_page",
]


def _fetch(store, sql: str, params=()):
    conn = store.read_conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def present_files(store) -> list[tuple[int, str, int]]:
    """present=1 且有书号：[(book_id, path, size_bytes)]，按 book_id, path 定序。"""
    rows = _fetch(store,
        "SELECT book_id, path, size_bytes FROM files"
        " WHERE present = 1 AND book_id IS NOT NULL"
        " ORDER BY book_id, path")
    return [(int(b), str(p), int(s)) for b, p, s in rows]


def present_book_zones(store) -> list[tuple[int, str]]:
    """present=1 且有书号：[(book_id, zone)]（同书双区各一行，由调用方去重）。"""
    rows = _fetch(store,
        "SELECT book_id, zone FROM files WHERE present = 1 AND book_id IS NOT NULL")
    return [(int(b), str(z or "")) for b, z in rows]


def book_id_by_path(store, path, *, zone: str | None = None) -> int | None:
    """路径反查 book_id（present=1；可限定专区）；查不到返回 None。"""
    sql = ("SELECT book_id FROM files WHERE path = ? AND present = 1"
           " AND book_id IS NOT NULL")
    params: list = [str(path)]
    if zone is not None:
        sql += " AND zone = ?"
        params.append(str(zone))
    rows = _fetch(store, sql + " LIMIT 1", tuple(params))
    return int(rows[0][0]) if rows else None


def first_present_file(store, book_id: int, *, zone: str | None = None) -> tuple[str, str] | None:
    """该书（可限定专区）file_id 最小的 present 行 → (path, zone)；无则 None。"""
    sql = "SELECT path, zone FROM files WHERE book_id = ? AND present = 1"
    params: list = [int(book_id)]
    if zone is not None:
        sql += " AND zone = ?"
        params.append(str(zone))
    rows = _fetch(store, sql + " ORDER BY file_id LIMIT 1", tuple(params))
    return (str(rows[0][0]), str(rows[0][1])) if rows else None


def first_present_path(store, book_id: int, *, zone: str | None = None) -> str | None:
    """同 first_present_file，只要路径。"""
    row = first_present_file(store, book_id, zone=zone)
    return row[0] if row is not None else None


def source_row(store, book_id: int, zone: str) -> tuple[str, str] | None:
    """本专区该书（file_id 最小）的 (现存路径, 书名)；书名缺失给空串，无行返回 None。"""
    rows = _fetch(store,
        "SELECT f.path, b.display_name FROM files f"
        " LEFT JOIN books b ON b.book_id = f.book_id"
        " WHERE f.book_id = ? AND f.zone = ? AND f.present = 1"
        " ORDER BY f.file_id LIMIT 1", (int(book_id), str(zone)))
    if not rows:
        return None
    path, name = rows[0]
    return str(path), ("" if name is None else str(name))


def present_files_in_zone(store, zone: str, *, since: str | None = None) -> list[tuple[int, str, str]]:
    """专区 present=1 的 [(book_id, path, zone)]；since 限定本轮同步触及（last_seen_at）。"""
    sql = ("SELECT DISTINCT book_id, path, zone FROM files"
           " WHERE zone = ? AND present = 1 AND book_id IS NOT NULL")
    params: list = [str(zone)]
    if since is not None:
        sql += " AND last_seen_at >= ?"
        params.append(str(since))
    rows = _fetch(store, sql, tuple(params))
    return [(int(b), str(p), str(z)) for b, p, z in rows]


def count_present_in_zone(store, zone: str) -> int:
    """专区 present=1 行数（#75 截断提示的 total）。"""
    rows = _fetch(store,
        "SELECT COUNT(*) FROM files WHERE zone = ? AND present = 1", (str(zone),))
    return int(rows[0][0]) if rows else 0


def zone_file_identities(store, zone: str) -> list[tuple[str, int, int, int, int]]:
    """专区 present=1 的身份快照：[(path, st_dev, st_ino, size_bytes, mtime_ns)]。"""
    rows = _fetch(store,
        "SELECT path, st_dev, st_ino, size_bytes, mtime_ns FROM files"
        " WHERE zone = ? AND present = 1", (str(zone),))
    return [(str(p), int(d), int(i), int(s), int(m)) for p, d, i, s, m in rows]


def zone_page(store, zone: str, *, keyword: str | None = None, limit: int = 200,
              offset: int = 0) -> list[tuple[int, str, int, frozenset[str]]]:
    """专区一页：[(book_id, display_name, size_bytes, 其他专区集合)]。

    keyword 走 SQL LIKE（#76：能命中 limit 之外的书），同时命中书名与标签
    （#91：标签显示名 / tag_id 子串）。其他专区的集合原样返回——专区显示名是
    UI 词汇，不在 store 层拼。
    """
    where = "f.zone = ? AND f.present = 1"
    params: list = [str(zone)]
    kw = (keyword or "").strip()
    if kw:
        where += (" AND (b.display_name LIKE ? OR EXISTS ("
                  "SELECT 1 FROM book_tags bt JOIN tags t ON t.tag_id = bt.tag_id"
                  " WHERE bt.book_id = b.book_id"
                  " AND (t.label LIKE ? OR t.tag_id LIKE ?)))")
        params += ["%" + kw + "%"] * 3
    conn = store.read_conn()
    try:
        raw = conn.execute(
            "SELECT b.book_id, b.display_name, b.size_bytes"
            " FROM files f JOIN books b ON b.book_id = f.book_id"
            f" WHERE {where}"
            " ORDER BY b.book_id LIMIT ? OFFSET ?",
            (*params, int(limit), int(max(0, offset))),
        ).fetchall()
        ids = [int(r[0]) for r in raw]
        others: dict[int, set[str]] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for bid, z in conn.execute(
                "SELECT DISTINCT book_id, zone FROM files"
                f" WHERE present = 1 AND zone <> ? AND book_id IN ({marks})",
                (str(zone), *ids),
            ).fetchall():
                others.setdefault(int(bid), set()).add(str(z))
    finally:
        conn.close()
    return [
        (int(bid), str(name), int(size), frozenset(others.get(int(bid)) or ()))
        for bid, name, size in raw
    ]