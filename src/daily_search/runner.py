"""每日运行编排：Provider → 去重 → LLM 摘要 → 报告 / feed 落盘。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Mapping, Sequence

from .agent.summarize import Summarizer, TopicDigest
from .config import AppConfig, TopicConfig
from .diff.dedupe import Deduplicator
from .diff.snapshot import SnapshotStore
from .providers.base import SearchProvider
from .providers.fallback_rss import FallbackRssProvider
from .providers.fixture import FixtureProvider
from .providers.registry import ProviderRegistry
from .providers.soqq import SoqqProvider
from .logging_utils import redact_text
from .publish.atomic import atomic_write_text
from .publish.feed import build_item, existing_items, merge_items, render_feed
from .publish.markdown import render_daily_index, render_topic_report, report_relpath

logger = logging.getLogger("daily_search.runner")


@dataclass
class TopicRun:
    digest: TopicDigest
    report_text: str
    report_path: Path | None = None


@dataclass
class DailyRun:
    day: str
    topics: list[TopicRun] = field(default_factory=list)
    feed_path: Path | None = None
    index_text: str = ""

    @property
    def degraded_topics(self) -> list[str]:
        return [run.digest.topic for run in self.topics if run.digest.degraded]

    @property
    def total_new(self) -> int:
        return sum(len(run.digest.points) for run in self.topics)


def resolve_day(value: str | None) -> str:
    if not value:
        return date.today().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"日期必须是 YYYY-MM-DD：{value}") from exc


def day_pub_datetime(day: str) -> datetime:
    """运行日 00:00 UTC（固定值，避免报告/feed 依赖运行时序）。"""
    return datetime.combine(date.fromisoformat(day), time(0, 0), tzinfo=dt_timezone.utc)


def build_registry(
    config: AppConfig,
    environ: Mapping[str, str] | None = None,
    fixture_path: str | Path | None = None,
    order: Sequence[str] | None = None,
) -> ProviderRegistry:
    """构造本次运行的 Provider 组合；fixture 模式完全离线。"""
    env = environ or {}
    if fixture_path:
        fixture = FixtureProvider.from_file(fixture_path)
        return ProviderRegistry({"fixture": fixture}, order=list(order or ["fixture"]))
    providers: dict[str, SearchProvider] = {
        "soqq": SoqqProvider(max_queries=config.defaults.max_queries_per_topic),
        "fallback_rss": FallbackRssProvider(config.defaults.fallback_feeds),
    }
    return ProviderRegistry(providers, order=list(order or config.defaults.provider_order))


class DailyRunner:
    def __init__(
        self,
        config: AppConfig,
        registry: ProviderRegistry,
        deduplicator: Deduplicator,
        summarizer: Summarizer,
        root: Path | str,
        repo_full_name: str = "",
        base_branch: str = "main",
    ) -> None:
        self.config = config
        self.registry = registry
        self.deduplicator = deduplicator
        self.summarizer = summarizer
        self.root = Path(root)
        self.repo_full_name = repo_full_name
        self.base_branch = base_branch

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        root: Path | str,
        summarizer: Summarizer,
        repo_full_name: str = "",
        base_branch: str = "main",
        fixture_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        order: Sequence[str] | None = None,
    ) -> "DailyRunner":
        root_path = Path(root)
        store = SnapshotStore(root_path / "data", config.defaults.snapshot_retention_days)
        deduplicator = Deduplicator(store, threshold=config.defaults.semantic_threshold)
        registry = build_registry(config, environ=environ, fixture_path=fixture_path, order=order)
        return cls(config, registry, deduplicator, summarizer, root_path, repo_full_name, base_branch)

    def _select_topics(self, topic_filter: str | None) -> list[TopicConfig]:
        if not topic_filter:
            return list(self.config.topics)
        topic = self.config.find(topic_filter)
        if topic is None:
            raise ValueError(f"topics.yaml 中找不到话题：{topic_filter}")
        return [topic]

    def run(
        self,
        day: str | None = None,
        topic_filter: str | None = None,
        dry_run: bool = False,
    ) -> DailyRun:
        run_day = resolve_day(day)
        topics = self._select_topics(topic_filter)
        result = DailyRun(day=run_day)
        now = day_pub_datetime(run_day)

        for topic in topics:
            max_results = self.config.max_results_for(topic)
            order = self.config.provider_order_for(topic)
            results, info = self.registry.search(topic, max_results=max_results, order=order)
            deduped = self.deduplicator.split(topic.slug, results, run_day)
            digest = self.summarizer.summarize(
                topic_name=topic.topic,
                slug=topic.slug,
                today=run_day,
                items=deduped.new,
                stats=deduped.stats.to_dict(),
                provider=info.used,
                fallback_reason=info.fallback_reason,
            )
            report_text = render_topic_report(
                digest,
                repo_full_name=self.repo_full_name,
                base_branch=self.base_branch,
                generated_at=f"{run_day}T00:00:00+00:00",
            )
            report_text = redact_text(report_text)
            topic_run = TopicRun(digest=digest, report_text=report_text)

            if dry_run:
                logger.info("[dry-run] %s 新增 %d 条（不写快照/feed/报告）", topic.slug, len(deduped.new))
                result.topics.append(topic_run)
                continue

            report_path = self.root / report_relpath(run_day, topic.slug)
            atomic_write_text(report_path, report_text)
            self.deduplicator.commit(topic.slug, deduped, run_day)
            topic_run.report_path = report_path
            result.topics.append(topic_run)

        result.index_text = render_daily_index(
            run_day,
            [run.digest for run in result.topics],
            repo_full_name=self.repo_full_name,
            base_branch=self.base_branch,
            generated_at=f"{run_day}T00:00:00+00:00",
        )
        result.index_text = redact_text(result.index_text)

        if dry_run:
            return result

        atomic_write_text(self.root / "reports" / run_day / "index.md", result.index_text)
        result.feed_path = self._update_feed(run_day, result, now)
        return result

    def _update_feed(self, run_day: str, result: DailyRun, now: datetime) -> Path | None:
        feed_topics = []
        for run in result.topics:
            topic = self.config.find(run.digest.slug) or self.config.find(run.digest.topic)
            if topic is not None and topic.channels.feed:
                feed_topics.append(run)
        if not feed_topics:
            return None

        feed_path = self.root / "feed.xml"
        existing_text = feed_path.read_text(encoding="utf-8") if feed_path.is_file() else None
        new_items = []
        for run in feed_topics:
            digest = run.digest
            link = (
                f"https://github.com/{self.repo_full_name}/blob/{self.base_branch}/"
                f"{report_relpath(run_day, digest.slug)}"
                if self.repo_full_name
                else report_relpath(run_day, digest.slug)
            )
            new_items.append(
                build_item(
                    title=f"{digest.topic} · {run_day}（新增 {len(digest.points)} 条）",
                    link=link,
                    description=redact_text(digest.summary[:300]),
                    day=run_day,
                    slug=digest.slug,
                    pub_date=now,
                )
            )
        merged = merge_items(existing_items(existing_text), new_items)
        atomic_write_text(feed_path, render_feed(merged, generated_at=now))
        return feed_path