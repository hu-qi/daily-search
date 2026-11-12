"""订阅流程：解析 Issue 模板并生成 topics.yaml 变更。"""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_search.config import load_config
from daily_search.subscribe import (
    SubscribeError,
    apply_request,
    parse_request,
    pr_body,
    subscribe_from_issue_body,
)

BODY = """### 话题名称

低空经济政策

### 关键词（逗号分隔）

低空经济, eVTOL, 无人机

### 排除词（逗号分隔，可留空）

概念股, 涨停

### 订阅人（GitHub 用户名，逗号分隔）

hu-qi, alice

### 发布渠道

- [X] Issue（每日汇总 Issue）
- [X] RSS feed（feed.xml）
- [ ] 飞书
- [X] 企业微信
"""


def test_parse_request_reads_all_fields() -> None:
    request = parse_request(BODY)
    assert request.topic == "低空经济政策"
    assert request.keywords == ["低空经济", "eVTOL", "无人机"]
    assert request.exclude == ["概念股", "涨停"]
    assert request.subscribers == ["hu-qi", "alice"]
    assert request.channels.issue is True
    assert request.channels.feed is True
    assert request.channels.feishu is False
    assert request.channels.wecom is True


def test_parse_request_without_topic_raises() -> None:
    with pytest.raises(SubscribeError, match="话题名称"):
        parse_request("### 关键词\n昇腾\n")


def test_parse_request_without_keywords_raises() -> None:
    with pytest.raises(SubscribeError, match="关键词"):
        parse_request("### 话题名称\n某话题\n")


def test_channels_default_when_checkbox_missing() -> None:
    request = parse_request("### 话题名称\n某话题\n### 关键词\nk\n")
    assert request.channels.issue is True
    assert request.channels.feed is True


def test_apply_request_adds_topic(repo_root: Path) -> None:
    config = load_config(repo_root / "topics.yaml")
    updated = apply_request(config, parse_request(BODY))
    assert len(updated.topics) == len(config.topics) + 1
    assert updated.find("低空经济政策") is not None


def test_apply_request_replaces_existing_topic(repo_root: Path) -> None:
    config = load_config(repo_root / "topics.yaml")
    body = "### 话题名称\n昇腾 CANN\n### 关键词\n昇腾, NPU\n"
    updated = apply_request(config, parse_request(body))
    topic = updated.find("昇腾-cann")
    assert topic is not None
    assert topic.keywords == ["昇腾", "NPU"]
    assert len(updated.topics) == len(config.topics)


def test_subscribe_from_issue_body_returns_yaml(repo_root: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "topics.yaml"
    config_path.write_bytes((repo_root / "topics.yaml").read_bytes())
    topic, yaml_text = subscribe_from_issue_body(BODY, config_path)
    assert topic.slug == "低空经济政策"
    assert "低空经济政策" in yaml_text
    assert "eVTOL" in yaml_text


def test_pr_body_mentions_channels() -> None:
    topic = parse_request(BODY).to_topic()
    body = pr_body(topic, "42")
    assert "低空经济政策" in body
    assert "issue=True" in body
    assert "#42" in body