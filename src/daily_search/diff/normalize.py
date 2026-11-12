"""URL 规范化与语义相似度（diff 的第一层与第二层）。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 常见追踪参数：既影响 URL 去重，也不带信息量
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "spm",
    "from",
    "ref",
    "ref_src",
    "share_source",
    "share_token",
    "scene",
    "click_id",
    "fbclid",
    "gclid",
    "yclid",
    "igshid",
    "mc_cid",
    "mc_eid",
}

DEFAULT_PORTS = {"http": "80", "https": "443"}
PUNCT_RE = re.compile(r"[\s\u3000!-/:-@\[-`{-~·—…、“”‘’《》【】（）]+")


def normalize_url(url: str) -> str:
    """统一 scheme/host/端口/尾斜杠/追踪参数，保证同一篇内容只有一个指纹。"""
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    scheme = (parts.scheme or "https").lower()
    if scheme not in ("http", "https"):
        return url
    if scheme == "http":
        # 中文站点普遍已全站 https；统一到 https 避免同页双指纹
        scheme = "https"

    host = (parts.hostname or "").lower()
    if not host:
        return url
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host
    if port and str(port) not in DEFAULT_PORTS.values():
        netloc = f"{host}:{port}"
    if parts.username:
        netloc = f"{parts.username}@{netloc}"

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    query = urlencode(sorted(query_pairs))

    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(url: str) -> str:
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    """全角转半角 + 小写 + 去除标点空白，供语义指纹使用。"""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return PUNCT_RE.sub("", text)


def shingles(text: str, n: int = 3) -> set[str]:
    """字符级 n-gram；对中文无需分词，短文本退化为整体。"""
    cleaned = normalize_text(text)
    if not cleaned:
        return set()
    if len(cleaned) <= n:
        return {cleaned}
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


def semantic_hash(title: str, snippet: str = "") -> str:
    cleaned = normalize_text(f"{title} {snippet}")
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def is_similar(
    left_title: str,
    left_snippet: str,
    right_title: str,
    right_snippet: str,
    threshold: float = 0.85,
) -> bool:
    """标题 + 摘要的 3-gram Jaccard 相似度判重。"""
    left = shingles(f"{left_title} {left_snippet}")
    right = shingles(f"{right_title} {right_snippet}")
    return jaccard(left, right) >= threshold