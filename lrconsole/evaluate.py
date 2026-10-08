"""把序列 + 規則變成一份完整判定（snapshot）。

輸出是一個純 dict，render 與 diff 都只吃這個 dict，所以任何時候都可以把
reports/snapshot-*.json 拿出來重畫或比對，不必重抓資料。
"""

import re
import statistics
from datetime import datetime, timedelta, timezone

from .expr import comparison_terms, evaluate, evaluate_value, referenced_names
from .series import Series, build_metrics

__all__ = ["build_snapshot", "breadth_lookback", "series_as_of", "status_breadth",
           "stale_limit_for", "stale_limit_for_indicator", "STATUS_ORDER", "status_worse"]

STATUS_ORDER = {"unknown": -1, "info": -1, "ok": 0, "watch": 1, "press": 2, "alarm": 3}
STATUS_TEXT = {"ok": "正常", "watch": "警示", "press": "明確壓力", "alarm": "警報",
               "unknown": "無資料", "info": "僅記錄"}

# 「多久沒更新算過期」必須跟著各指標自己宣告的 freq 走。一律用 7 天的話，
# 月頻的非農在 8/1 當天最新的本來就是 6 月的數字，會每天被標成「已 61 天
# 未更新」——一個天天亮的警告等於沒有警告，真正該注意的那次就沒人看了。
#
# 門檻抓得比發佈週期寬鬆，因為量的是「觀測值的日期」而不是「上次更新的
# 時間」，兩者差一整個發佈延遲：
#   每日 7  ── 連假加上資料修正，4～5 天是正常的
#   每週 14 ── H.4.1 的資料日是週三、週四才發，最舊會到 8～9 天
#   每月 75 ── FRED 的月頻series 標在該月 1 號，6 月的數字要等 7 月初才發，
#              而 7 月的要等 8 月初；8/6 當天「6 月」已經 66 天大卻完全正常
_STALE_LIMIT_DAYS = (("每日", 7), ("每週", 14), ("每月", 75))
DEFAULT_STALE_LIMIT_DAYS = 14


def stale_limit_for(freq):
    """freq 寫成「每週三」「每週四」這種帶星期的形式，所以比前綴而不是相等。"""
    text = (freq or "").strip()
    for prefix, limit in _STALE_LIMIT_DAYS:
        if text.startswith(prefix):
            return limit
    return DEFAULT_STALE_LIMIT_DAYS


def stale_limit_for_indicator(indicator):
    """指標自己寫死的 stale_limit_days 優先，否則按 freq 推。"""
    return indicator.get("stale_limit_days") or stale_limit_for(indicator.get("freq"))


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


