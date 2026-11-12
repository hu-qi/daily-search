"""离线 fixture Provider：本地 JSON 结果，用于 dry-run 与测试。

fixture 文件结构（两种都支持）：
    {"topics": {"<slug>": [{"title": ..., "url": ...}]}}
    {"<slug>": [{"title": ..., "url": ...}]}
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..config import TopicConfig
from .base import ProviderError, SearchResult


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class FixtureProvider:
    """从 fixture 读取预置搜索结果（不发网络请求）。"""

    name = "fixture"

    def __init__(self, data: Mapping[str, Any]) -> None:
        raw: Any = data.get("topics", data) if isinstance(data, Mapping) else {}
        self._data: dict[str, list[dict[str, Any]]] = {}
        if isinstance(raw, Mapping):
            for key, value in raw.items():
                rows: list[dict[str, Any]] = []
                for row in value or []:
                    if isinstance(row, Mapping):
                        rows.append(dict(row))
                self._data[str(key)] = rows

    @classmethod
    def from_file(cls, path: str | Path) -> "FixtureProvider":
        fixture_path = Path(path)
        if not fixture_path.is_file():
            raise ProviderError(f"fixture 文件不存在：{fixture_path}")
        try:
            return cls(json.loads(fixture_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"fixture 不是合法 JSON：{exc}") from exc

    def search(self, topic: TopicConfig, max_results: int = 20) -> Sequence[SearchResult]:
        rows = self._data.get(topic.slug) or self._data.get(topic.topic) or []
        results: list[SearchResult] = []
        for row in rows:
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            if not title or not url:
                continue
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=str(row.get("snippet") or ""),
                    published_at=_to_datetime(row.get("published_at")),
                    source=str(row.get("source") or "fixture"),
                )
            )
        return results[:max_results]