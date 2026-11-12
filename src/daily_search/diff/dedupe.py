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
    # 本次命中的历史条目 key（url_hash；缺失时用规范化 URL 指纹），
    # commit 时据此刷新 last_seen，避免旧闻在快照裁剪后被当成新增重推。
    matched_keys: set[str] = field(default_factory=set)


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
        entry_keys = [entry.url_hash or url_hash(entry.url) for entry in existing]
        seen_urls = set(entry_keys)
        seen_semantics: list[set[str]] = [
            shingles(f"{entry.title} {entry.snippet}") for entry in existing
        ]
        semantic_keys = list(entry_keys)

        accepted: list[SearchResult] = []
        duplicates = 0
        matched_keys: set[str] = set()
        for result in results:
            fingerprint = url_hash(result.url)
            if fingerprint in seen_urls:
                duplicates += 1
                matched_keys.add(fingerprint)
                continue
            tokens = shingles(f"{result.title} {result.snippet}")
            matched_index = next(
                (
                    index
                    for index, known in enumerate(seen_semantics)
                    if jaccard(tokens, known) >= self.threshold
                ),
                None,
            )
            if matched_index is not None:
                duplicates += 1
                matched_keys.add(semantic_keys[matched_index])
                continue
            # 同一天内的近似条目同样要收敛
            seen_urls.add(fingerprint)
            seen_semantics.append(tokens)
            semantic_keys.append(fingerprint)
            accepted.append(result)

        stats = DedupeStats(total=len(results), accepted=len(accepted), duplicates=duplicates)
        logger.debug("去重完成 slug=%s stats=%s", slug, stats.to_dict())
        return DedupeResult(
            new=accepted,
            stats=stats,
            known_entries=existing,
            matched_keys=matched_keys,
        )

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
            if key in result.matched_keys:
                # 今日再次命中：刷新 last_seen，保证快照保留期从「最近一次出现」开始算
                entry.last_seen = day
            elif not entry.last_seen:
                entry.last_seen = entry.first_seen or day
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