"""可行性 spike：探测 so.qq.com 在 runner 上的可访问性与风控情况。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable

import requests

from .providers.soqq import USER_AGENT, looks_blocked

DEFAULT_URL = "https://so.qq.com/"


@dataclass
class SpikeResult:
    url: str
    reachable: bool
    status_code: int | None
    blocked: bool
    elapsed_ms: int
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def default_fetch(url: str, timeout: float = 20.0) -> tuple[int | None, str]:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    return response.status_code, response.text


def probe(
    url: str = DEFAULT_URL,
    fetch: Callable[[str], tuple[int | None, str]] | None = None,
    timeout: float = 20.0,
) -> SpikeResult:
    fetcher = fetch or (lambda target: default_fetch(target, timeout=timeout))
    started = time.perf_counter()
    try:
        status, body = fetcher(url)
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return SpikeResult(
            url=url,
            reachable=False,
            status_code=None,
            blocked=False,
            elapsed_ms=elapsed,
            detail=f"{exc.__class__.__name__}",
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    blocked = looks_blocked(body or "")
    reachable = status is not None and 200 <= status < 400 and not blocked
    detail = "ok" if reachable else ("验证码/风控页" if blocked else f"HTTP {status}")
    return SpikeResult(
        url=url,
        reachable=reachable,
        status_code=status,
        blocked=blocked,
        elapsed_ms=elapsed,
        detail=detail,
    )


def render_markdown(result: SpikeResult) -> str:
    return "\n".join(
        [
            "### so.qq.com 可行性探测",
            "",
            f"- URL：{result.url}",
            f"- 可达：{'✅' if result.reachable else '❌'}",
            f"- HTTP：{result.status_code}",
            f"- 风控/验证码：{'是' if result.blocked else '否'}",
            f"- 耗时：{result.elapsed_ms} ms",
            f"- 结论：{result.detail}",
            "",
        ]
    )