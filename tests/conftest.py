"""测试公共夹具：仓库根路径、临时工作目录、可编排的假 HTTP 会话。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """按顺序返回预置响应；可用于 requests.Session 的替身。"""

    def __init__(self, responses: Iterable[Any]) -> None:
        self._responses: list[Any] = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _next(self, method: str, url: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise AssertionError("FakeSession 没有更多预置响应")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(method, url, kwargs)
        if not (hasattr(item, "json") and hasattr(item, "status_code")):
            raise AssertionError(f"预置响应缺少 json/status_code：{item!r}")
        return item

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._next("GET", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._next("POST", url, kwargs)

    def patch(self, url: str, **kwargs: Any) -> Any:
        return self._next("PATCH", url, kwargs)


@pytest.fixture
def fake_session() -> Callable[[Iterable[Any]], FakeSession]:
    return FakeSession


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path