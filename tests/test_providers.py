"""Provider：so.qq.com 解析/风控、RSS 兜底过滤、registry 熔断降级。"""

from __future__ import annotations

import pytest

from daily_search.config import TopicConfig
from daily_search.providers.base import ProviderBlocked, ProviderError, SearchResult
from daily_search.providers.fallback_rss import FallbackRssProvider, parse_feed
from daily_search.providers.fixture import FixtureProvider
from daily_search.providers.registry import ProviderRegistry
from daily_search.providers.soqq import SoqqProvider, build_queries, looks_blocked, parse_results

TOPIC = TopicConfig(topic="昇腾 CANN", keywords=["昇腾", "CANN"], exclude=["招聘"])

HTML = """
<html><body>
  <div class="result"><a href="https://so.qq.com/redirect?q=1">站内跳转链接不应被采纳</a></div>
  <a href="https://www.hiascend.com/developer/blog/cann8">昇腾 CANN 8.0 发布，算子与图引擎全面升级</a>
  <a href="https://gitee.com/ascend/cann-ops">Ascend cann-ops 新增融合算子示例</a>
  <a href="https://example.com/x">短</a>
</body></html>
"""


def test_parse_results_extracts_external_links_only() -> None:
    results = parse_results(HTML)
    assert [item.url for item in results] == [
        "https://www.hiascend.com/developer/blog/cann8",
        "https://gitee.com/ascend/cann-ops",
    ]


def test_parse_results_respects_max_results() -> None:
    assert len(parse_results(HTML, max_results=1)) == 1


def test_parse_results_detects_captcha() -> None:
    with pytest.raises(ProviderBlocked):
        parse_results("<html><body>请输入验证码后继续访问</body></html>")


def test_parse_results_without_links_raises_provider_error() -> None:
    with pytest.raises(ProviderError, match="未解析出"):
        parse_results("<html><body>没有链接</body></html>")


def test_looks_blocked_markers() -> None:
    assert looks_blocked("访问过于频繁，请稍后再试")
    assert not looks_blocked("<html>正常结果</html>")


def test_build_queries_limits_and_dedupes() -> None:
    queries = build_queries(TopicConfig(topic="昇腾 CANN", keywords=["昇腾", "CANN", "Ascend"]), limit=3)
    assert queries[0] == "昇腾 CANN"
    assert len(queries) == 3
    assert len(set(queries)) == 3


def test_soqq_search_aggregates_and_dedupes() -> None:
    calls: list[str] = []

    def fetcher(url: str) -> str:
        calls.append(url)
        return HTML

    provider = SoqqProvider(fetcher=fetcher, sleep=lambda _: None)
    results = provider.search(TOPIC, max_results=10)
    assert len(results) == 2
    assert all(item.source == "soqq" for item in results)
    assert len(calls) >= 1


def test_soqq_fetch_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    def fetcher(url: str) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ProviderError("网络抖动")
        return HTML

    provider = SoqqProvider(fetcher=fetcher, retries=1, sleep=lambda _: None)
    assert provider._fetch("https://so.qq.com/search?q=x") == HTML
    assert attempts["n"] == 2


def test_soqq_search_propagates_block_without_retry() -> None:
    attempts = {"n": 0}

    def fetcher(url: str) -> str:
        attempts["n"] += 1
        raise ProviderBlocked("风控")

    provider = SoqqProvider(fetcher=fetcher, retries=3, sleep=lambda _: None)
    with pytest.raises(ProviderBlocked):
        provider.search(TOPIC)
    assert attempts["n"] == 1


