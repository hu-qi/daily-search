"""OpenAI 兼容 LLM 客户端：超时 / 重试 / token 预算，凭据只从环境读取且绝不入日志。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests

logger = logging.getLogger("daily_search.agent.llm")


class LLMError(RuntimeError):
    """LLM 调用失败（不可用/超预算/返回异常）。异常文本不得包含凭据。"""


class LLMConfigError(LLMError):
    """凭据或地址缺失。"""


class LLMBudgetExceeded(LLMError):
    """单次请求估算 token 超出预算。"""


@dataclass
class LLMSettings:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: float = 30.0
    max_retries: int = 2
    token_budget: int = 8000
    max_output_tokens: int = 1200

    @classmethod
    def from_env(cls, environ: Mapping[str, str], **overrides: Any) -> "LLMSettings":
        settings = cls(
            base_url=environ.get("LLM_BASE_URL", "").strip(),
            api_key=environ.get("LLM_API_KEY", "").strip(),
            model=environ.get("LLM_MODEL", "").strip(),
        )
        for key, value in overrides.items():
            if value is not None and hasattr(settings, key):
                setattr(settings, key, value)
        return settings

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


def estimate_tokens(text: str) -> int:
    """粗略估算：中文约 1 字 1 token，英文约 4 字符 1 token。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    other = len(text) - cjk
    return cjk + ((other + 3) // 4 if other else 0)


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._session = session or requests.Session()
        self._sleep = sleep

    def _endpoint(self) -> str:
        base = self.settings.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def check_budget(self, prompt: str) -> int:
        estimated = estimate_tokens(prompt) + self.settings.max_output_tokens
        if estimated > self.settings.token_budget:
            raise LLMBudgetExceeded(
                f"请求估算 {estimated} tokens 超出预算 {self.settings.token_budget}"
            )
        return estimated

    def complete(self, prompt: str, temperature: float = 0.2) -> str:
        if not self.settings.configured:
            raise LLMConfigError("LLM 未配置（需要 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL）")
        self.check_budget(prompt)

        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": "你是严谨的中文信息分析助手，只输出要求的 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": self.settings.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

        attempts = self.settings.max_retries + 1
        last_error: str = ""
        for attempt in range(attempts):
            try:
                response = self._session.post(
                    self._endpoint(),
                    json=payload,
                    headers=headers,
                    timeout=self.settings.timeout_seconds,
                )
                if response.status_code >= 500:
                    last_error = f"上游 {response.status_code}"
                    raise LLMError(last_error)
                if response.status_code >= 400:
                    # 4xx 不重试（凭据/参数问题重试无意义），且不回显响应体防泄露
                    raise LLMError(f"LLM 请求被拒绝（HTTP {response.status_code}）")
                data = response.json()
                return self._extract_content(data)
            except requests.Timeout:
                last_error = f"超时（{self.settings.timeout_seconds}s）"
            except requests.RequestException as exc:
                last_error = f"网络错误：{exc.__class__.__name__}"
            except LLMError as exc:
                last_error = str(exc)
                if "被拒绝" in last_error:
                    raise
            except ValueError as exc:  # JSON 解析
                last_error = f"响应不是合法 JSON：{exc.__class__.__name__}"
            if attempt < attempts - 1:
                self._sleep(min(2**attempt, 8))
        raise LLMError(f"LLM 调用失败（重试 {attempts} 次）：{last_error}")

    @staticmethod
    def _extract_content(data: dict) -> str:
        try:
            choices = data["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM 响应缺少 choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM 返回空内容")
        return content.strip()