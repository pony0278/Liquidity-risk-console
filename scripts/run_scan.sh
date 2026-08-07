#!/usr/bin/env bash
# 排程用的包裝腳本（macOS／Linux）。
#
#   scripts/run_scan.sh                 # 依間隔決定要不要跑（預設每天）
#   scripts/run_scan.sh --force         # 忽略間隔，立刻跑一次
#   LRC_INTERVAL_DAYS=7 scripts/run_scan.sh
#
# 間隔是參數而不是 cron 表達式：cron 的 */3 在月底會有一天的縫
# （1,4,…,31 之後又是 1 號），而 GitHub 的排程本身也會延遲數十分鐘，
# 用戳記檔比較準。
#
# 為什麼預設是 1 天：23 個指標裡 18 個是每日收盤，而規則表裡的引信
# （USD/JPY 單日 −2%、10Y／30Y 單日 +10bps）判定的就是「單日」變動。
# 間隔拉長到 N 天，等於每 N 個交易日只看 1 個收盤，落在跳過那幾天的
# 尖刺永遠不會被評估到。慢燈（TGA、準備金、失業金）一週才動一次，
# 每天掃它只是每天確認「還沒發佈」，沒有代價。
#
# 想改成週報式的節奏就設 LRC_INTERVAL_DAYS=7（配合週四跑，週三的
# H.4.1 與週四的失業金都出來了）。

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 1

INTERVAL_DAYS="${LRC_INTERVAL_DAYS:-1}"
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
  # 用「差幾個 UTC 日」而不是「差幾個 86400 秒」。GitHub 的排程會延遲數十
  # 分鐘且每次不同，昨天 22:50 跑、今天 22:05 跑的話，秒數差只有 23 小時
  # 15 分，整除下來是 0 天——間隔設 1 時會被判成「還沒到」而整天不掃。
  # 抖動造成的漏掃比偶爾多掃一次糟得多。
  elapsed_days=$(( now_epoch / 86400 - last_epoch / 86400 ))
  if [ "$elapsed_days" -lt "$INTERVAL_DAYS" ]; then
    echo "距上次掃描 ${elapsed_days} 天（間隔設定 ${INTERVAL_DAYS} 天），本次跳過。"
    # 5＝「這次沒有掃」，跟 scan.py 自己的 0／10／20／30 分得開。跳過時
    # reports/latest.json 還是上一次的內容，呼叫端若把它當成本次結果，
    # 會把昨天的引信重新當成新的（例如又開一張 issue）。
    exit 5
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
  5)  echo "掃描本身回報 5——這個碼被包裝腳本用來表示『跳過』，請改掉。" ;;
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
