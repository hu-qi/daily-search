"""Provider 包：soqq（主）/ fallback_rss（兜底）/ fixture（离线与测试）。"""

from __future__ import annotations

from .base import ProviderBlocked, ProviderError, ProviderRunInfo, ProviderTimeout, SearchResult
from .fallback_rss import FallbackRssProvider
from .fixture import FixtureProvider
from .registry import ProviderRegistry
from .soqq import SoqqProvider

__all__ = [
    "FallbackRssProvider",
    "FixtureProvider",
    "ProviderBlocked",
    "ProviderError",
    "ProviderRegistry",
    "ProviderRunInfo",
    "ProviderTimeout",
    "SearchResult",
    "SoqqProvider",
]