"""可選的推播：Webhook（Slack／Discord 相容）。

沒有設定 LRC_WEBHOOK_URL 就整段跳過，掃描本身不受影響。
"""

import json
import os
import urllib.request

__all__ = ["send_webhook", "build_message"]


def build_message(snapshot, changes, max_lines=12):
    icon = {"alert": "🔴", "warn": "🟡", "info": "·"}
    fired = [w for w in snapshot["tripwires"] if w["state"] is True]
    head = "【流動性掃描 %s】第 %d 階 · %s" % (
        snapshot["scan_time"][:10], snapshot["level"], snapshot["verdict"]["headline"])
    lines = [head, ""]
    if fired:
        lines.append("觸發中：" + "、".join(w["code"] for w in fired))
        lines.append("")
    lines += ["%s %s" % (icon.get(sev, "·"), text) for sev, text in changes[:max_lines]]
    if len(changes) > max_lines:
        lines.append("…另有 %d 項變化，詳見報表。" % (len(changes) - max_lines))
    return "\n".join(lines)


def send_webhook(text, url=None, timeout=15):
    url = url or os.environ.get("LRC_WEBHOOK_URL", "").strip()
    if not url:
        return False, "未設定 LRC_WEBHOOK_URL，略過推播"
    payload = json.dumps({"text": text, "content": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "liquidity-risk-console/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, "推播完成（HTTP %s）" % response.status
    except Exception as exc:  # noqa: BLE001
        return False, "推播失敗：%s" % exc
