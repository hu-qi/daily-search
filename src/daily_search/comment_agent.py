"""Issue 评论 Agent：`/deep`、`/mute`、`/why` 三条指令（仅维护者可触发）。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from .agent.summarize import Summarizer
from .config import AppConfig
from .diff.normalize import normalize_url
from .diff.snapshot import SnapshotStore
from .providers.base import SearchProvider, SearchResult

logger = logging.getLogger("daily_search.comment_agent")

ALLOWED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
COMMAND_RE = re.compile(r"^\s*/(?P<cmd>deep|mute|why)\b\s*(?P<arg>.*)$", re.I | re.S)


@dataclass
class CommentRequest:
    body: str
    author: str = ""
    author_association: str = ""
    issue_number: int = 0


@dataclass
class CommentResponse:
    kind: str
    text: str
    ok: bool = True
    yaml_patch: str = ""


class CommentAgent:
    def __init__(
        self,
        config: AppConfig,
        store: SnapshotStore | None = None,
        searcher: Callable[[str], Sequence[SearchResult]] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.searcher = searcher

    def handle(self, request: CommentRequest) -> CommentResponse:
        body = request.body or ""
        match = COMMAND_RE.match(body.strip())
        if match is None:
            return CommentResponse(
                kind="ignored",
                text="未识别指令；支持 `/deep <问题>`、`/mute <关键词>`、`/why [链接]`。",
                ok=False,
            )
        if (request.author_association or "").upper() not in ALLOWED_ASSOCIATIONS:
            return CommentResponse(
                kind="denied",
                text="该指令仅仓库 OWNER/MEMBER/COLLABORATOR 可用。",
                ok=False,
            )

        command = match.group("cmd").lower()
        argument = match.group("arg").strip()
        if command == "deep":
            return self._deep(argument)
        if command == "mute":
            return self._mute(argument)
        return self._why(argument)

    def _deep(self, question: str) -> CommentResponse:
        if not question:
            return CommentResponse(kind="deep", text="用法：`/deep <要深搜的问题>`", ok=False)
        if self.searcher is None:
            return CommentResponse(kind="deep", text="当前运行未配置搜索能力，无法深搜。", ok=False)
        try:
            results = list(self.searcher(question))[:5]
        except Exception as exc:  # Provider 失败不应让评论流程崩溃
            logger.warning("深搜失败：%s", exc.__class__.__name__)
            return CommentResponse(kind="deep", text=f"深搜失败：{exc.__class__.__name__}", ok=False)
        if not results:
            return CommentResponse(kind="deep", text="深搜没有找到新增链接。")
        lines = [f"### 🔎 深搜：{question}", ""]
        lines += [f"- [{item.title}]({item.url})" for item in results]
        return CommentResponse(kind="deep", text="\n".join(lines))

    def _mute(self, keyword: str) -> CommentResponse:
        keyword = keyword.strip()
        if not keyword:
            return CommentResponse(kind="mute", text="用法：`/mute <关键词>`", ok=False)
        patch_lines = [f"# 已为以下话题追加排除词：{keyword}"]
        for topic in self.config.topics:
            if keyword in topic.exclude:
                patch_lines.append(f"# {topic.topic}: 已存在")
                continue
            patch_lines.append(f"# {topic.topic}: exclude += {keyword}")
        return CommentResponse(
            kind="mute",
            text=(
                f"已记录降噪关键词「{keyword}」。该变更需经 topics.yaml 的 PR 生效"
                f"（避免直接改动默认分支）。\n\n```yaml\n"
                + "\n".join(patch_lines)
                + "\n```"
            ),
            yaml_patch="\n".join(patch_lines),
        )

    def _why(self, argument: str) -> CommentResponse:
        url = argument.strip()
        if not url:
            return CommentResponse(
                kind="why",
                text=(
                    "入选规则：① URL 规范化后与历史快照比对；"
                    "② 标题+摘要 3-gram Jaccard ≥ 阈值的视为同一事件；"
                    "③ 只有两层都未命中的条目才进入当日报告。"
                ),
            )
        if self.store is None:
            return CommentResponse(kind="why", text="当前运行无法读取历史快照。", ok=False)
        target = normalize_url(url)
        for topic in self.config.topics:
            for entry in self.store.load(topic.slug):
                if normalize_url(entry.url) == target:
                    matched = [kw for kw in topic.keywords if kw.lower() in entry.title.lower()]
                    return CommentResponse(
                        kind="why",
                        text=(
                            f"该链接属于话题「{topic.topic}」：\n"
                            f"- 首次出现：{entry.first_seen or '未知'}\n"
                            f"- 最近出现：{entry.last_seen or '未知'}\n"
                            f"- 命中关键词：{('、'.join(matched)) or '（摘要匹配）'}\n"
                            f"- 原始链接：{entry.url}"
                        ),
                    )
        return CommentResponse(kind="why", text="该链接不在任何话题的历史快照中（可能是首次出现的新条目）。")