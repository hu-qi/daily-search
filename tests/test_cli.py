"""CLI 子命令：subscribe / notify / pages / spike。"""

from __future__ import annotations

from pathlib import Path

from daily_search.cli import main


def test_subscribe_cli_writes_config(repo_root: Path, tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "topics.yaml"
    config_path.write_bytes((repo_root / "topics.yaml").read_bytes())
    body_path = tmp_path / "body.md"
    body_path.write_text("### 话题名称\n低空经济政策\n### 关键词\n低空经济, eVTOL\n", encoding="utf-8")
    pr_path = tmp_path / "pr.md"

    code = main(
        [
            "--config",
            str(config_path),
            "subscribe",
            "--body-file",
            str(body_path),
            "--issue-number",
            "5",
            "--write",
            "--pr-body",
            str(pr_path),
        ],
        environ={},
    )
    assert code == 0
    text = config_path.read_text(encoding="utf-8")
    assert "低空经济政策" in text
    assert "低空经济政策" in pr_path.read_text(encoding="utf-8")
    assert "已写入" in capsys.readouterr().out


def test_subscribe_cli_invalid_body_returns_error(repo_root: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "topics.yaml"
    config_path.write_bytes((repo_root / "topics.yaml").read_bytes())
    body_path = tmp_path / "body.md"
    body_path.write_text("### 关键词\n只有关键词\n", encoding="utf-8")
    code = main(["--config", str(config_path), "subscribe", "--body-file", str(body_path)], environ={})
    assert code == 2


def test_notify_cli_missing_file_returns_error(tmp_path: Path) -> None:
    code = main(["notify", "--file", str(tmp_path / "nope.md")], environ={})
    assert code == 2


def test_notify_cli_skips_without_webhook(tmp_path: Path, capsys) -> None:
    report = tmp_path / "index.md"
    report.write_text("# 报告", encoding="utf-8")
    code = main(["notify", "--file", str(report), "--channels", "feishu"], environ={})
    assert code == 0
    assert "skipped" in capsys.readouterr().out


def test_pages_cli_builds_site(repo_root: Path, tmp_path: Path) -> None:
    code = main(["--root", str(repo_root), "pages", "--site-dir", str(tmp_path / "site")], environ={})
    assert code == 0
    assert (tmp_path / "site" / "index.html").is_file()


def test_spike_cli_reports_unreachable_without_network(monkeypatch, capsys) -> None:
    import daily_search.cli as cli_module
    from daily_search.spike import SpikeResult

    monkeypatch.setattr(
        cli_module,
        "probe",
        lambda url: SpikeResult(url=url, reachable=False, status_code=None, blocked=False, elapsed_ms=12, detail="ConnectionError"),
    )
    code = main(["spike", "--json"], environ={})
    assert code == 1
    assert "ConnectionError" in capsys.readouterr().out