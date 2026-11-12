"""diff 包：URL 规范化 + 语义去重 + 快照。"""

from __future__ import annotations

from .dedupe import DedupeResult, DedupeStats, Deduplicator
from .normalize import (
    is_similar,
    jaccard,
    normalize_text,
    normalize_url,
    semantic_hash,
    shingles,
    url_hash,
)
from .snapshot import Snapshot, SnapshotEntry, SnapshotStore

__all__ = [
    "DedupeResult",
    "DedupeStats",
    "Deduplicator",
    "Snapshot",
    "SnapshotEntry",
    "SnapshotStore",
    "is_similar",
    "jaccard",
    "normalize_text",
    "normalize_url",
    "semantic_hash",
    "shingles",
    "url_hash",
]