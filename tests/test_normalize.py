"""URL 规范化与语义相似度。"""

from __future__ import annotations

import pytest

from daily_search.diff.normalize import (
    is_similar,
    jaccard,
    normalize_text,
    normalize_url,
    semantic_hash,
    shingles,
    url_hash,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Example.com/a/?utm_source=x&b=2&a=1#frag", "https://example.com/a?a=1&b=2"),
        ("http://example.com", "https://example.com/"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/a?ref=wechat&spm=1.2", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com/a?utm_medium=x&utm_campaign=y", "https://example.com/a"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_url_hash_is_stable_across_tracking_variants() -> None:
    left = "https://example.com/post/1?utm_source=wechat&from=timeline"
    right = "http://www.example.com/post/1/#comments"
    assert url_hash(left) == url_hash(right)


def test_normalize_url_keeps_meaningful_query() -> None:
    normalized = normalize_url("https://example.com/search?q=昇腾&page=2")
    assert normalized.startswith("https://example.com/search?page=2&q=")
    assert "%E6%98%87%E8%85%BE" in normalized


def test_normalize_url_handles_blank_and_non_http() -> None:
    assert normalize_url("") == ""
    assert normalize_url("mailto:a@b.com") == "mailto:a@b.com"


def test_shingles_and_jaccard_identical() -> None:
    text = "昇腾 CANN 8.0 发布，图引擎升级"
    assert jaccard(shingles(text), shingles(text)) == 1.0


def test_jaccard_disjoint_is_zero() -> None:
    assert jaccard(shingles("今天天气不错"), shingles("股市涨停分析")) == 0.0


def test_jaccard_empty_sets() -> None:
    assert jaccard(set(), set()) == 0.0


def test_normalize_text_fullwidth_and_punctuation() -> None:
    assert normalize_text("ＣＡＮＮ，８．０！") == "cann80"


def test_is_similar_detects_near_duplicate() -> None:
    assert is_similar(
        "昇腾 CANN 8.0 发布：算子开发与图引擎全面升级",
        "CANN 8.0 在图引擎与算子开发上全面升级",
        "昇腾 CANN 8.0 发布：算子开发与图引擎全面升级",
        "CANN 8.0 在图引擎与算子开发上全面升级",
        threshold=0.85,
    )


def test_is_similar_threshold_is_configurable() -> None:
    left = ("昇腾 CANN 8.0 发布：算子与图引擎升级", "全面升级")
    right = ("昇腾 CANN 8.0 正式发布：算子与图引擎升级", "全面升级")
    assert is_similar(*left, *right, threshold=0.70) is True
    assert is_similar(*left, *right, threshold=0.95) is False


def test_is_similar_rejects_unrelated() -> None:
    assert not is_similar("鸿蒙 OHPM 中心仓新增依赖扫描", "安全合规", "人形机器人政策细则", "场景开放")


def test_semantic_hash_deterministic() -> None:
    assert semantic_hash("标题", "摘要") == semantic_hash("标题", "摘要")
    assert semantic_hash("标题 A") != semantic_hash("标题 B")