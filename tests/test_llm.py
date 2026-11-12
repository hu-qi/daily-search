"""LLM 客户端：超时/重试/预算，以及「异常与日志不得含 key」。"""

from __future__ import annotations

import json

from typing import Any

import pytest
import requests

from daily_search.agent.llm import (
    LLMBudgetExceeded,
    LLMClient,
    LLMConfigError,
    LLMError,
    LLMSettings,
    estimate_tokens,
)

API_KEY = "sk-test-secret-key-should-never-leak"


def _settings(**overrides: Any) -> LLMSettings:
    base: dict[str, Any] = dict(
        base_url="https://llm.example/v1",
        api_key=API_KEY,
        model="test-model",
        timeout_seconds=5,
        max_retries=2,
        token_budget=8000,
    )
    base.update(overrides)
    return LLMSettings(**base)


def _ok(content: str = '{"summary": "ok", "points": []}') -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_unconfigured_client_raises_config_error(fake_session) -> None:
    client = LLMClient(LLMSettings(), session=fake_session([]))
    with pytest.raises(LLMConfigError):
        client.complete("hi")


def test_complete_returns_content(fake_session) -> None:
    session = fake_session([type("R", (), {"status_code": 200, "json": lambda self: _ok("你好")})()])
    client = LLMClient(_settings(), session=session, sleep=lambda _: None)
    assert client.complete("hi") == "你好"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://llm.example/v1/chat/completions"
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")


def test_retries_on_5xx_then_succeeds(fake_session) -> None:
    responses = [type("R", (), {"status_code": 503, "json": lambda self: {}})(), type("R", (), {"status_code": 200, "json": lambda self: _ok("恢复")})()]
    client = LLMClient(_settings(), session=fake_session(responses), sleep=lambda _: None)
    assert client.complete("hi") == "恢复"


def test_client_error_is_not_retried(fake_session) -> None:
    session = fake_session([type("R", (), {"status_code": 401, "json": lambda self: {}})()])
    client = LLMClient(_settings(), session=session, sleep=lambda _: None)
    with pytest.raises(LLMError, match="被拒绝"):
        client.complete("hi")
    assert len(session.calls) == 1


def test_timeout_retries_and_reports(fake_session) -> None:
    session = fake_session([requests.Timeout(), requests.Timeout(), requests.Timeout()])
    client = LLMClient(_settings(max_retries=2), session=session, sleep=lambda _: None)
    with pytest.raises(LLMError, match="超时"):
        client.complete("hi")
    assert len(session.calls) == 3


def test_budget_exceeded(fake_session) -> None:
    client = LLMClient(_settings(token_budget=512, max_output_tokens=400), session=fake_session([]))
    with pytest.raises(LLMBudgetExceeded):
        client.complete("很长的提示" * 200)


def test_estimate_tokens_counts_cjk_by_char() -> None:
    assert estimate_tokens("昇腾") == 2
    assert estimate_tokens("abcd") == 1


def test_exception_text_never_contains_api_key(fake_session) -> None:
    session = fake_session([type("R", (), {"status_code": 401, "json": lambda self: {}})()])
    client = LLMClient(_settings(), session=session, sleep=lambda _: None)
    with pytest.raises(LLMError) as excinfo:
        client.complete("hi")
    assert API_KEY not in str(excinfo.value)


def test_missing_content_raises(fake_session) -> None:
    session = fake_session([type("R", (), {"status_code": 200, "json": lambda self: {"choices": []}})()])
    client = LLMClient(_settings(max_retries=0), session=session, sleep=lambda _: None)
    with pytest.raises(LLMError):
        client.complete("hi")


def test_endpoint_accepts_full_path() -> None:
    client = LLMClient(_settings(base_url="https://api.example/v1/chat/completions"))
    assert client._endpoint() == "https://api.example/v1/chat/completions"