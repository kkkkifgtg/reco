"""标签体系（数据驱动）——reco-labels 公开 API（工单 06；tag model）。

数据契约 §6.2；表契约 §7.2（tags/tag_aliases/book_tags/proposals 语义）；
接口只依赖窄协议 LabelsStore（reco-store 实现由 W5 对接，本包不 import）。
"""
from __future__ import annotations

from reco_labels.auto_tag import (
    AutoTagOutcome,
    TAG_PROPOSE_THRESHOLD,
    aliases_for_match,
    auto_tag_book,
)
from reco_labels.coverage import Coverage, axis_coverage, axis_vector
from reco_labels.lifecycle import (
    MergeResult,
    add_tag,
    archive_tag,
    merge_tags,
    set_aliases,
)
from reco_labels.match import Evidence, TagHit, explain_tag_hit, match_tags
from reco_labels.proposals import (
    TagProposal,
    accept_proposal,
    propose_tags,
    reject_proposal,
)
from reco_labels.record import RecordResult, record_tag_hits
from reco_labels.store import (
    BookTagRow,
    EventRow,
    InMemoryLabelsStore,
    LabelsStore,
    ManualTagProtected,
    ProposalRow,
    TagRow,
)
from reco_labels.taxonomy import Taxonomy, load_taxonomy, normalize_tag

__all__ = [
    "AutoTagOutcome",
    "BookTagRow",
    "Coverage",
    "TAG_PROPOSE_THRESHOLD",
    "aliases_for_match",
    "auto_tag_book",
    "Evidence",
    "EventRow",
    "InMemoryLabelsStore",
    "LabelsStore",
    "ManualTagProtected",
    "MergeResult",
    "ProposalRow",
    "RecordResult",
    "TagHit",
    "TagProposal",
    "TagRow",
    "Taxonomy",
    "accept_proposal",
    "add_tag",
    "archive_tag",
    "axis_coverage",
    "axis_vector",
    "explain_tag_hit",
    "load_taxonomy",
    "match_tags",
    "merge_tags",
    "normalize_tag",
    "propose_tags",
    "record_tag_hits",
    "reject_proposal",
    "set_aliases",
]

__version__ = "0.1.0"
