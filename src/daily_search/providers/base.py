"""Search Provider 抽象：统一的 SearchResult 与异常契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, Sequence, runtime_checkable

from ..config import TopicConfig


class ProviderError(RuntimeError):
    """Provider 不可用（未安装、网络错误、解析失败）。"""


class ProviderBlocked(ProviderError):
    """命中风控/验证码页——必须触发兜底，不得把风控页当成搜索结果。"""


class ProviderTimeout(ProviderError):
    """单次抓取超时。"""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published_at: datetime | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("SearchResult.title 不能为空")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"SearchResult.url 必须是 http(s) 绝对地址：{self.url!r}")

    def normalized_published(self) -> datetime | None:
        if self.published_at is None:
            return None
        if self.published_at.tzinfo is None:
            return self.published_at.replace(tzinfo=timezone.utc)
        return self.published_at.astimezone(timezone.utc)


@dataclass
class ProviderRunInfo:
    """一次 topic 搜索实际用了哪些 Provider、是否发生降级。"""

    used: str = ""
    attempted: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    fallback_reason: str = ""


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    def search(self, topic: TopicConfig, max_results: int = 20) -> Sequence[SearchResult]:
        """返回该话题的候选结果；不可用时抛 ProviderError 子类。"""
        raise NotImplementedError