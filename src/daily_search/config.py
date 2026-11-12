"""topics.yaml 的四级 schema 与加载校验（fail fast）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_PROVIDER_ORDER = ["soqq", "fallback_rss"]


class ConfigError(RuntimeError):
    """配置不可用时抛出，调用方应直接失败而不是带病运行。"""


class Channels(BaseModel):
    """发布渠道开关：Issue 与 feed 为必选，IM 为可选。"""

    issue: bool = True
    feed: bool = True
    feishu: bool = False
    wecom: bool = False


class TopicConfig(BaseModel):
    topic: str
    keywords: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    subscribers: list[str] = Field(default_factory=list)
    channels: Channels = Field(default_factory=Channels)
    max_results: int | None = Field(default=None, ge=1, le=100)
    provider_order: list[str] | None = None

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("topic 不能为空")
        if len(value) > 80:
            raise ValueError("topic 长度不得超过 80 字符")
        return value

    @field_validator("keywords")
    @classmethod
    def _keywords_required(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("keywords 至少需要一个非空关键词")
        # 去重且保序
        seen: set[str] = set()
        result: list[str] = []
        for item in cleaned:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @field_validator("exclude", "subscribers")
    @classmethod
    def _strip_list(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @property
    def slug(self) -> str:
        """目录名/快照名：保留中文，把空白与符号折叠为连字符。"""
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", self.topic.strip().lower())
        return slug.strip("-") or "topic"


class Defaults(BaseModel):
    provider_order: list[str] = Field(default_factory=lambda: list(DEFAULT_PROVIDER_ORDER))
    max_results: int = Field(default=20, ge=1, le=100)
    lookback_days: int = Field(default=1, ge=1, le=30)
    snapshot_retention_days: int = Field(default=14, ge=1, le=365)
    semantic_threshold: float = Field(default=0.85, ge=0.5, le=1.0)
    fallback_feeds: list[str] = Field(default_factory=list)
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_token_budget: int = Field(default=8000, ge=512, le=128_000)
    max_queries_per_topic: int = Field(default=3, ge=1, le=10)


class AppConfig(BaseModel):
    defaults: Defaults = Field(default_factory=Defaults)
    topics: list[TopicConfig]

    @model_validator(mode="after")
    def _unique_slugs(self) -> "AppConfig":
        if not self.topics:
            raise ValueError("topics 不能为空")
        seen: dict[str, str] = {}
        for topic in self.topics:
            slug = topic.slug
            if slug in seen:
                raise ValueError(f"话题 slug 重复：{seen[slug]} / {topic.topic} → {slug}")
            seen[slug] = topic.topic
        return self

    def find(self, identifier: str) -> TopicConfig | None:
        """按 slug 或原始 topic 名查找。"""
        key = identifier.strip().lower()
        for topic in self.topics:
            if topic.slug.lower() == key or topic.topic.lower() == key:
                return topic
        return None

    def provider_order_for(self, topic: TopicConfig) -> list[str]:
        return topic.provider_order or self.defaults.provider_order

    def max_results_for(self, topic: TopicConfig) -> int:
        return topic.max_results or self.defaults.max_results


def load_config(path: str | Path) -> AppConfig:
    """读取并校验 topics.yaml；任何结构问题都抛 ConfigError。"""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在：{config_path}")
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - 取决于输入
        raise ConfigError(f"topics.yaml 不是合法 YAML：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("topics.yaml 顶层必须是映射（defaults/topics）")
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(f"topics.yaml 校验失败：{exc}") from exc


def dump_config(config: AppConfig) -> str:
    """把配置写回 YAML（订阅流程用），保持字段顺序稳定。"""
    payload = config.model_dump(exclude_none=True)
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)