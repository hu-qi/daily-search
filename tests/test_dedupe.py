"""双层去重与快照：核心验收项「第二天只出现新增，重复率 < 10%」。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from daily_search.diff.dedupe import Deduplicator
from daily_search.diff.snapshot import SnapshotEntry, SnapshotStore
from daily_search.providers.base import SearchResult
from daily_search.providers.fixture import FixtureProvider


@pytest.fixture
def deduper(tmp_path: Path) -> Deduplicator:
    return Deduplicator(SnapshotStore(tmp_path / "data", retention_days=14), threshold=0.85)


def _results(*rows: tuple[str, str, str]) -> list[SearchResult]:
    return [SearchResult(title=t, url=u, snippet=s) for t, u, s in rows]


def test_two_day_fixture_repeat_rate_below_ten_percent(
    deduper: Deduplicator, fixture_dir: Path
) -> None:
    day1 = FixtureProvider.from_file(fixture_dir / "day1.json").search(
        _topic(), max_results=50
    )
    first = deduper.split("昇腾-cann", list(day1), "2026-11-10")
    assert first.stats.total == 6
    assert first.stats.accepted == 6
    deduper.commit("昇腾-cann", first, "2026-11-10")

    day2 = FixtureProvider.from_file(fixture_dir / "day2.json").search(
        _topic(), max_results=50
    )
    second = deduper.split("昇腾-cann", list(day2), "2026-11-11")

    assert second.stats.duplicates == 1  # 仅 utm 变体的旧条目
    assert second.stats.repeat_rate < 0.10
    assert all(item.url != "https://example.com/a2" for item in second.new)
    assert len(second.new) == 11


def test_semantic_duplicate_across_days_is_dropped(deduper: Deduplicator) -> None:
    first = deduper.split(
        "t", _results(("昇腾 CANN 8.0 发布：算子与图引擎升级", "https://a.com/1", "全面升级")), "2026-11-10"
    )
    deduper.commit("t", first, "2026-11-10")

    # 同一篇内容换了域名（URL 层抓不到），靠 3-gram Jaccard ≥ 0.85 拦下
    second = deduper.split(
        "t",
        _results(("昇腾 CANN 8.0 发布：算子与图引擎升级", "https://b.com/2", "全面升级")),
        "2026-11-11",
    )
    assert second.stats.accepted == 0
    assert second.stats.duplicates == 1


def test_duplicate_within_same_batch(deduper: Deduplicator) -> None:
    result = deduper.split(
        "t",
        _results(
            ("CANN 算子库新增稀疏矩阵乘样例", "https://a.com/1", "稀疏场景"),
            ("CANN 算子库新增稀疏矩阵乘样例", "https://a.com/1?utm_source=x", "稀疏场景"),
        ),
    )
    assert result.stats.total == 2
    assert result.stats.accepted == 1
    assert result.stats.repeat_rate == pytest.approx(0.5)


def test_semantic_duplicate_within_same_batch(deduper: Deduplicator) -> None:
    result = deduper.split(
        "t",
        _results(
            ("昇腾 CANN 8.0 发布：算子与图引擎升级", "https://a.com/1", "全面升级"),
            ("昇腾 CANN 8.0 发布：算子与图引擎升级", "https://b.com/2", "全面升级"),
        ),
    )
    assert result.stats.accepted == 1
    assert result.stats.duplicates == 1


def test_commit_writes_snapshot_and_prunes_old_entries(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    deduper = Deduplicator(store, threshold=0.85)
    old_day = (date(2026, 11, 11) - timedelta(days=30)).isoformat()
    store.save(
        "t",
        [SnapshotEntry(url="https://old.com", url_hash="h-old", norm_hash="n", title="旧", first_seen=old_day, last_seen=old_day)],
        "2026-11-11",
    )
    result = deduper.split("t", _results(("新条目", "https://new.com", "摘要")), "2026-11-11")
    entries = deduper.commit("t", result, "2026-11-11")
    urls = {entry.url for entry in entries}
    assert urls == {"https://new.com"}


def test_repeat_url_refreshes_last_seen_so_old_news_never_resurfaces(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    deduper = Deduplicator(store, threshold=0.85)
    entry = SnapshotEntry(
        url="https://example.com/old",
        url_hash="",
        norm_hash="n",
        title="昇腾 CANN 8.0 发布：算子与图引擎升级",
        snippet="全面升级",
        first_seen="2026-11-01",
        last_seen="2026-11-01",
    )
    store.save("t", [entry], "2026-11-01")

    same = _results(("昇腾 CANN 8.0 发布：算子与图引擎升级", "https://example.com/old", "全面升级"))
    for day in ("2026-11-05", "2026-11-10"):
        result = deduper.split("t", same, day)
        assert result.stats.accepted == 0
        deduper.commit("t", result, day)

    loaded = store.load("t", "2026-11-20")  # 14 天后裁剪边界：last_seen=11-10 仍应保留
    assert [item.url for item in loaded] == ["https://example.com/old"]
    assert loaded[0].first_seen == "2026-11-01"
    assert loaded[0].last_seen == "2026-11-10"


def test_semantic_repeat_refreshes_matched_entry_last_seen(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    deduper = Deduplicator(store, threshold=0.85)
    store.save(
        "t",
        [
            SnapshotEntry(
                url="https://a.com/1",
                url_hash="",
                norm_hash="n",
                title="昇腾 CANN 8.0 发布：算子与图引擎升级",
                snippet="全面升级",
                first_seen="2026-11-01",
                last_seen="2026-11-01",
            )
        ],
        "2026-11-01",
    )
    result = deduper.split(
        "t",
        _results(("昇腾 CANN 8.0 发布：算子与图引擎升级", "https://b.com/2", "全面升级")),
        "2026-11-10",
    )
    assert result.stats.accepted == 0
    deduper.commit("t", result, "2026-11-10")
    loaded = store.load("t", "2026-11-11")
    assert loaded[0].last_seen == "2026-11-10"


def test_snapshot_load_tolerates_corrupt_file(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    path = store.path_for("t")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert store.load("t", "2026-11-11") == []


def test_snapshot_missing_returns_empty(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    assert store.load("nothing", "2026-11-11") == []


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "data", retention_days=14)
    store.save(
        "t",
        [SnapshotEntry(url="https://a.com", url_hash="h", norm_hash="n", title="标题", first_seen="2026-11-11", last_seen="2026-11-11")],
        "2026-11-11",
    )
    loaded = store.load("t", "2026-11-11")
    assert loaded[0].url == "https://a.com"
    payload = json.loads(store.path_for("t").read_text(encoding="utf-8"))
    assert payload["version"] == 1


def test_repeat_rate_zero_when_no_candidates(deduper: Deduplicator) -> None:
    result = deduper.split("t", [], "2026-11-11")
    assert result.stats.total == 0
    assert result.stats.repeat_rate == 0.0


def _topic():
    from daily_search.config import TopicConfig

    return TopicConfig(topic="昇腾 CANN", keywords=["昇腾", "CANN"])