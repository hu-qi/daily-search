"""Markdown 报告渲染：每条结论必须带可点击原始链接。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence

from ..agent.summarize import TopicDigest

logger = logging.getLogger("daily_search.publish.markdown")

CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低", "unknown": "未知"}


def report_relpath(day: str, slug: str) -> str:
    return f"reports/{day}/{slug}.md"


def report_url(repo_full_name: str, base_branch: str, day: str, slug: str) -> str:
    """Issue / feed 里必须用可点击的仓库绝对地址。"""
    owner_repo = repo_full_name or "owner/repo"
    return f"https://github.com/{owner_repo}/blob/{base_branch or 'main'}/{report_relpath(day, slug)}"


def _fmt_rate(stats: dict) -> str:
    rate = stats.get("repeat_rate", 0.0)
    try:
        return f"{float(rate) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def render_topic_report(
    digest: TopicDigest,
    repo_full_name: str = "",
    base_branch: str = "main",
    generated_at: str | None = None,
) -> str:
    stats = digest.stats or {}
    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = [
        f"# {digest.topic} · {digest.date}",
        "",
        f"> degraded: {'true' if digest.degraded else 'false'} ｜ Provider: {digest.provider or 'n/a'}"
        f" ｜ 新增 {stats.get('accepted', len(digest.points))} / 候选 {stats.get('total', len(digest.points))}"
        f"（重复率 {_fmt_rate(stats)}）",
        f"> 生成时间：{stamp} ｜ 仓库：{repo_full_name or 'n/a'}",
    ]
    if digest.degraded and digest.degraded_reason:
        lines.append(f"> 降级原因：{digest.degraded_reason}")
    if digest.fallback_reason:
        lines.append(f"> Provider 说明：{digest.fallback_reason}")

    lines += ["", "## 今日变化", "", digest.summary or "（无摘要）", ""]

    if digest.points:
        lines += ["## 新增要点", ""]
        for index, point in enumerate(digest.points, start=1):
            confidence = CONFIDENCE_LABEL.get(point.confidence, point.confidence)
            marker = "" if point.worth_following else "（建议略过）"
            note = f" — {point.note}" if point.note else ""
            lines.append(
                f"{index}. [{point.title}]({point.url}){note}"
                f" · 置信度：{confidence}{marker}"
            )
        lines.append("")
        if digest.unreferenced:
            lines += ["## 其他新增链接", ""]
            for point in digest.unreferenced:
                lines.append(f"- [{point.title}]({point.url}) — {point.note or '未纳入摘要'}")
            lines.append("")
        lines += ["## 原始链接", ""]
        for point in digest.all_points:
            lines.append(f"- [{point.title}]({point.url})")
        lines.append("")
    elif digest.unreferenced:
        lines += ["## 其他新增链接", ""]
        for point in digest.unreferenced:
            lines.append(f"- [{point.title}]({point.url}) — {point.note or '未纳入摘要'}")
        lines.append("")
    else:
        lines += ["## 新增要点", "", "今日无新增条目。", ""]

    lines.append("<!-- daily-search: managed -->")
    return "\n".join(lines)


def render_daily_index(
    day: str,
    digests: Sequence[TopicDigest],
    repo_full_name: str = "",
    base_branch: str = "main",
    generated_at: str | None = None,
) -> str:
    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    degraded = sum(1 for digest in digests if digest.degraded)
    lines = [
        f"# 🔍 每日一搜 {day}",
        "",
        f"> 话题数 {len(digests)} ｜ 降级 {degraded} ｜ 生成时间 {stamp}",
        "",
        "| 话题 | 新增 | 重复率 | 报告 |",
        "| --- | --- | --- | --- |",
    ]
    for digest in digests:
        stats = digest.stats or {}
        link = report_url(repo_full_name, base_branch, day, digest.slug)
        flag = "（降级）" if digest.degraded else ""
        lines.append(
            f"| {digest.topic}{flag} | {stats.get('accepted', len(digest.points))} | "
            f"{_fmt_rate(stats)} | [报告]({link}) |"
        )
    lines += ["", "<!-- daily-search: managed -->", ""]
    return "\n".join(lines)