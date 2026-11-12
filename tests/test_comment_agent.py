"""Issue 评论 Agent：权限、三条指令与「不编造链接」。"""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_search.comment_agent import CommentAgent, CommentRequest
from daily_search.config import load_config
from daily_search.diff.snapshot import SnapshotEntry, SnapshotStore
from daily_search.providers.base import SearchResult, ProviderError


@pytest.fixture
def config(repo_root: Path):
    return load_config(repo_root / "topics.yaml")


def _agent(config, store=None, searcher=None) -> CommentAgent:
    return CommentAgent(config, store=store, searcher=searcher)


def test_non_maintainer_is_denied(config) -> None:
    response = _agent(config).handle(
        CommentRequest(body="/deep 昇腾 8.0", author="stranger", author_association="CONTRIBUTOR")
    )
    assert response.ok is False
    assert response.kind == "denied"


def test_unknown_command_is_ignored(config) -> None:
    response = _agent(config).handle(CommentRequest(body="你好", author="hu-qi", author_association="OWNER"))
    assert response.ok is False
    assert response.kind == "ignored"


def test_deep_returns_links(config) -> None:
    searcher = lambda _q: [SearchResult(title="深搜结果标题", url="https://example.com/deep")]
    response = _agent(config, searcher=searcher).handle(
        CommentRequest(body="/deep 昇腾 CANN 8.0 有哪些变化", author="hu-qi", author_association="OWNER")
    )
    assert response.ok is True
    assert "https://example.com/deep" in response.text


def test_deep_without_searcher(config) -> None:
    response = _agent(config).handle(CommentRequest(body="/deep 问题", author="a", author_association="MEMBER"))
    assert response.ok is False
    assert "未配置搜索能力" in response.text


def test_deep_provider_failure_is_reported(config) -> None:
    def searcher(_q):
        raise ProviderError("风控")

    response = _agent(config, searcher=searcher).handle(
        CommentRequest(body="/deep 问题", author="a", author_association="MEMBER")
    )
    assert response.ok is False
    assert "深搜失败" in response.text


def test_mute_lists_patch_targets(config) -> None:
    response = _agent(config).handle(
        CommentRequest(body="/mute 概念股", author="hu-qi", author_association="COLLABORATOR")
    )
    assert response.ok is True
    assert "概念股" in response.text
    assert "topics.yaml" in response.text
    assert response.yaml_patch


def test_mute_requires_keyword(config) -> None:
    response = _agent(config).handle(CommentRequest(body="/mute", author="a", author_association="OWNER"))
    assert response.ok is False


def test_why_without_argument_explains_policy(config) -> None:
    response = _agent(config).handle(CommentRequest(body="/why", author="a", author_association="OWNER"))
    assert "Jaccard" in response.text


def test_why_finds_snapshot_entry(config, tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    store.save(
        "昇腾-cann",
        [
            SnapshotEntry(
                url="https://example.com/1?utm_source=x",
                url_hash="h",
                norm_hash="n",
                title="昇腾 CANN 8.0 发布",
                first_seen="2026-11-10",
                last_seen="2026-11-11",
            )
        ],
        "2026-11-11",
    )
    response = _agent(config, store=store).handle(
        CommentRequest(body="/why https://example.com/1", author="a", author_association="OWNER")
    )
    assert "昇腾 CANN" in response.text
    assert "2026-11-10" in response.text


def test_why_unknown_link(config, tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    response = _agent(config, store=store).handle(
        CommentRequest(body="/why https://example.com/unknown", author="a", author_association="OWNER")
    )
    assert "不在任何话题" in response.text