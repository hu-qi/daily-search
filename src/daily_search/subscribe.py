"""订阅流程：解析「订阅话题」Issue 模板 → 生成 topics.yaml 变更。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .config import AppConfig, Channels, ConfigError, TopicConfig, dump_config, load_config

SECTION_RE = re.compile(r"^###\s+(.+?)\s*$", re.M)
CHECKBOX_RE = re.compile(r"^\s*-\s*\[(?P<checked>[xX ])\]\s*(?P<label>.+?)\s*$", re.M)

FIELD_TOPIC = "话题名称"
FIELD_KEYWORDS = "关键词"
FIELD_EXCLUDE = "排除词"
FIELD_SUBSCRIBERS = "订阅人"
FIELD_CHANNELS = "发布渠道"

CHANNEL_LABELS = {
    "issue": "issue",
    "rss": "feed",
    "feed": "feed",
    "飞书": "feishu",
    "feishu": "feishu",
    "企业微信": "wecom",
    "wecom": "wecom",
}


class SubscribeError(ValueError):
    """订阅请求不合法（模板缺字段/关键词为空）。"""


@dataclass
class TopicRequest:
    topic: str
    keywords: list[str]
    exclude: list[str]
    subscribers: list[str]
    channels: Channels

    def to_topic(self) -> TopicConfig:
        return TopicConfig(
            topic=self.topic,
            keywords=self.keywords,
            exclude=self.exclude,
            subscribers=self.subscribers,
            channels=self.channels,
        )


def split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(body or ""))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[start:end].strip()
    return sections


def _split_list(value: str) -> list[str]:
    items = re.split(r"[,，、\s]+", value.strip())
    return [item.strip() for item in items if item.strip()]


def _parse_channels(raw: str) -> Channels:
    channels = Channels(issue=False, feed=False, feishu=False, wecom=False)
    found_any = False
    for match in CHECKBOX_RE.finditer(raw or ""):
        if match.group("checked").lower() != "x":
            continue
        label = match.group("label").lower()
        for key, field in CHANNEL_LABELS.items():
            if key in label:
                setattr(channels, field, True)
                found_any = True
                break
    if not found_any:
        return Channels()  # 未勾选 → 默认 Issue + feed
    return channels


def _section(sections: dict[str, str], name: str) -> str:
    """按前缀匹配段落标题（模板标题常带补充说明，如「关键词（逗号分隔）」）。"""
    for key, value in sections.items():
        if key == name or key.startswith(name):
            return value
    return ""


def parse_request(body: str) -> TopicRequest:
    sections = split_sections(body)
    if _section(sections, FIELD_TOPIC) == "" and FIELD_TOPIC not in sections:
        raise SubscribeError(f"模板缺少「{FIELD_TOPIC}」段落")
    topic = _section(sections, FIELD_TOPIC).strip()
    if not topic:
        raise SubscribeError("话题名称不能为空")
    keywords = _split_list(_section(sections, FIELD_KEYWORDS))
    if not keywords:
        raise SubscribeError("关键词至少填写一个")
    return TopicRequest(
        topic=topic,
        keywords=keywords,
        exclude=_split_list(_section(sections, FIELD_EXCLUDE)),
        subscribers=_split_list(_section(sections, FIELD_SUBSCRIBERS)),
        channels=_parse_channels(_section(sections, FIELD_CHANNELS)),
    )


def apply_request(config: AppConfig, request: TopicRequest) -> AppConfig:
    topic = request.to_topic()
    existing = config.find(topic.topic)
    topics = [item for item in config.topics if item.slug != topic.slug]
    if existing is not None:
        topics = [item for item in config.topics if item.slug != existing.slug]
    topics.append(topic)
    payload: dict[str, Any] = config.model_dump(exclude_none=True)
    payload["topics"] = [item.model_dump(exclude_none=True) for item in topics]
    try:
        return AppConfig.model_validate(payload)
    except Exception as exc:
        raise SubscribeError(f"合并后配置校验失败：{exc}") from exc


def subscribe_from_issue_body(body: str, config_path: str | Path) -> tuple[TopicConfig, str]:
    """返回 (新增/更新的话题, 新的 topics.yaml 文本)。"""
    config = load_config(config_path)
    request = parse_request(body)
    updated = apply_request(config, request)
    topic = updated.find(request.topic)
    if topic is None:  # pragma: no cover - 防御性
        raise SubscribeError("合并后找不到话题")
    return topic, dump_config(updated)


def pr_body(topic: TopicConfig, issue_number: str = "") -> str:
    return "\n".join(
        [
            f"## 新增订阅话题：{topic.topic}",
            "",
            f"- slug：`{topic.slug}`",
            f"- 关键词：{('、'.join(topic.keywords)) or '—'}",
            f"- 排除词：{('、'.join(topic.exclude)) or '—'}",
            f"- 订阅人：{('、'.join(topic.subscribers)) or '—'}",
            f"- 渠道：issue={topic.channels.issue} feed={topic.channels.feed}"
            f" feishu={topic.channels.feishu} wecom={topic.channels.wecom}",
            "",
            f"由订阅 Issue #{issue_number or '?'} 自动生成（{date.today().isoformat()}）。",
            "合并后下一轮 `daily-search` 将自动开始追踪该话题。",
            "",
        ]
    )