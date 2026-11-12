"""命令行入口：`python -m daily_search <run|spike|subscribe|comment|pages>`。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from .agent.llm import LLMClient, LLMSettings
from .agent.summarize import Summarizer
from .comment_agent import CommentAgent, CommentRequest, CommentResponse
from .config import ConfigError, AppConfig, load_config
from .diff.snapshot import SnapshotStore
from .logging_utils import redact_text, setup_logging
from .publish.issue import IssuePublisher, render_issue_body
from .publish.im import render_im_text, send_notifications
from .publish.pages import build_site
from .runner import DailyRunner
from .spike import probe, render_markdown
from .subscribe import SubscribeError, pr_body, subscribe_from_issue_body

logger = logging.getLogger("daily_search.cli")

DEFAULT_CONFIG = "topics.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-search", description="每日一搜：只推新增的中文话题追踪")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="topics.yaml 路径")
    parser.add_argument("--root", default=".", help="仓库根目录（reports/data/feed 的基准）")
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一次每日搜索")
    run.add_argument("--date", default="", help="运行日期 YYYY-MM-DD（默认今天）")
    run.add_argument("--topic", default="", help="只跑指定话题（slug 或话题名）")
    run.add_argument("--dry-run", action="store_true", help="只输出，不写快照/feed/报告")
    run.add_argument("--fixture", default="", help="离线 fixture JSON 路径（不发网络请求）")
    run.add_argument("--provider-order", default="", help="Provider 顺序，逗号分隔")
    run.add_argument("--no-issue", action="store_true", help="不创建/更新当日 Issue")
    run.add_argument("--im", default="", help="额外推送渠道，逗号分隔（feishu,wecom）")
    run.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    spike = sub.add_parser("spike", help="探测 so.qq.com 可访问性")
    spike.add_argument("--url", default="https://so.qq.com/")
    spike.add_argument("--json", action="store_true")

    subscribe = sub.add_parser("subscribe", help="根据订阅 Issue 生成 topics.yaml 变更")
    subscribe.add_argument("--body-file", required=True)
    subscribe.add_argument("--issue-number", default="")
    subscribe.add_argument("--write", action="store_true", help="直接写入 topics.yaml（默认只打印）")
    subscribe.add_argument("--pr-body", default="", help="把 PR 描述写到该文件")

    comment = sub.add_parser("comment", help="处理 Issue 评论指令")
    comment.add_argument("--body-file", required=True)
    comment.add_argument("--author", default="")
    comment.add_argument("--association", default="")
    comment.add_argument("--issue-number", default="0")

    pages = sub.add_parser("pages", help="生成 GitHub Pages 站点")
    pages.add_argument("--site-dir", default="site")

    notify = sub.add_parser("notify", help="把已有报告摘要推送到 IM")
    notify.add_argument("--file", required=True, help="要推送的 Markdown 文件")
    notify.add_argument("--channels", default="feishu,wecom")
    notify.add_argument("--max-chars", type=int, default=1500)
    return parser


def _build_summarizer(config: AppConfig, environ: Mapping[str, str]) -> Summarizer:
    settings = LLMSettings.from_env(
        environ,
        timeout_seconds=config.defaults.llm_timeout_seconds,
        max_retries=config.defaults.llm_max_retries,
        token_budget=config.defaults.llm_token_budget,
    )
    if not settings.configured:
        logger.warning("LLM 未配置，本次将以「仅新增链接列表」降级运行")
        return Summarizer(None)
    return Summarizer(LLMClient(settings))


def _cmd_run(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    config = load_config(args.config)
    root = Path(args.root)
    order = [item.strip() for item in args.provider_order.split(",") if item.strip()] or None
    repo_full_name = environ.get("REPO_FULL_NAME", "")
    runner = DailyRunner.from_config(
        config,
        root=root,
        summarizer=_build_summarizer(config, environ),
        repo_full_name=repo_full_name,
        base_branch=environ.get("BASE_BRANCH", "main"),
        fixture_path=args.fixture or None,
        environ=environ,
        order=order,
    )
    daily = runner.run(day=args.date or None, topic_filter=args.topic or None, dry_run=args.dry_run)

    if args.json:
        print(json.dumps([run.digest.to_dict() for run in daily.topics], ensure_ascii=False, indent=2))
    elif args.dry_run:
        for run in daily.topics:
            print(redact_text(run.report_text, environ))
    else:
        print(redact_text(daily.index_text, environ))
        for run in daily.topics:
            print(f"- {run.digest.topic}: 新增 {len(run.digest.points)} 条"
                  f"{'（降级）' if run.digest.degraded else ''}")

    if args.dry_run:
        return 0

    notify_note = ""
    im_channels = [item.strip() for item in args.im.split(",") if item.strip()]
    if im_channels:
        text = render_im_text(
            daily.day,
            [run.digest for run in daily.topics],
            repo_full_name=repo_full_name,
            base_branch=environ.get("BASE_BRANCH", "main"),
        )
        results = send_notifications(text, im_channels, environ)
        notify_note = "、".join(f"{row['channel']}:{row['status']}" for row in results)

    if not args.no_issue:
        publisher = IssuePublisher(environ.get("GH_TOKEN") or environ.get("GITHUB_TOKEN", ""), repo_full_name)
        body = render_issue_body(
            daily.day,
            [run.digest for run in daily.topics],
            repo_full_name=repo_full_name,
            base_branch=environ.get("BASE_BRANCH", "main"),
            notify_note=notify_note,
        )
        outcome = publisher.publish(daily.day, redact_text(body, environ), labels=["daily-search"])
        logger.info("Issue 发布结果：%s", outcome)
        if (outcome.get("action") == "skipped") and not args.json:
            print("提示：未配置 GITHUB_TOKEN，本次跳过 Issue 发布（报告与 feed 已生成）。")
    return 0


def _cmd_subscribe(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    body = Path(args.body_file).read_text(encoding="utf-8")
    topic, yaml_text = subscribe_from_issue_body(body, args.config)
    if args.write:
        Path(args.config).write_text(yaml_text, encoding="utf-8")
        print(f"已写入 {args.config}")
    else:
        print(yaml_text)
    if args.pr_body:
        Path(args.pr_body).write_text(pr_body(topic, args.issue_number), encoding="utf-8")
    return 0


def _cmd_comment(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    config = load_config(args.config)
    store = SnapshotStore(Path(args.root) / "data", config.defaults.snapshot_retention_days)
    agent = CommentAgent(config, store=store)
    response: CommentResponse = agent.handle(
        CommentRequest(
            body=Path(args.body_file).read_text(encoding="utf-8"),
            author=args.author,
            author_association=args.association,
            issue_number=int(args.issue_number or 0),
        )
    )
    print(redact_text(response.text, environ))
    return 0 if response.ok else 1


def _cmd_pages(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    root = Path(args.root)
    written = build_site(root / "reports", Path(args.site_dir), root / "feed.xml")
    for path in written:
        print(path)
    return 0


def _cmd_notify(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    source = Path(args.file)
    if not source.is_file():
        logger.error("报告不存在：%s", source)
        return 2
    text = source.read_text(encoding="utf-8")[: args.max_chars]
    channels = [item.strip() for item in args.channels.split(",") if item.strip()]
    results = send_notifications(redact_text(text, environ), channels, environ)
    for row in results:
        print(f"{row['channel']}: {row['status']}{(' (' + row['reason'] + ')') if row.get('reason') else ''}")
    return 0 if all(row["status"] != "failed" for row in results) else 1


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    env = dict(os.environ if environ is None else environ)
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    setup_logging(args.log_level, secrets=None)
    try:
        if args.command == "run":
            return _cmd_run(args, env)
        if args.command == "spike":
            result = probe(args.url)
            print(json.dumps(result.to_dict(), ensure_ascii=False) if args.json else render_markdown(result))
            return 0 if result.reachable else 1
        if args.command == "subscribe":
            return _cmd_subscribe(args, env)
        if args.command == "comment":
            return _cmd_comment(args, env)
        if args.command == "pages":
            return _cmd_pages(args, env)
        if args.command == "notify":
            return _cmd_notify(args, env)
    except (ConfigError, SubscribeError, ValueError) as exc:
        logger.error("执行失败：%s", exc)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())