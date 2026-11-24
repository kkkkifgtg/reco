"""#71 —— 画像版本快照落库（profile_versions）的跨包集成测试（根 tests/，CODING-STANDARDS §2）。

被测链路：反馈写入口 → rec_feedback 落库 → update_profile 折叠 → profile_updated
快照 append 进事件流 → profile_versions 投影。跨 reco-core / reco-store / reco-cli /
reco-ui，故放根 tests/ 并标 integration（ADR-0001「包独立可测」）。

红测试（修复前）：profile_versions 表 0 行 —— 画像只有内存态，无历史版本。
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration

T0 = "2026-11-21T00:00:00+00:00"

_PROFILE_COLS = (
    "created_at, axis_weights_json, sample_counts_json, confidence, top_tags_json,"
    " weights_json, half_life_days_json, reason, event_id_high_water, config_fingerprint"
)


def _seed(store) -> None:
    """真实 book 行（mutes 有外键）+ 两条 tag_hit（画像有真实轴权重）。"""
    from reco_ui.glue import seed_books

    seed_books(store, books=4, liked=2, candidate=2, now=T0)
    for axis, book in (("mechanism", 1), ("mechanism", 1), ("mood.dark", 2)):
        store.append_event(
            "tag_hit",
            book_id=book,
            payload={"axis": axis, "tag_id": axis, "confidence": 1.0},
            occurred_at=T0,
            config_fingerprint="t71/seed",
        )


def _snapshot_rows(store):
    conn = store.read_conn()
    try:
        return conn.execute(
            f"SELECT {_PROFILE_COLS} FROM profile_versions ORDER BY version_id"
        ).fetchall()
    finally:
        conn.close()


def _last_event_id(store, event_type: str) -> int:
    conn = store.read_conn()
    try:
        return int(conn.execute(
            "SELECT max(event_id) FROM events WHERE event_type = ?", (event_type,)
        ).fetchone()[0])
    finally:
        conn.close()


def test_feedback_writes_profile_version_snapshot(tmp_path):
    """给一次反馈 → profile_versions 增加 1 行，内容与 export_profile 一致。"""
    from reco_core.feedback import rehydrate_from_events
    from reco_core.profile import export_profile
    from reco_store import open_store
    from reco_ui.glue import record_feedback

    store = open_store(tmp_path / "pv.db")
    try:
        _seed(store)
        assert _snapshot_rows(store) == []  # 修复前：快照从不落库

        record_feedback(store, "dislike", book_id=2, axis="mood.dark", value=1.0, now=T0)
        rows = _snapshot_rows(store)
        assert len(rows) == 1, "一次反馈应落一行画像版本快照"
        (created_at, axis_json, counts_json, confidence, top_json,
         weights_json, half_life_json, reason, high_water, fingerprint) = rows[0]
        # 与 export_profile 同源（acceptance：内容与 export_profile 一致）。
        # 注意：per_axis/axis_weights 是带衰减的折叠态，比较必须钉在与快照相同
        # 的时钟（T0）上——load_profile 用墙钟重放，权重随真实时间衰减漂移，
        # 逐位相等不可能成立（评审修复时实测：2.0 vs 1.9457…）。
        exported = export_profile(rehydrate_from_events(store.read_events(), now=T0))
        assert json.loads(axis_json) == exported["axis_weights"]
        # acceptance「内容与 export_profile 一致」：快照必须是完整 sample_counts
        # （含 per_axis —— _Folder.restore 依赖它恢复 Laplace 折叠态；三键口径会让
        # restore 失忆，评审 M1）。behavior_signals/completeness 的持久化需
        # profile_versions 表结构 v2（§5.3 迁移纪律），不在本 PR 范围，见 PR body 披露。
        assert json.loads(counts_json) == exported["sample_counts"]
        assert confidence == pytest.approx(exported["confidence"]["value"], abs=1e-12)
        # top_tags 存的是未衰减的轴权重和（[[axis, weight], ...]），顺序与 export_profile 一致
        assert [t[0] for t in json.loads(top_json)] == [t["axis"] for t in exported["top_tags"]]
        assert json.loads(weights_json)["version"] == exported["weights"]["version"]
        assert json.loads(half_life_json) == exported["half_life_days"]
        assert reason.startswith("profile v1"), reason
        # 折叠到本次反馈事件为止（快照自身不计入）
        assert high_water == _last_event_id(store, "rec_feedback")
        assert created_at and fingerprint  # 时间与 config_fingerprint 都落库

        # 再来一次反馈 → 第二行（版本号递增）
        record_feedback(store, "like", book_id=3, axis="mood.dark", value=1.0,
                        now="2026-11-21T00:00:01+00:00")
        rows = _snapshot_rows(store)
        assert len(rows) == 2
        assert rows[1][7].startswith("profile v2"), rows[1][7]

        # 快照是普通事件：重放逐位一致（profile_versions 在 COMPARE_TABLES 内）
        assert store.verify_replay().ok
    finally:
        store.close()


def test_add_to_pool_does_not_snapshot(tmp_path):
    """add_to_pool 不进口味流（§7 隔离）→ 不产生画像版本快照。"""
    from reco_store import open_store
    from reco_ui.glue import record_feedback

    store = open_store(tmp_path / "pv-pool.db")
    try:
        _seed(store)
        record_feedback(store, "add_to_pool", book_id=3, now=T0)
        assert _snapshot_rows(store) == []
    finally:
        store.close()


def test_cli_feedback_writes_snapshot(tmp_path):
    """CLI 写入口（cmd_feedback）同样落快照：两入口不分裂（CLI/GUI 一致）。"""
    import contextlib
    import io

    from reco_cli.app import cmd_feedback
    from reco_store import open_store

    db = tmp_path / "pv-cli.db"
    store = open_store(db)
    try:
        _seed(store)
    finally:
        store.close()

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cmd_feedback("dislike", db=db, book_id=2, axis="mood.dark") == 0

    store = open_store(db)
    try:
        rows = _snapshot_rows(store)
        assert len(rows) == 1
        assert rows[0][7].startswith("profile v1")
    finally:
        store.close()
