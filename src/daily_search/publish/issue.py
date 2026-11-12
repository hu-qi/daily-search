"""当日 Issue：按话题分组、每条带链接、附重复率统计。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Sequence

import requests

from ..agent.summarize import TopicDigest
from .markdown import CONFIDENCE_LABEL, report_url

logger = logging.getLogger("daily_search.publish.issue")

ISSUE_TITLE_TEMPLATE = "🔍 每日一搜 {day}"
GITHUB_API = "https://api.github.com"
USER_AGENT = "daily-search/0.1"


def issue_title(day: str | date) -> str:
    return ISSUE_TITLE_TEMPLATE.format(day=day.isoformat() if isinstance(day, date) else day)


def render_issue_body(
    day: str,
    digests: Sequence[TopicDigest],
    repo_full_name: str = "",
    base_branch: str = "main",
    notify_note: str = "",
) -> str:
    total_new = sum(len(digest.points) for digest in digests)
    degraded = [digest.topic for digest in digests if digest.degraded]
    lines = [
        f"## 🔍 每日一搜 {day}",
        "",
        f"- 话题：{len(digests)} 个；今日新增：**{total_new}** 条",
        f"- 降级话题：{('、'.join(degraded)) if degraded else '无'}",
    ]
    if notify_note:
        lines.append(f"- 通知：{notify_note}")
    lines += ["", "---", ""]

    for digest in digests:
        stats = digest.stats or {}
        link = report_url(repo_full_name, base_branch, day, digest.slug)
        lines.append(f"### {digest.topic}{'（降级）' if digest.degraded else ''}")
        lines.append("")
        lines.append(digest.summary or "（无摘要）")
        lines.append("")
        if digest.points:
            for point in digest.points:
                note = f" — {point.note}" if point.note else ""
                confidence = CONFIDENCE_LABEL.get(point.confidence, point.confidence)
                lines.append(f"- [{point.title}]({point.url}){note} ｜ 置信度 {confidence}")
        else:
            lines.append("- 今日无新增条目")
        lines += [
            "",
            f"> 新增 {stats.get('accepted', len(digest.points))} / 候选 {stats.get('total', 0)}"
            f" ｜ 重复率 {float(stats.get('repeat_rate', 0.0)) * 100:.1f}%"
            f" ｜ Provider：{digest.provider or 'n/a'}"
            f" ｜ [完整报告]({link})",
            "",
        ]
        if digest.degraded and digest.degraded_reason:
            lines += [f"> 降级原因：{digest.degraded_reason}", ""]

    lines += ["---", "", "由 `daily-search` 自动生成；回复 `/deep <问题>`、`/mute <关键词>`、`/why` 可继续操作。", ""]
    return "\n".join(lines)


class IssuePublisher:
    """用 workflow 的 GITHUB_TOKEN 创建/更新当日 Issue（token 只从 env 读）。"""

    def __init__(
        self,
        token: str,
        repo_full_name: str,
        session: requests.Session | None = None,
        api_base: str = GITHUB_API,
    ) -> None:
        self._token = token
        self.repo_full_name = repo_full_name
        self._session = session or requests.Session()
        self._api_base = api_base.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.repo_full_name)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        }

    def find_open_issue(self, title: str) -> dict[str, Any] | None:
        url = f"{self._api_base}/repos/{self.repo_full_name}/issues"
        response = self._session.get(
            url,
            headers=self._headers(),
            params={"state": "open", "per_page": 100},
            timeout=20,
        )
        response.raise_for_status()
        for issue in response.json():
            if issue.get("title") == title and "pull_request" not in issue:
                return issue
        return None

    def publish(self, day: str, body: str, labels: Sequence[str] | None = None) -> dict[str, Any]:
        if not self.enabled:
            logger.warning("未配置 GITHUB_TOKEN/仓库，跳过 Issue 发布")
            return {"action": "skipped", "reason": "missing token or repo"}

        title = issue_title(day)
        existing = self.find_open_issue(title)
        if existing:
            response = self._session.patch(
                f"{self._api_base}/repos/{self.repo_full_name}/issues/{existing['number']}",
                headers=self._headers(),
                json={"body": body},
                timeout=20,
            )
            response.raise_for_status()
            return {"action": "updated", "number": existing["number"]}

        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = list(labels)
        response = self._session.post(
            f"{self._api_base}/repos/{self.repo_full_name}/issues",
            headers=self._headers(),
            json=payload,
            timeout=20,
        )
        try:
            response.raise_for_status()
        except Exception:
            if not labels:
                raise
            # label 未在仓库创建时 GitHub 返回 422；退化为不带 label 再建一次，保证当天有 Issue
            logger.warning("带 label 创建 Issue 失败，按无 label 重试")
            response = self._session.post(
                f"{self._api_base}/repos/{self.repo_full_name}/issues",
                headers=self._headers(),
                json={"title": title, "body": body},
                timeout=20,
            )
            response.raise_for_status()
        created = response.json()
        return {"action": "created", "number": created.get("number")}