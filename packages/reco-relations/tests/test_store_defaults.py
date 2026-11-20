"""工单 07 切片 4b：存储层默认拒绝（D2 纵深，L611）。

save_relation_fields 默认只收 source=auto；manual 只能走显式 edit 字段。
rebuild_graph_from_store 永不把 manual 字段改写成 auto（graph_json 可重建）。
"""

from __future__ import annotations

import pytest

import reco_relations as rr


def test_save_defaults_to_auto() -> None:
    store: dict = {}
    rr.save_relation_fields(store, {"topology": "1v1"})
    assert store["fields"]["topology"]["source"] == "auto"


def test_save_manual_requires_edit() -> None:
    store: dict = {}
    with pytest.raises(ValueError, match="edit=True"):
        rr.save_relation_fields(store, {"topology": "三角"}, source="manual")


def test_save_manual_with_edit_marks_manual() -> None:
    store: dict = {}
    rr.save_relation_fields(store, {"topology": "三角"}, source="manual", edit=True)
    assert store["fields"]["topology"]["source"] == "manual"


def test_rebuild_preserves_manual_never_d2() -> None:
    store: dict = {}
    rr.save_relation_fields(store, {"edges[c1->c2].consent": "自愿"}, source="manual", edit=True)
    # 扫描重提换 EdgeKey：manual 标注保留，绝不变 auto
    rebuilt = rr.rebuild_graph_from_store(store, {"edges[c1->c2]": "edges[c1->c3]"})
    assert rebuilt["fields"]["edges[c1->c3].consent"] == {"value": "自愿", "source": "manual"}


def test_rebuild_maps_renamed_edge_key() -> None:
    store: dict = {}
    rr.save_relation_fields(store, {"edges[c1->c2].type": "对等"})
    rebuilt = rr.rebuild_graph_from_store(store, {"edges[c1->c2]": "edges[c1->c3]"})
    assert rebuilt["fields"]["edges[c1->c3].type"]["value"] == "对等"
    assert "edges[c1->c2]" not in str(rebuilt["fields"])
