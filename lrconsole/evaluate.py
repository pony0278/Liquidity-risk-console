"""把序列 + 規則變成一份完整判定（snapshot）。

輸出是一個純 dict，render 與 diff 都只吃這個 dict，所以任何時候都可以把
reports/snapshot-*.json 拿出來重畫或比對，不必重抓資料。
"""

from .expr import evaluate, evaluate_value, referenced_names
from .series import Series, build_metrics

__all__ = ["build_snapshot", "STATUS_ORDER", "status_worse"]

STATUS_ORDER = {"unknown": -1, "info": -1, "ok": 0, "watch": 1, "press": 2, "alarm": 3}
STATUS_TEXT = {"ok": "正常", "watch": "警示", "press": "明確壓力", "alarm": "警報",
               "unknown": "無資料", "info": "僅記錄"}


def status_worse(a, b):
    return a if STATUS_ORDER.get(a, -1) >= STATUS_ORDER.get(b, -1) else b


def _fmt(value, decimals, unit=""):
    if value is None:
        return "—"
    text = "%.*f" % (decimals, value)
    if unit == "%":
        return text + "%"
    if unit == "bps":
        return text + " bps"
    if unit == "$B":
        return "$%sB" % text
    if unit == "$":
        return "$" + text
    if unit == "K":
        return text + "K"
    return text


def _fmt_signed(value, decimals, unit=""):
    if value is None:
        return ""
    text = "%+.*f" % (decimals, value)
    if unit == "%":
        return text + "pp"
    if unit == "bps":
        return text + "bps"
    return text


def _match_bands(bands, value):
    if value is None:
        return None, None
    for band in bands:
        low, high = band.get("min"), band.get("max")
        if low is not None and value < low:
            continue
        if high is not None and value >= high:
            continue
        return band.get("status", "ok"), band.get("label")
    return None, None


# ---------------------------------------------------------------- 序列組裝

def resolve_series(indicator_cfg, fetched, history, record_failures=True):
    """把抓到的資料與歷史併起來，並算出 derived 序列。

    回傳 (series_map, notes)。derived 以「各成分序列日期交集」逐日計算，
    因此衍生指標（SOFR−IORB、2s30s…）同樣有完整歷史與單日變動。
    """
    series_map = {}
    notes = {}

    for indicator in indicator_cfg:
        key = indicator["key"]
        merged = history.get(key, Series())
        result = fetched.get(key)
        if result is not None and result.ok:
            merged = merged.merged_with(result.series)
            notes[key] = {"ok": True, "provider": result.provider, "detail": result.detail}
        elif indicator.get("sources") and record_failures:
            notes[key] = {
                "ok": False,
                "provider": result.provider if result else None,
                "detail": (result.detail if result else "未嘗試"),
            }
        if merged:
            series_map[key] = merged

    # derived 需要成分先就位，可能有多層相依，因此重複掃到收斂為止。
    pending = [i for i in indicator_cfg if i.get("derived") or i.get("derived_diff")]
    for _ in range(len(pending) + 1):
        progressed = False
        for indicator in list(pending):
            key = indicator["key"]
            if indicator.get("derived_diff"):
                source = series_map.get(indicator["derived_diff"])
                if not source:
                    continue
                points = [
                    (source.points[i][0], source.points[i][1] - source.points[i - 1][1])
                    for i in range(1, len(source.points))
                ]
                series_map[key] = Series(points)
                pending.remove(indicator)
                progressed = True
                continue

            expr = indicator["derived"]
            names = referenced_names(expr)
            if not names <= set(series_map):
                continue
            dicts = {n: series_map[n].as_dict() for n in names}
            common = set.intersection(*[set(d) for d in dicts.values()]) if dicts else set()
            points = []
            for date in sorted(common):
                value = evaluate_value(expr, {n: dicts[n][date] for n in names})
                if value is not None:
                    points.append((date, float(value)))
            if points:
                series_map[key] = Series(points)
            pending.remove(indicator)
            progressed = True
        if not pending or not progressed:
            break

    for indicator in pending:
        notes[indicator["key"]] = {"ok": False, "provider": "derived",
                                   "detail": "成分序列不足，無法計算"}
    return series_map, notes


# ---------------------------------------------------------------- 判定

