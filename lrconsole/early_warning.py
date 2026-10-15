"""預警層：看「速度」與「背離」，不看水位。

水位型的引信（HY > 350、VIX > 30）要等越過門檻才亮，本質上是確認。2026 年
9 月底的那一段就是這樣：9/25–10/12 這五條全亮，股市還在創新高；水位型的
引信一條都沒亮，直到 10/14 股市補跌。

這個模組做三件事：
  1. 今天亮了幾條、各自從哪天開始亮
  2. 這一波預警從哪天開始（連續亮著 ≥ min_signals 條，中間斷 ≤ merge_gap 天
     視為同一波）
  3. 用同一套規則把整段歷史重跑一遍，列出每一波的結果——包括誤報

回測與即時判讀必須用一模一樣的公式，否則回測說的就不是這台控制盤會說的
話。metric_history() 逐一對應 series.build_metrics() 的定義，測試會在每一個
歷史日期上比對兩者。
"""

import bisect
import re
from datetime import date as date_type

from .evaluate import describe_terms
from .expr import evaluate, referenced_names
from .series import _WINDOWS

__all__ = ["build", "metric_history", "find_episodes"]

_CHANGE = re.compile(r"^(?P<key>.+)_(?P<kind>d|pct)(?P<n>\d+)(?P<bps>_bps)?$")
_WINDOWED = re.compile(r"^(?P<key>.+)_(?P<kind>dd|ru|min|max)(?P<n>\d+)$")
_SUFFIX = re.compile(r"^(?P<key>.+)_(?P<kind>prev|n|pos_streak|up_streak)$")


def _base_key(name, series_map):
    """變數名稱 → 它取自哪一條序列。認不得就回傳 None。"""
    if name in series_map:
        return name
    for pattern in (_CHANGE, _WINDOWED, _SUFFIX):
        match = pattern.match(name)
        if match and match.group("key") in series_map:
            return match.group("key")
    return None


def metric_history(name, series, unit="", key=None):
    """某個變數在序列每一個觀測日的值（與 series.points 對齊）。

    每一種都照抄 Series 的方法：change／pct_change 數「觀測值」不數日曆日，
    drawdown 的窗口不滿 60 筆時就用現有的（build_metrics 也是這樣），
    _dN_bps 只有單位是 % 的序列才有。build_metrics 沒有產生的變數（例如
    _d10），這裡也一律是 None——即時判讀是「未知」，回測就不能算出一個數字。
    """
    values = series.values
    count = len(values)
    out = [None] * count

    match = _CHANGE.match(name)
    if match:
        n, pct, bps = int(match.group("n")), match.group("kind") == "pct", bool(match.group("bps"))
        if n not in _WINDOWS or (bps and (pct or unit != "%")):
            return out
        for i in range(n, count):
            prior, latest = values[i - n], values[i]
            if pct:
                out[i] = None if prior == 0 else (latest / prior - 1.0) * 100.0
            else:
                out[i] = (latest - prior) * (100.0 if bps else 1.0)
        return out

    match = _WINDOWED.match(name)
    if match:
        n, kind = int(match.group("n")), match.group("kind")
        if n not in ((60,) if kind in ("dd", "ru") else (60, 250)):
            return out
        for i in range(count):
            window = values[max(0, i - n + 1): i + 1]
            if kind in ("dd", "ru"):
                if len(window) < 2:
                    continue
                ref = max(window) if kind == "dd" else min(window)
                out[i] = None if ref == 0 else (values[i] / ref - 1.0) * 100.0
            else:
                out[i] = min(window) if kind == "min" else max(window)
        return out

    match = _SUFFIX.match(name)
    if match:
        kind = match.group("kind")
        streak = 0
        for i in range(count):
            if kind == "prev":
                out[i] = values[i - 1] if i >= 1 else None
            elif kind == "n":
                out[i] = i + 1
            elif kind == "pos_streak":
                streak = streak + 1 if values[i] > 0 else 0
                out[i] = streak
            else:
                streak = streak + 1 if i >= 1 and values[i] > values[i - 1] else 0
                out[i] = streak
        return out

    # 水位本身。認不得的名稱（例如 x_foo）不能被當成水位——那是即時判讀裡
    # 不存在的變數，回測也只能是未知
    return list(values) if key is None or name == key else out


class _AsOf:
    """「某一天能看到的最新值」：信用利差和股市的交易日不一樣，用日期對齊。"""

    def __init__(self, dates, values):
        self.dates, self.values = dates, values

    def at(self, date):
        i = bisect.bisect_right(self.dates, date) - 1
        return self.values[i] if i >= 0 else None


