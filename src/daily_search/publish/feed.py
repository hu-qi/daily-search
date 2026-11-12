"""RSS 2.0 feed：按「话题 × 日期」追加条目，保留最近 N 条。"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Iterable, Sequence
from xml.sax.saxutils import escape

logger = logging.getLogger("daily_search.publish.feed")

FEED_LIMIT = 60


@dataclass
class FeedItem:
    title: str
    link: str
    description: str
    pub_date: datetime
    guid: str
    category: str = ""

    def pub_date_text(self) -> str:
        moment = self.pub_date
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return format_datetime(moment.astimezone(timezone.utc))


def _rfc822_now() -> datetime:
    return datetime.now(timezone.utc)


def _strip_managed_marker(xml_text: str) -> str:
    return xml_text.replace("<!-- daily-search: managed -->", "").strip()


def existing_items(xml_text: str | None) -> list[FeedItem]:
    if not xml_text:
        return []
    try:
        root = ET.fromstring(_strip_managed_marker(xml_text))
    except ET.ParseError:
        logger.warning("现有 feed.xml 解析失败，按空 feed 重建")
        return []
    items: list[FeedItem] = []
    for node in root.findall("./channel/item"):
        title = node.findtext("title") or ""
        link = node.findtext("link") or ""
        guid = node.findtext("guid") or link
        description = node.findtext("description") or ""
        published = node.findtext("pubDate")
        category = node.findtext("category") or ""
        if not title or not link:
            continue
        try:
            from email.utils import parsedate_to_datetime

            pub = parsedate_to_datetime(published) if published else _rfc822_now()
        except (TypeError, ValueError):
            pub = _rfc822_now()
        items.append(
            FeedItem(
                title=title,
                link=link,
                description=description,
                pub_date=pub,
                guid=guid,
                category=category,
            )
        )
    return items


def _sort_key(item: FeedItem) -> float:
    moment = item.pub_date
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def merge_items(existing: Iterable[FeedItem], new: Iterable[FeedItem], limit: int = FEED_LIMIT) -> list[FeedItem]:
    by_guid: dict[str, FeedItem] = {item.guid: item for item in existing}
    for item in new:
        by_guid[item.guid] = item  # 新条目覆盖同 guid 旧条目
    ordered = sorted(by_guid.values(), key=_sort_key, reverse=True)
    return ordered[:limit]


def render_feed(
    items: Sequence[FeedItem],
    title: str = "每日一搜",
    link: str = "https://github.com/hu-qi/daily-search",
    description: str = "只推新增的中文话题追踪",
    generated_at: datetime | None = None,
) -> str:
    now = generated_at or _rfc822_now()
    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        f"    <title>{escape(title)}</title>",
        f"    <link>{escape(link)}</link>",
        f"    <description>{escape(description)}</description>",
        "    <language>zh-CN</language>",
        f"    <lastBuildDate>{format_datetime(now.astimezone(timezone.utc))}</lastBuildDate>",
    ]
    body: list[str] = []
    for item in items:
        body += [
            "    <item>",
            f"      <title>{escape(item.title)}</title>",
            f"      <link>{escape(item.link)}</link>",
            f"      <guid isPermaLink=\"false\">{escape(item.guid)}</guid>",
            f"      <description>{escape(item.description)}</description>",
            f"      <pubDate>{item.pub_date_text()}</pubDate>",
        ]
        if item.category:
            body.append(f"      <category>{escape(item.category)}</category>")
        body.append("    </item>")
    tail = ["  </channel>", "</rss>", "<!-- daily-search: managed -->", ""]
    return "\n".join(head + body + tail)


def build_item(title: str, link: str, description: str, day: str, slug: str, pub_date: datetime | None = None) -> FeedItem:
    moment = pub_date or _rfc822_now()
    return FeedItem(
        title=title,
        link=link,
        description=description,
        pub_date=moment,
        guid=f"daily-search:{slug}:{day}",
        category=slug,
    )