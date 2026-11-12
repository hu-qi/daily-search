#!/usr/bin/env bash
# 每日一搜便捷脚本：spike → dry-run → 落盘（凭据只从环境变量读取，不落文件）
set -euo pipefail

TOPIC=""
DATE="$(date -u +%F)"
ROOT="."
DRY_ONLY="false"

usage() {
  cat <<'EOF'
用法：run_daily_search.sh [--topic NAME] [--date YYYY-MM-DD] [--root DIR] [--dry-only]

  --topic     只跑一个话题（slug 或话题名）
  --date      运行日期，默认今天（UTC）
  --root      仓库根目录，默认当前目录
  --dry-only  只做探测与干跑，不写 reports/data/feed
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --topic) TOPIC="${2:-}"; shift 2 ;;
    --date) DATE="${2:-}"; shift 2 ;;
    --root) ROOT="${2:-}"; shift 2 ;;
    --dry-only) DRY_ONLY="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 2 ;;
  esac
done

cd "$ROOT"

echo "==> 探测 so.qq.com 可达性"
python -m daily_search spike --json || echo "探测失败：将依赖 fallback_rss 兜底"

ARGS=(run --date "$DATE" --no-issue --dry-run)
if [[ -n "$TOPIC" ]]; then ARGS+=(--topic "$TOPIC"); fi
echo "==> 干跑（只读）"
python -m daily_search "${ARGS[@]}"

if [[ "$DRY_ONLY" == "true" ]]; then
  echo "==> --dry-only：跳过落盘"
  exit 0
fi

echo "==> 落盘（reports/ data/ feed.xml）"
ARGS=(run --date "$DATE" --no-issue)
if [[ -n "$TOPIC" ]]; then ARGS+=(--topic "$TOPIC"); fi
python -m daily_search "${ARGS[@]}"

echo "==> 完成：reports/${DATE}/"