#!/usr/bin/env bash
# 排程用的包裝腳本（macOS／Linux）。
#
#   scripts/run_scan.sh                 # 依間隔決定要不要跑（預設 3 天）
#   scripts/run_scan.sh --force         # 忽略間隔，立刻跑一次
#   LRC_INTERVAL_DAYS=1 scripts/run_scan.sh
#
# 設計成「可以每天叫，但只有滿 3 天才真的掃」。cron 的 */3 在月底會有
# 一天的縫（1,4,…,31 之後又是 1 號），改用戳記檔比較準。

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

INTERVAL_DAYS="${LRC_INTERVAL_DAYS:-3}"
STAMP="$REPO_DIR/data/.last_scan"
LOG_DIR="$REPO_DIR/logs"
KEEP_LOGS="${LRC_KEEP_LOGS:-40}"
FORCE=0
EXTRA_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
    *) EXTRA_ARGS+=("$arg") ;;
  esac
done

PYTHON="${LRC_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "找不到 python3，請先安裝或設定 LRC_PYTHON=/path/to/python3" >&2
  exit 1
fi

now_epoch=$(date +%s)
if [ "$FORCE" -eq 0 ] && [ -f "$STAMP" ]; then
  last_epoch=$(cat "$STAMP" 2>/dev/null || echo 0)
  elapsed_days=$(( (now_epoch - last_epoch) / 86400 ))
  if [ "$elapsed_days" -lt "$INTERVAL_DAYS" ]; then
    echo "距上次掃描 ${elapsed_days} 天（間隔設定 ${INTERVAL_DAYS} 天），本次跳過。"
    exit 0
  fi
fi

mkdir -p "$LOG_DIR" "$REPO_DIR/data"
LOG_FILE="$LOG_DIR/scan-$(date +%Y%m%d-%H%M%S).log"

echo "== 開始掃描 $(date '+%Y-%m-%d %H:%M:%S') ==" | tee "$LOG_FILE"
"$PYTHON" scan.py "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}" 2>&1 | tee -a "$LOG_FILE"
STATUS=${PIPESTATUS[0]}

echo "$now_epoch" > "$STAMP"

# 只保留最近幾份 log，免得排程跑一年塞滿目錄
ls -1t "$LOG_DIR"/scan-*.log 2>/dev/null | tail -n "+$((KEEP_LOGS + 1))" | while read -r old; do
  rm -f "$old"
done

REPORT="$REPO_DIR/reports/index.html"
case "$STATUS" in
  0)  echo "掃描完成，無觸發。" ;;
  10|30)
      echo "⚠ 有引信觸發，報表：$REPORT"
      if [ "${LRC_OPEN_ON_ALERT:-1}" = "1" ]; then
        if command -v open >/dev/null 2>&1; then open "$REPORT"
        elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$REPORT" >/dev/null 2>&1
        fi
      fi
      ;;
  20) echo "掃描完成，但有指標抓取失敗，詳見 $LOG_FILE" ;;
  *)  echo "掃描失敗（離開碼 $STATUS），詳見 $LOG_FILE" ;;
esac

exit "$STATUS"
