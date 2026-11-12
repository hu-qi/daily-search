"""仓库配置校验：6 个 workflow、2 个 Issue 模板、无明文凭据。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WORKFLOW_DIR = Path(".github") / "workflows.pending"
TEMPLATE_DIR = Path(".github") / "ISSUE_TEMPLATE"
EXPECTED_WORKFLOWS = [
    "daily-search.yml",
    "subscribe-from-issue.yml",
    "issue-comment-agent.yml",
    "notify.yml",
    "pages.yml",
    "ci.yml",
]


def _triggers(data: dict) -> dict:
    # PyYAML 会把未加引号的 on: 解析成布尔 True
    triggers = data.get("on", data.get(True))
    assert isinstance(triggers, dict), "workflow 必须声明 on 触发条件"
    return triggers


@pytest.mark.parametrize("name", EXPECTED_WORKFLOWS)
def test_workflow_file_valid_and_manual(repo_root: Path, name: str) -> None:
    path = repo_root / WORKFLOW_DIR / name
    assert path.is_file(), f"缺少 workflow：{name}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("name"), f"{name} 缺少 name"
    triggers = _triggers(data)
    assert "workflow_dispatch" in triggers, f"{name} 缺少 workflow_dispatch"
    assert data.get("jobs"), f"{name} 缺少 jobs"
    assert data.get("permissions"), f"{name} 缺少最小权限声明"


def test_daily_search_schedule_is_0800_beijing(repo_root: Path) -> None:
    data = yaml.safe_load((repo_root / WORKFLOW_DIR / "daily-search.yml").read_text(encoding="utf-8"))
    schedule = _triggers(data)["schedule"]
    assert schedule[0]["cron"] == "0 0 * * *"  # UTC 00:00 = 北京时间 08:00


def test_no_workflow_passes_secrets_to_fork_prs(repo_root: Path) -> None:
    for name in EXPECTED_WORKFLOWS:
        text = (repo_root / WORKFLOW_DIR / name).read_text(encoding="utf-8")
        assert "pull_request_target" not in text, f"{name} 不应使用 pull_request_target"
        if "pull_request:" in text:
            assert "secrets." not in text.split("pull_request:")[1].split("jobs:")[0]


def test_issue_templates_exist_and_are_valid(repo_root: Path) -> None:
    subscribe = repo_root / TEMPLATE_DIR / "subscribe-topic.yml"
    feedback = repo_root / TEMPLATE_DIR / "report-false-positive.yml"
    for path in (subscribe, feedback):
        assert path.is_file(), f"缺少模板：{path.name}"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["name"] and data["description"]
        assert data["body"], "issue form 必须有 body"

    subscribe_data = yaml.safe_load(subscribe.read_text(encoding="utf-8"))
    labels = [field.get("attributes", {}).get("label") for field in subscribe_data["body"] if field.get("attributes")]
    for required in ("话题名称", "关键词", "发布渠道"):
        assert any(label and label.startswith(required) for label in labels), f"订阅模板缺少「{required}」段落"


def test_repo_contains_no_plaintext_credentials(repo_root: Path) -> None:
    patterns = (
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    )
    skip_dirs = {".git", "__pycache__", ".pytest_cache", "site", "node_modules", "tests"}
    offenders: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix not in {".py", ".yml", ".yaml", ".md", ".toml", ".json", ".sh"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in patterns):
            offenders.append(str(path.relative_to(repo_root)))
    assert offenders == [], f"发现疑似明文凭据：{offenders}"


def test_workflow_secret_references_are_expressions(repo_root: Path) -> None:
    for name in EXPECTED_WORKFLOWS:
        text = (repo_root / WORKFLOW_DIR / name).read_text(encoding="utf-8")
        for match in re.finditer(r"^\s*(LLM_API_KEY|LLM_BASE_URL|LLM_MODEL|GH_TOKEN|FEISHU_WEBHOOK_URL|WECOM_WEBHOOK_URL):\s*(.+)$", text, re.M):
            value = match.group(2).strip()
            assert value.startswith("${{"), f"{name} 中 {match.group(1)} 必须来自 secrets 表达式"