"""so.qq.com（元宝搜索）Provider：Playwright 无头浏览器抓取 + 风控识别。

设计要点：
- 前端渲染、无公开 API，因此只有 Playwright 一条真实抓取路径；
- 命中验证码/风控页立刻抛 ProviderBlocked，交给 registry 切兜底源；
- 解析用标准库 HTMLParser，避免引入额外解析依赖；
- fetcher 可注入（单元测试用固定 HTML，不发真实请求）。
"""

from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Callable, Sequence
from urllib.parse import quote_plus, urlparse

from ..config import TopicConfig
from .base import ProviderBlocked, ProviderError, ProviderTimeout, SearchResult

SEARCH_URL = "https://so.qq.com/search?q={query}"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BLOCK_MARKERS = (
    "验证码",
    "安全验证",
    "访问过于频繁",
    "请求过于频繁",
    "异常访问",
    "captcha",
    "unusual traffic",
)
MIN_TITLE_LEN = 8
INTERNAL_HOSTS = ("so.qq.com", "www.qq.com", "qq.com")

ABSOLUTE_LINK_RE = re.compile(r"""<a\b[^>]*href=["'](https?://[^"'#]+)["'][^>]*>(.*?)</a>""", re.S)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def looks_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker.lower() in lowered for marker in BLOCK_MARKERS)


def _clean(text: str) -> str:
    return SPACE_RE.sub(" ", TAG_RE.sub(" ", text)).strip()


class _LinkCollector(HTMLParser):
    """收集页面上的绝对外链与其可见文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if href.startswith(("http://", "https://")):
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, _clean(" ".join(self._text))))
            self._href = None
            self._text = []


def parse_results(html: str, max_results: int = 20) -> list[SearchResult]:
    """从搜索页 HTML 提取外部结果链接（过滤站内跳转与过短标题）。"""
    if looks_blocked(html):
        raise ProviderBlocked("so.qq.com 返回验证码/风控页")

    collector = _LinkCollector()
    try:
        collector.feed(html)
    except Exception as exc:  # pragma: no cover - HTMLParser 极少抛错
        raise ProviderError(f"so.qq.com 页面解析失败：{exc}") from exc

    results: list[SearchResult] = []
    seen: set[str] = set()
    for href, text in collector.links:
        host = (urlparse(href).hostname or "").lower()
        if any(host == internal or host.endswith("." + internal) for internal in INTERNAL_HOSTS):
            continue
        if len(text) < MIN_TITLE_LEN:
            continue
        if href in seen:
            continue
        seen.add(href)
        results.append(
            SearchResult(title=text[:200], url=href, snippet=text[:400], source="soqq")
        )
        if len(results) >= max_results:
            break
    if not results:
        # 页面结构变化或结果被前端异步加载，按 Provider 不可用处理（交给兜底）
        raise ProviderError("so.qq.com 页面未解析出任何外部结果链接")
    return results


def build_queries(topic: TopicConfig, limit: int = 3) -> list[str]:
    """查询串：话题本体优先，再拼前几个关键词。"""
    queries = [topic.topic]
    for keyword in topic.keywords:
        candidate = keyword if keyword in topic.topic else f"{topic.topic} {keyword}"
        if candidate not in queries:
            queries.append(candidate)
        if len(queries) >= limit:
            break
    return queries


PageFetcher = Callable[[str], str]


class SoqqProvider:
    """so.qq.com Provider（Playwright 抓取，可注入 fetcher 便于测试）。"""

    name = "soqq"

    def __init__(
        self,
        fetcher: PageFetcher | None = None,
        timeout_seconds: float = 45.0,
        retries: int = 1,
        max_queries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._fetcher = fetcher
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.max_queries = max(1, max_queries)
        self._sleep = sleep

    def _playwright_fetch(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ProviderError(
                "未安装 playwright，无法抓取 so.qq.com；请 pip install '.[browser]' 并执行 playwright install chromium"
            ) from exc

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(locale="zh-CN")
                    page.goto(url, timeout=self.timeout_seconds * 1000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    return page.content()
                finally:
                    browser.close()
        except ProviderError:
            raise
        except Exception as exc:  # playwright 的 TimeoutError 等
            if "Timeout" in type(exc).__name__:
                raise ProviderTimeout(f"so.qq.com 抓取超时（{self.timeout_seconds}s）") from exc
            raise ProviderError(f"so.qq.com 抓取失败：{type(exc).__name__}") from exc

    def _fetch(self, url: str) -> str:
        attempts = self.retries + 1
        last: ProviderError | None = None
        for attempt in range(attempts):
            try:
                return (self._fetcher or self._playwright_fetch)(url)
            except ProviderBlocked:
                raise
            except ProviderError as exc:
                last = exc
            if attempt < attempts - 1:
                self._sleep(2**attempt)
        raise last or ProviderError("so.qq.com 抓取失败")

    def search(self, topic: TopicConfig, max_results: int = 20) -> Sequence[SearchResult]:
        collected: list[SearchResult] = []
        seen: set[str] = set()
        for query in build_queries(topic, limit=self.max_queries):
            html = self._fetch(SEARCH_URL.format(query=quote_plus(query)))
            for result in parse_results(html, max_results=max_results):
                if result.url in seen:
                    continue
                seen.add(result.url)
                collected.append(result)
            if len(collected) >= max_results:
                break
        if not collected:
            raise ProviderError("so.qq.com 未返回任何结果")
        return collected[:max_results]