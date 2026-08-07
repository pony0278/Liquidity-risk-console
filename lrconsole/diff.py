"""兩次掃描之間的變化摘要。

「這三天發生了什麼」比「現在的水位是多少」更難從表格看出來，所以每次
掃描都會針對上一份 snapshot 產生一段變更清單，並依重要性排序：
階梯 → 引信 → 傳導鏈節點 → 燈號 → 數值大幅變動。
"""

from .evaluate import STATUS_ORDER

__all__ = ["diff_snapshots", "tripwire_delta", "render_markdown_summary"]

_MOVE_THRESHOLD = {
    "hy_oas": 15, "ig_oas": 8, "ccc_oas": 40, "hy_ig": 15,
    "sofr_iorb": 3, "vix": 2.5, "vvix": 8, "move": 8,
    "y30": 0.08, "y10": 0.08, "y2": 0.08, "slope_2s30s": 10,
    "usdjpy": 1.5, "dxy": 0.8, "wti": 4, "claims": 10,
    "term_premium": 0.1, "rrp": 50, "reserves": 100, "tga": 50,
    "net_liquidity": 100, "srf": 5,
}


def _index(snapshot, field, key="key"):
    return {item[key]: item for item in snapshot.get(field, [])}


def tripwire_delta(current, previous):
    """引信集合的變化：{"new": [...], "cleared": [...], "ongoing": [...]}，值是代號。

    「還亮著」跟「剛亮起來」是兩件事。改成每天掃之後，一根連續亮 30 天的
    引信會生出 30 則通知與 30 張 issue，講的卻是同一件事——所以通知與開
    issue 都該看「集合有沒有變」，而不是「集合空不空」。

    沒有上一份快照時，亮著的一律算新的：第一次掃描就靜音比重複吵更糟。
    """
    codes = {w["id"]: w.get("code", w["id"]) for w in current.get("tripwires", [])}
    order = [w["id"] for w in current.get("tripwires", [])]
    # 引信被改名或從設定裡拿掉時，它仍可能出現在上一份快照裡（於是算「解除」）。
    for wire in (previous or {}).get("tripwires", []):
        if wire["id"] not in codes:
            codes[wire["id"]] = wire.get("code", wire["id"])
            order.append(wire["id"])

    now = {w["id"] for w in current.get("tripwires", []) if w["state"] is True}
    before = ({w["id"] for w in previous.get("tripwires", []) if w.get("state") is True}
              if previous else set())

    def names(ids):
        return [codes[i] for i in order if i in ids]

    return {"new": names(now - before), "cleared": names(before - now),
            "ongoing": names(now & before)}