def _assess_indicator(indicator, series_map, metrics, notes):
    key = indicator["key"]
    series = series_map.get(key, Series())
    value = series.latest()
    unit = indicator.get("unit", "")
    decimals = indicator.get("decimals", 2)

    status, label = "unknown", None
    basis = None
    if value is not None:
        if indicator.get("bands"):
            status, label = _match_bands(indicator["bands"], value)
            basis = "水位"
        elif indicator.get("change_bands"):
            cfg = indicator["change_bands"]
            change = series.change(cfg.get("window", 1))
            status, label = _match_bands(cfg["bands"], change)
            basis = "%d 期變動" % cfg.get("window", 1)
        else:
            # 沒有閾值帶的指標（USD/JPY、DXY、2Y）不該亮綠燈——它們是
            # 對照用的讀數，硬給一個「正常」會讓人誤以為已經檢查過了。
            status, label = "info", None
            basis = "僅記錄"
        if status is None:
            status = "unknown"

    change_1 = series.change(1)
    if unit == "%" and change_1 is not None:
        change_text = _fmt_signed(change_1 * 100, 0, "bps")
    else:
        change_text = _fmt_signed(change_1, decimals, unit)

    note = notes.get(key, {})
    return {
        "key": key,
        "label": indicator.get("label", key),
        "tier": indicator.get("tier"),
        "ext": bool(indicator.get("ext")),
        "star": bool(indicator.get("star")),
        "hidden": bool(indicator.get("hidden")),
        "unit": unit,
        "decimals": decimals,
        "value": value,
        "display": _fmt(value, decimals, unit),
        "date": series.latest_date(),
        "points": len(series),
        "status": status,
        "status_label": label or STATUS_TEXT.get(status, status),
        "basis": basis,
        "change_1": change_1,
        "change_display": change_text,
        "threshold_text": indicator.get("threshold_text", ""),
        "freq": indicator.get("freq", ""),
        "source_label": indicator.get("source_label", ""),
        "note": indicator.get("note", ""),
        "fetch_ok": note.get("ok", True),
        "fetch_detail": note.get("detail", ""),
        "fetch_provider": note.get("provider"),
    }


def _format_readout(template, var_names, metrics):
    if not template:
        return ""
    values = [metrics.get(name) for name in var_names]
    if any(v is None for v in values):
        return template if not var_names else "資料不足"
    try:
        return template % tuple(values)
    except (TypeError, ValueError):
        return ""


def _verdict(level, ladder_titles, tier_status, indicators_by_key, fired, metrics):
    worst_tier = None
    worst = "ok"
    for tier, info in tier_status.items():
        if STATUS_ORDER.get(info["status"], -1) > STATUS_ORDER.get(worst, -1):
            worst, worst_tier = info["status"], tier
    tier_name = tier_status.get(worst_tier, {}).get("short", "各層") if worst_tier else "各層"

    headline = {
        1: "沒有流動性危機。壓力集中在%s，性質是重定價。" % tier_name,
        2: "壓力已從估值層走進信用層——HY OAS 進入擴張區。",
        3: "去槓桿事件進行中：波動率與相關性同時上升。",
        4: "資金水管出現緊張。這是真正的流動性事件，不是重定價。",
        5: "主權信譽層級的壓力：殖利率與美元同時失守。",
    }.get(level, ladder_titles.get(level, ""))

    paragraphs = []

    sofr = indicators_by_key.get("sofr_iorb")
    if sofr and sofr["value"] is not None:
        if sofr["value"] < 0:
            paragraphs.append(
                "資金水管仍寬鬆——SOFR 在 IORB 之下 %.0f bps，隔夜擔保融資成本沒有緊張跡象。"
                "這是判斷「不是流動性事件」最乾淨的證據。" % abs(sofr["value"]))
        else:
            paragraphs.append(
                "SOFR 已站上 IORB %.0f bps（連續 %s 日為正）。這是水管層的直接訊號，"
                "優先度高於其他所有指標。" % (sofr["value"], metrics.get("sofr_iorb_pos_streak") or 0))
    else:
        paragraphs.append("資金水管指標（SOFR−IORB）本次未取得資料，Tier 1 判讀暫缺。")

    y30, y2, slope = (indicators_by_key.get(k) for k in ("y30", "y2", "slope_2s30s"))
    if y30 and y30["value"] is not None:
        shape = ""
        if slope and slope["value"] is not None:
            direction = slope["change_1"]
            if direction is not None and direction > 0:
                shape = "，曲線續陡（2s30s %s，單日 %s）" % (slope["display"], slope["change_display"])
            elif direction is not None and direction < 0:
                shape = "，曲線走平（2s30s %s，單日 %s）" % (slope["display"], slope["change_display"])
            else:
                shape = "，2s30s %s" % slope["display"]
        paragraphs.append(
            "長端在 %s（單日 %s）%s。看曲線形狀而非單一水位：短端跌而長端噴是期限溢價，"
            "兩者同漲才是升息預期。" % (y30["display"], y30["change_display"] or "持平", shape))

    hy, vix, move = (indicators_by_key.get(k) for k in ("hy_oas", "vix", "move"))
    credit_bits = []
    if hy and hy["value"] is not None:
        credit_bits.append("HY OAS %s（%s）" % (hy["display"], hy["status_label"]))
    if vix and vix["value"] is not None:
        credit_bits.append("VIX %s" % vix["display"])
    if move and move["value"] is not None:
        credit_bits.append("MOVE %s" % move["display"])
    if credit_bits:
        paragraphs.append(
            "信用與波動率：%s。信用是所有崩盤的領先指標，波動率是事件驅動——"
            "兩者同時轉向才代表事件在擴散。" % "、".join(credit_bits))

    if fired:
        paragraphs.append("本次觸發的升級／盤中引信：%s。任一成立即應重新評估。"
                          % "、".join("「%s」" % t["code"] for t in fired))
    else:
        paragraphs.append("本次沒有任何升級觸發器或盤中引信成立。")

    return {"level": level, "headline": headline, "paragraphs": paragraphs}


