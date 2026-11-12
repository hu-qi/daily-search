"""日志脱敏：仓库与 Actions 日志不得出现明文 key。"""

from __future__ import annotations

import logging
import re

from daily_search.logging_utils import REDACTED, RedactionFilter, redact_text, secret_values, setup_logging

SECRET = "sk-live-abcdef1234567890abcdef"
WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/abcd1234-efgh-5678"


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("daily_search", logging.INFO, __file__, 1, message, None, None)


def test_filter_redacts_env_secret_values() -> None:
    filt = RedactionFilter(["sk-live-abc1234567890"])
    record = _record("调用失败 key=sk-live-abc1234567890 结束")
    filt.filter(record)
    assert "sk-live-abc1234567890" not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_filter_redacts_common_token_patterns() -> None:
    filt = RedactionFilter([])
    record = _record(f"header Bearer abcdefghijklmnopqrstuvwxyz token={SECRET} hook={WEBHOOK}")
    filt.filter(record)
    message = record.getMessage()
    assert SECRET not in message
    assert WEBHOOK not in message
    assert "abcdefghijklmnopqrstuvwxyz" not in message


def test_secret_values_sorted_by_length_and_skips_short() -> None:
    values = secret_values({"LLM_API_KEY": "short", "GH_TOKEN": "a-much-longer-token-value", "OTHER": "ignored"})
    assert values == ["a-much-longer-token-value"]


def test_redact_text_uses_env(monkeypatch) -> None:
    environ = {"LLM_API_KEY": "sk-very-secret-value-123456"}
    text = redact_text("key is sk-very-secret-value-123456 end", environ)
    assert "sk-very-secret-value-123456" not in text
    assert REDACTED in text


def test_setup_logging_is_idempotent() -> None:
    logger = setup_logging("DEBUG")
    handler_count = len(logger.handlers)
    setup_logging("INFO")
    assert len(logger.handlers) == handler_count
    assert any(isinstance(f, RedactionFilter) for h in logger.handlers for f in h.filters)


def test_filter_scrubs_exception_text() -> None:
    filt = RedactionFilter([SECRET])
    try:
        raise RuntimeError(f"boom {SECRET}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "daily_search", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    filt.filter(record)
    assert record.exc_info is None
    assert SECRET not in (record.exc_text or "")


def test_github_pat_patterns_redacted() -> None:
    filt = RedactionFilter([])
    record = _record("ghp_0123456789abcdefghijklmnopqrstuvwxyz and github_pat_11ABCDEFG0123456789ABCDEF")
    filt.filter(record)
    assert "ghp_" not in record.getMessage()
    assert re.search(r"github_pat_", record.getMessage()) is None