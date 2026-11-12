"""端到端：runner 与 CLI 的落盘语义（dry-run 必须只读）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_search.agent.summarize import Summarizer
from daily_search.cli import main
from daily_search.config import load_config
from daily_search.diff.dedupe import Deduplicator
from daily_search.diff.snapshot import SnapshotStore
from daily_search.providers.base import ProviderError
from daily_search.providers.registry import ProviderRegistry
from daily_search.runner import DailyRunner


def _runner(repo_root: Path, tmp_path: Path, fixture: Path, order=None) -> DailyRunner:
    config = load_config(repo_root / "topics.yaml")
    return DailyRunner.from_config(
        config,
        root=tmp_path,
        summarizer=Summarizer(None),
        repo_full_name="hu-qi/daily-search",
        base_branch="main",
        fixture_path=fixture,
        order=order,
    )


def test_run_writes_reports_snapshots_and_feed(repo_root: Path, tmp_path: Path) -> None:
    runner = _runner(repo_root, tmp_path, repo_root / "fixtures" / "search-2026-11-11.json")
    daily = runner.run(day="2026-11-11")

    assert daily.day == "2026-11-11"
    assert daily.total_new == 7
    assert (tmp_path / "reports" / "2026-11-11" / "昇腾-cann.md").is_file()
    assert (tmp_path / "reports" / "2026-11-11" / "index.md").is_file()
    assert (tmp_path / "data" / "昇腾-cann" / "latest.json").is_file()
    assert (tmp_path / "feed.xml").is_file()
    assert daily.feed_path is not None


def test_dry_run_writes_nothing(repo_root: Path, tmp_path: Path) -> None:
    runner = _runner(repo_root, tmp_path, repo_root / "fixtures" / "search-2026-11-11.json")
    daily = runner.run(day="2026-11-11", dry_run=True)
    assert daily.total_new == 7
    assert list(tmp_path.rglob("*")) == []


def test_second_run_same_day_has_no_new_items(repo_root: Path, tmp_path: Path) -> None:
    fixture = repo_root / "fixtures" / "search-2026-11-11.json"
    runner = _runner(repo_root, tmp_path, fixture)
    runner.run(day="2026-11-11")

    runner2 = _runner(repo_root, tmp_path, fixture)
    daily = runner2.run(day="2026-11-11")
    assert daily.total_new == 0
    for run in daily.topics:
        assert run.digest.stats["repeat_rate"] == pytest.approx(1.0)


def test_single_topic_run_does_not_overwrite_daily_index(repo_root: Path, tmp_path: Path) -> None:
    fixture = repo_root / "fixtures" / "search-2026-11-11.json"
    _runner(repo_root, tmp_path, fixture).run(day="2026-11-11")
    index_path = tmp_path / "reports" / "2026-11-11" / "index.md"
    before = index_path.read_text(encoding="utf-8")
    assert "鸿蒙 OHPM" in before

    _runner(repo_root, tmp_path, fixture).run(day="2026-11-11", topic_filter="昇腾-cann")
    assert index_path.read_text(encoding="utf-8") == before


def test_all_providers_down_still_writes_degraded_report(repo_root: Path, tmp_path: Path) -> None:
    class _Broken:
        name = "broken"

        def search(self, topic, max_results=20):
            raise ProviderError("抓取失败")

    config = load_config(repo_root / "topics.yaml")
    runner = DailyRunner(
        config,
        ProviderRegistry({"broken": _Broken()}, order=["broken"]),
        Deduplicator(SnapshotStore(tmp_path / "data")),
        Summarizer(None),
        tmp_path,
        repo_full_name="hu-qi/daily-search",
    )
    daily = runner.run(day="2026-11-11", topic_filter="昇腾-cann")
    assert daily.total_new == 0
    assert daily.topics[0].digest.degraded is True
    report = tmp_path / "reports" / "2026-11-11" / "昇腾-cann.md"
    assert report.is_file()
    assert "degraded: true" in report.read_text(encoding="utf-8")


def test_run_unknown_topic_raises(repo_root: Path, tmp_path: Path) -> None:
    runner = _runner(repo_root, tmp_path, repo_root / "fixtures" / "search-2026-11-11.json")
    with pytest.raises(ValueError, match="找不到话题"):
        runner.run(day="2026-11-11", topic_filter="不存在")


def test_cli_dry_run_prints_and_writes_nothing(repo_root: Path, tmp_path: Path, capsys) -> None:
    code = main(
        [
            "--config",
            str(repo_root / "topics.yaml"),
            "--root",
            str(tmp_path),
            "run",
            "--date",
            "2026-11-11",
            "--dry-run",
            "--no-issue",
            "--fixture",
            str(repo_root / "fixtures" / "search-2026-11-11.json"),
        ],
        environ={},
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "昇腾 CANN · 2026-11-11" in out
    assert "degraded: true" in out
    assert list(tmp_path.rglob("*")) == []


def test_cli_invalid_date_returns_error(repo_root: Path, tmp_path: Path) -> None:
    code = main(
        [
            "--config",
            str(repo_root / "topics.yaml"),
            "--root",
            str(tmp_path),
            "run",
            "--date",
            "2026/11/11",
            "--dry-run",
            "--no-issue",
        ],
        environ={},
    )
    assert code == 2