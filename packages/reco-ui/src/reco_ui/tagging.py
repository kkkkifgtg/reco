"""GUI 侧画像接线（#85）：导入/再扫描/标为喜欢后打通 提取→挖掘→打标→画像折叠。

- 提取向量：reco_io.extract.extract_and_record（幂等；features/vectors 投影 upsert）；
- 打标：reco_labels.auto_tag.tag_book_and_fold（book_tags(source=auto) + proposals
  双层边界；仅 liked 折叠 tag_hit，candidate 不进画像——防未确认偏好污染）；
- 全部在池线程调用（§11.1 UI 线程零 DB/零阻塞）；正文只进分词器与提取器，
  计数以外的内容零落库（B 类）；
- 失败诚实降级：单本失败不阻断导入/扫描主流程（调用方 catch 后 warn）。
"""
from __future__ import annotations


def book_id_by_path(store, path: str) -> int | None:
    """files 投影按路径查 book_id（present=1）；查不到返回 None（诚实跳过）。"""
    conn = store.read_conn()
    try:
        row = conn.execute(
            "SELECT book_id FROM files WHERE path = ? AND present = 1"
            " AND book_id IS NOT NULL LIMIT 1", (str(path),)).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row is not None else None


def process_book_for_profile(store, path: str, zone: str, *, now: str) -> dict:
    """单本处理：提取向量 → 分词挖掘 → 打标 →（仅 liked）画像折叠。

    book_id 以 files.path 反查（导入/移动/扫描落库后必然存在）；查不到时
    返回 {"skipped": True}（诚实跳过，不猜）。
    """
    from reco_io.extract import extract_and_record
    from reco_io.mining import mine_alias_counts_with_variants
    from reco_labels.auto_tag import tag_book_and_fold

    book_id = book_id_by_path(store, path)
    if book_id is None:
        return {"skipped": True}
    extract_and_record(store, book_id, path, now=now, config_fingerprint="gui/extract/1")
    counts, variants = mine_alias_counts_with_variants(path)
    outcome = tag_book_and_fold(store, book_id, zone, counts, now=now,
                                alias_variants=variants,
                                config_fingerprint="gui/autotag/1")
    return {"book_id": book_id,
            "extracted": True,
            "tagged": bool(outcome.inserted or outcome.updated),
            "folded": zone == "liked",
            "inserted": list(outcome.inserted),
            "proposed": list(outcome.proposed),
            "alias_proposed": list(outcome.proposed_aliases)}
