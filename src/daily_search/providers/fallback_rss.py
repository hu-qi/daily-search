"""兜底 Provider：公开 RSS/Atom 源，无凭据、可离线单测。

只保留命中关键词（且未命中排除词）的条目；任何网络/解析错误都转换为
ProviderError，由 registry 决定是否继续降级。
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Callable, Sequence
from urllib.parse import quote_plus

import requests

from ..config import TopicConfig
from .base import ProviderError, ProviderTimeout, SearchResult

USER_AGENT = "daily-search/0.1 (+https://github.com/hu-qi/daily-search)"
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed


def _matches(text: str, keywords: Sequence[str], exclude: Sequence[str]) -> bool:
    lowered = text.lower()
    if any(word.lower() in lowered for word in exclude):
        return False
    return any(word.lower() in lowered for word in keywords)


def parse_feed(xml_text: str, topic: TopicConfig) -> list[SearchResult]:
    """解析 RSS 2.0 / Atom，按关键词与排除词过滤。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProviderError(f"RSS 解析失败：{exc}") from exc

    results: list[SearchResult] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        if not _matches(f"{title} {description}", topic.keywords, topic.exclude):
            continue
        results.append(
            SearchResult(
                title=title[:200],
                url=link,
                snippet=description[:400],
                published_at=_parse_date(item.findtext("pubDate")),
                source="fallback_rss",
            )
        )

    for entry in root.findall(f".//{ATOM_NS}entry"):
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
        link_el = entry.find(f"{ATOM_NS}link")
        link = (link_el.get("href") if link_el is not None else "") or ""
        summary = (entry.findtext(f"{ATOM_NS}summary") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        if not _matches(f"{title} {summary}", topic.keywords, topic.exclude):
            continue
        results.append(
            SearchResult(
                title=title[:200],
                url=link,
                snippet=summary[:400],
                published_at=_parse_date(
                    entry.findtext(f"{ATOM_NS}updated") or entry.findtext(f"{ATOM_NS}published")
                ),
                source="fallback_rss",
            )
        )
    return results


class FallbackRssProvider:
    """按配置的 RSS 列表聚合结果（可注入 fetch 便于测试）。"""

    name = "fallback_rss"

    def __init__(
        self,
        feeds: Sequence[str],
        timeout_seconds: float = 20.0,
        fetch: Callable[[str], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.feeds = [feed for feed in feeds if feed]
        self.timeout_seconds = timeout_seconds
        self._fetch_impl = fetch
        self._sleep = sleep

    def _fetch(self, url: str) -> str:
        if self._fetch_impl is not None:
            try:
                return self._fetch_impl(url)
            except TimeoutError as exc:
                raise ProviderTimeout(f"RSS 抓取超时：{url}") from exc
        try:
            response = requests.get(
                url,
                timeout=self.timeout_seconds,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return response.text
        except requests.Timeout as exc:
            raise ProviderTimeout(f"RSS 抓取超时：{url}") from exc
        except requests.RequestException as exc:
            raise ProviderError(f"RSS 抓取失败：{url} → {exc.__class__.__name__}") from exc

    @staticmethod
    def _expand_feed(feed: str, topic: TopicConfig) -> str:
        """支持按话题关键词构造检索型 RSS（如 Google News），避免通用源与话题错配。"""
        if "{query}" not in feed and "{topic}" not in feed:
            return feed
        query = quote_plus(" OR ".join(topic.keywords))
        return feed.replace("{query}", query).replace("{topic}", quote_plus(topic.topic))

    def search(self, topic: TopicConfig, max_results: int = 20) -> Sequence[SearchResult]:
        configured = topic.fallback_feeds or self.feeds
        if not configured:
            raise ProviderError("未配置 fallback_feeds，兜底源不可用")
        feeds = [self._expand_feed(feed, topic) for feed in configured if feed]

        results: list[SearchResult] = []
        seen: set[str] = set()
        errors: list[str] = []
        for feed in feeds:
            try:
                xml_text = self._fetch(feed)
            except ProviderError as exc:
                errors.append(str(exc))
                continue
            for result in parse_feed(xml_text, topic):
                if result.url in seen:
                    continue
                seen.add(result.url)
                results.append(result)
        if not results:
            detail = "；".join(errors) if errors else "所有 RSS 源均无匹配条目"
            raise ProviderError(f"兜底源无结果：{detail}")
        return results[:max_results]