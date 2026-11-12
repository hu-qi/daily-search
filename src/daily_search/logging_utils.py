"""日志与脱敏：任何写入日志/报告的内容都不得包含 LLM key、token、webhook 地址。"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable, Mapping

# 需要整体替换的环境变量名（值可能出现在任意文本里）
SECRET_ENV_KEYS = (
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "FEISHU_WEBHOOK_URL",
    "WECOM_WEBHOOK_URL",
    "WEBHOOK_URL",
)

# 常见凭据形态（即使没有配 env 也能兜底）
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9\-]{8,}"),
)

REDACTED = "[REDACTED]"


def secret_values(environ: Mapping[str, str] | None = None) -> list[str]:
    """收集当前环境里所有敏感变量的值（长度 >= 6，避免把短值误伤）。"""
    env = os.environ if environ is None else environ
    values: list[str] = []
    for key in SECRET_ENV_KEYS:
        value = env.get(key)
        if value and len(value) >= 6:
            values.append(value)
    # 长度降序，先替换长的，避免子串残留
    return sorted(set(values), key=len, reverse=True)


class RedactionFilter(logging.Filter):
    """把日志记录里的敏感值替换为 [REDACTED]。"""

    def __init__(self, secrets: Iterable[str] | None = None) -> None:
        super().__init__()
        self._secrets = list(secrets) if secrets is not None else secret_values()

    def redact(self, text: str) -> str:
        for value in self._secrets:
            if value and value in text:
                text = text.replace(value, REDACTED)
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - 防御性
            return True
        record.msg = self.redact(message)
        record.args = ()
        if record.exc_info:
            # 异常栈沿用 message 的脱敏结果，避免 traceback 里带出凭据
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(level: str = "INFO", secrets: Iterable[str] | None = None) -> logging.Logger:
    """配置根 logger：统一格式 + 脱敏过滤器（幂等）。"""
    logger = logging.getLogger("daily_search")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    handler_name = "daily_search_handler"
    handler = next((h for h in logger.handlers if h.get_name() == handler_name), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler.set_name(handler_name)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    if not any(isinstance(f, RedactionFilter) for f in handler.filters):
        handler.addFilter(RedactionFilter(secrets))
    return logger


def redact_text(text: str, environ: Mapping[str, str] | None = None) -> str:
    """对任意文本做一次脱敏（用于 Issue / feed / 报告落盘前自检）。"""
    return RedactionFilter(secret_values(environ)).redact(text)