"""工单 07 切片 4c：§6.1 公开 API 面完整性。

ADR-0001/§6.1 全部接口签名必须可导入；reco-relations 不跨包 import 上游
（duck-typed 独立接缝，A8）；包源码零 numpy/sklearn/torch/sqlite 依赖。
"""

from __future__ import annotations

from pathlib import Path

import reco_relations


def test_public_surface_complete() -> None:
    expected = {
        "empty_graph",
        "validate_relation_graph",
        "extract_relation_graph",
        "merge_manual_overrides",
        "relation_axes",
        "relation_similarity",
        "apply_auto_extraction",
        "snapshot_from_fields",
        "graph_field_paths",
        "save_relation_fields",
        "rebuild_graph_from_store",
        "explain_relation",
    }
    missing = {name for name in expected if not hasattr(reco_relations, name)}
    assert not missing, f"公开 API 缺失: {missing}"


def test_package_declares_no_third_party_runtime_deps() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'dependencies = [\n  "reco-core",\n]' in text  # 只依赖工作区内核包


def test_no_forbidden_imports_in_source() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "reco_relations"
    for py in src_root.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for banned in ("import numpy", "import sklearn", "import torch", "import sqlite3", "reco_store", "reco_io"):
            assert banned not in text, f"{py.name} 含禁止引用: {banned}"
