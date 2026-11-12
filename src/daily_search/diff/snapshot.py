"""历史快照：`data/<slug>/latest.json`，保留 N 天，用于「只推新增」。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("daily_search.diff.snapshot")

SNAPSHOT_VERSION = 1


@dataclass
class SnapshotEntry:
    url: str
    url_hash: str
    norm_hash: str
    title: str
    snippet: str = ""
    source: str = ""
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "SnapshotEntry | None":
        url = str(raw.get("url") or "")
        if not url:
            return None
        return cls(
            url=url,
            url_hash=str(raw.get("url_hash") or ""),
            norm_hash=str(raw.get("norm_hash") or ""),
            title=str(raw.get("title") or ""),
            snippet=str(raw.get("snippet") or ""),
            source=str(raw.get("source") or ""),
            first_seen=str(raw.get("first_seen") or ""),
            last_seen=str(raw.get("last_seen") or ""),
        )


@dataclass
class Snapshot:
    slug: str
    updated_at: str = ""
    entries: list[SnapshotEntry] = field(default_factory=list)


def _as_date(value: date | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


class SnapshotStore:
    """读写快照；损坏文件按「无历史」处理并告警，不阻断当天产出。"""

    def __init__(self, root: Path | str, retention_days: int = 14) -> None:
        self.root = Path(root)
        self.retention_days = retention_days

    def path_for(self, slug: str) -> Path:
        return self.root / slug / "latest.json"

    def load(self, slug: str, today: date | str | None = None) -> list[SnapshotEntry]:
        path = self.path_for(slug)
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("快照读取失败，按无历史处理：%s (%s)", path, exc.__class__.__name__)
            return []
        rows = raw.get("entries", raw) if isinstance(raw, dict) else raw
        entries: list[SnapshotEntry] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            entry = SnapshotEntry.from_dict(row)
            if entry is not None:
                entries.append(entry)
        return self.prune(entries, today)

    def prune(self, entries: list[SnapshotEntry], today: date | str | None = None) -> list[SnapshotEntry]:
        cutoff = _as_date(today) - timedelta(days=self.retention_days)
        kept: list[SnapshotEntry] = []
        for entry in entries:
            stamp = entry.last_seen or entry.first_seen
            if not stamp:
                kept.append(entry)
                continue
            try:
                seen_on = date.fromisoformat(stamp)
            except ValueError:
                kept.append(entry)
                continue
            if seen_on >= cutoff:
                kept.append(entry)
        return kept

    def save(self, slug: str, entries: list[SnapshotEntry], today: date | str | None = None) -> Path:
        from ..publish.atomic import atomic_write_text  # 延迟导入避免循环依赖

        path = self.path_for(slug)
        payload = {
            "version": SNAPSHOT_VERSION,
            "slug": slug,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "entries": [entry.to_dict() for entry in self.prune(entries, today)],
        }
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return path