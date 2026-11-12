"""双层去重：URL 指纹 + 语义相似度，只保留新增/变化条目。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from ..providers.base import SearchResult
from .normalize import jaccard, semantic_hash, shingles, url_hash
from .snapshot import SnapshotEntry, SnapshotStore

logger = logging.getLogger("daily_search.diff.dedupe")


@dataclass
class DedupeStats:
    total: int = 0
    accepted: int = 0
    duplicates: int = 0

    @property
    def repeat_rate(self) -> float:
        """重复率 = 重复条数 / 本次候选条数（验收阈值 < 10%）。"""
        return (self.duplicates / self.total) if self.total else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "total": self.total,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "repeat_rate": round(self.repeat_rate, 4),
        }


@dataclass
class DedupeResult:
    new: list[SearchResult] = field(default_factory=list)
    stats: DedupeStats = field(default_factory=DedupeStats)
    known_entries: list[SnapshotEntry] = field(default_factory=list)


class Deduplicator:
    def __init__(self, store: SnapshotStore, threshold: float = 0.85) -> None:
        self.store = store
        self.threshold = threshold

    def split(
        self,
        slug: str,
        results: list[SearchResult],
        today: date | str | None = None,
    ) -> DedupeResult:
        existing = self.store.load(slug, today)
        seen_urls = {entry.url_hash for entry in existing if entry.url_hash}
        seen_semantics: list[set[str]] = [
            shingles(f"{entry.title} {entry.snippet}") for entry in existing
        ]

        accepted: list[SearchResult] = []
        duplicates = 0
        for result in results:
            fingerprint = url_hash(result.url)
            if fingerprint in seen_urls:
                duplicates += 1
                continue
            tokens = shingles(f"{result.title} {result.snippet}")
            if any(jaccard(tokens, known) >= self.threshold for known in seen_semantics):
                duplicates += 1
                continue
            # 同一天内的近似条目同样要收敛
            seen_urls.add(fingerprint)
            seen_semantics.append(tokens)
            accepted.append(result)

        stats = DedupeStats(total=len(results), accepted=len(accepted), duplicates=duplicates)
        logger.debug("去重完成 slug=%s stats=%s", slug, stats.to_dict())
        return DedupeResult(new=accepted, stats=stats, known_entries=existing)

    def commit(
        self,
        slug: str,
        result: DedupeResult,
        today: date | str | None = None,
    ) -> list[SnapshotEntry]:
        """把新增条目并入快照（重复条目刷新 last_seen），并按保留期裁剪。"""
        day = today.isoformat() if isinstance(today, date) else (today or date.today().isoformat())
        merged: dict[str, SnapshotEntry] = {}
        for entry in result.known_entries:
            key = entry.url_hash or url_hash(entry.url)
            if entry.url_hash and not entry.last_seen:
                entry.last_seen = day
            merged[key] = entry

        for item in result.new:
            fingerprint = url_hash(item.url)
            merged[fingerprint] = SnapshotEntry(
                url=item.url,
                url_hash=fingerprint,
                norm_hash=semantic_hash(item.title, item.snippet),
                title=item.title,
                snippet=item.snippet[:400],
                source=item.source,
                first_seen=day,
                last_seen=day,
            )

        entries = list(merged.values())
        self.store.save(slug, entries, day)
        return entries