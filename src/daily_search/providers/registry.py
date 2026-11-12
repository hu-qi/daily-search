"""Provider 注册与降级：按顺序尝试，连续风控则本次运行全量切兜底。"""

from __future__ import annotations

from typing import Sequence

from ..config import TopicConfig
from .base import ProviderBlocked, ProviderError, ProviderRunInfo, SearchProvider, SearchResult


class ProviderRegistry:
    """一次运行内共享的降级状态。

    - 单个 Provider 连续 ``block_limit`` 次 :class:`ProviderBlocked` → 本次运行内禁用；
    - 其它 :class:`ProviderError`（超时/无结果/解析失败）→ 立即尝试下一个；
    - 全部失败 → 返回空结果 + fallback_reason（由报告标注 degraded，保证当天仍有产出）。
    """

    def __init__(
        self,
        providers: dict[str, SearchProvider],
        order: Sequence[str] | None = None,
        block_limit: int = 2,
    ) -> None:
        self.providers = dict(providers)
        self.order = list(order or providers.keys())
        self.block_limit = max(1, block_limit)
        self._blocked_streak: dict[str, int] = {}
        self._disabled: set[str] = set()

    def available(self) -> list[str]:
        return [name for name in self.order if name in self.providers and name not in self._disabled]

    def search(
        self,
        topic: TopicConfig,
        max_results: int = 20,
        order: Sequence[str] | None = None,
    ) -> tuple[list[SearchResult], ProviderRunInfo]:
        info = ProviderRunInfo()
        requested = [name for name in (order or self.order) if name in self.providers]
        if not requested:
            # 配置里的 Provider 本环境未注册（如离线 fixture 模式）→ 用注册顺序兜底
            requested = [name for name in self.order if name in self.providers]
        candidates = requested
        for name in candidates:
            if name in self._disabled:
                info.blocked.append(name)
                continue
            provider = self.providers[name]
            info.attempted.append(name)
            while True:
                try:
                    results = list(provider.search(topic, max_results=max_results))
                except ProviderBlocked as exc:
                    self._blocked_streak[name] = self._blocked_streak.get(name, 0) + 1
                    if self._blocked_streak[name] >= self.block_limit:
                        # 连续风控达到阈值：本次运行内不再使用该 Provider
                        self._disabled.add(name)
                        info.blocked.append(name)
                        info.fallback_reason = (
                            f"{name} 连续 {self._blocked_streak[name]} 次风控（{exc}），已切换兜底源"
                        )
                        break
                    info.fallback_reason = f"{name} 命中风控（{exc}），重试一次"
                    continue
                except ProviderError as exc:
                    info.fallback_reason = f"{name} 不可用：{exc}"
                    break
                if not results:
                    info.fallback_reason = f"{name} 未返回结果"
                    break
                self._blocked_streak[name] = 0
                info.used = name
                return results[:max_results], info

        if not info.fallback_reason:
            info.fallback_reason = "所有 Provider 均不可用"
        return [], info