def test_soqq_missing_playwright_raises_provider_error(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    provider = SoqqProvider()
    with pytest.raises(ProviderError, match="playwright"):
        provider.search(TOPIC)


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>昇腾 CANN 新版本发布</title><link>https://example.com/1</link>
        <description>CANN 版本更新</description><pubDate>Wed, 11 Nov 2026 08:00:00 +0800</pubDate></item>
  <item><title>昇腾相关岗位招聘</title><link>https://example.com/2</link>
        <description>招聘信息</description></item>
  <item><title>无关新闻</title><link>https://example.com/3</link><description>其他</description></item>
</channel></rss>
"""


def test_parse_feed_filters_by_keyword_and_exclude() -> None:
    results = parse_feed(RSS, TOPIC)
    assert [item.url for item in results] == ["https://example.com/1"]
    assert results[0].published_at is not None


def test_parse_feed_invalid_xml_raises() -> None:
    with pytest.raises(ProviderError):
        parse_feed("<rss", TOPIC)


def test_parse_feed_supports_atom() -> None:
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>鸿蒙 OHPM 新特性</title>
        <link href="https://example.com/atom1"/>
        <summary>OHPM 介绍</summary>
        <updated>2026-11-11T10:00:00Z</updated></entry>
    </feed>"""
    topic = TopicConfig(topic="鸿蒙 OHPM", keywords=["OHPM"])
    results = parse_feed(atom, topic)
    assert results[0].url == "https://example.com/atom1"
    assert results[0].published_at is not None


def test_fallback_provider_without_feeds_raises() -> None:
    with pytest.raises(ProviderError, match="fallback_feeds"):
        FallbackRssProvider([]).search(TOPIC)


def test_fallback_provider_uses_injected_fetch() -> None:
    provider = FallbackRssProvider(["https://feed.example/rss"], fetch=lambda url: RSS)
    results = provider.search(TOPIC, max_results=5)
    assert [item.url for item in results] == ["https://example.com/1"]
    assert results[0].source == "fallback_rss"


def test_fixture_provider_parses_rows(tmp_path) -> None:
    import json

    path = tmp_path / "f.json"
    path.write_text(
        json.dumps(
            {"topics": {"昇腾-cann": [{"title": "标题", "url": "https://example.com/x", "snippet": "s"}]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    results = FixtureProvider.from_file(path).search(TOPIC)
    assert results[0].url == "https://example.com/x"
    assert results[0].snippet == "s"


class _Stub:
    def __init__(self, name: str, outcome) -> None:
        self.name = name
        self.outcome = outcome
        self.calls = 0

    def search(self, topic, max_results=20):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return list(self.outcome)


def _result(url: str = "https://example.com/1") -> SearchResult:
    return SearchResult(title="标题内容足够长", url=url)


def test_registry_falls_back_on_provider_error() -> None:
    broken = _Stub("soqq", ProviderError("抓取失败"))
    healthy = _Stub("fallback_rss", [_result()])
    registry = ProviderRegistry({"soqq": broken, "fallback_rss": healthy}, order=["soqq", "fallback_rss"])
    results, info = registry.search(TOPIC)
    assert len(results) == 1
    assert info.used == "fallback_rss"
    assert "soqq 不可用" in info.fallback_reason


def test_registry_retries_once_then_switches_after_two_blocks() -> None:
    blocked = _Stub("soqq", ProviderBlocked("风控"))
    healthy = _Stub("fallback_rss", [_result()])
    registry = ProviderRegistry({"soqq": blocked, "fallback_rss": healthy}, order=["soqq", "fallback_rss"])
    results, info = registry.search(TOPIC)
    assert blocked.calls == 2
    assert info.used == "fallback_rss"
    assert "连续 2 次风控" in info.fallback_reason

    # 同一次运行内后续话题不再尝试被禁用的 Provider
    _, info2 = registry.search(TOPIC)
    assert blocked.calls == 2
    assert "soqq" in info2.blocked


def test_registry_all_fail_returns_empty_with_reason() -> None:
    registry = ProviderRegistry(
        {"a": _Stub("a", ProviderError("x")), "b": _Stub("b", ProviderError("y"))},
        order=["a", "b"],
    )
    results, info = registry.search(TOPIC)
    assert results == []
    assert info.used == ""
    assert "不可用" in info.fallback_reason


def test_registry_falls_back_to_registered_order_when_config_names_absent() -> None:
    fixture = _Stub("fixture", [_result()])
    registry = ProviderRegistry({"fixture": fixture}, order=["fixture"])
    results, info = registry.search(TOPIC, order=["soqq", "fallback_rss"])
    assert [item.url for item in results] == ["https://example.com/1"]
    assert info.used == "fixture"