def build_snapshot(indicator_cfg, rules_cfg, series_map, notes, scan_time, data_notes=None):
    unit_map = {i["key"]: i.get("unit", "") for i in indicator_cfg}
    metrics = build_metrics(series_map, unit_map)

    indicators = [_assess_indicator(i, series_map, metrics, notes) for i in indicator_cfg]
    by_key = {i["key"]: i for i in indicators}

    tiers = {}
    for tier in rules_cfg.get("_tiers", []) or []:
        tiers[tier["n"]] = tier
    tier_status = {}
    for indicator in indicators:
        if indicator["hidden"] or indicator["tier"] is None:
            continue
        tier = indicator["tier"]
        entry = tier_status.setdefault(tier, {"status": "unknown", "driver": None})
        if STATUS_ORDER.get(indicator["status"], -1) > STATUS_ORDER.get(entry["status"], -1):
            entry["status"] = indicator["status"]
            entry["driver"] = indicator
    tier_summary = {}
    for tier, entry in sorted(tier_status.items()):
        meta = tiers.get(tier, {})
        driver = entry["driver"]
        tier_summary[tier] = {
            "n": tier,
            "short": meta.get("short", "TIER %d" % tier),
            "title": meta.get("title", ""),
            "status": entry["status"],
            "label": driver["status_label"] if driver else STATUS_TEXT["unknown"],
            "readout": ("%s %s" % (driver["label"], driver["display"])) if driver else "—",
        }

    tripwires = []
    for wire in rules_cfg.get("tripwires", []):
        state = evaluate(wire["expr"], metrics)
        tripwires.append({
            "id": wire["id"],
            "group": wire.get("group", ""),
            "code": wire["code"],
            "desc": wire.get("desc", ""),
            "expr": wire["expr"],
            "state": state,
        })
    fired = [w for w in tripwires if w["state"] is True]

    ladder = []
    current_level = 1
    for rung in rules_cfg.get("ladder", []):
        state = evaluate(rung.get("expr"), metrics)
        entry = {
            "level": rung["level"],
            "title": rung["title"],
            "signal": rung.get("signal", ""),
            "state": state,
            "readout": _format_readout(rung.get("readout"), rung.get("readout_vars", []), metrics),
        }
        ladder.append(entry)
        if state is True:
            current_level = max(current_level, rung["level"])
    for entry in ladder:
        entry["here"] = entry["level"] == current_level

    chains = []
    for chain in rules_cfg.get("chains", []):
        nodes = []
        live_count = 0
        for index, node in enumerate(chain["nodes"], start=1):
            if node.get("expr"):
                state = evaluate(node["expr"], metrics)
                node_state = "live" if state is True else ("unknown" if state is None else "cold")
            else:
                node_state = node.get("default_state", "cold")
            if node_state == "live":
                live_count += 1
            nodes.append({
                "step": node.get("step", "%02d" % index),
                "label": node["label"],
                "cond": node.get("cond", ""),
                "jump": node.get("jump"),
                "state": node_state,
            })
        armed = any(n["state"] == "armed" for n in nodes)
        if live_count >= len(nodes) - 1:
            chain_state, chain_status = "CRITICAL", "alarm"
        elif live_count >= 3:
            chain_state, chain_status = "ACTIVE", "press"
        elif live_count >= 1:
            chain_state, chain_status = "ACTIVE", "press" if live_count >= 2 else "watch"
        elif armed:
            chain_state, chain_status = "ARMED", "watch"
        else:
            chain_state, chain_status = "COLD", "ok"
        chains.append({
            "id": chain["id"],
            "title": chain["title"],
            "note": chain.get("note", ""),
            "nodes": nodes,
            "live": live_count,
            "total": len(nodes),
            "state": chain_state,
            "status": chain_status,
        })

    crowding = []
    for card in rules_cfg.get("crowding", []):
        state = evaluate(card.get("state_expr"), metrics)
        chosen = card.get("state_true" if state else "state_false", {})
        if state is None:
            chosen = {"level": "unknown", "text": "資料不足"}
        crowding.append({
            "title": card["title"],
            "level": chosen.get("level", "watch"),
            "state_text": chosen.get("text", ""),
            "density": card.get("density", ""),
            "trigger": card.get("trigger", ""),
            "readout": _format_readout(card.get("live_readout"), card.get("live_vars", []), metrics),
        })

    ladder_titles = {r["level"]: r["title"] for r in rules_cfg.get("ladder", [])}
    verdict = _verdict(current_level, ladder_titles, tier_summary, by_key, fired, metrics)

    overall = "ok"
    for info in tier_summary.values():
        overall = status_worse(overall, info["status"])

    return {
        "scan_time": scan_time,
        "data_as_of": max([i["date"] for i in indicators if i["date"]] or [None]),
        "level": current_level,
        "overall_status": overall,
        "verdict": verdict,
        "tiers": tier_summary,
        "indicators": indicators,
        "tripwires": tripwires,
        "ladder": ladder,
        "chains": chains,
        "crowding": crowding,
        "data_notes": data_notes or [],
    }
