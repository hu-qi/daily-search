"""发布层：Markdown / RSS / Issue / IM / Pages / 原子写。"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from daily_search.agent.summarize import DigestPoint, TopicDigest
from daily_search.publish.atomic import atomic_write_text
from daily_search.publish.feed import build_item, existing_items, merge_items, render_feed
from daily_search.publish.im import render_im_text, send_notifications
from daily_search.publish.issue import IssuePublisher, issue_title, render_issue_body
from daily_search.publish.markdown import render_daily_index, render_topic_report, report_url
from daily_search.publish.pages import build_site


def _digest(degraded: bool = False, slug: str = "昇腾-cann") -> TopicDigest:
    return TopicDigest(
        topic="昇腾 CANN",
        slug=slug,
        date="2026-11-11",
        summary="今日新增两条：版本发布与算子示例。",
        points=[
            DigestPoint(title="CANN 8.0 发布", url="https://example.com/1", note="版本发布", confidence="high"),
            DigestPoint(title="算子示例", url="https://example.com/2", note="新增示例", confidence="medium"),
        ],
        degraded=degraded,
        degraded_reason="未配置 LLM" if degraded else "",
        provider="fixture",
        stats={"total": 2, "accepted": 2, "duplicates": 0, "repeat_rate": 0.0},
    )


def test_topic_report_contains_links_and_stats() -> None:
    text = render_topic_report(_digest(), "hu-qi/daily-search", "main", generated_at="2026-11-11T00:00:00+00:00")
    assert "# 昇腾 CANN · 2026-11-11" in text
    assert "degraded: false" in text
    assert "[CANN 8.0 发布](https://example.com/1)" in text
    assert "重复率 0.0%" in text
    assert "## 原始链接" in text


def test_topic_report_marks_degraded_and_reason() -> None:
    text = render_topic_report(_digest(degraded=True))
    assert "degraded: true" in text
    assert "降级原因：未配置 LLM" in text


def test_unreferenced_items_render_as_extra_links() -> None:
    digest = _digest()
    digest.unreferenced = [
        DigestPoint(title="未入摘要的第三条", url="https://example.com/3", note="（未纳入 LLM 摘要）", confidence="unknown")
    ]
    report = render_topic_report(digest, "hu-qi/daily-search", "main", generated_at="2026-11-11T00:00:00+00:00")
    assert "## 其他新增链接" in report
    assert "https://example.com/3" in report

    body = render_issue_body("2026-11-11", [digest], "hu-qi/daily-search", "main")
    assert "其他新增链接" in body
    assert "https://example.com/3" in body
    assert "今日新增：**3** 条" in body


def test_daily_index_links_absolute_report_urls() -> None:
    text = render_daily_index("2026-11-11", [_digest()], "hu-qi/daily-search", "main")
    expected = report_url("hu-qi/daily-search", "main", "2026-11-11", "昇腾-cann")
    assert expected == "https://github.com/hu-qi/daily-search/blob/main/reports/2026-11-11/昇腾-cann.md"
    assert expected in text
    assert "| 昇腾 CANN | 2 | 0.0% |" in text


def test_issue_title_format() -> None:
    assert issue_title("2026-11-11") == "🔍 每日一搜 2026-11-11"


def test_issue_body_groups_topics_with_links() -> None:
    body = render_issue_body("2026-11-11", [_digest(), _digest(slug="鸿蒙-ohpm")], "hu-qi/daily-search", "main")
    assert "## 🔍 每日一搜 2026-11-11" in body
    assert "https://example.com/1" in body
    assert "reports/2026-11-11/昇腾-cann.md" in body
    assert "/deep" in body


def test_feed_roundtrip_and_merge() -> None:
    moment = datetime(2026, 11, 11, tzinfo=timezone.utc)
    item = build_item("昇腾 CANN · 2026-11-11", "https://example.com/r1", "摘要", "2026-11-11", "昇腾-cann", moment)
    xml_text = render_feed([item], generated_at=moment)
    parsed = existing_items(xml_text)
    assert parsed[0].guid == item.guid
    assert parsed[0].link == "https://example.com/r1"

    newer = build_item("昇腾 CANN · 2026-11-12", "https://example.com/r2", "摘要2", "2026-11-12", "昇腾-cann", moment.replace(day=12))
    merged = merge_items(parsed, [newer])
    assert [row.guid for row in merged] == [newer.guid, item.guid]


def test_feed_merge_keeps_single_entry_per_guid() -> None:
    moment = datetime(2026, 11, 11, tzinfo=timezone.utc)
    first = build_item("t", "https://example.com/1", "d", "2026-11-11", "s", moment)
    replaced = build_item("t2", "https://example.com/2", "d2", "2026-11-11", "s", moment)
    merged = merge_items([first], [replaced])
    assert len(merged) == 1
    assert merged[0].link == "https://example.com/2"


def test_feed_is_valid_xml() -> None:
    moment = datetime(2026, 11, 11, tzinfo=timezone.utc)
    xml_text = render_feed(
        [build_item("标题 <&>", "https://example.com/1?a=1&b=2", "描述 & 内容", "2026-11-11", "s", moment)],
        generated_at=moment,
    )
    root = ET.fromstring(xml_text)
    assert root.tag == "rss"
    assert root.findtext("./channel/item/title") == "标题 <&>"


def test_issue_publisher_skipped_without_token() -> None:
    outcome = IssuePublisher("", "hu-qi/daily-search").publish("2026-11-11", "body")
    assert outcome["action"] == "skipped"


def test_issue_publisher_creates_when_absent(fake_session) -> None:
    session = fake_session(
        [
            type("R", (), {"status_code": 200, "json": lambda self: [], "raise_for_status": lambda self: None})(),
            type("R", (), {"status_code": 201, "json": lambda self: {"number": 7}, "raise_for_status": lambda self: None})(),
        ]
    )
    publisher = IssuePublisher("ghs_test_token", "hu-qi/daily-search", session=session)
    outcome = publisher.publish("2026-11-11", "body", labels=["daily-search"])
    assert outcome == {"action": "created", "number": 7}
    assert session.calls[1][2]["json"]["labels"] == ["daily-search"]


def test_issue_publisher_updates_existing(fake_session) -> None:
    session = fake_session(
        [
            type("R", (), {"status_code": 200, "json": lambda self: [{"number": 9, "title": issue_title("2026-11-11")}], "raise_for_status": lambda self: None})(),
            type("R", (), {"status_code": 200, "json": lambda self: {"number": 9}, "raise_for_status": lambda self: None})(),
        ]
    )
    publisher = IssuePublisher("ghs_test_token", "hu-qi/daily-search", session=session)
    assert publisher.publish("2026-11-11", "body") == {"action": "updated", "number": 9}
    assert session.calls[1][0] == "PATCH"


def test_issue_publisher_retries_without_labels(fake_session) -> None:
    def raise_422(self=None) -> None:
        raise RuntimeError("HTTP 422")

    def post(method, url, kwargs):
        if "labels" in kwargs.get("json", {}):
            return type("R", (), {"status_code": 422, "json": lambda self: {}, "raise_for_status": raise_422})()
        return type("R", (), {"status_code": 201, "json": lambda self: {"number": 8}, "raise_for_status": lambda self: None})()

    session = fake_session(
        [
            type("R", (), {"status_code": 200, "json": lambda self: [], "raise_for_status": lambda self: None})(),
            post,
            post,
        ]
    )
    publisher = IssuePublisher("ghs_test_token", "hu-qi/daily-search", session=session)
    assert publisher.publish("2026-11-11", "body", labels=["daily-search"]) == {"action": "created", "number": 8}
    assert "labels" in session.calls[1][2]["json"]
    assert "labels" not in session.calls[2][2]["json"]


def test_im_text_and_skip_without_webhook(fake_session) -> None:
    text = render_im_text("2026-11-11", [_digest()], "hu-qi/daily-search", "main")
    assert "昇腾 CANN" in text
    assert "https://example.com/1" in text

    results = send_notifications(text, ["feishu", "wecom"], {}, session=fake_session([]))
    assert [row["status"] for row in results] == ["skipped", "skipped"]


def test_im_sent_and_failure_isolated(fake_session) -> None:
    session = fake_session(
        [
            type("R", (), {"status_code": 200, "json": lambda self: {}})(),
            RuntimeError("boom"),
        ]
    )
    results = send_notifications(
        "hi",
        ["feishu", "wecom"],
        {"FEISHU_WEBHOOK_URL": "https://example.com/hook1", "WECOM_WEBHOOK_URL": "https://example.com/hook2"},
        session=session,
    )
    assert results[0]["status"] == "sent"
    assert results[1]["status"] == "failed"


def test_im_rejects_business_error_code_even_with_http_200(fake_session) -> None:
    session = fake_session(
        [
            type("R", (), {"status_code": 200, "json": lambda self: {"code": 9499, "msg": "Bad Request"}})(),
            type("R", (), {"status_code": 200, "json": lambda self: {"errcode": 40001, "errmsg": "invalid"}})(),
        ]
    )
    results = send_notifications(
        "hi",
        ["feishu", "wecom"],
        {"FEISHU_WEBHOOK_URL": "https://example.com/hook1", "WECOM_WEBHOOK_URL": "https://example.com/hook2"},
        session=session,
    )
    assert [row["status"] for row in results] == ["failed", "failed"]
    assert "9499" in results[0]["reason"]
    assert "40001" in results[1]["reason"]


def test_pages_build_copies_reports_and_feed(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "2026-11-11"
    reports.mkdir(parents=True)
    (reports / "昇腾-cann.md").write_text("# x", encoding="utf-8")
    feed = tmp_path / "feed.xml"
    feed.write_text("<rss/>", encoding="utf-8")

    written = build_site(tmp_path / "reports", tmp_path / "site", feed)
    site = tmp_path / "site"
    assert (site / "reports" / "2026-11-11" / "昇腾-cann.md").is_file()
    assert (site / "feed.xml").is_file()
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "昇腾-cann" in index
    assert len(written) == 3


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out.md"
    atomic_write_text(target, "内容")
    assert target.read_text(encoding="utf-8") == "内容"
    assert list(target.parent.glob("*.tmp")) == []