def diff_snapshots(current, previous):
    """回傳 [(severity, text), ...]，severity 為 alert／warn／info。"""
    if not previous:
        return [("info", "沒有上一次的掃描記錄，這是第一份基準。")]

    changes = []

    if current["level"] != previous.get("level"):
        direction = "升級" if current["level"] > previous.get("level", 0) else "降級"
        changes.append((
            "alert" if current["level"] > previous.get("level", 0) else "info",
            "升級階梯%s：第 %s 階 → 第 %s 階（%s）" % (
                direction, previous.get("level"), current["level"],
                current["verdict"]["headline"]),
        ))

    prev_wires = _index(previous, "tripwires", "id")
    for wire in current.get("tripwires", []):
        before = prev_wires.get(wire["id"], {}).get("state")
        after = wire["state"]
        if before == after:
            continue
        if after is True:
            changes.append(("alert", "引信觸發：%s" % wire["code"]))
        elif before is True and after is False:
            changes.append(("info", "引信解除：%s" % wire["code"]))
        elif after is None:
            changes.append(("warn", "引信無法判定（資料缺）：%s" % wire["code"]))

    prev_chains = _index(previous, "chains", "id")
    for chain in current.get("chains", []):
        before = prev_chains.get(chain["id"])
        if not before:
            continue
        if chain["live"] != before.get("live"):
            direction = "推進" if chain["live"] > before.get("live", 0) else "回退"
            newly = [n["label"] for n, o in zip(chain["nodes"], before.get("nodes", []))
                     if n["state"] == "live" and o.get("state") != "live"]
            detail = "（新觸發：%s）" % "、".join(newly) if newly else ""
            changes.append((
                "alert" if chain["live"] > before.get("live", 0) else "info",
                "%s %s：%d/%d → %d/%d 節點%s" % (
                    chain["title"], direction, before.get("live", 0), before.get("total", 0),
                    chain["live"], chain["total"], detail),
            ))

    prev_indicators = _index(previous, "indicators")
    for indicator in current.get("indicators", []):
        if indicator["hidden"]:
            continue
        before = prev_indicators.get(indicator["key"])
        if not before:
            continue
        if indicator["status"] != before.get("status"):
            worse = STATUS_ORDER.get(indicator["status"], -1) > STATUS_ORDER.get(before.get("status"), -1)
            changes.append((
                "alert" if worse else "info",
                "%s 燈號 %s → %s（%s → %s）" % (
                    indicator["label"], before.get("status_label"), indicator["status_label"],
                    before.get("display"), indicator["display"]),
            ))
            continue
        if indicator["value"] is None or before.get("value") is None:
            if indicator["value"] is None and before.get("value") is not None:
                changes.append(("warn", "%s 本次未取得資料（上次 %s）"
                                % (indicator["label"], before.get("display"))))
            continue
        move = indicator["value"] - before["value"]
        threshold = _MOVE_THRESHOLD.get(indicator["key"])
        if threshold and abs(move) >= threshold:
            changes.append(("warn", "%s %s → %s（%+.*f）" % (
                indicator["label"], before.get("display"), indicator["display"],
                indicator["decimals"], move)))

    for indicator in current.get("indicators", []):
        if not indicator["fetch_ok"] and not indicator["hidden"]:
            changes.append(("warn", "%s 抓取失敗：%s"
                            % (indicator["label"], indicator["fetch_detail"] or "未知原因")))

    if not changes:
        changes.append(("info", "與上次掃描相比沒有實質變化。"))

    order = {"alert": 0, "warn": 1, "info": 2}
    changes.sort(key=lambda c: order.get(c[0], 3))
    return changes


def render_markdown_summary(snapshot, changes):
    icon = {"alert": "🔴", "warn": "🟡", "info": "·"}
    lines = [
        "# 流動性與尾部風險掃描 — %s" % snapshot["scan_time"][:10],
        "",
        "**判定**：第 %d 階 · %s" % (snapshot["level"], snapshot["verdict"]["headline"]),
        "",
        "**資料截至**：%s ／ **掃描時間**：%s" % (
            snapshot.get("data_as_of") or "—", snapshot["scan_time"]),
        "",
        "## 與上次掃描的差異",
        "",
    ]
    lines += ["- %s %s" % (icon.get(sev, "·"), text) for sev, text in changes]

    lines += ["", "## 層級燈號", "", "| 層 | 燈號 | 讀數 |", "| --- | --- | --- |"]
    for tier in sorted(snapshot["tiers"]):
        info = snapshot["tiers"][tier]
        lines.append("| TIER %d %s | %s | %s |" % (tier, info["short"], info["label"], info["readout"]))

    fired = [w for w in snapshot["tripwires"] if w["state"] is True]
    lines += ["", "## 觸發中的引信", ""]
    lines += ["- 🔴 %s — %s" % (w["code"], w["desc"]) for w in fired] or ["- 無"]

    lines += ["", "## 傳導鏈進度", ""]
    for chain in snapshot["chains"]:
        lines.append("- **%s** — %s，%d/%d 節點" % (
            chain["title"], chain["state"], chain["live"], chain["total"]))

    lines += ["", "## 指標盤", "", "| 指標 | 最新 | 單日 | 燈號 | 資料日 |", "| --- | --- | --- | --- | --- |"]
    for indicator in snapshot["indicators"]:
        if indicator["hidden"]:
            continue
        lines.append("| %s | %s | %s | %s | %s |" % (
            indicator["label"], indicator["display"], indicator["change_display"] or "—",
            indicator["status_label"], indicator["date"] or "—"))

    lines += ["", "---", "", "_本表為個人監測用的指標整理，不構成投資建議。_", ""]
    return "\n".join(lines)
