"""摘要降级与 LLM 输出校验：链接不可编造。"""

from __future__ import annotations

import json

from daily_search.agent.summarize import Summarizer, extract_json
from daily_search.agent.llm import LLMError
from daily_search.providers.base import SearchResult


def _items() -> list[SearchResult]:
    return [
        SearchResult(title="昇腾 CANN 8.0 发布", url="https://example.com/1", snippet="升级"),
        SearchResult(title="cann-ops 新增算子", url="https://example.com/2", snippet="示例"),
    ]


class _StubClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.prompts: list[str] = []

    def complete(self, prompt: str, temperature: float = 0.2) -> str:
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.content or ""


def test_no_client_degrades_to_link_list() -> None:
    digest = Summarizer(None).summarize("昇腾 CANN", "昇腾-cann", "2026-11-11", _items(), {"total": 2})
    assert digest.degraded is True
    assert digest.degraded_reason == "未配置 LLM"
    assert [point.url for point in digest.points] == ["https://example.com/1", "https://example.com/2"]


def test_no_items_is_not_degraded() -> None:
    digest = Summarizer(None).summarize("昇腾 CANN", "昇腾-cann", "2026-11-11", [], {"total": 0})
    assert digest.points == []
    assert digest.degraded is False
    assert "无新增" in digest.summary


def test_llm_error_degrades_with_reason() -> None:
    client = _StubClient(error=LLMError("上游 500"))
    digest = Summarizer(client).summarize("话题", "t", "2026-11-11", _items())
    assert digest.degraded is True
    assert "LLM 摘要失败" in digest.degraded_reason
    assert len(digest.points) == 2


def test_valid_json_is_parsed_and_links_kept() -> None:
    payload = {
        "summary": "今日新增两条。",
        "points": [
            {"url": "https://example.com/1", "note": "版本发布", "confidence": "high", "worth_following": True},
            {"url": "https://example.com/2", "note": "算子示例", "confidence": "medium"},
        ],
    }
    digest = Summarizer(_StubClient(json.dumps(payload, ensure_ascii=False))).summarize(
        "昇腾 CANN", "昇腾-cann", "2026-11-11", _items(), {"accepted": 2}
    )
    assert digest.degraded is False
    assert digest.summary == "今日新增两条。"
    assert [point.url for point in digest.points] == ["https://example.com/1", "https://example.com/2"]
    assert digest.points[0].confidence == "high"


def test_hallucinated_links_are_dropped() -> None:
    payload = {"summary": "x", "points": [{"url": "https://evil.example/fake", "note": "编造"}]}
    digest = Summarizer(_StubClient(json.dumps(payload))).summarize("话题", "t", "2026-11-11", _items())
    assert digest.degraded is True
    assert all("evil.example" not in point.url for point in digest.points)


def test_fenced_json_is_accepted() -> None:
    content = '```json\n{"summary": "s", "points": [{"url": "https://example.com/1"}]}\n```'
    digest = Summarizer(_StubClient(content)).summarize("话题", "t", "2026-11-11", _items())
    assert digest.degraded is False
    assert len(digest.points) == 1


def test_extract_json_rejects_non_object() -> None:
    import pytest

    with pytest.raises(ValueError):
        extract_json("没有 json")
    with pytest.raises(ValueError):
        extract_json("[1, 2]")


def test_prompt_contains_links_and_no_secret() -> None:
    client = _StubClient('{"summary": "s", "points": []}')
    Summarizer(client).summarize("话题", "t", "2026-11-11", _items())
    prompt = client.prompts[0]
    assert "https://example.com/1" in prompt
    assert "禁止编造" in prompt


def test_partial_llm_points_are_kept_as_unreferenced_links() -> None:
    items = [SearchResult(title=f"新增 {i}", url=f"https://example.com/{i}") for i in range(3)]
    payload = {"summary": "只讲一条", "points": [{"url": "https://example.com/0", "note": "重点"}]}
    digest = Summarizer(_StubClient(json.dumps(payload, ensure_ascii=False))).summarize(
        "话题", "t", "2026-11-11", items, {"accepted": 3}
    )
    assert len(digest.points) == 1
    assert [point.url for point in digest.unreferenced] == ["https://example.com/1", "https://example.com/2"]
    assert [point.url for point in digest.all_points] == [f"https://example.com/{i}" for i in range(3)]


def test_prompt_uses_configured_max_items_instead_of_hardcoded_twenty() -> None:
    items = [SearchResult(title=f"新增 {i}", url=f"https://example.com/{i}") for i in range(25)]
    client = _StubClient(json.dumps({"summary": "s", "points": [{"url": "https://example.com/0"}]}))
    digest = Summarizer(client).summarize(
        "话题", "t", "2026-11-11", items, max_items=25
    )
    assert "https://example.com/24" in client.prompts[0]
    assert len(digest.unreferenced) == 24


def test_max_points_limits_output() -> None:
    points = [
        {"url": "https://example.com/1"},
        {"url": "https://example.com/2"},
    ]
    digest = Summarizer(_StubClient(json.dumps({"summary": "s", "points": points})), max_points=1).summarize(
        "话题", "t", "2026-11-11", _items()
    )
    assert len(digest.points) == 1