"""LLM 摘要：生成「一搜一页」；失败即降级为仅新增链接列表。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from ..providers.base import SearchResult
from .llm import LLMError

logger = logging.getLogger("daily_search.agent.summarize")

MAX_ITEMS_IN_PROMPT = 20
SNIPPET_IN_PROMPT = 160

PROMPT_TEMPLATE = """你在为中文话题「{topic}」编写{today}的每日增量摘要。

下面只有今日「新增」条目（已与历史快照去重），请仅基于这些条目作答，禁止编造：
{items}

要求：
1. 每条要点必须引用给定的 url（原样照抄，不要改写、不要拼接）；
2. 输出严格 JSON，格式：
{{"summary": "3 句以内的今日变化总结",
  "points": [{{"url": "给定链接", "note": "一句话说明这条新增了什么", "confidence": "high|medium|low", "worth_following": true}}]}}
3. 最多 {max_points} 条 points；没有内容的条目直接略过；
4. 不要输出 markdown 代码块以外的任何文字。"""


@dataclass
class DigestPoint:
    title: str
    url: str
    note: str = ""
    confidence: str = "medium"
    worth_following: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "note": self.note,
            "confidence": self.confidence,
            "worth_following": self.worth_following,
        }


@dataclass
class TopicDigest:
    topic: str
    slug: str
    date: str
    summary: str
    points: list[DigestPoint] = field(default_factory=list)
    # LLM 只挑选了部分条目时，未被引用的新增条目也必须保留链接，
    # 否则快照已写入「已见」但报告/Issue 永久丢失（违反「只推新增」）。
    unreferenced: list[DigestPoint] = field(default_factory=list)
    degraded: bool = False
    degraded_reason: str = ""
    provider: str = ""
    fallback_reason: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def all_points(self) -> list[DigestPoint]:
        return [*self.points, *self.unreferenced]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "slug": self.slug,
            "date": self.date,
            "summary": self.summary,
            "points": [point.to_dict() for point in self.points],
            "unreferenced": [point.to_dict() for point in self.unreferenced],
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "provider": self.provider,
            "fallback_reason": self.fallback_reason,
            "stats": self.stats,
        }


def _format_items(items: Sequence[SearchResult], limit: int = MAX_ITEMS_IN_PROMPT) -> str:
    lines: list[str] = []
    for index, item in enumerate(items[: max(1, limit)], start=1):
        snippet = re.sub(r"\s+", " ", item.snippet)[:SNIPPET_IN_PROMPT]
        lines.append(f"{index}. {item.title}\n   url: {item.url}\n   摘要: {snippet}")
    return "\n".join(lines)


def extract_json(text: str) -> dict[str, Any]:
    """从可能带代码块/前后缀的响应中提取第一个 JSON 对象。"""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("响应中找不到 JSON 对象")
        candidate = text[start : end + 1]
    data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return data


def _link_only_digest(
    topic_name: str,
    slug: str,
    today: str,
    items: Sequence[SearchResult],
    reason: str,
    provider: str,
    fallback_reason: str,
    stats: dict[str, Any],
) -> TopicDigest:
    points = [
        DigestPoint(
            title=item.title,
            url=item.url,
            note=re.sub(r"\s+", " ", item.snippet)[:120] or "（仅链接）",
            confidence="unknown",
            worth_following=True,
        )
        for item in items
    ]
    return TopicDigest(
        topic=topic_name,
        slug=slug,
        date=today,
        summary="LLM 不可用，已降级为仅新增链接列表。" if items else "今日无新增条目。",
        points=points,
        degraded=True,
        degraded_reason=reason,
        provider=provider,
        fallback_reason=fallback_reason,
        stats=stats,
    )


class Completer(Protocol):
    """最小 LLM 契约：Summarizer 只依赖 complete()，便于测试注入。"""

    def complete(self, prompt: str, temperature: float = 0.2) -> str:
        ...


class Summarizer:
    """把新增条目压缩成一页摘要；任何 LLM 异常都必须保留链接列表。"""

    def __init__(
        self,
        client: Completer | None = None,
        max_points: int = 8,
        max_items_in_prompt: int = MAX_ITEMS_IN_PROMPT,
    ) -> None:
        self.client = client
        self.max_points = max_points
        self.max_items_in_prompt = max_items_in_prompt

    def summarize(
        self,
        topic_name: str,
        slug: str,
        today: str,
        items: Sequence[SearchResult],
        stats: dict[str, Any] | None = None,
        provider: str = "",
        fallback_reason: str = "",
        max_items: int | None = None,
    ) -> TopicDigest:
        stats = stats or {}
        if not items:
            return TopicDigest(
                topic=topic_name,
                slug=slug,
                date=today,
                summary="今日无新增条目。" if not fallback_reason else "本次搜索不可用，无新增条目。",
                points=[],
                degraded=bool(fallback_reason),
                degraded_reason=fallback_reason,
                provider=provider,
                fallback_reason=fallback_reason,
                stats=stats,
            )

        if self.client is None:
            return _link_only_digest(
                topic_name, slug, today, items, "未配置 LLM", provider, fallback_reason, stats
            )

        prompt_limit = max_items if max_items is not None else self.max_items_in_prompt
        prompt = PROMPT_TEMPLATE.format(
            topic=topic_name,
            today=today,
            items=_format_items(items, prompt_limit),
            max_points=self.max_points,
        )
        try:
            response = self.client.complete(prompt)
            data = extract_json(response)
        except (LLMError, ValueError) as exc:
            logger.warning("LLM 摘要失败，降级为仅链接：%s", exc.__class__.__name__)
            return _link_only_digest(
                topic_name, slug, today, items, f"LLM 摘要失败：{exc}", provider, fallback_reason, stats
            )

        allowed = {item.url: item for item in items}
        points: list[DigestPoint] = []
        used_urls: set[str] = set()
        for raw in data.get("points") or []:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            item = allowed.get(url)
            if item is None:
                # 模型改了链接/编造链接：丢弃该条，绝不输出非来源链接
                logger.warning("丢弃 LLM 返回的非来源链接（slug=%s）", slug)
                continue
            if url in used_urls:
                continue
            confidence = str(raw.get("confidence") or "medium").lower()
            if confidence not in {"high", "medium", "low"}:
                confidence = "medium"
            points.append(
                DigestPoint(
                    title=item.title,
                    url=item.url,
                    note=str(raw.get("note") or "")[:200],
                    confidence=confidence,
                    worth_following=bool(raw.get("worth_following", True)),
                )
            )
            used_urls.add(url)
            if len(points) >= self.max_points:
                break

        if not points:
            return _link_only_digest(
                topic_name, slug, today, items, "LLM 未返回可用的新增要点", provider, fallback_reason, stats
            )

        # LLM 成功但只挑选了部分条目：其余新增条目补为「其他新增链接」，
        # 保证快照写入已见的每条新增在当日产出中都有落点。
        unreferenced = [
            DigestPoint(
                title=item.title,
                url=item.url,
                note="（未纳入 LLM 摘要，仅保留原始链接）",
                confidence="unknown",
                worth_following=False,
            )
            for item in items
            if item.url not in used_urls
        ]

        summary = str(data.get("summary") or "").strip() or "见下方新增要点。"
        return TopicDigest(
            topic=topic_name,
            slug=slug,
            date=today,
            summary=summary[:600],
            points=points,
            unreferenced=unreferenced,
            degraded=False,
            provider=provider,
            fallback_reason=fallback_reason,
            stats=stats,
        )