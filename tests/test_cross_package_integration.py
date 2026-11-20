"""W14 全验收修正——跨包集成测试（根 tests/，全仓安装环境执行）。

从 packages/reco-labels/tests/test_normalize_match.py 移入：reco_io→reco_labels
的 W4 接缝是**跨包集成**，不属 labels 单包独立可测契约（ADR-0001 独立 venv 演练
实锤：labels 独立安装后 ImportError）。根 tests/ 在全仓安装（reco-io+reco-labels
齐备）时执行；手算期望原样保留（独立来源，E4）。
"""
from __future__ import annotations

from reco_io.features import extract_statistics
from reco_labels import load_taxonomy, match_tags
from reco_labels.seed import build_seed_rows
from types import SimpleNamespace

SEED_MATCHABLE = ("mech.hypnosis", "催眠", "精神控制", "洗脑", "mind control")


def _taxonomy():
    from reco_labels import InMemoryLabelsStore

    store = InMemoryLabelsStore()
    for row in build_seed_rows():
        store.upsert_tag(row)
    return load_taxonomy(store)


def test_w4_seam_real_extractor_to_hits():
    """手算：样本含「精神控制」1 次、「洗脑」2 次 → 一个 TagHit，total=3。"""
    stats = extract_statistics(
        "他被精神控制了。随后被洗脑，洗脑很彻底。",
        alias_probe=SEED_MATCHABLE,
    )
    assert stats["alias_counts"] == {"mech.hypnosis": 0, "催眠": 0, "精神控制": 1, "洗脑": 2, "mind control": 0}
    hits = match_tags(stats, _taxonomy())
    assert len(hits) == 1
    assert hits[0].tag_id == "mech.hypnosis"
    assert hits[0].basis["total"] == 3