def _replay(exprs, series_map, unit_map):
    """把一組規則在每個歷史日期上重跑。回傳 (日期軸, {expr: [狀態...]}, 今天的變數值)。"""
    names = set()
    for expr in exprs:
        names |= referenced_names(expr)
    lookups = {}
    axis = set()
    for name in names:
        key = _base_key(name, series_map)
        if key is None:
            continue
        series = series_map[key]
        lookups[name] = _AsOf(series.dates,
                              metric_history(name, series, unit_map.get(key, ""), key=key))
        axis.update(series.dates)
    # 每個交易日判一次。FRED 的 ICE 利差在月底會標週末日期（2026/2/28 是週六），
    # 匯率週日晚上就開盤——把週末算成判讀日，會憑空多出「起點是週六」的一波，
    # 也會把「提前幾個交易日」灌水。週末的值不會丟：週一用最新值時會讀到它。
    axis = sorted(d for d in axis if date_type.fromisoformat(d).weekday() < 5)

    states = {expr: [] for expr in exprs}
    for date in axis:
        variables = {name: lookup.at(date) for name, lookup in lookups.items()}
        for expr in exprs:
            states[expr].append(evaluate(expr, variables))
    latest = {name: lookup.at(axis[-1]) for name, lookup in lookups.items()} if axis else {}
    return axis, states, latest


def find_episodes(lit_counts, min_signals, merge_gap):
    """一波預警＝亮 ≥ min_signals 條的日子；中間斷 ≤ merge_gap 天算同一波。
    回傳 [(起點索引, 終點索引), ...]。"""
    episodes = []
    for i, count in enumerate(lit_counts):
        if count is None or count < min_signals:
            continue
        if episodes and i - episodes[-1][1] <= merge_gap + 1:
            episodes[-1] = (episodes[-1][0], i)
        else:
            episodes.append((i, i))
    return episodes


def _classify(start, axis, calm, outcome, window):
    """亮起時股市已經在跌＝確認；否則看之後 window 天內有沒有 outcome。"""
    if calm[start] is not True:
        return {"result": "concurrent"}
    ahead = range(start + 1, min(len(axis), start + 1 + window))
    for i in ahead:
        if outcome[i] is True:
            return {"result": "hit", "lead": i - start, "hit_date": axis[i]}
    if len(ahead) < window:
        return {"result": "pending", "elapsed": len(ahead)}
    return {"result": "miss"}


def build(rules_cfg, indicator_cfg, series_map):
    cfg = (rules_cfg or {}).get("early_warning")
    if not cfg or not cfg.get("signals"):
        return None
    unit_map = {i["key"]: i.get("unit", "") for i in indicator_cfg}
    cfg_by_key = {i["key"]: i for i in indicator_cfg}
    record = cfg.get("record", {})
    signals = cfg["signals"]
    exprs = [s["expr"] for s in signals]
    extra = [e for e in (record.get("calm_expr"), record.get("outcome_expr")) if e]
    axis, states, latest = _replay(exprs + extra, series_map, unit_map)
    if not axis:
        return None

    min_signals = cfg.get("min_signals", 2)
    lit = []
    for i in range(len(axis)):
        row = [states[e][i] for e in exprs]
        # 任一條未知就整天未知：拿已知的幾條數出「只亮 1 條」，等於把抓不到當成沒事
        lit.append(None if any(s is None for s in row) else sum(1 for s in row if s))

    episodes = find_episodes(lit, min_signals, cfg.get("merge_gap", 5))
    calm = states.get(record.get("calm_expr"), [True] * len(axis))
    outcome = states.get(record.get("outcome_expr"), [None] * len(axis))
    window = record.get("window", 30)
    history = []
    for start, end in episodes:
        entry = {"start": axis[start], "end": axis[end],
                 "ongoing": end == len(axis) - 1, "peak": max(lit[start:end + 1])}
        entry.update(_classify(start, axis, calm, outcome, window))
        history.append(entry)

    today = len(axis) - 1
    current = []
    for signal, expr in zip(signals, exprs):
        run = states[expr]
        since = None
        if run[today] is True:
            i = today
            while i > 0 and run[i - 1] is True:
                i -= 1
            since = axis[i]
        current.append({
            "id": signal["id"],
            "code": signal["code"],
            "desc": signal.get("desc", ""),
            "expr": expr,
            "state": run[today],
            "since": since,
            "terms": describe_terms(expr, latest, cfg_by_key),
        })

    active = lit[today] is not None and lit[today] >= min_signals
    first_valid = next((axis[i] for i, c in enumerate(lit) if c is not None), None)
    summary = {k: sum(1 for e in history if e["result"] == k)
               for k in ("hit", "miss", "pending", "concurrent")}
    return {
        "as_of": axis[today],
        "count": lit[today],
        "total": len(signals),
        "min_signals": min_signals,
        "active": active,
        "since": history[-1]["start"] if active and history else None,
        "days": (today - axis.index(history[-1]["start"]) + 1) if active and history else 0,
        "signals": current,
        "record": {
            "from": first_valid,
            "calm_label": record.get("calm_label", ""),
            "outcome_label": record.get("outcome_label", ""),
            "window": window,
            "summary": summary,
            "episodes": history,
        },
    }
