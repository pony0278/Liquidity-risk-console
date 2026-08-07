#!/usr/bin/env bash
# 安裝 cron 排程（macOS／Linux）。
#
#   scripts/install_cron.sh            # 每天 08:10 叫一次
#   scripts/install_cron.sh --remove   # 移除
#
# 間隔由 run_scan.sh 的戳記檔（data/.last_scan）決定，預設每天一次；
# 想拉長就設 LRC_INTERVAL_DAYS。把間隔放在戳記檔而不是 cron 的 */N，
# 是因為 */3 這種寫法是「每月第 1,4,7…31 天」，月底到月初會出現一天的
# 縫，而且電腦關機錯過就整個跳過（這裡下一次叫到就會補上）。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$REPO_DIR/scripts/run_scan.sh"
MARKER="# liquidity-risk-console"
HOUR="${LRC_CRON_HOUR:-8}"
MINUTE="${LRC_CRON_MINUTE:-10}"
LINE="$MINUTE $HOUR * * * cd $REPO_DIR && $RUNNER >> $REPO_DIR/logs/cron.log 2>&1 $MARKER"

current="$(crontab -l 2>/dev/null || true)"
cleaned="$(printf '%s\n' "$current" | grep -v "$MARKER" || true)"

if [ "${1:-}" = "--remove" ]; then
  printf '%s\n' "$cleaned" | grep -v '^$' | crontab -
  echo "已移除排程。"
  exit 0
fi

chmod +x "$RUNNER"
mkdir -p "$REPO_DIR/logs"
{ printf '%s\n' "$cleaned" | grep -v '^$' || true; printf '%s\n' "$LINE"; } | crontab -

echo "已安裝排程："
echo "  $LINE"
echo
echo "確認：crontab -l"
echo "手動跑一次：$RUNNER --force"
echo
echo "注意 macOS：cron 需要「完全磁碟取用權限」才能寫入某些目錄，"
echo "若排程沒動靜，改用 launchd（scripts/com.lrc.scan.plist）。"
