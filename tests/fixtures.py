"""合成歷史資料，讓整條管線可以完全離線測試。

不打任何網路，只產生 data/history.csv 給 scan.py --no-fetch 讀。
"""

import csv
import math
import os
from datetime import date, timedelta

__all__ = ["write_history", "CALM", "STRESS"]


def _business_days(count, end=None):
    end = end or date.today()
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _weekly(count, end=None, weekday=2):
    end = end or date.today()
    cursor = end
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    days = [cursor - timedelta(weeks=i) for i in range(count)]
    return list(reversed(days))


def _monthly(count, end=None):
    end = end or date.today().replace(day=1)
    days, year, month = [], end.year, end.month
    for _ in range(count):
        days.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(days))


# 每個情境給的是「最後一天的值 + 前 n 天的形狀」，形狀用簡單函數帶出來，
# 這樣單日變動、5 日變動、回落幅度都是真的，不是硬塞的。
CALM = {
    "daily": {
        "sofr": lambda i, n: 3.57 + 0.002 * math.sin(i / 7.0),
        "iorb": lambda i, n: 3.65,
        "srf": lambda i, n: 0.0,
        "rrp": lambda i, n: 180.0 - 0.4 * i,
        "hy_oas": lambda i, n: 260.0 + 0.06 * i,
        "ig_oas": lambda i, n: 92.0 + 0.01 * i,
        "ccc_oas": lambda i, n: 760.0 + 0.1 * i,
        "vix": lambda i, n: 15.0 + 2.0 * math.sin(i / 11.0),
        "vvix": lambda i, n: 92.0 + 5.0 * math.sin(i / 13.0),
        "move": lambda i, n: 85.0 + 4.0 * math.sin(i / 9.0),
        "vxtlt": lambda i, n: 13.0 + 1.2 * math.sin(i / 9.0),
        "y30": lambda i, n: 4.40 + 0.0004 * i,
        "y10": lambda i, n: 4.10 + 0.0003 * i,
        "y2": lambda i, n: 3.95 + 0.0002 * i,
        "term_premium": lambda i, n: 0.30 + 0.0003 * i,
        "usdjpy": lambda i, n: 148.0 + 0.02 * i,
        "dxy": lambda i, n: 100.0 + 0.004 * i,
        "wti": lambda i, n: 72.0 + 2.0 * math.sin(i / 17.0),
        "ndx": lambda i, n: 20000.0 * (1 + 0.0006 * i),
        "sox": lambda i, n: 5000.0 * (1 + 0.0007 * i),
        "n225": lambda i, n: 40000.0 * (1 + 0.0005 * i),
    },
    "weekly": {
        "tga": lambda i, n: 700.0 + 3.0 * math.sin(i / 5.0),
        "reserves": lambda i, n: 3200.0 - 1.0 * i,
        "walcl": lambda i, n: 6800.0 - 0.9 * i,
        "claims": lambda i, n: 218.0 + 2.0 * math.sin(i / 6.0),
    },
    "monthly": {
        "payems_level": lambda i, n: 160000.0 + 120.0 * i,
    },
}

STRESS = {
    "daily": {
        # 水管翻正並連續三日：這是唯一真正定義流動性危機的訊號。
        "sofr": lambda i, n: 3.72 if i >= n - 3 else 3.57,
        "iorb": lambda i, n: 3.65,
        "srf": lambda i, n: 62.0 if i >= n - 2 else 0.0,
        "rrp": lambda i, n: max(2.0, 120.0 - 0.9 * i),
        "hy_oas": lambda i, n: 280.0 + (12.0 * (i - (n - 20)) if i > n - 20 else 0.0),
        "ig_oas": lambda i, n: 95.0 + (1.6 * (i - (n - 20)) if i > n - 20 else 0.0),
        "ccc_oas": lambda i, n: 800.0 + (22.0 * (i - (n - 20)) if i > n - 20 else 0.0),
        "vix": lambda i, n: 16.0 + (2.0 * (i - (n - 10)) if i > n - 10 else 0.0),
        "vvix": lambda i, n: 95.0 + (4.0 * (i - (n - 10)) if i > n - 10 else 0.0),
        "move": lambda i, n: 95.0 + (6.0 * (i - (n - 10)) if i > n - 10 else 0.0),
        "vxtlt": lambda i, n: 15.0 + (1.1 * (i - (n - 10)) if i > n - 10 else 0.0),
        "y30": lambda i, n: 5.10 + (0.13 if i == n - 1 else 0.0) + 0.0005 * i,
        "y10": lambda i, n: 4.70 + (0.11 if i == n - 1 else 0.0) + 0.0004 * i,
        "y2": lambda i, n: 4.30 - 0.0002 * i,
        "term_premium": lambda i, n: 0.80 + 0.001 * i,
        # 最後一天日圓急升 3%：carry unwind 啟動的樣子。
        "usdjpy": lambda i, n: (163.0 if i < n - 1 else 158.1),
        "dxy": lambda i, n: 101.0 - (0.35 * (i - (n - 6)) if i > n - 6 else 0.0),
        "wti": lambda i, n: 88.0 + 0.02 * i,
        "ndx": lambda i, n: 24000.0 * (1 - 0.012 * max(0, i - (n - 15))),
        "sox": lambda i, n: 6000.0 * (1 - 0.015 * max(0, i - (n - 15))),
        "n225": lambda i, n: 62000.0 * (1 - 0.014 * max(0, i - (n - 15))),
    },
    "weekly": {
        "tga": lambda i, n: 700.0 + (70.0 if i == n - 1 else 0.0),
        "reserves": lambda i, n: 3200.0 - 22.0 * i,
        "walcl": lambda i, n: 6800.0 - 9.0 * i,
        "claims": lambda i, n: 240.0 + 0.5 * i,
    },
    "monthly": {
        "payems_level": lambda i, n: 160000.0 + 30.0 * i,
    },
}


def write_history(path, scenario, daily_points=260, weekly_points=80, monthly_points=24):
    rows = []

    days = _business_days(daily_points)
    for key, shape in scenario["daily"].items():
        for i, day in enumerate(days):
            rows.append((day.isoformat(), key, shape(i, len(days))))

    weeks = _weekly(weekly_points)
    for key, shape in scenario["weekly"].items():
        for i, day in enumerate(weeks):
            rows.append((day.isoformat(), key, shape(i, len(weeks))))

    months = _monthly(monthly_points)
    for key, shape in scenario["monthly"].items():
        for i, day in enumerate(months):
            rows.append((day.isoformat(), key, shape(i, len(months))))

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "key", "value"])
        for date_str, key, value in sorted(rows):
            writer.writerow([date_str, key, repr(round(value, 6))])
    return path
