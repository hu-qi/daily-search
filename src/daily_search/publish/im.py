"""可选 IM 推送（飞书 / 企业微信）：缺 webhook 时跳过并告警，不阻断主流程。"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

import requests

from ..agent.summarize import TopicDigest
from .markdown import report_url

logger = logging.getLogger("daily_search.publish.im")

USER_AGENT = "daily-search/0.1"


def render_im_text(
    day: str,
    digests: Sequence[TopicDigest],
    repo_full_name: str = "",
    base_branch: str = "main",
    max_points: int = 3,
) -> str:
    lines = [f"🔍 每日一搜 {day}"]
    for digest in digests:
        link = report_url(repo_full_name, base_branch, day, digest.slug)
        lines.append(f"\n【{digest.topic}】新增 {len(digest.points)} 条{'（降级）' if digest.degraded else ''}")
        for point in digest.points[:max_points]:
            lines.append(f"- {point.title} {point.url}")
        lines.append(f"完整报告：{link}")
    return "\n".join(lines)


def send_notifications(
    text: str,
    channels: Sequence[str],
    environ: Mapping[str, str],
    session: requests.Session | None = None,
    timeout: float = 15.0,
) -> list[dict[str, str]]:
    """按渠道发送；返回每个渠道的结果（sent / skipped / failed）。"""
    results: list[dict[str, str]] = []
    http = session or requests.Session()
    for channel in channels:
        if channel == "feishu":
            webhook = (environ.get("FEISHU_WEBHOOK_URL") or "").strip()
            payload = {"msg_type": "text", "content": {"text": text}}
        elif channel == "wecom":
            webhook = (environ.get("WECOM_WEBHOOK_URL") or "").strip()
            payload = {"msgtype": "text", "text": {"content": text}}
        else:
            results.append({"channel": channel, "status": "skipped", "reason": "未知渠道"})
            continue

        if not webhook:
            logger.warning("未配置 %s webhook，跳过推送", channel)
            results.append({"channel": channel, "status": "skipped", "reason": "missing webhook"})
            continue
        try:
            response = http.post(
                webhook,
                json=payload,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            results.append({"channel": channel, "status": "sent"})
        except Exception as exc:  # 通知失败不影响报告与 Issue
            logger.warning("%s 推送失败：%s", channel, exc.__class__.__name__)
            results.append({"channel": channel, "status": "failed", "reason": exc.__class__.__name__})
    return results