"""人物关系数据与相似度（工单 07 实现：§6.1 接口契约）。"""

from __future__ import annotations

from .explain import explain_relation
from .extract import extract_relation_graph
from .fields import (
    apply_auto_extraction,
    graph_field_paths,
    merge_manual_overrides,
    snapshot_from_fields,
)
from .graph import (
    AGE_GROUP_VALUES,
    CONSENT_VALUES,
    EDGE_TYPE_VALUES,
    EVIDENCE_SOURCE_VALUES,
    EVOLUTION_VALUES,
    POWER_BALANCE_VALUES,
    ROLE_VALUES,
    TOPOLOGY_VALUES,
    empty_graph,
    validate_relation_graph,
)
from .similarity import relation_axes, relation_similarity
from .storage import rebuild_graph_from_store, save_relation_fields

__all__ = [
    "AGE_GROUP_VALUES",
    "CONSENT_VALUES",
    "EDGE_TYPE_VALUES",
    "EVIDENCE_SOURCE_VALUES",
    "EVOLUTION_VALUES",
    "POWER_BALANCE_VALUES",
    "ROLE_VALUES",
    "TOPOLOGY_VALUES",
    "apply_auto_extraction",
    "empty_graph",
    "explain_relation",
    "extract_relation_graph",
    "graph_field_paths",
    "merge_manual_overrides",
    "rebuild_graph_from_store",
    "relation_axes",
    "relation_similarity",
    "save_relation_fields",
    "snapshot_from_fields",
    "validate_relation_graph",
]

__version__ = "0.1.0"
