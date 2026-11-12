"""topics.yaml 校验：fail fast、默认值、slug 与查找。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from daily_search.config import AppConfig, ConfigError, load_config


def test_load_repo_topics(repo_root: Path) -> None:
    config = load_config(repo_root / "topics.yaml")
    assert [topic.slug for topic in config.topics] == ["昇腾-cann", "鸿蒙-ohpm", "具身智能政策"]
    assert config.defaults.semantic_threshold == 0.85
    assert config.defaults.provider_order == ["soqq", "fallback_rss"]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="配置文件不存在"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text("topics: [\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_non_mapping_root_raises(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="顶层必须是映射"):
        load_config(path)


def test_empty_keywords_rejected(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(
        yaml.safe_dump({"topics": [{"topic": "某话题", "keywords": []}]}, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="keywords"):
        load_config(path)


def test_blank_topic_rejected(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(
        yaml.safe_dump({"topics": [{"topic": "  ", "keywords": ["x"]}]}, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_duplicate_slug_rejected(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "topics": [
                    {"topic": "Alpha", "keywords": ["a"]},
                    {"topic": "alpha", "keywords": ["b"]},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="slug 重复"):
        load_config(path)


def test_keywords_deduplicated_in_order(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text(
        yaml.safe_dump(
            {"topics": [{"topic": "话题", "keywords": ["b", "a", "b", " "], "exclude": ["x", ""]}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    topic = config.topics[0]
    assert topic.keywords == ["b", "a"]
    assert topic.exclude == ["x"]


def test_channels_default_to_issue_and_feed() -> None:
    topic = AppConfig.model_validate(
        {"topics": [{"topic": "话题", "keywords": ["k"]}]}
    ).topics[0]
    assert topic.channels.issue is True
    assert topic.channels.feed is True
    assert topic.channels.feishu is False
    assert topic.channels.wecom is False


def test_find_by_slug_and_name(repo_root: Path) -> None:
    config = load_config(repo_root / "topics.yaml")
    assert config.find("昇腾-cann") is not None
    assert config.find("昇腾 CANN") is not None
    assert config.find("不存在") is None


def test_topic_overrides_apply() -> None:
    config = AppConfig.model_validate(
        {
            "defaults": {"max_results": 5},
            "topics": [
                {"topic": "话题", "keywords": ["k"], "max_results": 9, "provider_order": ["fallback_rss"]}
            ],
        }
    )
    topic = config.topics[0]
    assert config.max_results_for(topic) == 9
    assert config.provider_order_for(topic) == ["fallback_rss"]


def test_slug_keeps_chinese_and_folds_spaces() -> None:
    config = AppConfig.model_validate(
        {"topics": [{"topic": "  具身智能 / 政策  ", "keywords": ["k"]}]}
    )
    assert config.topics[0].slug == "具身智能-政策"