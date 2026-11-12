"""GitHub Pages 归档：把 reports/ 与 feed.xml 复制成静态站点。"""

from __future__ import annotations

import logging
import shutil
from datetime import date
from pathlib import Path

from .atomic import atomic_write_text

logger = logging.getLogger("daily_search.publish.pages")

INDEX_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日一搜 · 历史归档</title>
<style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;max-width:760px;margin:40px auto;padding:0 16px;line-height:1.7}}
h1{{font-size:22px}} li{{margin:4px 0}} a{{color:#0b62c4;text-decoration:none}} a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<h1>🔍 每日一搜 · 历史归档</h1>
<p><a href="feed.xml">RSS 订阅</a></p>
<ul>
{items}
</ul>
<p>最近更新：{updated}</p>
</body>
</html>
"""


def build_site(reports_dir: Path | str, site_dir: Path | str, feed_path: Path | str | None = None) -> list[Path]:
    """复制报告与 feed，并生成索引页；返回写入的文件列表。"""
    reports = Path(reports_dir)
    site = Path(site_dir)
    site.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    entries: list[tuple[str, str, str]] = []  # (date, slug, href)

    if reports.is_dir():
        target_reports = site / "reports"
        for report in sorted(reports.rglob("*.md")):
            relative = report.relative_to(reports)
            destination = target_reports / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(report, destination)
            written.append(destination)
            day = relative.parts[0] if len(relative.parts) > 1 else ""
            entries.append((day, relative.stem, f"reports/{relative.as_posix()}"))

    feed = Path(feed_path) if feed_path else None
    if feed and feed.is_file():
        destination = site / "feed.xml"
        shutil.copyfile(feed, destination)
        written.append(destination)

    entries.sort(key=lambda row: (row[0], row[1]), reverse=True)
    items = "\n".join(
        f'  <li>{day} · <a href="{href}">{slug}</a></li>' for day, slug, href in entries
    ) or "  <li>暂无报告</li>"
    index = INDEX_TEMPLATE.format(items=items, updated=date.today().isoformat())
    index_path = atomic_write_text(site / "index.html", index)
    written.append(index_path)
    logger.info("Pages 站点已生成：%d 个文件", len(written))
    return written