def _band_track(bands, value):
    """把閾值帶攤成一條可畫的軌道，並算出「離下一個門檻還有多遠」。

    這是給一般讀者看的關鍵資訊：與其記住「HY OAS 350 是門檻」，不如直接
    講「現在 284，離門檻還有 66bps」。回傳 None 代表這格沒有閾值帶。
    """
    if not bands or value is None:
        return None

    edges = sorted({e for band in bands for e in (band.get("min"), band.get("max"))
                    if e is not None})
    if not edges:
        return None

    span = (edges[-1] - edges[0]) or abs(edges[0]) or 1.0
    lo = min(edges[0] - span * 0.35, value - span * 0.1)
    hi = max(edges[-1] + span * 0.35, value + span * 0.1)
    width = (hi - lo) or 1.0

    # 逐點取樣再合併，而不是自己推區間代數。閾值帶是「由上而下第一個吻合
    # 者勝出」，所以沒寫 min 的那些帶其實隱含「前一帶的上界」當下界——直接
    # 拿 lo 當下界會讓每一段都從最左邊開始、彼此重疊，寬度加總遠超過 100%。
    # 用 _match_bands 本人來判定每個取樣點，語意就不可能跟燈號判定不一致。
    samples = 240
    marks = []
    for i in range(samples):
        point = lo + (i + 0.5) / samples * width
        status, label = _match_bands(bands, point)
        marks.append((status or "unknown", label))

    segments = []
    for i, (status, label) in enumerate(marks):
        if segments and segments[-1]["status"] == status and segments[-1]["label"] == label:
            segments[-1]["width_pct"] += 100.0 / samples
            segments[-1]["to"] = lo + (i + 1) / samples * width
            continue
        segments.append({
            "status": status,
            "label": label or STATUS_TEXT.get(status, ""),
            "start_pct": i / samples * 100.0,
            "width_pct": 100.0 / samples,
            "from": lo + i / samples * width,
            "to": lo + (i + 1) / samples * width,
        })

    above = [e for e in edges if e > value]
    below = [e for e in edges if e < value]
    next_edge = above[0] if above else None
    prev_edge = below[-1] if below else None

    return {
        "lo": lo,
        "hi": hi,
        "marker_pct": max(0.0, min(100.0, (value - lo) / width * 100.0)),
        "segments": segments,
        "next_edge": next_edge,
        "next_distance": None if next_edge is None else next_edge - value,
        "prev_edge": prev_edge,
        "prev_distance": None if prev_edge is None else value - prev_edge,
    }


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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
            # 未來日期可能來自這次抓取，也可能是舊 history.csv 留下來的，
            # 所以在合併之後再濾一次。
            merged = Series([(d, v) for d, v in merged.points if d <= today])
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

    track = _band_track(indicator.get("bands"), value)
    distance_text = ""
    if track and track["next_distance"] is not None:
        distance_text = "離 %s 還有 %s" % (
            _fmt(track["next_edge"], decimals, unit),
            _fmt(abs(track["next_distance"]), decimals, unit))
    elif track and track["prev_distance"] is not None:
        distance_text = "已超過 %s 門檻 %s" % (
            _fmt(track["prev_edge"], decimals, unit),
            _fmt(abs(track["prev_distance"]), decimals, unit))

    # 在自己的歷史區間裡站在哪個位置。你原始文件手寫的「仍在十年區間第 16
    # 百分位」就是這個——沒有它，284bps 到底算緊還是鬆只能靠記憶。
    # 在自己的歷史區間裡站在哪個位置。原始文件手寫的「仍在十年區間第 16
    # 百分位」就是這個——沒有它，284bps 到底算緊還是鬆只能靠記憶。
    pct_rank = None
    span_years = None
    if len(series) >= 60 and value is not None:
        below = sum(1 for v in series.values if v < value)
        pct_rank = below / len(series) * 100.0
        try:
            first = datetime.strptime(series.dates[0], "%Y-%m-%d")
            last = datetime.strptime(series.dates[-1], "%Y-%m-%d")
            span_years = (last - first).days / 365.25
        except ValueError:
            span_years = None

    stale_days = None
    if series.latest_date():
        try:
            stale_days = (datetime.now(timezone.utc).date()
                          - datetime.strptime(series.latest_date(), "%Y-%m-%d").date()).days
        except ValueError:
            stale_days = None
    # 少數來源的「觀測頻率」與「發佈節奏」是兩回事（見 stale_limit_days 的
    # 說明），那種就直接寫死門檻，不要為了消警告去謊報 freq——freq 會印在
    # 頁面的來源欄給讀者看。
    stale_limit = stale_limit_for_indicator(indicator)
    is_stale = stale_days is not None and stale_days > stale_limit

    note = notes.get(key, {})
    return {
        "key": key,
        "track": track,
        "stale_days": stale_days,
        "stale_limit": stale_limit,
        "stale": is_stale,
        "pct_rank": pct_rank,
        "span_years": span_years,
        "range_text": ("近 %.1f 年區間第 %.0f 百分位" % (span_years, pct_rank))
        if pct_rank is not None and span_years else "",
        "distance_text": distance_text,
        "severity": max(0, STATUS_ORDER.get(status, -1)) + 1 if status in STATUS_ORDER
        and status not in ("unknown", "info") else 0,
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


# ---------- 距下一階還有多遠 ----------
#
# 每一階的規則都是「某個變數越過某個門檻」。刻度把它攤成一條線：
#   左端＝平常的樣子，右端＝觸發。
# 「平常」有明確定義，不是挑一個好看的數字：
#   水位（hy_oas、vix…）    → 近兩年的中位數
#   變動（usdjpy_pct1…）    → 近兩年「同一種變動」的典型幅度，取觸發的方向。
#                             用 0 當錨點的話，USD/JPY 隨便一天 −0.4% 都會被
#                             畫成往「去槓桿」走了兩成，那只是日常雜訊
#   連續天數（_pos_streak） → 0
# 門檻從規則本身拆出來（expr.comparison_terms），不另外寫一份。

_CHANGE_METRIC = re.compile(r"^(?P<key>.+)_(?P<kind>d|pct)(?P<n>\d+)(?P<bps>_bps)?$")
_STREAK_METRIC = re.compile(r"^(?P<key>.+)_(?P<kind>pos|up)_streak$")
_WINDOW_WORD = {1: "單日", 5: "5 日", 20: "20 期", 60: "60 期"}
_ANCHOR_WINDOW = 500  # 約兩年的日資料，與 pct_rank 的「近兩年」同一個口徑


def _describe_var(name, cfg_by_key):
    """規則裡的變數名稱 → 它是哪個指標的哪一種量。"""
    cfg = cfg_by_key.get(name)
    if cfg is not None:
        return {"kind": "level", "key": name, "cfg": cfg, "label": cfg.get("label", name)}
    match = _CHANGE_METRIC.match(name)
    if match and match.group("key") in cfg_by_key:
        cfg = cfg_by_key[match.group("key")]
        n = int(match.group("n"))
        return {"kind": "change", "key": match.group("key"), "cfg": cfg, "n": n,
                "pct": match.group("kind") == "pct", "bps": bool(match.group("bps")),
                "label": "%s %s" % (cfg.get("label", match.group("key")),
                                    _WINDOW_WORD.get(n, "%d 期" % n))}
    match = _STREAK_METRIC.match(name)
    if match and match.group("key") in cfg_by_key:
        cfg = cfg_by_key[match.group("key")]
        word = "連續為正" if match.group("kind") == "pos" else "連續上升"
        return {"kind": "streak", "key": match.group("key"), "cfg": cfg,
                "label": "%s %s" % (cfg.get("label", match.group("key")), word)}
    return {"kind": "other", "key": None, "cfg": {}, "label": name}


def _anchor(desc, series_map, threshold):
    if desc["kind"] in ("streak", "other"):
        return 0.0
    series = series_map.get(desc["key"])
    if not series:
        return None
    if desc["kind"] == "level":
        return statistics.median(series.values[-_ANCHOR_WINDOW:])
    n = desc["n"]
    values = series.values[-(_ANCHOR_WINDOW + n):]
    moves = []
    for prior, latest in zip(values, values[n:]):
        if desc["pct"]:
            if prior:
                moves.append((latest / prior - 1.0) * 100.0)
        else:
            moves.append((latest - prior) * (100.0 if desc["bps"] else 1.0))
    if not moves:
        return None
    typical = statistics.median([abs(m) for m in moves])
    return typical if threshold > 0 else -typical if threshold < 0 else 0.0


def _period_word(cfg):
    freq = cfg.get("freq") or ""
    for prefix, word in (("每日", "天"), ("每週", "週"), ("每月", "個月")):
        if freq.startswith(prefix):
            return word
    return "期"


def _fmt_var(value, desc):
    if value is None:
        return "—"
    cfg = desc["cfg"]
    unit, decimals = cfg.get("unit", ""), cfg.get("decimals", 2)
    if desc["kind"] == "level":
        return _fmt(value, decimals, unit)
    if desc["kind"] == "change":
        if desc["pct"]:
            return "%+.1f%%" % value
        if desc["bps"]:
            return "%+.0f bps" % value
        if unit == "%":
            # y30_d5 這類量的原始單位是百分點；讀者看的是 bps
            return "%+.0f bps" % (value * 100.0)
        return _fmt_signed(value, decimals, unit)
    if desc["kind"] == "streak":
        return "%d %s" % (round(value), _period_word(cfg))
    return "%.2f" % value


def _position(current, anchor, op, threshold):
    """0＝平常、1＝觸發。只有條件真的成立才會是 1，跟該階的燈號永遠一致。"""
    if current is None or anchor is None:
        return None
    above = op in (">", ">=")
    met = {">": current > threshold, ">=": current >= threshold,
           "<": current < threshold, "<=": current <= threshold}[op]
    if met:
        return 1.0
    span = (threshold - anchor) if above else (anchor - threshold)
    if span <= 0:
        # 「平常」本身就在門檻外——這條規則平常就該亮著，畫成刻度沒有意義
        return None
    progress = ((current - anchor) if above else (anchor - current)) / span
    # 取到千分位：SRF 平常是 $0.001B、今天 $0.002B 這種浮點雜訊，不該讓它
    # 在「誰最接近觸發」的排序裡贏過畫面上同樣是 0% 的另一條
    return round(max(0.0, min(0.99, progress)), 3)


def _rung_proximity(expr, metrics, series_map, cfg_by_key):
    parsed = comparison_terms(expr)
    if parsed is None:
        return None
    mode, terms = parsed
    gauges = []
    for name, op, threshold in terms:
        desc = _describe_var(name, cfg_by_key)
        current = metrics.get(name)
        anchor = _anchor(desc, series_map, threshold)
        gauges.append({
            "var": name,
            "label": desc["label"],
            "op": op,
            "current": current,
            "trigger": threshold,
            "anchor": anchor,
            "position": _position(current, anchor, op, threshold),
            "current_text": _fmt_var(current, desc),
            "trigger_text": _fmt_var(threshold, desc),
        })
    positions = [g["position"] for g in gauges]
    if any(p is None for p in positions):
        # 跟 evaluate() 同一個原則：任一半沒資料就整條未知。用剩下那半畫出
        # 「離觸發很遠」，等於把抓不到當成沒問題。
        position = None
    else:
        position = min(positions) if mode == "all" else max(positions)
    if mode == "any":
        # 「或」的時候只要一條成立就夠，最接近觸發的那條排第一
        gauges.sort(key=lambda g: -(g["position"] or 0.0))
    return {"mode": mode, "position": position, "gauges": gauges}


# ---------- 整盤亮燈：同一個階也有輕重 ----------

_LIT = ("alarm", "press", "watch")


def status_breadth(snapshot):
    """非隱藏指標的燈號分布。unknown／info 不算進分母——沒燈號不等於正常。"""
    counts = {"alarm": 0, "press": 0, "watch": 0, "ok": 0}
    for indicator in snapshot.get("indicators", []):
        if not indicator.get("hidden") and indicator.get("status") in counts:
            counts[indicator["status"]] += 1
    counts["lit"] = sum(counts[s] for s in _LIT)
    counts["rated"] = counts["lit"] + counts["ok"]
    return counts


def series_as_of(series_map, date):
    return {key: Series([p for p in series.points if p[0] <= date])
            for key, series in series_map.items()}


def breadth_lookback(indicator_cfg, rules_cfg, series_map, snapshot, days=30):
    """今天的燈號分布，以及「同一套門檻套在 days 天前的資料上」的分布。

    不讀 30 天前存下來的 snapshot：那份是用當時的門檻判的，中間只要調過
    一次閾值，比出來的差異就是「尺換了」而不是「市場變了」——跟 --rebuild
    不准重算變更清單是同一個道理。資料也用現在這份歷史，事後回補的點
    （例如 Yahoo 補回 7 月底的 MOVE）會一起算進去，比當時看到的更接近真相。
    """
    try:
        ref = datetime.strptime((snapshot.get("scan_time") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    then = (ref - timedelta(days=days)).isoformat()
    past = build_snapshot(indicator_cfg, rules_cfg, series_as_of(series_map, then), {}, then)
    return {"days": days, "then_date": then,
            "now": status_breadth(snapshot), "then": status_breadth(past)}


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
        elif sofr["value"] == 0:
            paragraphs.append(
                "SOFR 與 IORB 齊平（0 bps）。還沒翻正，但寬鬆的緩衝剛好用完——"
                "這格從負值走到零，本身就是水管在收緊的訊號，接下來盯的是它會不會站上正值。")
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
            # 嚴重度用 1–4 的格數呈現，讓顏色以外還有一個可讀的通道——
            # 警示（琥珀）與明確壓力（橘）在紅綠色盲下幾乎同色。
            "severity": max(0, STATUS_ORDER.get(entry["status"], -1)) + 1
            if entry["status"] not in ("unknown", "info") else 0,
            "driver_key": driver["key"] if driver else None,
            "driver_label": driver["label"] if driver else None,
            "plain": meta.get("plain", ""),
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
    cfg_by_key = {i["key"]: i for i in indicator_cfg}
    for rung in rules_cfg.get("ladder", []):
        state = evaluate(rung.get("expr"), metrics)
        entry = {
            "level": rung["level"],
            "title": rung["title"],
            "signal": rung.get("signal", ""),
            "state": state,
            "readout": _format_readout(rung.get("readout"), rung.get("readout_vars", []), metrics),
            # 第 1 階的規則是 true，拆不出門檻，這格會是 None——它是地板，
            # 沒有「離它多遠」可言。
            "proximity": _rung_proximity(rung.get("expr"), metrics, series_map, cfg_by_key),
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
            # 歷史先例：這條鏈實際走完時的形狀。重點不是跌幅，是各個節點
            # 出現的順序——「日圓在第幾幕」決定它是領先訊號還是落後訊號。
            "precedents": chain.get("precedents", []),
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
